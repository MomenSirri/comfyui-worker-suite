"""Verify required nodes on a temporary CPU-only ComfyUI server."""

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


HOST = "127.0.0.1"
PORT = 8199
BASE_URL = f"http://{HOST}:{PORT}"
REQUIRED_NODES = {
    "ByteDance2DraftToFinalVideoNode": "draft_task_id",
    "SaveStringKJ": "string",
}


def get_json(opener, path, timeout=5):
    """Read local metadata without submitting or executing a workflow."""
    with opener.open(f"{BASE_URL}{path}", timeout=timeout) as response:
        return json.load(response)


def check_running(process):
    exit_code = process.poll()
    if exit_code is not None:
        raise RuntimeError(
            f"ComfyUI exited before verification finished (code {exit_code})."
        )


def wait_for_server(process, opener, timeout):
    deadline = time.monotonic() + timeout
    last_error = "No response"
    while time.monotonic() < deadline:
        check_running(process)
        remaining = deadline - time.monotonic()
        try:
            stats = get_json(opener, "/system_stats", min(5, remaining))
        except (URLError, OSError, ValueError) as error:
            last_error = str(error)
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
            continue
        check_running(process)
        return stats
    raise RuntimeError(
        f"ComfyUI did not become ready within {timeout:g}s: {last_error}"
    )


def verify_schema(node_id, input_name, response):
    info = response.get(node_id)
    if not isinstance(info, dict):
        raise RuntimeError(f"Required node is not registered: {node_id}")

    inputs = {}
    for group in info.get("input", {}).values():
        inputs.update(group)
    input_spec = inputs.get(input_name)
    if not isinstance(input_spec, list) or input_spec[0:1] != ["STRING"]:
        raise RuntimeError(f"{node_id} lacks its STRING input {input_name!r}.")

    outputs = info.get("output", [])
    if node_id == "ByteDance2DraftToFinalVideoNode" and "VIDEO" not in outputs:
        raise RuntimeError(f"{node_id} lacks its VIDEO output.")
    if node_id == "SaveStringKJ" and info.get("output_node") is not True:
        raise RuntimeError(f"{node_id} is not an output node.")

    return {
        "node_id": node_id,
        "display_name": info.get("display_name", node_id),
        "inputs": {input_name: input_spec[0]},
        "outputs": outputs,
        "output_node": info.get("output_node", False),
    }


def verify_nodes(comfy_dir, timeout):
    comfy_dir = Path(comfy_dir).resolve()
    main_path = comfy_dir / "main.py"
    if not main_path.is_file():
        raise RuntimeError(f"ComfyUI entry point does not exist: {main_path}")

    # Refuse to inspect another server already listening on the test port.
    with socket.socket() as probe:
        probe.bind((HOST, PORT))

    command = [
        sys.executable,
        str(main_path),
        "--cpu",
        "--listen", HOST,
        "--port", str(PORT),
        "--disable-auto-launch",
    ]
    opener = build_opener(ProxyHandler({}))
    process = subprocess.Popen(command, cwd=comfy_dir)
    try:
        stats = wait_for_server(process, opener, timeout)
        version = stats.get("system", {}).get("comfyui_version")
        if not version:
            raise RuntimeError("ComfyUI did not report its version.")
        verified = []
        for node_id, input_name in REQUIRED_NODES.items():
            check_running(process)
            response = get_json(opener, f"/object_info/{node_id}")
            verified.append(verify_schema(node_id, input_name, response))
        check_running(process)
        print(json.dumps({
            "comfyui_version": version,
            "verified_nodes": verified,
        }), flush=True)
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-dir", default="/comfyui")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be a positive, finite number")
    try:
        verify_nodes(args.comfy_dir, args.timeout)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Required-node verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
