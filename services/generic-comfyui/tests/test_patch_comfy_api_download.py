"""Regressions for the build-time patch against the actual ComfyUI helper."""

import ast
import asyncio
import contextlib
import os
from io import BytesIO
from pathlib import Path
import re
import sys
import traceback
import types
import unittest
from unittest.mock import Mock
from urllib.parse import urljoin, urlparse
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from patch_comfy_api_download import patch_source


FIXTURE = Path(__file__).parent / "fixtures/comfyui_download_helpers_v0_38_0.txt"


class ClientError(Exception):
    pass


class ClientPayloadError(ClientError):
    pass


class ApiServerError(Exception):
    pass


class FakeContent:
    def __init__(self, chunks, delay=0):
        self.chunks = list(chunks)
        self.delay = delay
        self.cancelled = False

    async def read(self, _size):
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
                self.delay = 0
            chunk = self.chunks.pop(0) if self.chunks else b""
            if isinstance(chunk, Exception):
                raise chunk
            return chunk
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def at_eof(self):
        return not self.chunks


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, content, content_length=None):
        self.content = content
        self.content_length = content_length

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass


class TestDownloadPatch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.source = FIXTURE.read_text(encoding="utf-8")
        self.patched = patch_source(self.source)
        tree = ast.parse(self.patched)
        # Load only the functions under test, avoiding ComfyUI/torch imports.
        names = {
            "_raise_if_download_stalled",
            "download_url_to_bytesio",
            "download_url_to_video_output",
            "_generate_operation_id",
        }
        tree.body = [
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ]
        self.responses = []
        self.requests = []
        responses, requests = self.responses, self.requests

        class Session:
            def __init__(self, **_kwargs):
                pass

            async def get(self, url, **kwargs):
                requests.append((url, kwargs))
                return responses.pop(0)

            async def close(self):
                pass

        async def sleep_with_interrupt(delay, *_args):
            await asyncio.sleep(delay)

        async def diagnose_connectivity():
            return {"internet_accessible": True}

        self.namespace = {
            "__builtins__": __builtins__,
            "asyncio": asyncio,
            "contextlib": contextlib,
            "BytesIO": BytesIO,
            "Path": Path,
            "os": os,
            "re": re,
            "uuid": uuid,
            "urljoin": urljoin,
            "urlparse": urlparse,
            "aiohttp": types.SimpleNamespace(
                ClientSession=Session, ClientTimeout=lambda **kw: kw
            ),
            "request_logger": Mock(),
            "ClientError": ClientError,
            "ClientPayloadError": ClientPayloadError,
            "ApiServerError": ApiServerError,
            "LocalNetworkError": type("LocalNetworkError", (Exception,), {}),
            "diagnose_connectivity": diagnose_connectivity,
            "ProcessingInterrupted": type("ProcessingInterrupted", (Exception,), {}),
            "is_processing_interrupted": lambda: False,
            "sleep_with_interrupt": sleep_with_interrupt,
            "to_aiohttp_url": lambda url: url,
            "InputImpl": types.SimpleNamespace(VideoFromFile=lambda value: value),
            "_RETRY_STATUS": {408, 429, 500, 502, 503, 504},
            "_VIDEO_DOWNLOAD_TIMEOUT_S": 600,
            "_VIDEO_DOWNLOAD_IDLE_TIMEOUT_S": 60,
            "_VIDEO_DOWNLOAD_MAX_RETRIES": 2,
        }
        # Future annotations avoid requiring the full ComfyUI type system.
        code = compile(
            "from __future__ import annotations\n" + ast.unparse(tree),
            "patched_helper", "exec",
        )
        exec(code, self.namespace)
        self.download = self.namespace["download_url_to_bytesio"]

    def test_patch_is_idempotent(self):
        self.assertEqual(patch_source(self.patched), self.patched)

    def test_unknown_upstream_layout_fails(self):
        changed = self.source.replace(
            "diag = await diagnose_connectivity()",
            "diag = await changed_diagnostic()",
        )
        with self.assertRaisesRegex(RuntimeError, "final retry failure"):
            patch_source(changed)

    async def test_retry_discards_partial_previous_body(self):
        self.responses.extend([
            FakeResponse(FakeContent([b"partial", ClientError("lost connection")])),
            FakeResponse(FakeContent([b"complete"]), content_length=8),
        ])
        dest = BytesIO()
        await self.download(
            "https://example.test/video.mp4", dest,
            max_retries=1, retry_delay=0, idle_timeout=60,
        )
        self.assertEqual(dest.read(), b"complete")
        self.assertEqual(len(self.requests), 2)

    async def test_read_survives_polling_timeout(self):
        content = FakeContent([b"video"], delay=1.05)
        self.responses.append(FakeResponse(content, content_length=5))
        dest = BytesIO()
        await self.download(
            "https://example.test/video.mp4", dest,
            timeout=5, idle_timeout=3, max_retries=0,
        )
        self.assertEqual(dest.read(), b"video")
        self.assertFalse(content.cancelled)

    async def test_stalled_read_times_out_and_cleans_up(self):
        content = FakeContent([b"video"], delay=60)
        self.responses.append(FakeResponse(content))
        with self.assertRaisesRegex(ApiServerError, "after 1 attempt.*TimeoutError"):
            await asyncio.wait_for(
                self.download(
                    "https://example.test/video.mp4", BytesIO(),
                    idle_timeout=0.01, max_retries=0,
                ), timeout=3,
            )
        self.assertTrue(content.cancelled)

    def test_patch_adds_the_imports_its_code_uses(self):
        # The functions under test are loaded without the helper's own imports,
        # so nothing else notices an insertion whose module is not imported.
        for module in ("os", "re"):
            self.assertIn(f"\nimport {module}\n", self.patched)

    async def test_failed_download_does_not_expose_the_signed_link(self):
        # aiohttp ends the text of a response error with the request URL, and
        # ComfyUI logs the traceback of a failed node with every chained error.
        signed = "https://cdn.example.test/video.mp4?X-Amz-Signature=secret-signature"
        refused = f"400, message='Bad status line', url='{signed}'"
        # A video download has an idle limit and its own message. Every other
        # download ends in the upstream message, with the cause chained.
        messages = {60: "Media download failed after 1 attempt", None: "appears unreachable"}

        for idle_timeout, message in messages.items():
            with self.subTest(idle_timeout=idle_timeout):
                self.responses.append(FakeResponse(FakeContent([ClientError(refused)])))

                with self.assertRaisesRegex(ApiServerError, message) as raised:
                    await self.download(
                        signed, BytesIO(), idle_timeout=idle_timeout, max_retries=0
                    )

                logged = "".join(traceback.format_exception(raised.exception))
                self.assertIn("ClientError: 400, message='Bad status line'", logged)
                self.assertIn("cdn.example.test/video.mp4?[redacted]", logged)
                self.assertNotIn("secret-signature", logged)

    async def test_short_response_retries(self):
        self.responses.extend([
            FakeResponse(FakeContent([b"cut"]), content_length=8),
            FakeResponse(FakeContent([b"complete"]), content_length=8),
        ])
        dest = BytesIO()
        await self.download(
            "https://example.test/video.mp4", dest,
            idle_timeout=60, max_retries=1, retry_delay=0,
        )
        self.assertEqual(dest.read(), b"complete")
        self.assertEqual(len(self.requests), 2)

    async def test_video_wrapper_preserves_redirect_policy_and_defaults(self):
        calls = []

        async def record_download(url, dest, **kwargs):
            calls.append((url, kwargs))

        self.namespace["download_url_to_bytesio"] = record_download
        await self.namespace["download_url_to_video_output"](
            "https://example.test/video.mp4", allow_redirects=False
        )
        self.assertEqual(calls[0][1], {
            "timeout": 600, "idle_timeout": 60, "max_retries": 2,
            "cls": None, "allow_redirects": False,
        })


if __name__ == "__main__":
    unittest.main()
