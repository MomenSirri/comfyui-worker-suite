"""The cases every ComfyUI handler of the suite has to pass.

A service runs them from its own `tests/test_backend_contract.py`:

    class TestBackendContract(ContractCases, unittest.TestCase):
        handler = handler            # the service's handler module
        entry_module = "handler"     # what the image starts
        serves_original_tools = True # behind the Upscale and Enhancement endpoints
        serves_video = False         # takes clips and returns videos

        def run_handler(self, job):
            return handler.handler(job)
"""

import json
import os
import subprocess
import sys
from unittest.mock import patch

from . import backend
from .fake_comfy import (
    INPUT_HOST,
    JPEG,
    MP4,
    PNG,
    RESULT_MP4,
    RESULT_PNG,
    SIGNATURE,
    STORAGE_BUCKET,
    STORAGE_ENDPOINT,
    FakeComfy,
    signed,
)

IMAGE_GRAPH = {
    "1": {"class_type": "LoadImage", "inputs": {"image": backend.INPUT_FILENAME}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "ComfyUI"}},
}
VIDEO_GRAPH = {
    "3": {"class_type": "LoadVideo", "inputs": {"file": "azai_in_clip.mp4"}},
    "9": {"class_type": "SaveVideo", "inputs": {"video": ["3", 0], "filename_prefix": "video/result"}},
}
COMFY_API_KEY = "comfyui-0123456789abcdef"

# Imports what the image starts, as the worker does, then asks the SDK for its level.
SDK_LOG_LEVEL_PROBE = """
import importlib
import importlib.util
import sys

sys.path[:0] = sys.argv[2:]
if importlib.util.find_spec("runpod") is None:
    sys.exit(3)

importlib.import_module(sys.argv[1])
from runpod.serverless.modules.rp_logger import RunPodLogger

print(RunPodLogger.level)
"""


class ContractCases:
    handler = None
    entry_module = "handler"
    serves_original_tools = False
    serves_video = False

    def run_handler(self, job):
        raise NotImplementedError

    def setUp(self):
        self.comfy = FakeComfy(self.handler)

    def run_job(self, job):
        result = self.comfy.run(self.run_handler, job)
        # A signed link is a credential. Whatever happens to the job, the worker's
        # log and its progress updates must not hold one.
        self.assertNotIn(SIGNATURE, self.comfy.log)
        return result

    def kinds(self):
        """The readings of an answer that apply to this handler's jobs."""
        return ([None] if self.serves_original_tools else []) + ["image"]

    def assert_stored(self, accepted, body, content_type=None):
        """The accepted link names an object of the bucket the backend trusts."""
        key = backend.trusted_object_key(accepted["data"], STORAGE_ENDPOINT, STORAGE_BUCKET)
        stored = {item["key"]: item for item in self.comfy.bucket}
        self.assertIn(key, stored)
        self.assertEqual(stored[key]["body"], body)
        if content_type:
            self.assertEqual(stored[key]["content_type"], content_type)

    def assert_fails_at_once(self, result, kinds=None):
        """The backend fails and refunds the job instead of waiting for its age limit."""
        for kind in kinds or self.kinds():
            with self.subTest(read_as=kind or "original tool"):
                self.assertEqual(backend.settle(result, kind), "failed")

    def assert_keeps_the_link_secret(self, result):
        answer = json.dumps(result, default=str)
        self.assertNotIn(SIGNATURE, answer)
        self.assertNotIn(SIGNATURE, self.comfy.log)

    # --- Upscale and Enhancement ----------------------------------------------

    def test_upscale_job_with_the_image_inline(self):
        if not self.serves_original_tools:
            self.skipTest("not behind the Upscale or Enhancement endpoint")
        self.comfy.saves("9", "ComfyUI_00001_.png", RESULT_PNG)

        result = self.run_job(backend.upscale_job(backend.inline(PNG), IMAGE_GRAPH))

        accepted = backend.settle(result)
        self.assert_stored(accepted, RESULT_PNG)
        self.assertEqual(self.comfy.input_directory[backend.INPUT_FILENAME]["body"], PNG)
        self.assertEqual(self.comfy.queued[0]["prompt"], IMAGE_GRAPH)

    def test_enhancement_job_with_the_image_in_the_graph(self):
        if not self.serves_original_tools:
            self.skipTest("not behind the Upscale or Enhancement endpoint")
        self.comfy.saves("9", "ComfyUI_00001_.png", RESULT_PNG)

        result = self.run_job(backend.enhancement_job(IMAGE_GRAPH))

        self.assert_stored(backend.settle(result), RESULT_PNG)
        self.assertEqual(self.comfy.input_directory, {})

    def test_upscale_job_with_the_image_by_link(self):
        if not self.serves_original_tools:
            self.skipTest("not behind the Upscale or Enhancement endpoint")
        link = self.comfy.link(backend.INPUT_FILENAME, PNG)
        self.comfy.saves("9", "ComfyUI_00001_.png", RESULT_PNG)

        result = self.run_job(backend.upscale_job(link, IMAGE_GRAPH))

        self.assert_stored(backend.settle(result), RESULT_PNG)
        self.assertEqual(self.comfy.input_directory[backend.INPUT_FILENAME]["body"], PNG)

    # --- Studio ---------------------------------------------------------------

    def test_studio_job_with_inputs_in_each_delivery(self):
        sources = {"azai_in_1.png": PNG, "azai_in_2.jpg": JPEG}
        for delivery in ("base64", "url", "image-url"):
            with self.subTest(delivery=delivery):
                self.setUp()
                files = [
                    (name, body if delivery == "base64" else self.comfy.link(name, body))
                    for name, body in sources.items()
                ]
                self.comfy.saves("9", "ComfyUI_00001_.png", RESULT_PNG)

                result = self.run_job(backend.studio_job(IMAGE_GRAPH, files, delivery))

                self.assert_stored(backend.settle(result, "image"), RESULT_PNG)
                received = {name: item["body"] for name, item in self.comfy.input_directory.items()}
                self.assertEqual(received, sources)

    def test_studio_job_hands_the_provider_key_to_comfyui(self):
        files = [("azai_in_1.png", self.comfy.link("azai_in_1.png", PNG))]
        self.comfy.saves("9", "ComfyUI_00001_.png", RESULT_PNG)

        self.run_job(backend.studio_job(IMAGE_GRAPH, files, "url", COMFY_API_KEY))

        self.assertEqual(
            self.comfy.queued[0].get("extra_data"), {"api_key_comfy_org": COMFY_API_KEY}
        )
        self.assertNotIn(COMFY_API_KEY, self.comfy.log)

    def test_studio_video_job_with_a_clip(self):
        if not self.serves_video:
            self.skipTest("this worker takes and returns images only")
        files = [
            ("azai_in_1.png", self.comfy.link("azai_in_1.png", PNG)),
            ("azai_in_clip.mp4", self.comfy.link("azai_in_clip.mp4", MP4)),
        ]
        # The loader reports the clip it was given, then the result is saved.
        self.comfy.saves("3", "azai_in_clip.mp4", MP4, folder="input", video=True)
        self.comfy.saves("9", "result_00001_.mp4", RESULT_MP4, subfolder="video", video=True)

        result = self.run_job(backend.studio_job(VIDEO_GRAPH, files, "url"))

        self.assert_stored(backend.settle(result, "video"), RESULT_MP4, "video/mp4")
        clip = self.comfy.input_directory["azai_in_clip.mp4"]
        self.assertEqual((clip["body"], clip["content_type"]), (MP4, "video/mp4"))
        # The clip is an input: storing it again would only cost time and space.
        self.assertEqual(len(self.comfy.bucket), 1)
        self.assertFalse(result.get("errors"), result.get("errors"))
        self.assertIsNot(result.get("success"), False)

    # --- Jobs that must fail --------------------------------------------------

    def test_failed_node_fails_the_job_without_exposing_a_link(self):
        # HTTP clients end their error text with the request URL.
        provider_link = signed("https://cdn.example.com/results/clip.mp4")
        # A message about credits is also collected into the credit figures.
        self.comfy.fails_at(
            "8",
            "ProviderNode",
            f"Insufficient credits: 402, message='Payment Required', url='{provider_link}'",
        )
        if self.serves_video:
            # A loader has already reported its input when the node after it fails.
            self.comfy.saves("3", "azai_in_clip.mp4", MP4, folder="input", video=True)

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", PNG)], "base64"))

        self.assert_fails_at_once(result, self.kinds() + (["video"] if self.serves_video else []))
        self.assert_keeps_the_link_secret(result)
        self.assertEqual(self.comfy.bucket, [])

    def test_failed_node_beside_a_text_output_fails_the_job(self):
        # An answer that holds something, but no file, must still say it failed.
        self.comfy.says("5", "a prompt the graph wrote before it failed")
        self.comfy.fails_at("8", "ProviderNode", "Insufficient credits")

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", PNG)], "base64"))

        self.assert_fails_at_once(result)

    def test_unreachable_input_link_fails_the_job_without_exposing_it(self):
        for delivery in ("url", "image-url"):
            with self.subTest(delivery=delivery):
                self.setUp()
                link = self.comfy.link("azai_in_1.png", b"", status=403)

                result = self.run_job(
                    backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", link)], delivery)
                )

                self.assert_fails_at_once(result)
                self.assert_keeps_the_link_secret(result)
                self.assertNotIn(INPUT_HOST, json.dumps(result, default=str))
                self.assertEqual(self.comfy.queued, [])

    def test_input_link_above_the_size_limit_is_refused(self):
        link = self.comfy.link("azai_in_1.png", PNG)

        with patch.object(self.handler, "INPUT_DOWNLOAD_MAX_BYTES", len(PNG) - 1):
            result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", link)], "url"))

        self.assert_fails_at_once(result)
        self.assertIn("limit", json.dumps(result, default=str))
        self.assertEqual(self.comfy.queued, [])

    def test_input_link_that_redirects_is_followed_with_the_redirect_left_unread(self):
        # The stand-in refuses, as an error, a redirect answer that requests
        # would handle itself: requests reads such an answer whole, whatever its
        # size, also when told not to follow it.
        stored = self.comfy.link("stored.png", PNG)
        moved = self.comfy.link("moved.png", b"x" * 4096, status=302, headers={"Location": stored})
        self.comfy.saves("9", "ComfyUI_00001_.png", RESULT_PNG)

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", moved)], "url"))

        self.assert_stored(backend.settle(result, "image"), RESULT_PNG)
        self.assertEqual(self.comfy.input_directory["azai_in_1.png"]["body"], PNG)
        self.assertEqual([request["url"] for request in self.comfy.link_requests], [moved, stored])

    def test_input_link_that_keeps_redirecting_is_refused(self):
        circle = signed(f"https://{INPUT_HOST}/prompts/circle.png")
        self.assertEqual(self.comfy.link("circle.png", b"", status=302, headers={"Location": circle}), circle)

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", circle)], "url"))

        self.assert_fails_at_once(result)
        self.assert_keeps_the_link_secret(result)
        self.assertLess(len(self.comfy.link_requests), 10)
        self.assertEqual(self.comfy.queued, [])

    def test_answer_that_is_neither_a_file_nor_a_redirect_is_refused(self):
        # 304 is not an error to requests, and its body is not the file.
        link = self.comfy.link("azai_in_1.png", b"<html>not modified</html>", status=304)

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", link)], "url"))

        self.assert_fails_at_once(result)
        self.assertEqual(self.comfy.input_directory, {})

    def test_compressed_input_is_refused(self):
        # A compressed body grows while it is decoded, past a limit that counts
        # what was decoded.
        link = self.comfy.link("azai_in_1.png", PNG, headers={"Content-Encoding": "gzip"})

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", link)], "url"))

        self.assert_fails_at_once(result)
        self.assertEqual(self.comfy.link_requests[0]["headers"], {"Accept-Encoding": "identity"})
        self.assertEqual(self.comfy.queued, [])

    # --- What counts as a result ----------------------------------------------

    def test_image_a_loader_reports_back_is_not_a_result(self):
        self.comfy.saves("1", "azai_in_1.png", PNG, folder="input")
        self.comfy.saves("9", "ComfyUI_00001_.png", RESULT_PNG)

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", PNG)], "base64"))

        for kind in self.kinds():
            with self.subTest(read_as=kind or "original tool"):
                self.assert_stored(backend.settle(result, kind), RESULT_PNG)
        self.assertEqual(len(self.comfy.bucket), 1)

    def test_file_that_two_nodes_report_is_one_result(self):
        self.comfy.saves("9", "ComfyUI_00001_.png", RESULT_PNG)
        self.comfy.saves("10", "ComfyUI_00001_.png", RESULT_PNG)

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", PNG)], "base64"))

        for kind in self.kinds():
            with self.subTest(read_as=kind or "original tool"):
                self.assert_stored(backend.settle(result, kind), RESULT_PNG)
        self.assertEqual(len(self.comfy.bucket), 1)

    def test_result_without_bytes_fails_the_job(self):
        self.comfy.saves("9", "ComfyUI_00001_.png", b"")

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", PNG)], "base64"))

        self.assert_fails_at_once(result)
        self.assertEqual(self.comfy.bucket, [])

    def test_graph_that_left_only_a_preview_fails_the_job(self):
        self.comfy.saves("7", "ComfyUI_temp_00001_.png", RESULT_PNG, folder="temp")

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", PNG)], "base64"))

        self.assert_fails_at_once(result)
        self.assertEqual(self.comfy.bucket, [])

    def test_graph_without_a_result_fails_the_job(self):
        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", PNG)], "base64"))

        self.assert_fails_at_once(result)

    def test_failed_upload_fails_the_job_without_exposing_a_link(self):
        self.comfy.storage_error = (
            "Could not connect to the endpoint URL: "
            + signed(f"{STORAGE_ENDPOINT}/{STORAGE_BUCKET}/10-26/job/a.png")
        )
        self.comfy.saves("9", "ComfyUI_00001_.png", RESULT_PNG)

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", PNG)], "base64"))

        self.assert_fails_at_once(result)
        self.assert_keeps_the_link_secret(result)

    def test_storage_without_its_keys_fails_the_job(self):
        # With BUCKET_ENDPOINT_URL set and a key missing, the SDK writes the
        # result to the worker's disk and returns that path as the link.
        self.comfy.storage_has_keys = False
        self.comfy.saves("9", "ComfyUI_00001_.png", RESULT_PNG)

        result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", PNG)], "base64"))

        self.assert_fails_at_once(result)
        # Before the graph runs: a provider node would be paid for nothing.
        self.assertEqual(self.comfy.queued, [])

    def test_graph_that_never_ends_is_stopped_at_the_run_time_limit(self):
        self.comfy.never_finishes = True

        with patch.object(self.handler, "WORKFLOW_EXECUTION_TIMEOUT_S", 600):
            result = self.run_job(backend.studio_job(IMAGE_GRAPH, [("azai_in_1.png", PNG)], "base64"))

        self.assert_fails_at_once(result)
        self.assertTrue(self.comfy.interrupted)
        answer = json.dumps(result, default=str)
        self.assertIn("timed out", answer)
        self.assertNotIn("unexpected error", answer)

    # --- The worker's own log -------------------------------------------------

    def test_sdk_does_not_log_the_job_output(self):
        # At DEBUG, its default, the RunPod SDK logs the handler's whole return
        # value, which holds the signed result links.
        service_dir = os.path.dirname(os.path.abspath(self.handler.__file__))
        environment = {
            name: value
            for name, value in os.environ.items()
            if name not in ("RUNPOD_LOG_LEVEL", "RUNPOD_DEBUG_LEVEL")
        }
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                SDK_LOG_LEVEL_PROBE,
                self.entry_module,
                service_dir,
                os.path.join(service_dir, "src"),
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if probe.returncode == 3:
            self.skipTest("the RunPod SDK is not installed")
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(probe.stdout.split()[-1], "INFO")
