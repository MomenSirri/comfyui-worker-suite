"""Stand-ins for everything a ComfyUI handler talks to while it runs one job.

A handler reaches ComfyUI through `requests` and one websocket, fetches input
files from signed links through `requests`, and stores results with the RunPod
SDK's upload helpers. `FakeComfy` answers all of them, so the handlers of the
suite can be run as they are, on a machine without ComfyUI, a GPU or a bucket.
"""

import contextlib
import copy
import io
import json
import logging
import os
import shutil
import tempfile
import time
import urllib.parse
from unittest.mock import patch

import requests
import websocket

COMFY_HOST = "127.0.0.1:8188"
PROMPT_ID = "prompt-0001"

# The storage the backend is configured to accept results from, and how a
# worker's endpoint names it: BUCKET_ENDPOINT_URL carries the bucket as its path.
STORAGE_ENDPOINT = "https://storage.example.com"
STORAGE_BUCKET = "worker-results"
INPUT_HOST = "inputs.example.com"

# Stands for the credential in every signed link of a test.
SIGNATURE = "secret-signature"

PNG = b"\x89PNG\r\n\x1a\n" + b"input image"
JPEG = b"\xff\xd8\xff\xe0" + b"input photo"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"input clip"
RESULT_PNG = b"\x89PNG\r\n\x1a\n" + b"result image"
RESULT_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"result video"

CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

# A websocket that has nothing more to say reports a timeout. A handler that
# keeps listening after this many has missed the end of the job.
MAX_IDLE_RECEIVES = 200


def signed(url):
    return f"{url}?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature={SIGNATURE}"


class _Response:
    def __init__(self, url, status=200, body=b"", content_type="application/json", headers=None):
        self.url = url
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": content_type, **(headers or {})}

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            # requests names the whole URL, query string included.
            raise requests.HTTPError(
                f"{self.status_code} Client Error: Forbidden for url: {self.url}",
                response=self,
            )

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _json(url, value, status=200):
    return _Response(url, status, json.dumps(value).encode("utf-8"))


class _Socket:
    def __init__(self, comfy):
        self._comfy = comfy
        self._messages = iter(())
        self._idle_receives = 0
        self.connected = False

    def connect(self, _url, **_options):
        self.connected = True
        self._messages = iter(self._comfy.websocket_messages())

    def settimeout(self, _seconds):
        pass

    def recv(self):
        try:
            return next(self._messages)
        except StopIteration:
            self._idle_receives += 1
            if self._idle_receives > MAX_IDLE_RECEIVES:
                raise AssertionError(
                    "the handler kept listening after ComfyUI had nothing more to say"
                ) from None
            raise websocket.WebSocketTimeoutException("timed out") from None

    def close(self):
        self.connected = False


class FakeComfy:
    """ComfyUI, the input links and the result bucket of one job."""

    def __init__(self, handler_module, output_directory=None):
        self._handler = handler_module
        self._links = {}
        self._files = {}
        self._outputs = {}
        self._unreported_files = {}
        self._earlier_graph_files = {}
        self._failure = None
        self._clock = 0.0
        # A graph that never reports its end, to try the run-time limit.
        self.never_finishes = False
        # False leaves BUCKET_ENDPOINT_URL without its two keys.
        self.storage_has_keys = True
        # False runs the worker without a bucket at all: results come back inline.
        self.storage_configured = True
        # Text of the error the bucket refuses every upload with.
        self.storage_error = None
        # False lets ComfyUI answer a question about its queue with an error.
        self.queue_readable = True

        # What the job left behind.
        self.input_directory = {}
        self.link_requests = []
        self.queued = []
        self.bucket = []
        self.progress = []
        self.interrupted = False
        self.log = ""
        # ComfyUI's output directory. A worker keeps it from one job to the next.
        self._owns_output_directory = output_directory is None
        self.output_directory = output_directory or tempfile.mkdtemp(prefix="comfy-output-")

    def close(self):
        if self._owns_output_directory:
            shutil.rmtree(self.output_directory, ignore_errors=True)

    def next_job(self):
        """ComfyUI as the next job on the same worker finds it: its disk as it was left."""
        return FakeComfy(self._handler, self.output_directory)

    # --- Setting up one job ---------------------------------------------------

    def link(self, name, body, status=200, content_type=None, headers=None):
        """Serve a file at a signed link and return the link."""
        if content_type is None:
            content_type = CONTENT_TYPES.get(os.path.splitext(name)[1], "application/octet-stream")
        url = signed(f"https://{INPUT_HOST}/prompts/{name}")
        self._links[url] = (status, body, content_type, headers)
        return url

    def saves(self, node_id, filename, body, subfolder="", folder="output", video=False):
        """Let a node leave a file, as ComfyUI's history reports it.

        A video is listed under `images` with the `animated` hint beside it; that
        is how the core video nodes report one. `folder="input"` is the echo of a
        loader node, which reports the file it was given.
        """
        output = self._outputs.setdefault(str(node_id), {"images": []})
        output["images"].append({"filename": filename, "subfolder": subfolder, "type": folder})
        if video:
            output["animated"] = [True]
        self._files[(folder, subfolder, filename)] = body

    def says(self, node_id, text):
        """Let a node leave a text output beside any files."""
        self._outputs.setdefault(str(node_id), {})["text"] = [text]

    def writes(self, path, text):
        """Let a node write a text file into the output directory without reporting it."""
        self._unreported_files[path] = text

    def still_runs_an_earlier_graph(self, path, text):
        """Let the graph of an earlier job go on while this job runs, and write a text file.

        A handler that gave up on its graph, at the run-time limit or over a
        lost connection, leaves it to ComfyUI, which takes one graph at a time.
        """
        self._earlier_graph_files[path] = text

    def fails_at(self, node_id, node_type, message):
        """Let a node raise while the graph runs."""
        self._failure = {
            "prompt_id": PROMPT_ID,
            "node_id": str(node_id),
            "node_type": node_type,
            "exception_message": message,
            "exception_type": "Exception",
            "traceback": [],
        }

    # --- Running it -----------------------------------------------------------

    def run(self, entry, job):
        """Run one job through a handler. Returns what the handler returned."""
        captured = io.StringIO()
        log_handler = logging.StreamHandler(captured)
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        try:
            with self._patched(), contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                return entry(copy.deepcopy(job))
        finally:
            root_logger.removeHandler(log_handler)
            # Progress updates are sent to RunPod and shown with the job's status.
            self.log = captured.getvalue() + "\n".join(self.progress)

    def _environment(self):
        variables = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith("BUCKET_") and name != "COMFY_ORG_API_KEY"
        }
        if self.storage_configured:
            variables["BUCKET_ENDPOINT_URL"] = f"{STORAGE_ENDPOINT}/{STORAGE_BUCKET}"
            if self.storage_has_keys:
                variables["BUCKET_ACCESS_KEY_ID"] = "test-access-key"
                variables["BUCKET_SECRET_ACCESS_KEY"] = "test-secret-key"
        return variables

    @contextlib.contextmanager
    def _patched(self):
        upload = self._handler.rp_upload
        serverless = self._handler.runpod.serverless
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, self._environment(), clear=True))
            stack.enter_context(patch.object(requests, "get", self._get))
            stack.enter_context(patch.object(requests, "post", self._post))
            stack.enter_context(patch.object(websocket, "WebSocket", lambda *_a, **_k: _Socket(self)))
            stack.enter_context(patch.object(time, "sleep", lambda _seconds: None))
            stack.enter_context(
                patch.object(self._handler, "COMFY_OUTPUT_DIR", self.output_directory, create=True)
            )
            if self.never_finishes:
                stack.enter_context(patch.object(time, "monotonic", self._tick))
            stack.enter_context(
                patch.object(upload, "upload_image", self._upload_image, create=True)
            )
            stack.enter_context(
                patch.object(upload, "upload_file_to_bucket", self._upload_file_to_bucket, create=True)
            )
            stack.enter_context(
                patch.object(serverless, "progress_update", self._progress_update, create=True)
            )
            yield

    def _tick(self):
        self._clock += 30.0
        return self._clock

    def _progress_update(self, _job, message):
        self.progress.append(str(message))

    # --- ComfyUI's HTTP API and the input links -------------------------------

    def _get(self, url, **options):
        parts = urllib.parse.urlsplit(url)
        if parts.netloc != COMFY_HOST:
            self.link_requests.append({"url": url, **options})
            if url not in self._links:
                raise requests.ConnectionError(f"No route to host for url: {url}")
            status, body, content_type, headers = self._links[url]
            response = _Response(url, status, body, content_type, headers)
            hook = (options.get("hooks") or {}).get("response")
            if hook:
                response = hook(response) or response
            if 300 <= status < 400 and "Location" in response.headers:
                # requests reads the whole body of a redirect answer into memory
                # before it follows it, and also when told not to follow it.
                raise AssertionError("requests would read the body of this redirect answer")
            return response

        if parts.path in ("/", "/object_info", "/system_stats"):
            return _json(url, {})
        if parts.path == "/queue":
            if not self.queue_readable:
                return _json(url, {"error": "internal server error"}, status=500)
            return _json(url, self._queue())
        if parts.path == f"/history/{PROMPT_ID}":
            return _json(url, self._history())
        if parts.path == "/view":
            query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
            key = tuple(query.get(name, [""])[0] for name in ("type", "subfolder", "filename"))
            if key in self._files:
                return _Response(url, 200, self._files[key], "application/octet-stream")
        return _json(url, {"error": "not found"}, status=404)

    def _post(self, url, **options):
        parts = urllib.parse.urlsplit(url)
        if parts.netloc != COMFY_HOST:
            raise requests.ConnectionError(f"No route to host for url: {url}")

        if parts.path == "/upload/image":
            name, stream, content_type = options["files"]["image"]
            self.input_directory[name] = {"body": stream.read(), "content_type": content_type}
            return _json(url, {"name": name, "subfolder": "", "type": "input"})
        if parts.path == "/prompt":
            self.queued.append(json.loads(options["data"]))
            # The graphs run from here on: first the earlier one, then this job's.
            self._write_to_output_directory(self._earlier_graph_files)
            self._write_to_output_directory(self._unreported_files)
            return _json(url, {"prompt_id": PROMPT_ID, "number": 1, "node_errors": {}})
        if parts.path == "/interrupt":
            self.interrupted = True
            return _json(url, {})
        return _json(url, {"error": "not found"}, status=404)

    def _queue(self):
        # ComfyUI runs one graph at a time. An earlier graph has ended by the
        # time this job's is reported; this job's stays listed from then on.
        if self.queued:
            running = [[1, PROMPT_ID, {}, {}, []]]
        elif self._earlier_graph_files:
            running = [[0, "prompt-0000", {}, {}, []]]
        else:
            running = []
        return {"queue_running": running, "queue_pending": []}

    def _write_to_output_directory(self, files):
        for path, text in files.items():
            target = os.path.join(self.output_directory, *path.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as written:
                written.write(text)

    def _history(self):
        if not self.queued:
            return {}
        if self.never_finishes:
            status = {"status_str": "", "completed": False, "messages": []}
        elif self._failure:
            status = {
                "status_str": "error",
                "completed": False,
                "messages": [["execution_error", self._failure]],
            }
        else:
            status = {
                "status_str": "success",
                "completed": True,
                "messages": [["execution_success", {"prompt_id": PROMPT_ID}]],
            }
        return {PROMPT_ID: {"outputs": self._outputs, "status": status, "meta": {}}}

    def websocket_messages(self):
        def message(kind, data):
            return json.dumps({"type": kind, "data": data})

        yield message("status", {"status": {"exec_info": {"queue_remaining": 1}}})
        yield message("execution_start", {"prompt_id": PROMPT_ID})
        for node_id in self._outputs:
            yield message("executing", {"node": node_id, "prompt_id": PROMPT_ID})
            yield message(
                "executed",
                {"node": node_id, "prompt_id": PROMPT_ID, "output": self._outputs[node_id]},
            )
        if self.never_finishes:
            return
        if self._failure:
            yield message("execution_error", self._failure)
            return
        yield message("executing", {"node": None, "prompt_id": PROMPT_ID})
        yield message("execution_success", {"prompt_id": PROMPT_ID})

    # --- The result bucket, behind the RunPod SDK's upload helpers ------------

    def _upload_image(self, job_id, image_location, *_args, **_options):
        extension = os.path.splitext(image_location)[1]
        name = f"{len(self.bucket):08x}{extension}"
        if not self._storage_usable():
            # Without its credentials the SDK writes to the worker's disk and
            # returns that path as if it were the link.
            return f"simulated_uploaded/{name}"
        with open(image_location, "rb") as stored:
            return self._store(f"{job_id}/{name}", stored.read(), "image/" + extension.lstrip("."))

    def _upload_file_to_bucket(
        self, file_name, file_location, bucket_creds=None, bucket_name=None, prefix=None, extra_args=None
    ):
        if not self._storage_usable():
            return f"local_upload/{file_name}"
        with open(file_location, "rb") as stored:
            return self._store(
                f"{prefix}/{file_name}" if prefix else file_name,
                stored.read(),
                (extra_args or {}).get("ContentType"),
            )

    def _storage_usable(self):
        return self.storage_configured and self.storage_has_keys

    def _store(self, key, body, content_type):
        if self.storage_error:
            raise RuntimeError(self.storage_error)
        # The SDK uses the month as the bucket name, under the endpoint's path.
        key = f"{time.strftime('%m-%y')}/{key}"
        self.bucket.append({"key": key, "body": body, "content_type": content_type})
        return signed(f"{STORAGE_ENDPOINT}/{STORAGE_BUCKET}/{key}")
