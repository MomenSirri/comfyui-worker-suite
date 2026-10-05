from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request, error
import json
import mimetypes
import os


ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("WEBAPP_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEBAPP_PORT", "7860"))
RUNPOD_TIMEOUT_S = int(os.environ.get("RUNPOD_TIMEOUT_S", "900"))


def json_response(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def runpod_request(endpoint_id, api_key, action, payload=None, job_id=None):
    if action not in {"run", "runsync", "status"}:
        raise ValueError("Unsupported RunPod action")

    if action == "status":
        if not job_id:
            raise ValueError("Missing jobId for status request")
        url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
        body = None
        method = "GET"
    else:
        url = f"https://api.runpod.ai/v2/{endpoint_id}/{action}"
        body = json.dumps(payload).encode("utf-8")
        method = "POST"

    req = request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method=method,
    )

    with request.urlopen(req, timeout=RUNPOD_TIMEOUT_S) as resp:
        response_body = resp.read().decode("utf-8")
        return resp.status, json.loads(response_body) if response_body else {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def do_POST(self):
        if self.path != "/api/runpod":
            json_response(self, 404, {"error": "Not found"})
            return

        try:
            body = read_json(self)
            endpoint_id = str(body.get("endpointId", "")).strip()
            api_key = str(body.get("apiKey", "")).strip()
            action = str(body.get("action", "runsync")).strip()
            payload = body.get("payload")
            job_id = str(body.get("jobId", "")).strip()

            if not endpoint_id:
                json_response(self, 400, {"error": "Missing endpointId"})
                return
            if not api_key:
                json_response(self, 400, {"error": "Missing apiKey"})
                return
            if action == "status":
                if not job_id:
                    json_response(self, 400, {"error": "Missing jobId"})
                    return
            elif not isinstance(payload, dict):
                json_response(self, 400, {"error": "Missing payload object"})
                return

            status, response_payload = runpod_request(
                endpoint_id, api_key, action, payload=payload, job_id=job_id
            )
            json_response(self, status, response_payload)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail)
            except json.JSONDecodeError:
                pass
            json_response(
                self,
                exc.code,
                {"error": "RunPod request failed", "status": exc.code, "details": detail},
            )
        except Exception as exc:
            json_response(self, 500, {"error": str(exc)})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            path = "/index.html"

        candidate = (ROOT / path.lstrip("/")).resolve()
        if ROOT not in candidate.parents and candidate != ROOT:
            self.send_error(403)
            return
        if not candidate.exists() or not candidate.is_file():
            self.send_error(404)
            return

        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print(f"Serving webapp on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
