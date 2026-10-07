"""Container entrypoint: the base image's worker, with three additions.

Links are kept out of the log. When vLLM cannot fetch an image it reports the whole
link, and the worker logs that report. A link to private storage carries its signature
in the query string, so the query string of every link is removed from a log record
before it is written.

A Hugging Face token stays off the command line. The base image turns every variable
named after a vLLM flag into that flag, so HF_TOKEN becomes `--hf-token <token>`, and
it logs the command it starts. The variable alone is enough: the download library
reads it itself.

The compile caches are built in. At every start vLLM compiles the model, and on some
GPUs the DeepGEMM kernels as well, unless it finds what an earlier start left in its
cache folders, and a serverless worker starts from the image each time. The image
holds those folders once per GPU generation, and the ones made on this worker's
generation are put where vLLM looks. vLLM does not tell generations apart itself, and
a cache from another one does not fit. With COMPILE_CACHE_UPLOAD_URL set, the worker
sends its folders to that address once vLLM is ready, so that they can be built into
the next image: see compile-cache/README.md.

Two kinds of kernels are kept with those caches, in folders the worker names before
anything is built:

- The compiled model's Triton kernels. PyTorch decides where they go when the first
  one is built: inside vLLM's cache folder if vLLM has named it by then, and under
  /tmp otherwise, which is what happens on the Blackwell cards. The compiled model
  looks for them in that same folder at the next start.
- The kernels the NVIDIA driver compiles. A kernel that was not built for the GPU's
  architecture is compiled by the driver when it is first used, and kept in the
  driver's own cache, by default ~/.nv/ComputeCache. On the Blackwell cards that is
  vLLM's attention kernel, and it takes most of two minutes at every start.
"""

import logging
import os
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from typing import IO, Any

WORKER_SOURCE = "/src"

# One folder per GPU compute capability, such as "8.9", each with the cache folders below.
BUILT_IN_COMPILE_CACHES = "/opt/azai/compile-cache"

# The variables that say where kernels are kept, and the folder in vLLM's cache root
# the worker gives each: Triton's kernels, and the ones the NVIDIA driver compiles.
KERNEL_CACHE_FOLDERS = {"TRITON_CACHE_DIR": "triton", "CUDA_CACHE_PATH": "nv_compute_cache"}

# The folders in vLLM's cache root that are kept: its compiled model, the kernels
# DeepGEMM compiles at run time, and the two above.
CACHE_FOLDERS = ("torch_compile_cache", "deep_gemm", *KERNEL_CACHE_FOLDERS.values())

# A link up to its query string, then the query string.
LINK_QUERY = re.compile(r"(https?://[^\s\"'<>?#]+)\?[^\s\"'<>)]*")

# A Hugging Face access token.
HF_TOKEN = re.compile(r"\bhf_[A-Za-z0-9]{8,}")

# The longest the base image waits for vLLM, in seconds.
STARTUP_TIMEOUT = int(os.getenv("VLLM_STARTUP_TIMEOUT", "1200"))


def redact_links(text: str) -> str:
    return LINK_QUERY.sub(r"\1?[removed]", text)


def redact(text: str) -> str:
    return HF_TOKEN.sub("hf_[removed]", redact_links(text))


def keep_secrets_out_of_logs() -> None:
    create_record = logging.getLogRecordFactory()

    def create_redacted_record(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = create_record(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:
            # Arguments that do not fit the message: logging reports that itself.
            return record
        record.msg = redact(message)
        record.args = None
        return record

    logging.setLogRecordFactory(create_redacted_record)


def keep_token_off_the_command_line() -> None:
    """Stop the base image from passing HF_TOKEN to vLLM as `--hf-token`."""
    import args_builder  # the base image's module, in WORKER_SOURCE

    args_builder.RESERVED_ENV_VARS = frozenset(args_builder.RESERVED_ENV_VARS | {"HF_TOKEN"})


def vllm_cache_root() -> str:
    """vLLM's cache root, worked out the way vLLM does it (vllm/envs.py)."""
    cache_home = os.getenv("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.expanduser(os.getenv("VLLM_CACHE_ROOT") or os.path.join(cache_home, "vllm"))


def keep_kernels_with_the_caches() -> None:
    """Make Triton and the NVIDIA driver use kept folders, whatever the environment named.

    A folder named elsewhere would not be sent or built in. VLLM_CACHE_ROOT moves
    them, together with vLLM's own folders.
    """
    for variable, name in KERNEL_CACHE_FOLDERS.items():
        folder = os.path.join(vllm_cache_root(), name)
        named = os.getenv(variable)
        if named and named != folder:
            logging.info("%s was %s; the worker uses %s, which it keeps", variable, named, folder)
        os.environ[variable] = folder


def query_gpu(field: str) -> str | None:
    """One property of the first GPU, as nvidia-smi prints it, or None if it cannot be read."""
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={field}", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = result.stdout.splitlines()
    return lines[0].strip() if lines else None


def gpu_compute_capability() -> str | None:
    """The first GPU's CUDA compute capability, such as "8.9", or None if it cannot be read."""
    value = query_gpu("compute_cap")
    return value if value and re.fullmatch(r"\d+\.\d+", value) else None


def gpu_driver_version() -> str | None:
    """The NVIDIA driver's version, such as "580.65.06", or None if it cannot be read."""
    value = query_gpu("driver_version")
    return value if value and re.fullmatch(r"\d+(\.\d+)+", value) else None


def use_built_in_compile_caches() -> None:
    """Put the caches made on this GPU generation where vLLM looks, if the image has them."""
    capability = gpu_compute_capability()
    source = os.path.join(BUILT_IN_COMPILE_CACHES, capability or "")
    # The driver's own cache only fits the driver version it was made with.
    driver = gpu_driver_version()
    if capability is None or not os.path.isdir(source):
        logging.info("Compile cache: none built in for GPU compute capability %s, driver %s", capability, driver)
        return
    used = []
    for folder in CACHE_FOLDERS:
        built_in = os.path.join(source, folder)
        target = os.path.join(vllm_cache_root(), folder)
        if os.path.isdir(built_in) and not os.path.exists(target):
            shutil.copytree(built_in, target)
            used.append(folder)
    logging.info(
        "Compile cache: using %s built in for GPU compute capability %s, driver %s",
        ", ".join(used) or "nothing",
        capability,
        driver,
    )


def wait_for_vllm(timeout: float = STARTUP_TIMEOUT, interval: float = 5.0) -> bool:
    health = f"http://127.0.0.1:{os.getenv('VLLM_PORT', '8000')}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=10) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(interval)
    return False


def pack_cache_folders(archive: IO[bytes], capability: str) -> list[str]:
    """Write the cache folders that exist to archive, as a gzipped tar file.

    The file holds one folder, named after the GPU's compute capability, with the
    cache folders inside it. Returns the names of the folders it wrote.
    """
    packed = []
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for folder in CACHE_FOLDERS:
            path = os.path.join(vllm_cache_root(), folder)
            if os.path.isdir(path):
                tar.add(path, arcname=f"{capability}/{folder}")
                packed.append(folder)
    return packed


def send_compile_cache(upload_url: str) -> None:
    """Send the cache folders to upload_url with one PUT, once vLLM is ready."""
    try:
        capability = gpu_compute_capability()
        if capability is None:
            logging.error("Compile cache not sent: the GPU's compute capability could not be read")
            return
        if not wait_for_vllm():
            logging.error("Compile cache not sent: vLLM did not become ready")
            return
        with tempfile.TemporaryFile() as archive:
            try:
                sent = pack_cache_folders(archive, capability)
            except OSError:
                # Triton writes a kernel under a temporary name and renames it. A job that is
                # running can have one built while the folder is read; a second pass finds it.
                logging.warning("Compile cache: a file changed while it was packed; packing again")
                archive.seek(0)
                archive.truncate()
                sent = pack_cache_folders(archive, capability)
            size = archive.tell()
            archive.seek(0)
            request = urllib.request.Request(
                upload_url,
                data=archive,
                method="PUT",
                headers={"Content-Length": str(size), "Content-Type": "application/gzip"},
            )
            with urllib.request.urlopen(request, timeout=600) as response:
                logging.info(
                    "Compile cache sent: %s (%s bytes) for GPU compute capability %s, driver %s, HTTP %s",
                    ", ".join(sent) or "nothing",
                    size,
                    capability,
                    gpu_driver_version(),
                    response.status,
                )
    except Exception:
        # The worker serves jobs whether or not the cache could be sent.
        logging.exception("Compile cache not sent")


if __name__ == "__main__":
    # The base image's own format; its later call to basicConfig then changes nothing.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    keep_secrets_out_of_logs()
    sys.path.insert(0, WORKER_SOURCE)
    keep_token_off_the_command_line()
    keep_kernels_with_the_caches()
    use_built_in_compile_caches()
    upload_url = os.getenv("COMPILE_CACHE_UPLOAD_URL", "").strip()
    if upload_url:
        threading.Thread(target=send_compile_cache, args=(upload_url,), daemon=True).start()
    runpy.run_path(f"{WORKER_SOURCE}/main.py", run_name="__main__")
