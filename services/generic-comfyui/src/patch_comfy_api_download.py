"""Apply bounded, retry-safe timeouts to ComfyUI API video downloads.

ComfyUI is installed during the Docker build, so this patch is applied directly
to its download helper in the image. The transformations deliberately require
exact upstream anchors: if ComfyUI changes the helper, the image build fails and
forces a review instead of silently shipping without the timeout protection.
"""

from __future__ import annotations

import argparse
from pathlib import Path


PATCH_MARKER = "_VIDEO_DOWNLOAD_IDLE_TIMEOUT_S"


def _replace_once(source: str, old: str, new: str, description: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            f"Cannot patch ComfyUI download helper: expected one {description} "
            f"anchor, found {count}."
        )
    return source.replace(old, new, 1)


def _replace_variant(
    source: str, variants: tuple[tuple[str, str], ...], description: str
) -> str:
    """Replace exactly one known upstream variant, failing on unfamiliar code."""
    matches = [(old, new) for old, new in variants if old in source]
    if len(matches) != 1:
        raise RuntimeError(
            f"Cannot patch ComfyUI download helper: expected one known "
            f"{description} variant, found {len(matches)}."
        )
    old, new = matches[0]
    return _replace_once(source, old, new, description)


def patch_source(source: str) -> str:
    """Return the patched ComfyUI helper source."""
    if PATCH_MARKER in source:
        return source

    source = _replace_once(
        source,
        "import asyncio\nimport contextlib\nimport uuid\n",
        "import asyncio\nimport contextlib\nimport os\nimport re\nimport uuid\n",
        "import",
    )
    source = _replace_once(
        source,
        "from aiohttp.client_exceptions import ClientError, ContentTypeError\n",
        "from aiohttp.client_exceptions import (\n"
        "    ClientError,\n"
        "    ClientPayloadError,\n"
        "    ContentTypeError,\n"
        ")\n",
        "aiohttp exception import",
    )
    source = _replace_once(
        source,
        "_RETRY_STATUS = {408, 429, 500, 502, 503, 504}\n\n\n",
        "_RETRY_STATUS = {408, 429, 500, 502, 503, 504}\n"
        "_VIDEO_DOWNLOAD_TIMEOUT_S = max(\n"
        "    1.0, float(os.environ.get(\"COMFY_API_VIDEO_DOWNLOAD_TIMEOUT_S\", \"600\"))\n"
        ")\n"
        "_VIDEO_DOWNLOAD_IDLE_TIMEOUT_S = max(\n"
        "    1.0,\n"
        "    float(os.environ.get(\"COMFY_API_VIDEO_DOWNLOAD_IDLE_TIMEOUT_S\", \"60\")),\n"
        ")\n"
        "_VIDEO_DOWNLOAD_MAX_RETRIES = max(\n"
        "    0, int(os.environ.get(\"COMFY_API_VIDEO_DOWNLOAD_MAX_RETRIES\", \"2\"))\n"
        ")\n\n\n"
        "def _raise_if_download_stalled(\n"
        "    attempt_started_at: float,\n"
        "    last_progress_at: float,\n"
        "    *,\n"
        "    timeout: float | None,\n"
        "    idle_timeout: float | None,\n"
        ") -> None:\n"
        "    now = asyncio.get_running_loop().time()\n"
        "    if timeout is not None and now - attempt_started_at >= timeout:\n"
        "        raise asyncio.TimeoutError(\n"
        "            f\"Download attempt exceeded {timeout:g} seconds.\"\n"
        "        )\n"
        "    if idle_timeout is not None and now - last_progress_at >= idle_timeout:\n"
        "        raise asyncio.TimeoutError(\n"
        "            f\"Download made no progress for {idle_timeout:g} seconds.\"\n"
        "        )\n\n\n",
        "retry configuration",
    )
    source = _replace_once(
        source,
        "async def download_url_to_bytesio(\n"
        "    url: str,\n"
        "    dest: BytesIO | IO[bytes] | str | Path | None,\n"
        "    *,\n"
        "    timeout: float | None = None,\n"
        "    max_retries: int = 5,\n",
        "async def download_url_to_bytesio(\n"
        "    url: str,\n"
        "    dest: BytesIO | IO[bytes] | str | Path | None,\n"
        "    *,\n"
        "    timeout: float | None = None,\n"
        "    idle_timeout: float | None = None,\n"
        "    max_retries: int = 5,\n",
        "download_url_to_bytesio signature",
    )
    source = _replace_once(
        source,
        "        op_id = _generate_operation_id(\"GET\", url, attempt)\n"
        "        timeout_cfg = aiohttp.ClientTimeout(total=timeout)\n",
        "        op_id = _generate_operation_id(\"GET\", url, attempt)\n"
        "        timeout_cfg = aiohttp.ClientTimeout(total=timeout)\n"
        "        attempt_started_at = asyncio.get_running_loop().time()\n"
        "        last_progress_at = attempt_started_at\n\n"
        "        # Also covers older ComfyUI releases without a retry buffer reset.\n"
        "        if isinstance(dest, BytesIO):\n"
        "            dest.seek(0)\n"
        "            dest.truncate(0)\n",
        "download attempt loop",
    )
    # v0.38 keeps a read task alive across polling intervals. Preserve this
    # behavior: cancelling each slow read can discard data from the stream.
    if "                read_task: asyncio.Task | None = None\n" in source:
        source = _replace_once(
            source,
            "                        if not done:\n"
            "                            continue\n",
            "                        if not done:\n"
            "                            _raise_if_download_stalled(\n"
            "                                attempt_started_at,\n"
            "                                last_progress_at,\n"
            "                                timeout=timeout,\n"
            "                                idle_timeout=idle_timeout,\n"
            "                            )\n"
            "                            continue\n",
            "pending stream read",
        )
        source = _replace_once(
            source,
            "                        if not chunk:\n"
            "                            if resp.content.at_eof():\n"
            "                                break\n"
            "                            continue\n"
            "                        sink.write(chunk)\n"
            "                        written += len(chunk)\n",
            "                        if not chunk:\n"
            "                            if resp.content.at_eof():\n"
            "                                break\n"
            "                            _raise_if_download_stalled(\n"
            "                                attempt_started_at,\n"
            "                                last_progress_at,\n"
            "                                timeout=timeout,\n"
            "                                idle_timeout=idle_timeout,\n"
            "                            )\n"
            "                            continue\n"
            "                        sink.write(chunk)\n"
            "                        written += len(chunk)\n"
            "                        last_progress_at = asyncio.get_running_loop().time()\n",
            "stream progress",
        )
        source = _replace_once(
            source,
            "                if isinstance(dest, BytesIO):\n",
            "                expected_bytes = resp.content_length\n"
            "                if (\n"
            "                    expected_bytes is not None\n"
            "                    and not resp.headers.get(\"Content-Encoding\")\n"
            "                    and written != expected_bytes\n"
            "                ):\n"
            "                    raise ClientPayloadError(\n"
            "                        f\"Incomplete download: expected {expected_bytes} bytes, \"\n"
            "                        f\"received {written}.\"\n"
            "                    )\n\n"
            "                if isinstance(dest, BytesIO):\n",
            "stream length validation",
        )
    else:
        source = _patch_legacy_streaming_loop(source)
    source = _replace_variant(
        source,
        (
            (
                "        except (ClientError, OSError) as e:\n",
                "        except (asyncio.TimeoutError, ClientError, OSError) as e:\n",
            ),
            (
                "        except (ClientError, OSError, asyncio.TimeoutError) as e:\n",
                "        except (ClientError, OSError, asyncio.TimeoutError) as e:\n",
            ),
        ),
        "retryable exception handler",
    )
    # aiohttp ends the text of a response error with the request URL, and a
    # provider's result link is signed. ComfyUI logs a failed node's message and
    # its traceback with every chained error. So the query string is cut from the
    # message, and the upstream errors that follow chain a stand-in for the
    # original error instead of the error itself.
    failure_guard = (
        "            reason = re.sub(\n"
        "                r\"\\?\\S*\", \"?[redacted]\", f\"{type(e).__name__}: {e}\"\n"
        "            )\n"
        "            if idle_timeout is not None:\n"
        "                raise ApiServerError(\n"
        "                    f\"Media download failed after {attempt} attempt(s): {reason}\"\n"
        "                ) from None\n"
        "            # The original error names the request URL: chain this instead.\n"
        "            e = Exception(reason)\n\n"
    )
    source = _replace_variant(
        source,
        tuple(
            (anchor, failure_guard + anchor)
            for anchor in (
                "            diag = await _diagnose_connectivity()\n",
                "            diag = await diagnose_connectivity()\n",
            )
        ),
        "final retry failure",
    )
    legacy_wrapper = (
        "async def download_url_to_video_output(\n"
        "    video_url: str,\n"
        "    *,\n"
        "    timeout: float = None,\n"
        "    max_retries: int = 5,\n"
        "    cls: type[COMFY_IO.ComfyNode] = None,\n"
        ") -> InputImpl.VideoFromFile:\n"
        "    \"\"\"Downloads a video from a URL and returns a `VIDEO` output.\"\"\"\n"
        "    result = BytesIO()\n"
        "    await download_url_to_bytesio(video_url, result, timeout=timeout, max_retries=max_retries, cls=cls)\n"
    )
    current_wrapper = (
        "async def download_url_to_video_output(\n"
        "    video_url: str,\n"
        "    *,\n"
        "    timeout: float = None,\n"
        "    max_retries: int = 5,\n"
        "    cls: type[COMFY_IO.ComfyNode] = None,\n"
        "    allow_redirects: bool = True,\n"
        ") -> InputImpl.VideoFromFile:\n"
        "    \"\"\"Downloads a video from a URL and returns a `VIDEO` output.\"\"\"\n"
        "    result = BytesIO()\n"
        "    await download_url_to_bytesio(\n"
        "        video_url,\n"
        "        result,\n"
        "        timeout=timeout,\n"
        "        max_retries=max_retries,\n"
        "        cls=cls,\n"
        "        allow_redirects=allow_redirects,\n"
        "    )\n"
    )

    def bounded_wrapper(wrapper: str) -> str:
        wrapper = wrapper.replace(
            "    timeout: float = None,\n",
            "    timeout: float | None = _VIDEO_DOWNLOAD_TIMEOUT_S,\n"
            "    idle_timeout: float | None = _VIDEO_DOWNLOAD_IDLE_TIMEOUT_S,\n",
        ).replace(
            "    max_retries: int = 5,\n",
            "    max_retries: int = _VIDEO_DOWNLOAD_MAX_RETRIES,\n",
        )
        if "        timeout=timeout,\n" in wrapper:
            return wrapper.replace(
                "        timeout=timeout,\n",
                "        timeout=timeout,\n        idle_timeout=idle_timeout,\n",
            )
        return wrapper.replace(
            "timeout=timeout, max_retries=max_retries",
            "timeout=timeout, idle_timeout=idle_timeout, max_retries=max_retries",
        )

    source = _replace_variant(
        source,
        tuple(
            (wrapper, bounded_wrapper(wrapper))
            for wrapper in (legacy_wrapper, current_wrapper)
        ),
        "video download wrapper",
    )
    return source


def _patch_legacy_streaming_loop(source: str) -> str:
    return _replace_once(
        source,
        "                written = 0\n"
        "                while True:\n"
        "                    try:\n"
        "                        chunk = await asyncio.wait_for(resp.content.read(1024 * 1024), timeout=1.0)\n"
        "                    except asyncio.TimeoutError:\n"
        "                        chunk = b\"\"\n"
        "                    except asyncio.CancelledError:\n"
        "                        raise ProcessingInterrupted(\"Task cancelled\") from None\n\n"
        "                    if is_processing_interrupted():\n"
        "                        raise ProcessingInterrupted(\"Task cancelled\")\n\n"
        "                    if not chunk:\n"
        "                        if resp.content.at_eof():\n"
        "                            break\n"
        "                        continue\n\n"
        "                    sink.write(chunk)\n"
        "                    written += len(chunk)\n",
        "                written = 0\n"
        "                while True:\n"
        "                    try:\n"
        "                        chunk = await asyncio.wait_for(resp.content.read(1024 * 1024), timeout=1.0)\n"
        "                    except asyncio.TimeoutError:\n"
        "                        _raise_if_download_stalled(\n"
        "                            attempt_started_at,\n"
        "                            last_progress_at,\n"
        "                            timeout=timeout,\n"
        "                            idle_timeout=idle_timeout,\n"
        "                        )\n"
        "                        chunk = b\"\"\n"
        "                    except asyncio.CancelledError:\n"
        "                        raise ProcessingInterrupted(\"Task cancelled\") from None\n\n"
        "                    if is_processing_interrupted():\n"
        "                        raise ProcessingInterrupted(\"Task cancelled\")\n\n"
        "                    if not chunk:\n"
        "                        if resp.content.at_eof():\n"
        "                            break\n"
        "                        _raise_if_download_stalled(\n"
        "                            attempt_started_at,\n"
        "                            last_progress_at,\n"
        "                            timeout=timeout,\n"
        "                            idle_timeout=idle_timeout,\n"
        "                        )\n"
        "                        continue\n\n"
        "                    sink.write(chunk)\n"
        "                    written += len(chunk)\n"
        "                    last_progress_at = asyncio.get_running_loop().time()\n\n"
        "                expected_bytes = resp.content_length\n"
        "                if (\n"
        "                    expected_bytes is not None\n"
        "                    and not resp.headers.get(\"Content-Encoding\")\n"
        "                    and written != expected_bytes\n"
        "                ):\n"
        "                    raise ClientPayloadError(\n"
        "                        f\"Incomplete download: expected {expected_bytes} bytes, \"\n"
        "                        f\"received {written}.\"\n"
        "                    )\n",
        "streaming loop",
    )


def patch_file(path: Path) -> bool:
    """Patch ``path`` and return whether it changed."""
    original = path.read_text(encoding="utf-8")
    patched = patch_source(original)
    if patched == original:
        return False
    path.write_text(patched, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    changed = patch_file(args.path)
    state = "patched" if changed else "already patched"
    print(f"ComfyUI API download helper {state}: {args.path}")


if __name__ == "__main__":
    main()
