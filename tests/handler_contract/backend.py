"""What the AZ-AI backend sends to a worker and what it accepts back.

The accepting half is a port of the backend's own checks
(`libs/integrations/src/runpod/runpod-response.validator.ts`), kept in the
order and with the limits they have there, so that a handler which passes here
answers in a shape the backend reads. When the backend changes its checks, this
file changes with it.

`docs/handler-contract.md` describes the same contract in words.
"""

import base64
import re
import urllib.parse

# The names the backend gives a Studio job's inputs. A worker that hands an
# input back beside its result is recognised by this prefix.
STUDIO_INPUT_NAME_PREFIX = "azai_in_"

# The one name the two original tools give their input image.
INPUT_FILENAME = "input_image.png"

MEDIA_EXTENSIONS = {
    "image": (".png", ".jpg", ".jpeg", ".webp"),
    "video": (".mp4", ".webm", ".mov"),
}

MAX_OUTPUT_URL_LENGTH = 4096
MAX_OBJECT_KEY_LENGTH = 1024
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
SAFE_FILENAME_PATTERN = re.compile(r"^[^\\/]{1,255}$", re.DOTALL)


class Rejected(Exception):
    """The backend cannot read the answer.

    For the user this is worse than a failed job: nothing completes and nothing
    is refunded until the job reaches its age limit.
    """


# --- What RunPod makes of a handler's return value ---------------------------


def runpod_job(handler_return):
    """The status and output RunPod reports for what a handler returned.

    This follows `run_job` of the RunPod SDK (1.7.13): a truthy `error` fails
    the job, `refresh_worker` is taken out, and the rest is the job's output.
    """
    if not isinstance(handler_return, dict):
        return "COMPLETED", handler_return
    output = dict(handler_return)
    error = output.pop("error", None)
    output.pop("refresh_worker", None)
    return ("FAILED" if error else "COMPLETED"), output


# --- What the backend accepts -------------------------------------------------


def _is_record(value):
    return isinstance(value, dict)


def _has_control_characters(value):
    return any(ord(character) <= 31 or ord(character) == 127 for character in value)


def _safe_filename(value):
    # The original counts UTF-16 units, so a character outside the basic plane
    # counts twice.
    return (
        isinstance(value, str)
        and SAFE_FILENAME_PATTERN.fullmatch(value) is not None
        and len(value.encode("utf-16-le")) // 2 <= 255
        and not _has_control_characters(value)
    )


def parse_completed_output(output):
    """The answer of an Upscale or Enhancement job: exactly one stored image."""
    if not _is_record(output) or not isinstance(output.get("images"), list):
        raise Rejected("invalid_output")
    if len(output["images"]) != 1:
        raise Rejected("invalid_output_image_count")

    image = output["images"][0]
    if not _is_record(image):
        raise Rejected("invalid_output_image")
    data = image.get("data")
    if not isinstance(data, str) or not data or len(data) > MAX_OUTPUT_URL_LENGTH:
        raise Rejected("invalid_output_url")
    if not _safe_filename(image.get("filename")):
        raise Rejected("invalid_output_filename")
    if image.get("type") != "s3_url":
        raise Rejected("invalid_output_type")
    return {"data": data, "filename": image["filename"], "type": "s3_url"}


def _linked_filename(url):
    path = re.split(r"[?#]", url, maxsplit=1)[0]
    return path[path.rfind("/") + 1 :]


def _media_candidate(value):
    url = filename = None
    if isinstance(value, str):
        url = value
    elif _is_record(value):
        # Bytes returned inline are not a stored result; only links are read.
        if "type" in value and value["type"] != "s3_url":
            return None
        url = value.get("data") if value.get("data") is not None else value.get("url")
        filename = value.get("filename")
    if (
        not isinstance(url, str)
        or len(url) > MAX_OUTPUT_URL_LENGTH
        or not url.startswith("https://")
    ):
        return None
    name = filename if _safe_filename(filename) else _linked_filename(url)
    return {"url": url, "filename": name}


def _worker_reported_failure(output):
    if output.get("success") is False:
        return True
    if isinstance(output.get("error"), str) and output["error"].strip():
        return True
    status = output.get("status")
    status = status.strip().lower() if isinstance(status, str) else ""
    return status != "" and status not in ("success", "ok", "completed")


def parse_completed_media(output, kind):
    """The answer of a Studio job: exactly one stored file of the tool's kind.

    Returns the file, or "failed" when the worker itself says the render failed.
    """
    if not _is_record(output):
        raise Rejected("invalid_output")

    candidates = []
    for key in ("videos", "images", "files", "message"):
        entries = output.get(key)
        if not isinstance(entries, list):
            entries = [entries]
        if len(entries) > 32:
            raise Rejected("invalid_output")
        for entry in entries:
            candidate = _media_candidate(entry)
            if candidate:
                candidates.append(candidate)

    results = []
    seen_urls = set()
    for candidate in candidates:
        first_with_this_url = candidate["url"] not in seen_urls
        seen_urls.add(candidate["url"])
        if (
            first_with_this_url
            and _linked_filename(candidate["url"]).lower().endswith(MEDIA_EXTENSIONS[kind])
            and not candidate["filename"].startswith(STUDIO_INPUT_NAME_PREFIX)
        ):
            results.append(candidate)

    if not results:
        if _worker_reported_failure(output):
            return "failed"
        raise Rejected("invalid_output")
    if len(results) != 1:
        raise Rejected("invalid_output_media_count")
    return {"data": results[0]["url"], "filename": results[0]["filename"], "type": "s3_url"}


def _origin(parts):
    """Scheme, host and port of a URL, with the scheme's own port left out."""
    port = None if parts.port == {"https": 443, "http": 80}.get(parts.scheme) else parts.port
    return parts.scheme, (parts.hostname or "").lower(), port


def trusted_object_key(output_url, endpoint, bucket):
    """The object a result link names, when it is in the storage the backend accepts."""
    if not endpoint or not bucket:
        raise Rejected("missing_storage_configuration")
    if not BUCKET_PATTERN.fullmatch(bucket):
        raise Rejected("invalid_storage_bucket")
    if (
        not output_url
        or len(output_url) > MAX_OUTPUT_URL_LENGTH
        or output_url.strip() != output_url
        or _has_control_characters(output_url)
    ):
        raise Rejected("invalid_output_url")

    raw = re.match(r"^https://[^/?#]+(/[^?#]*)?(?:\?[^#]*)?$", output_url, re.IGNORECASE)
    raw_path = raw.group(1) if raw else None
    if (
        not raw_path
        or "\\" in raw_path
        or re.search(r"%(?:25)*(?:2e|2f|5c)", raw_path, re.IGNORECASE)
        or any(part in (".", "..") for part in raw_path.split("/"))
    ):
        raise Rejected("invalid_output_url")

    storage = urllib.parse.urlsplit(endpoint)
    output = urllib.parse.urlsplit(output_url)
    if storage.scheme != "https" or output.scheme != "https":
        raise Rejected("insecure_output_url")
    if (
        output.username
        or output.password
        or output.fragment
        or storage.username
        or storage.password
        or storage.query
        or storage.fragment
    ):
        raise Rejected("invalid_output_url")
    if _origin(storage) != _origin(output):
        raise Rejected("untrusted_output_origin")

    expected_prefix = re.sub(r"/{2,}", "/", f"{storage.path.rstrip('/')}/{bucket}/")
    if not output.path.startswith(expected_prefix):
        raise Rejected("untrusted_output_path")

    encoded_key = output.path[len(expected_prefix) :]
    try:
        if re.search(r"%(?![0-9A-Fa-f]{2})", encoded_key):
            raise ValueError("malformed escape")
        key = urllib.parse.unquote(encoded_key, errors="strict")
    except ValueError:
        raise Rejected("invalid_output_key") from None
    segments = key.split("/")
    if (
        not key
        or len(key) > MAX_OBJECT_KEY_LENGTH
        or "\\" in key
        or _has_control_characters(key)
        or any(segment in ("", ".", "..") for segment in segments)
    ):
        raise Rejected("invalid_output_key")
    return key


def settle(handler_return, kind=None):
    """How the backend settles a job whose handler returned this.

    `kind` is "image" or "video" for a Studio job and left out for Upscale and
    Enhancement. Returns the accepted file, or "failed" for a job the backend
    fails and refunds at once. Raises Rejected for an answer it cannot read.
    """
    status, output = runpod_job(handler_return)
    if status == "FAILED":
        return "failed"
    return parse_completed_media(output, kind) if kind else parse_completed_output(output)


# --- What the backend sends ---------------------------------------------------


def inline(data):
    """An input file as the backend inlines it: base64 without a prefix."""
    return base64.b64encode(data).decode("ascii")


def upscale_job(image, workflow, job_id="job-upscale"):
    """An Upscale job. `image` is base64, or the signed link in `url` input mode."""
    return {
        "id": job_id,
        "input": {
            "workflow": workflow,
            "images": [{"name": INPUT_FILENAME, "image": image}],
        },
    }


def enhancement_job(workflow, link=None, job_id="job-enhancement"):
    """An Enhancement job. The image is inside the graph unless a link is sent."""
    images = [{"name": INPUT_FILENAME, "image": link}] if link else []
    return {"id": job_id, "input": {"workflow": workflow, "images": images}}


def studio_job(workflow, files, delivery, comfy_api_key=None, job_id="job-studio"):
    """A Studio job. `files` is a list of (name, bytes or signed link) in order.

    `delivery` is the worker's `input` in STUDIO_WORKERS: `base64` puts the
    bytes into `image`, `url` puts the link into `url`, and `image-url` puts
    the link into `image`. A clip is one more entry of `images`.
    """
    field = {"base64": "image", "url": "url", "image-url": "image"}[delivery]
    job_input = {
        "workflow": workflow,
        "images": [
            {"name": name, field: inline(source) if delivery == "base64" else source}
            for name, source in files
        ],
    }
    if comfy_api_key:
        job_input["comfy_org_api_key"] = comfy_api_key
    return {"id": job_id, "input": job_input}
