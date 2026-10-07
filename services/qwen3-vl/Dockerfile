# AZ-AI prompt worker: Qwen3-VL-32B-Instruct (4-bit, NVFP4) behind RunPod's vLLM worker.
#
# This image adds settings and a small entrypoint. The job handler, the OpenAI routes
# and vLLM come from the base image, and the weights are not in the image: see README.md.
#
# The base is pinned by tag and digest, because a production image must not change when
# a tag is rebuilt upstream. v2.28.0 is vLLM 0.30.0 on CUDA 13.0.
ARG BASE_IMAGE=runpod/worker-v1-vllm:v2.28.0@sha256:6d8e4ff417e09aeffe6a55e547ed81203b02228533c5305e726caaa7dab34571
FROM ${BASE_IMAGE}

COPY config/qwen3-vl-32b.yaml /etc/azai/qwen3-vl-32b.yaml
COPY scripts/check-serve-config.py scripts/start.py /opt/azai/

# vLLM's own parser reads the settings, so a misspelled key fails the build.
RUN python3 /opt/azai/check-serve-config.py /etc/azai/qwen3-vl-32b.yaml

# vLLM's compile caches for this model and these settings, one file per GPU generation:
# compile-cache/README.md. Each file unpacks to a folder named after a GPU compute
# capability, and start.py gives vLLM the one that fits the worker's GPU. The image
# also builds without any file. A file made before v08 has its Triton kernels inside
# the compiled model's folder, where this entrypoint no longer lets vLLM look, so a
# cache without a triton folder fails the build.
RUN --mount=type=bind,source=compile-cache,target=/mnt/compile-cache \
    mkdir -p /opt/azai/compile-cache \
    && for archive in /mnt/compile-cache/*.tar.gz; do \
        if [ -f "$archive" ]; then tar -xzf "$archive" -C /opt/azai/compile-cache || exit 1; fi; \
    done \
    && for cache in /opt/azai/compile-cache/*/; do \
        if [ -d "$cache" ] && [ ! -d "${cache}triton" ]; then \
            echo "compile cache ${cache} has no triton folder: make it again with this image" >&2; exit 1; \
        fi; \
    done \
    && echo "compile caches for GPU compute capability: $(ls /opt/azai/compile-cache | tr '\n' ' ')"

# The weights are Red Hat AI's 4-bit build of the model, pinned to one commit of its
# repository. The worker asks Hugging Face for this revision before it starts vLLM and
# reports a wrong one at once. Qwen's own 8-bit weights (Qwen/Qwen3-VL-32B-Instruct-FP8)
# need a 48 GB GPU and are the default of the tags up to v07.
ENV MODEL_NAME=RedHatAI/Qwen3-VL-32B-Instruct-NVFP4 \
    MODEL_REVISION=409d48ed83fde917cab7410bf938d3e3d66f7669 \
    VLLM_CONFIG_FILE=/etc/azai/qwen3-vl-32b.yaml \
    # Jobs one worker takes at a time; the same number as max-num-seqs.
    MAX_CONCURRENCY=8 \
    # Image links: follow no redirect, wait 15 seconds, accept at most 32 MB.
    VLLM_MEDIA_URL_ALLOW_REDIRECTS=0 \
    VLLM_IMAGE_FETCH_TIMEOUT=15 \
    VLLM_MAX_MEDIA_DOWNLOAD_SIZE_MB=32 \
    # At its default level the RunPod SDK writes every answer to the worker log.
    RUNPOD_LOG_LEVEL=INFO

# The base image's worker, started through start.py so that links lose their query
# string before they reach the log.
ENTRYPOINT ["python3", "/opt/azai/start.py"]
