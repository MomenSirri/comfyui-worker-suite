"""Runpod entrypoint; retain the customized handler's result-delivery contract."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# The RunPod SDK reads its log level once, while it is imported, and at its
# default, DEBUG, it logs each job's whole output with the presigned result
# links. See the same lines in handler.py.
if not os.environ.get("RUNPOD_LOG_LEVEL"):
    os.environ["RUNPOD_LOG_LEVEL"] = "INFO"

import runpod
from handler import handler as comfy_handler
from ltx_worker.adapter import execute


def handler(job):
    return execute(job, comfy_handler)


if __name__ == "__main__":
    # A ComfyUI process/interrupt endpoint and credit tracker are shared. One
    # active Runpod job per container; scale out with additional workers.
    runpod.serverless.start({"handler": handler, "concurrency_modifier": lambda _: 1})
