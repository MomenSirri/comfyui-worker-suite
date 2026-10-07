"""The job contract between the AZ-AI backend and the suite's ComfyUI handlers.

`backend` holds what the backend sends and accepts, `fake_comfy` stands in for
ComfyUI and the result bucket, and `cases` are the tests each handler runs.
"""

import os
import sys
import types


def load_handler(service_dir):
    """Import a service's `handler` module the way its own unit tests do.

    The RunPod SDK is replaced by a stand-in unless something imported it
    first, so the cases run where the SDK is not installed.
    """
    for path in (service_dir, os.path.join(service_dir, "src")):
        if path not in sys.path:
            sys.path.append(path)

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

    return handler
