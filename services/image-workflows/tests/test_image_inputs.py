"""Test image uploads without starting the RunPod worker or loading GPU dependencies."""
import base64
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

runpod_module = types.ModuleType("runpod")
runpod_serverless = types.ModuleType("runpod.serverless")
runpod_serverless.start = lambda *_args, **_kwargs: None
runpod_upload = types.SimpleNamespace(
    upload_image=lambda *_args, **_kwargs: None,
    upload_file_to_bucket=lambda *_args, **_kwargs: None,
)
runpod_utils = types.ModuleType("runpod.serverless.utils")
runpod_utils.rp_upload = runpod_upload
runpod_module.serverless = runpod_serverless
sys.modules.setdefault("runpod", runpod_module)
sys.modules.setdefault("runpod.serverless", runpod_serverless)
sys.modules.setdefault("runpod.serverless.utils", runpod_utils)
sys.modules.setdefault("runpod.serverless.utils.rp_upload", runpod_upload)

import handler

SIGNED_URL = "https://bucket.example.com/prompts/key.png?X-Amz-Signature=secret-signature"


class ImageInputTests(unittest.TestCase):
    def setUp(self):
        get = patch("handler.requests.get")
        post = patch("handler.requests.post")
        self.get, self.post = get.start(), post.start()
        self.addCleanup(get.stop)
        self.addCleanup(post.stop)

    def download(self, content_type="image/jpeg", chunks=(b"image bytes",)):
        response = self.get.return_value.__enter__.return_value
        response.headers = {"Content-Type": content_type}
        response.iter_content.return_value = iter(chunks)
        return response

    def uploaded(self):
        name, stream, mime = self.post.call_args.kwargs["files"]["image"]
        return name, stream.getvalue(), mime

    def test_url_download_and_upload(self):
        # A link may be in 'image' or 'data', beside base64, or in 'url'.
        for key in ("image", "data", "url"):
            with self.subTest(key=key):
                self.get.reset_mock()
                self.download()

                result = handler.upload_images([{"name": "photo.jpg", key: SIGNED_URL}])

                self.assertEqual(result["status"], "success")
                self.get.assert_called_once_with(SIGNED_URL, stream=True, timeout=(10, 60))
                self.assertEqual(self.uploaded(), ("photo.jpg", b"image bytes", "image/jpeg"))

    def test_base64_and_data_uri(self):
        encoded = base64.b64encode(b"image bytes").decode()
        for value in (encoded, "data:image/png;base64," + encoded):
            with self.subTest(value=value):
                result = handler.upload_images([{"name": "image.png", "image": value}])

                self.assertEqual(result["status"], "success")
                self.assertEqual(self.uploaded(), ("image.png", b"image bytes", "image/png"))
        self.get.assert_not_called()

    def test_invalid_downloads_are_not_uploaded(self):
        limit = handler.INPUT_DOWNLOAD_MAX_BYTES
        for content_type, chunks in (
            ("text/html", (b"html",)),
            ("image/png", ()),
            ("image/png", (b"x" * (limit + 1),)),
            ("image/png", (b"x" * limit, b"x")),
        ):
            with self.subTest(content_type=content_type, size=sum(map(len, chunks))):
                self.download(content_type, chunks)

                result = handler.upload_images([{"name": "image.png", "image": "http://example.com/image"}])

                self.assertEqual(result["status"], "error")
                self.post.assert_not_called()

    def test_size_limit_is_a_setting(self):
        self.download("image/png", (b"12345", b"6789"))

        with patch.object(handler, "INPUT_DOWNLOAD_MAX_BYTES", 8):
            result = handler.upload_images([{"name": "image.png", "url": SIGNED_URL}])

        self.assertEqual(result["status"], "error")
        self.assertIn("download limit", result["details"][0])
        self.post.assert_not_called()

    def test_http_error_and_timeout(self):
        response = self.download()
        response.raise_for_status.side_effect = handler.requests.HTTPError("HTTP 404")

        result = handler.upload_images([{"name": "image.png", "image": "https://example.com/image"}])

        self.assertEqual(result["status"], "error")
        self.get.side_effect = handler.requests.Timeout()

        result = handler.upload_images([{"name": "image.png", "image": "https://example.com/image"}])

        self.assertEqual(result["status"], "error")
        self.assertIn("Timed out", result["details"][0])
        self.post.assert_not_called()

    @patch("builtins.print")
    def test_download_errors_do_not_expose_the_signed_url(self, mock_print):
        failure = handler.requests.HTTPError(f"403 Client Error: Forbidden for url: {SIGNED_URL}")
        failure.response = types.SimpleNamespace(status_code=403)
        self.download().raise_for_status.side_effect = failure

        result = handler.upload_images([{"name": "image.png", "image": SIGNED_URL}])

        self.assertEqual(result["status"], "error")
        self.assertIn("HTTP 403", result["details"][0])
        for text in (str(result), str(mock_print.call_args_list)):
            self.assertNotIn("secret-signature", text)
            self.assertNotIn("bucket.example.com", text)

    def test_value_that_is_not_text_is_refused(self):
        result = handler.upload_images([{"name": "image.png", "image": {"nested": "value"}}])

        self.assertEqual(result["status"], "error")
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
