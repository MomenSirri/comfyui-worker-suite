import base64
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

runpod_module = types.ModuleType("runpod")
runpod_serverless = types.ModuleType("runpod.serverless")
runpod_serverless.start = lambda *_args, **_kwargs: None
runpod_upload = types.SimpleNamespace(upload_image=lambda *_args, **_kwargs: None)
runpod_utils = types.ModuleType("runpod.serverless.utils")
runpod_utils.rp_upload = runpod_upload
runpod_module.serverless = runpod_serverless
sys.modules.setdefault("runpod", runpod_module)
sys.modules.setdefault("runpod.serverless", runpod_serverless)
sys.modules.setdefault("runpod.serverless.utils", runpod_utils)
sys.modules.setdefault("runpod.serverless.utils.rp_upload", runpod_upload)

import handler


class TestRunpodWorkerComfy(unittest.TestCase):
    def test_valid_input_with_workflow_only(self):
        input_data = {"workflow": {"key": "value"}}
        validated_data, error = handler.validate_input(input_data)

        self.assertIsNone(error)
        self.assertEqual(validated_data["workflow"], {"key": "value"})
        self.assertIsNone(validated_data["images"])
        self.assertIsNone(validated_data["videos"])
        self.assertIsNone(validated_data["files"])

    def test_valid_input_with_workflow_and_images(self):
        input_data = {
            "workflow": {"key": "value"},
            "images": [{"name": "image1.png", "image": "base64string"}],
        }
        validated_data, error = handler.validate_input(input_data)

        self.assertIsNone(error)
        self.assertEqual(validated_data["workflow"], input_data["workflow"])
        self.assertEqual(validated_data["images"], input_data["images"])

    def test_input_missing_workflow(self):
        input_data = {"images": [{"name": "image1.png", "image": "base64string"}]}
        validated_data, error = handler.validate_input(input_data)

        self.assertIsNone(validated_data)
        self.assertEqual(error, "Missing 'workflow' parameter")

    def test_input_with_invalid_images_structure(self):
        input_data = {
            "workflow": {"key": "value"},
            "images": [{"name": "image1.png"}],
        }
        validated_data, error = handler.validate_input(input_data)

        self.assertIsNone(validated_data)
        self.assertEqual(
            error,
            "Each 'images' item must include one of: 'image', 'data', 'url'",
        )

    def test_invalid_json_string_input(self):
        validated_data, error = handler.validate_input("invalid json")

        self.assertIsNone(validated_data)
        self.assertEqual(error, "Invalid JSON format in input")

    def test_valid_json_string_input(self):
        input_data = '{"workflow": {"key": "value"}}'
        validated_data, error = handler.validate_input(input_data)

        self.assertIsNone(error)
        self.assertEqual(validated_data["workflow"], {"key": "value"})

    def test_empty_input(self):
        validated_data, error = handler.validate_input(None)

        self.assertIsNone(validated_data)
        self.assertEqual(error, "Please provide input")

    @patch("handler.requests.get")
    def test_check_server_server_up(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = handler.check_server("http://127.0.0.1:8188", 1, 50)

        self.assertTrue(result)

    @patch("handler.requests.get")
    def test_check_server_server_down(self, mock_get):
        mock_get.side_effect = handler.requests.RequestException()

        result = handler.check_server("http://127.0.0.1:8188", 1, 50)

        self.assertFalse(result)

    @patch("handler.requests.post")
    def test_request_comfyui_interrupt(self, mock_post):
        mock_response = MagicMock()
        mock_post.return_value = mock_response

        error = handler.request_comfyui_interrupt("prompt-123")

        self.assertIsNone(error)
        mock_post.assert_called_once_with(
            "http://127.0.0.1:8188/interrupt", timeout=10
        )
        mock_response.raise_for_status.assert_called_once_with()

    @patch("handler.request_comfyui_interrupt", return_value=None)
    def test_workflow_deadline_interrupts_and_raises(self, mock_interrupt):
        with self.assertRaises(handler.WorkflowExecutionTimeoutError) as raised:
            handler.raise_if_workflow_timed_out(
                "prompt-123", wait_started_at=100.0, timeout_s=20, now=120.0
            )

        self.assertIn("timed out after 20 seconds", str(raised.exception))
        self.assertIn("prompt-123", str(raised.exception))
        mock_interrupt.assert_called_once_with("prompt-123")

    @patch("handler.request_comfyui_interrupt")
    def test_workflow_deadline_does_not_interrupt_early(self, mock_interrupt):
        handler.raise_if_workflow_timed_out(
            "prompt-123", wait_started_at=100.0, timeout_s=20, now=119.9
        )

        mock_interrupt.assert_not_called()

    @patch("handler.request_comfyui_interrupt")
    def test_disabled_workflow_deadline_does_not_interrupt(self, mock_interrupt):
        handler.raise_if_workflow_timed_out(
            "prompt-123", wait_started_at=100.0, timeout_s=0, now=999.0
        )

        mock_interrupt.assert_not_called()

    @patch("handler.requests.post")
    def test_queue_workflow(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"prompt_id": "123"}
        mock_post.return_value = mock_response

        result = handler.queue_workflow(
            {"prompt": "test"}, "client-1", comfy_org_api_key="secret"
        )

        self.assertEqual(result, {"prompt_id": "123"})
        request_body = json.loads(mock_post.call_args.kwargs["data"].decode("utf-8"))
        self.assertEqual(request_body["client_id"], "client-1")
        self.assertEqual(request_body["extra_data"]["api_key_comfy_org"], "secret")

    @patch("handler.requests.get")
    def test_get_history(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"key": "value"}
        mock_get.return_value = mock_response

        result = handler.get_history("123")

        self.assertEqual(result, {"key": "value"})
        mock_get.assert_called_with("http://127.0.0.1:8188/history/123", timeout=30)

    def test_execution_terminal_state_accepts_execution_success(self):
        terminal_state = handler._execution_terminal_state(
            {
                "type": "execution_success",
                "data": {"prompt_id": "prompt-123"},
            },
            "prompt-123",
        )

        self.assertEqual(terminal_state["status"], "success")
        self.assertEqual(terminal_state["event"], "execution_success")

    def test_execution_terminal_state_accepts_legacy_executing_sentinel(self):
        terminal_state = handler._execution_terminal_state(
            {
                "type": "executing",
                "data": {"prompt_id": "prompt-123", "node": None},
            },
            "prompt-123",
        )

        self.assertEqual(terminal_state["status"], "success")
        self.assertEqual(terminal_state["event"], "executing")

    def test_execution_terminal_state_ignores_another_prompt(self):
        terminal_state = handler._execution_terminal_state(
            {
                "type": "execution_success",
                "data": {"prompt_id": "another-prompt"},
            },
            "prompt-123",
        )

        self.assertIsNone(terminal_state)

    def test_execution_terminal_state_handles_interruption(self):
        terminal_state = handler._execution_terminal_state(
            {
                "type": "execution_interrupted",
                "data": {
                    "prompt_id": "prompt-123",
                    "node_id": "7",
                    "node_type": "SeedanceNode",
                },
            },
            "prompt-123",
        )

        self.assertEqual(terminal_state["status"], "error")
        self.assertIn("interrupted", terminal_state["error"])
        self.assertEqual(terminal_state["data"]["node_id"], "7")

    def test_execution_terminal_state_preserves_execution_error_details(self):
        terminal_state = handler._execution_terminal_state(
            {
                "type": "execution_error",
                "data": {
                    "prompt_id": "prompt-123",
                    "node_id": "8",
                    "node_type": "SeedanceNode",
                    "exception_message": "generation failed",
                },
            },
            "prompt-123",
        )

        self.assertEqual(terminal_state["status"], "error")
        self.assertIn("SeedanceNode", terminal_state["error"])
        self.assertIn("generation failed", terminal_state["error"])

    def test_history_terminal_state_recovers_execution_success(self):
        history = {
            "prompt-123": {
                "status": {
                    "status_str": "success",
                    "completed": True,
                    "messages": [
                        [
                            "execution_success",
                            {"prompt_id": "prompt-123", "timestamp": 1234},
                        ]
                    ],
                }
            }
        }

        terminal_state = handler._history_terminal_state(history, "prompt-123")

        self.assertEqual(terminal_state["status"], "success")
        self.assertEqual(terminal_state["event"], "execution_success")

    @patch("handler.get_history")
    def test_wait_for_prompt_history_retries_until_persisted(self, mock_get_history):
        expected_history = {"prompt-123": {"outputs": {}}}
        mock_get_history.side_effect = [{}, expected_history]

        history = handler.wait_for_prompt_history(
            "prompt-123", timeout_s=1, poll_interval_s=0
        )

        self.assertEqual(history, expected_history)
        self.assertEqual(mock_get_history.call_count, 2)

    def _run_handler_with_websocket_messages(self, websocket_side_effect):
        prompt_id = "prompt-123"
        history = {
            prompt_id: {
                "outputs": {},
                "status": {
                    "status_str": "success",
                    "completed": True,
                    "messages": [
                        ["execution_success", {"prompt_id": prompt_id}]
                    ],
                },
            }
        }
        websocket_client = MagicMock()
        websocket_client.connected = True
        websocket_client.recv.side_effect = websocket_side_effect
        output_media = {
            "images": [{"filename": "output.png", "type": "base64", "data": "x"}],
            "videos": [],
            "audio": [],
            "files": [],
            "texts": [],
        }

        with (
            patch("handler.check_server", return_value=True),
            patch("handler.websocket.WebSocket", return_value=websocket_client),
            patch("handler.queue_workflow", return_value={"prompt_id": prompt_id}),
            patch("handler.get_history", return_value=history) as mock_get_history,
            patch("handler.wait_for_prompt_history", return_value=history),
            patch("handler.collect_output_media", return_value=(output_media, [])),
            patch(
                "handler.collect_text_artifacts",
                return_value=({"files": [], "texts": []}, []),
            ),
            patch("handler.extract_comfy_credit_usage", return_value={}),
            patch("handler.read_credit_tracker_usage", return_value=None),
            patch(
                "handler.estimate_credit_usage",
                return_value=handler.build_empty_credit_usage(),
            ),
        ):
            result = handler.handler(
                {
                    "id": "job-123",
                    "input": {
                        "workflow": {
                            "1": {"class_type": "TestOutput", "inputs": {}}
                        }
                    },
                }
            )

        return result, mock_get_history

    def test_handler_finishes_on_execution_success(self):
        result, mock_get_history = self._run_handler_with_websocket_messages(
            [
                json.dumps(
                    {
                        "type": "execution_success",
                        "data": {"prompt_id": "prompt-123"},
                    }
                )
            ]
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["prompt_id"], "prompt-123")
        mock_get_history.assert_not_called()

    def test_handler_recovers_completion_from_history_after_websocket_timeout(self):
        result, mock_get_history = self._run_handler_with_websocket_messages(
            [handler.websocket.WebSocketTimeoutException("timed out")]
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["prompt_id"], "prompt-123")
        mock_get_history.assert_called_once_with("prompt-123")

    def test_decode_data_value_base64(self):
        test_data = base64.b64encode(b"test").decode("utf-8")

        result = handler._decode_data_value(test_data, "dummy.png")

        self.assertEqual(result, b"test")

    @patch("handler.requests.post")
    def test_upload_images_successful(self, mock_post):
        mock_response = MagicMock()
        mock_post.return_value = mock_response
        test_image_data = base64.b64encode(b"Test Image Data").decode("utf-8")
        images = [{"name": "test_image.png", "image": test_image_data}]

        responses = handler.upload_images(images)

        self.assertEqual(responses["status"], "success")

    @patch("handler.requests.post")
    def test_upload_images_failed(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = handler.requests.HTTPError("bad")
        mock_post.return_value = mock_response
        test_image_data = base64.b64encode(b"Test Image Data").decode("utf-8")
        images = [{"name": "test_image.png", "image": test_image_data}]

        responses = handler.upload_images(images)

        self.assertEqual(responses["status"], "error")

    @patch("handler.get_file_data", return_value=b"video-bytes")
    @patch.dict(os.environ, {"BUCKET_ENDPOINT_URL": ""})
    def test_collect_output_media_classifies_mp4_as_video(self, _mock_get_file_data):
        outputs = {
            "2": {
                "images": [
                    {"filename": "ComfyUI_00001_.mp4", "subfolder": "", "type": "output"}
                ]
            }
        }

        output_media, errors = handler.collect_output_media(outputs, "job-123")

        self.assertEqual(errors, [])
        self.assertEqual(len(output_media["videos"]), 1)
        self.assertEqual(output_media["videos"][0]["type"], "base64")

    @patch("handler._upload_output_bytes", return_value="https://example.com/out.mp4")
    @patch("handler.get_file_data", return_value=b"video-bytes")
    @patch.dict(os.environ, {"BUCKET_ENDPOINT_URL": "https://s3.example.com"})
    def test_collect_output_media_uses_s3_when_configured(
        self, _mock_get_file_data, mock_upload
    ):
        outputs = {
            "2": {
                "videos": [
                    {"filename": "ComfyUI_00001_.mp4", "subfolder": "", "type": "output"}
                ]
            }
        }

        output_media, errors = handler.collect_output_media(outputs, "job-123")

        self.assertEqual(errors, [])
        self.assertEqual(output_media["videos"][0]["data"], "https://example.com/out.mp4")
        mock_upload.assert_called_once()

    @patch("handler._upload_output_bytes", return_value="https://example.com/seedance.txt")
    @patch("handler.get_file_data", return_value=b"SCENE CONTEXT\nGenerated prompt")
    @patch.dict(os.environ, {"BUCKET_ENDPOINT_URL": "https://s3.example.com"})
    def test_collect_text_artifacts_uploads_history_txt(
        self, _mock_get_file_data, mock_upload
    ):
        outputs = {
            "9": {
                "files": [
                    {
                        "filename": "seedance_prompt_00001.txt",
                        "subfolder": "seedance_prompt_outputs",
                        "type": "output",
                    }
                ]
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            text_outputs, errors = handler.collect_text_artifacts(
                outputs, "job-123", output_dir=temp_dir
            )

        self.assertEqual(errors, [])
        self.assertEqual(text_outputs["files"][0]["filename"], "seedance_prompt_00001.txt")
        self.assertEqual(text_outputs["files"][0]["type"], "s3_url")
        self.assertEqual(text_outputs["files"][0]["data"], "https://example.com/seedance.txt")
        self.assertEqual(text_outputs["texts"][0]["text"], "SCENE CONTEXT\nGenerated prompt")
        mock_upload.assert_called_once()

    @patch.dict(os.environ, {"BUCKET_ENDPOINT_URL": ""})
    def test_collect_text_artifacts_scans_output_folder_for_txt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_subdir = os.path.join(temp_dir, "seedance_prompt_outputs")
            os.makedirs(output_subdir)
            text_path = os.path.join(output_subdir, "seedance_prompt_00001.txt")
            with open(text_path, "wb") as file_handle:
                file_handle.write(b"SCENE CONTEXT\nPrompt from disk")

            text_outputs, errors = handler.collect_text_artifacts(
                {}, "job-123", output_dir=temp_dir, min_mtime=0
            )

        self.assertEqual(errors, [])
        self.assertEqual(text_outputs["files"], [])
        self.assertEqual(len(text_outputs["texts"]), 1)
        self.assertEqual(text_outputs["texts"][0]["filename"], "seedance_prompt_00001.txt")
        self.assertEqual(text_outputs["texts"][0]["subfolder"], "seedance_prompt_outputs")
        self.assertEqual(text_outputs["texts"][0]["text"], "SCENE CONTEXT\nPrompt from disk")

    @patch.dict(os.environ, {"BUCKET_ENDPOINT_URL": ""})
    def test_collect_text_artifacts_reads_inline_history_text(self):
        outputs = {
            "8": {
                "images": [
                    {
                        "filename": "seedance_prompt_preview_00002_.png",
                        "subfolder": "seedance_prompt_outputs",
                        "type": "output",
                    }
                ]
            },
            "10": {"text": ["SCENE CONTEXT\nPrompt from history"]},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            text_outputs, errors = handler.collect_text_artifacts(
                outputs, "job-123", output_dir=temp_dir
            )

        self.assertEqual(errors, [])
        self.assertEqual(text_outputs["files"], [])
        self.assertEqual(len(text_outputs["texts"]), 1)
        self.assertEqual(text_outputs["texts"][0]["filename"], "node_10_text_00001.txt")
        self.assertEqual(text_outputs["texts"][0]["text"], "SCENE CONTEXT\nPrompt from history")

    def test_read_credit_tracker_usage_maps_sqlite_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "usage_log.db")
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE credit_usage (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        project_name TEXT,
                        user_name TEXT,
                        workflow_name TEXT,
                        partner_node_name TEXT,
                        pricing_mode TEXT,
                        quantity INTEGER,
                        duration_seconds REAL,
                        resolution TEXT,
                        estimated_credits REAL,
                        estimated_usd REAL,
                        notes TEXT,
                        prompt_id TEXT,
                        node_id TEXT,
                        node_class_type TEXT,
                        node_title TEXT,
                        model_name TEXT,
                        input_summary TEXT,
                        source TEXT,
                        dedupe_key TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO credit_usage (
                        timestamp, project_name, user_name, workflow_name,
                        partner_node_name, pricing_mode, quantity,
                        duration_seconds, resolution, estimated_credits,
                        estimated_usd, notes, prompt_id, node_id,
                        node_class_type, node_title, model_name, input_summary,
                        source, dedupe_key
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "2026-07-08T12:00:00+00:00",
                        "Project",
                        "Momen",
                        "Workflow",
                        "Kling 3.0 Video",
                        "price_badge_estimate",
                        1,
                        5.0,
                        "1080p",
                        118.16,
                        0.56,
                        "tracker row",
                        "prompt-1",
                        "3",
                        "KlingVideoNode",
                        "Kling 3.0 Video",
                        "kling-v3",
                        "",
                        "prompt_scan_price_badge",
                        "dedupe-1",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            usage = handler.read_credit_tracker_usage("prompt-1", db_path)

        self.assertEqual(usage["total_estimated_credits"], 118.16)
        self.assertEqual(usage["total_estimated_usd"], 0.56)
        self.assertEqual(usage["source"], "credit_tracker:prompt_scan_price_badge")
        self.assertEqual(usage["nodes"][0]["node_id"], "3")
        self.assertEqual(usage["nodes"][0]["partner_node_name"], "Kling 3.0 Video")


SIGNED_URL = "https://bucket.example.com/prompts/key.mp4?X-Amz-Signature=secret-signature"


class TestSignedLinks(unittest.TestCase):
    """A signed link is a credential: it must reach neither the log nor the job output."""

    def download(self, mock_get, chunks=(b"clip ", b"bytes")):
        response = mock_get.return_value.__enter__.return_value
        response.iter_content.return_value = iter(chunks)
        return response

    @patch("handler.requests.get")
    def test_url_input_is_streamed(self, mock_get):
        self.download(mock_get)

        result = handler._decode_data_value(SIGNED_URL, "clip.mp4")

        self.assertEqual(result, b"clip bytes")
        mock_get.assert_called_once_with(
            SIGNED_URL, stream=True, timeout=(10, handler.INPUT_DOWNLOAD_TIMEOUT_S)
        )

    @patch("handler.requests.get")
    def test_url_input_over_the_size_limit_is_refused(self, mock_get):
        self.download(mock_get, chunks=(b"12345", b"6789"))

        with patch.object(handler, "INPUT_DOWNLOAD_MAX_BYTES", 8):
            with self.assertRaises(ValueError) as raised:
                handler._decode_data_value(SIGNED_URL, "clip.mp4")

        self.assertIn("download limit", str(raised.exception))

    @patch("handler.requests.get")
    def test_empty_url_input_is_refused(self, mock_get):
        self.download(mock_get, chunks=())

        with self.assertRaises(ValueError):
            handler._decode_data_value(SIGNED_URL, "clip.mp4")

    @patch("handler.requests.post")
    @patch("handler.requests.get")
    def test_failed_download_does_not_expose_the_signed_link(self, mock_get, mock_post):
        failure = handler.requests.HTTPError(f"403 Client Error: Forbidden for url: {SIGNED_URL}")
        failure.response = types.SimpleNamespace(status_code=403)
        self.download(mock_get).raise_for_status.side_effect = failure

        result = handler.upload_images([{"name": "clip.mp4", "url": SIGNED_URL}])

        self.assertEqual(result["status"], "error")
        self.assertIn("HTTP 403", result["details"][0])
        self.assertNotIn("secret-signature", str(result))
        self.assertNotIn("bucket.example.com", str(result))
        mock_post.assert_not_called()

    @patch("builtins.print")
    @patch("handler.requests.get")
    def test_download_timeout_is_reported_without_the_link(self, mock_get, mock_print):
        mock_get.side_effect = handler.requests.Timeout(f"timed out: {SIGNED_URL}")

        result = handler.upload_images([{"name": "clip.mp4", "url": SIGNED_URL}])

        self.assertEqual(result["status"], "error")
        self.assertIn("Timed out downloading", result["details"][0])
        self.assertNotIn("secret-signature", str(result))
        self.assertNotIn("secret-signature", str(mock_print.call_args_list))

    @patch("builtins.print")
    @patch("handler.requests.get")
    def test_refused_connection_is_reported_without_the_link(self, mock_get, mock_print):
        # requests puts the path and query of the URL into this message.
        mock_get.side_effect = handler.requests.ConnectionError(
            "HTTPSConnectionPool(host='bucket.example.com', port=443): Max retries "
            "exceeded with url: /prompts/key.mp4?X-Amz-Signature=secret-signature"
        )

        result = handler.upload_images([{"name": "clip.mp4", "url": SIGNED_URL}])

        self.assertEqual(result["status"], "error")
        self.assertIn("ConnectionError", result["details"][0])
        for text in (str(result), str(mock_print.call_args_list)):
            self.assertNotIn("secret-signature", text)
            self.assertNotIn("bucket.example.com", text)

    @patch("builtins.print")
    def test_result_upload_logs_the_object_but_not_its_signature(self, mock_print):
        with patch.object(handler.rp_upload, "upload_image", return_value=SIGNED_URL):
            url = handler._upload_output_bytes("job-1", "out.mp4", b"video")

        self.assertEqual(url, SIGNED_URL)
        printed = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("bucket.example.com/prompts/key.mp4", printed)
        self.assertNotIn("secret-signature", printed)


if __name__ == "__main__":
    unittest.main()
