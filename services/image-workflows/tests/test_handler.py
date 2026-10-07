import base64
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Make sure the service root is known and can be used to import handler.py
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


class TestRunpodWorkerComfy(unittest.TestCase):
    def test_finalize_job_result_success_with_images(self):
        images = [
            {"data": "img1", "filename": "ComfyUI_00001_.png", "type": "base64"},
            {"data": "img2", "filename": "ComfyUI_00002_.png", "type": "base64"},
        ]

        result = handler._finalize_job_result(images)

        self.assertEqual(result["images"], images)

    def test_finalize_job_result_keeps_the_answer_of_earlier_releases(self):
        images = [
            {"data": "https://s3.example.com/a.png", "filename": "a.png", "type": "s3_url"},
            {"data": "https://s3.example.com/b.png", "filename": "b.png", "type": "s3_url"},
        ]

        result = handler._finalize_job_result(images)

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["message"], ["https://s3.example.com/a.png", "https://s3.example.com/b.png"]
        )

    def test_finalize_job_result_fails_when_no_images(self):
        result = handler._finalize_job_result([], warnings=["Node 55 produced unhandled output keys: ['text']."])
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Workflow produced no images.")
        self.assertIn("details", result)
        self.assertTrue(any("unhandled output keys" in item for item in result["details"]))
        self.assertTrue(result.get("refresh_worker"))

    def test_valid_input_with_workflow_only(self):
        input_data = {"workflow": {"key": "value"}}
        validated_data, error = handler.validate_input(input_data)
        self.assertIsNone(error)
        self.assertEqual(
            validated_data,
            {"workflow": {"key": "value"}, "images": None, "comfy_org_api_key": None},
        )

    def test_valid_input_with_workflow_and_images(self):
        input_data = {
            "workflow": {"key": "value"},
            "images": [{"name": "image1.png", "image": "base64string"}],
        }
        validated_data, error = handler.validate_input(input_data)
        self.assertIsNone(error)
        self.assertEqual(
            validated_data,
            {
                "workflow": {"key": "value"},
                "images": [{"name": "image1.png", "image": "base64string"}],
                "comfy_org_api_key": None,
            },
        )

    def test_valid_input_with_each_place_an_image_may_be(self):
        for key in ("image", "data", "url"):
            with self.subTest(key=key):
                images = [{"name": "image1.png", key: "https://example.com/image1.png"}]

                validated_data, error = handler.validate_input(
                    {"workflow": {"key": "value"}, "images": images}
                )

                self.assertIsNone(error)
                self.assertEqual(validated_data["images"], images)

    def test_input_missing_workflow(self):
        input_data = {"images": [{"name": "image1.png", "image": "base64string"}]}
        validated_data, error = handler.validate_input(input_data)
        self.assertIsNotNone(error)
        self.assertEqual(error, "Missing 'workflow' parameter")

    def test_input_with_invalid_images_structure(self):
        for images in ([{"name": "image1.png"}], [{"image": "base64string"}], ["image1.png"], "image1.png"):
            with self.subTest(images=images):
                validated_data, error = handler.validate_input(
                    {"workflow": {"key": "value"}, "images": images}
                )

                self.assertIsNone(validated_data)
                self.assertEqual(
                    error,
                    "'images' must be a list of objects with 'name' and one of "
                    "'image', 'data' or 'url'",
                )

    def test_invalid_json_string_input(self):
        input_data = "invalid json"
        validated_data, error = handler.validate_input(input_data)
        self.assertIsNotNone(error)
        self.assertEqual(error, "Invalid JSON format in input")

    def test_valid_json_string_input(self):
        input_data = '{"workflow": {"key": "value"}}'
        validated_data, error = handler.validate_input(input_data)
        self.assertIsNone(error)
        self.assertEqual(
            validated_data,
            {"workflow": {"key": "value"}, "images": None, "comfy_org_api_key": None},
        )

    def test_empty_input(self):
        input_data = None
        validated_data, error = handler.validate_input(input_data)
        self.assertIsNotNone(error)
        self.assertEqual(error, "Please provide input")

    @patch("handler.requests.get")
    def test_check_server_server_up(self, mock_requests):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_requests.return_value = mock_response

        result = handler.check_server("http://127.0.0.1:8188", 1, 50)
        self.assertTrue(result)

    @patch("handler.requests.get")
    def test_check_server_server_down(self, mock_requests):
        mock_requests.side_effect = handler.requests.RequestException()
        result = handler.check_server("http://127.0.0.1:8188", 1, 50)
        self.assertFalse(result)

    @patch("handler.requests.post")
    def test_queue_prompt(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"prompt_id": "123"}
        mock_post.return_value = mock_response

        result = handler.queue_workflow({"prompt": "test"}, "client-123")

        self.assertEqual(result, {"prompt_id": "123"})

    @patch("handler.requests.get")
    def test_get_history(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"key": "value"}
        mock_get.return_value = mock_response

        result = handler.get_history("123")

        self.assertEqual(result, {"key": "value"})
        mock_get.assert_called_once_with("http://127.0.0.1:8188/history/123", timeout=30)

    @patch("handler.requests.post")
    def test_upload_images_successful(self, mock_post):
        mock_response = unittest.mock.Mock()
        mock_response.status_code = 200
        mock_response.text = "Successfully uploaded"
        mock_post.return_value = mock_response

        test_image_data = base64.b64encode(b"Test Image Data").decode("utf-8")

        images = [{"name": "test_image.png", "image": test_image_data}]

        responses = handler.upload_images(images)

        self.assertEqual(len(responses), 3)
        self.assertEqual(responses["status"], "success")

    @patch("handler.requests.post")
    def test_upload_images_failed(self, mock_post):
        mock_response = unittest.mock.Mock()
        mock_response.status_code = 400
        mock_response.text = "Error uploading"
        mock_response.raise_for_status.side_effect = handler.requests.RequestException(
            "Error uploading"
        )
        mock_post.return_value = mock_response

        test_image_data = base64.b64encode(b"Test Image Data").decode("utf-8")

        images = [{"name": "test_image.png", "image": test_image_data}]

        responses = handler.upload_images(images)

        self.assertEqual(len(responses), 3)
        self.assertEqual(responses["status"], "error")


class TestOutputStorage(unittest.TestCase):
    """A worker with half its storage settings cannot deliver a result."""

    def storage(self, **variables):
        cleared = {name: value for name, value in os.environ.items() if not name.startswith("BUCKET_")}
        return patch.dict(os.environ, {**cleared, **variables}, clear=True)

    def test_no_bucket_means_results_come_back_inline(self):
        with self.storage():
            handler.validate_output_storage()

    def test_bucket_with_both_keys_is_accepted(self):
        with self.storage(
            BUCKET_ENDPOINT_URL="https://s3.example.com/results",
            BUCKET_ACCESS_KEY_ID="access",
            BUCKET_SECRET_ACCESS_KEY="secret",
        ):
            handler.validate_output_storage()

    def test_bucket_without_a_key_is_refused_and_names_what_is_missing(self):
        with self.storage(
            BUCKET_ENDPOINT_URL="https://s3.example.com/results", BUCKET_ACCESS_KEY_ID="access"
        ):
            with self.assertRaises(ValueError) as raised:
                handler.validate_output_storage()

        self.assertIn("BUCKET_SECRET_ACCESS_KEY", str(raised.exception))
        self.assertNotIn("BUCKET_ACCESS_KEY_ID", str(raised.exception))

    def test_job_is_refused_before_the_graph_runs(self):
        with (
            self.storage(BUCKET_ENDPOINT_URL="https://s3.example.com/results"),
            patch("handler.check_server") as check_server,
        ):
            result = handler.handler({"id": "job-1", "input": {"workflow": {"1": {}}}})

        self.assertIn("S3 upload configuration is missing", result["error"])
        check_server.assert_not_called()


class TestRunTimeLimit(unittest.TestCase):
    def test_no_limit_unless_one_is_set(self):
        # An endpoint that allows long jobs keeps them unless it sets the variable.
        self.assertEqual(handler.WORKFLOW_EXECUTION_TIMEOUT_S, 0)
        with patch("handler.request_comfyui_interrupt") as interrupt:
            handler.raise_if_workflow_timed_out("prompt-1", wait_started_at=0, now=10**9)
        interrupt.assert_not_called()

    def test_graph_past_its_limit_is_interrupted(self):
        with patch("handler.request_comfyui_interrupt", return_value=None) as interrupt:
            with self.assertRaises(handler.WorkflowExecutionTimeoutError):
                handler.raise_if_workflow_timed_out(
                    "prompt-1", wait_started_at=0, timeout_s=60, now=61
                )
        interrupt.assert_called_once_with("prompt-1")


if __name__ == "__main__":
    unittest.main()
