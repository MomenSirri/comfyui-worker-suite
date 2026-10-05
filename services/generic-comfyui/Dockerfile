# Build argument for base image selection
ARG BASE_IMAGE=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

# Stage 1: Base image with common dependencies
FROM ${BASE_IMAGE} AS base

# Build arguments for this stage with sensible defaults for standalone builds
ARG COMFYUI_VERSION=latest
ARG CUDA_VERSION_FOR_COMFY=12.8
ARG ENABLE_PYTORCH_UPGRADE=true
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
ARG CREDIT_TRACKER_REPO=https://github.com/brick-momen-sirri/comfyui_credit_tracker.git
ARG CREDIT_TRACKER_VERSION=8fbe08eb13d3875a5ca608e09131c29c4fd84386
ARG KJNODES_VERSION=d3cfe21625e5170126ce06fbfcfe1d88108688c3

# Prevents prompts from packages asking for user input during installation
ENV DEBIAN_FRONTEND=noninteractive
# Prefer binary wheels over source distributions for faster pip installations
ENV PIP_PREFER_BINARY=1
# Ensures output from python is printed immediately to the terminal without buffering
ENV PYTHONUNBUFFERED=1
# Speed up some cmake builds
ENV CMAKE_BUILD_PARALLEL_LEVEL=8

# Install Python, git and other necessary tools
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-venv \
    git \
    wget \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    ffmpeg \
    openssh-server \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip \
    && apt-get autoremove -y \
    && apt-get clean -y \
    && rm -rf /var/lib/apt/lists/*

# Install uv (latest) using official installer and create isolated venv
RUN wget -qO- https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv \
    && ln -s /root/.local/bin/uvx /usr/local/bin/uvx \
    && uv venv /opt/venv

# Use the virtual environment for all subsequent commands
ENV PATH="/opt/venv/bin:${PATH}"

# Install comfy-cli + dependencies needed by it to install ComfyUI. Clean the
# installer cache in the same layer so it is not shipped in the final image.
RUN uv pip install comfy-cli pip setuptools wheel \
    && uv cache clean

# Fetch the latest release metadata as part of the build graph. Its content
# changes whenever ComfyUI publishes a stable release, invalidating Docker's
# cache for the install step below instead of silently reusing an older release.
ADD https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest /tmp/comfyui-latest-release.json

# Install ComfyUI. COMFYUI_VERSION defaults to the newest stable release, while
# still allowing an explicit version to be supplied for reproducible rollbacks.
RUN if [ -n "${CUDA_VERSION_FOR_COMFY}" ]; then \
      /usr/bin/yes | comfy --workspace /comfyui install --version "${COMFYUI_VERSION}" --cuda-version "${CUDA_VERSION_FOR_COMFY}" --nvidia; \
    else \
      /usr/bin/yes | comfy --workspace /comfyui install --version "${COMFYUI_VERSION}" --nvidia; \
    fi \
    && rm -rf /root/.cache /tmp/comfyui-latest-release.json

# comfy-cli creates a complete, CUDA-aware environment at /comfyui/.venv.
# Use that environment for ComfyUI and the Runpod handler instead of installing
# a second copy of PyTorch and ComfyUI's dependencies into /opt/venv. Keep
# /opt/venv later in PATH because it contains the comfy CLI itself.
ENV VIRTUAL_ENV="/comfyui/.venv"
ENV PATH="${VIRTUAL_ENV}/bin:/opt/venv/bin:${PATH}"

# ComfyUI's API media helper otherwise has no bounded video-download timeout
# and can wait forever on a response that stops making progress. Apply a
# compatibility-checked patch during the build so the same result URL is
# retried without rerunning or rebilling the generation task.
COPY src/patch_comfy_api_download.py /tmp/patch_comfy_api_download.py
RUN python /tmp/patch_comfy_api_download.py \
      /comfyui/comfy_api_nodes/util/download_helpers.py \
    && rm /tmp/patch_comfy_api_download.py

# Bake the credit tracker custom node into ComfyUI so serverless runs log the
# same usage rows as the local ComfyUI installation.
RUN git clone "${CREDIT_TRACKER_REPO}" /comfyui/custom_nodes/comfyui_credit_tracker \
    && cd /comfyui/custom_nodes/comfyui_credit_tracker \
    && git checkout "${CREDIT_TRACKER_VERSION}" \
    && rm -rf .git

# Install only custom-node and handler extras into ComfyUI's existing runtime
# environment. ComfyUI core requirements and CUDA PyTorch are already present.
# Avoid --force-reinstall so an already compatible PyTorch build is not copied
# into another multi-gigabyte layer.
RUN for r in /comfyui/custom_nodes/*/requirements.txt; do \
         [ -f "$r" ] && uv pip install --python /comfyui/.venv/bin/python -r "$r" || true; \
       done \
    && uv pip install --python /comfyui/.venv/bin/python \
         runpod requests websocket-client \
         "transformers>=4.50.3,<5" "huggingface-hub<1.0" \
    && if [ "$ENABLE_PYTORCH_UPGRADE" = "true" ]; then \
         uv pip install --python /comfyui/.venv/bin/python --upgrade \
           torch torchvision torchaudio --index-url ${PYTORCH_INDEX_URL}; \
       fi \
    && rm -rf /root/.cache

# Save String KJ writes text artifacts that the handler returns in output.texts.
# Pin the node pack so rebuilding the same ComfyUI release is reproducible.
RUN git clone https://github.com/kijai/ComfyUI-KJNodes.git /comfyui/custom_nodes/ComfyUI-KJNodes \
    && cd /comfyui/custom_nodes/ComfyUI-KJNodes \
    && git checkout "${KJNODES_VERSION}" \
    && uv pip install --python /comfyui/.venv/bin/python -r requirements.txt \
    && rm -rf .git /root/.cache

# Start ComfyUI on CPU and verify the actual registered node schemas. ComfyUI
# can otherwise start successfully while individual node imports have failed.
COPY src/verify_required_nodes.py /verify_required_nodes.py
RUN python /verify_required_nodes.py

# Change working directory to ComfyUI
WORKDIR /comfyui

# Support for the network volume
ADD src/extra_model_paths.yaml ./

# Go back to the root
WORKDIR /

# Add application code and scripts
ADD src/start.sh src/network_volume.py handler.py credit_estimator.py test_input.json ./
RUN sed -i 's/\r$//' /start.sh && chmod +x /start.sh

# Add script to install custom nodes
COPY scripts/comfy-node-install.sh /usr/local/bin/comfy-node-install
RUN sed -i 's/\r$//' /usr/local/bin/comfy-node-install \
    && chmod +x /usr/local/bin/comfy-node-install

# Prevent pip from asking for confirmation during uninstall steps in custom nodes
ENV PIP_NO_INPUT=1

# Copy helper script to switch Manager network mode at container start
COPY scripts/comfy-manager-set-mode.sh /usr/local/bin/comfy-manager-set-mode
RUN sed -i 's/\r$//' /usr/local/bin/comfy-manager-set-mode \
    && chmod +x /usr/local/bin/comfy-manager-set-mode

# Set the default command to run when starting the container
CMD ["/start.sh"]

# Stage 2: Download models
FROM base AS downloader

ARG HUGGINGFACE_ACCESS_TOKEN
# Set default model type if none is provided
ARG MODEL_TYPE=flux1-dev-fp8

# Change working directory to ComfyUI
WORKDIR /comfyui

# Create necessary directories upfront
RUN mkdir -p models/checkpoints models/vae models/unet models/clip models/text_encoders models/diffusion_models models/model_patches

# Download checkpoints/vae/unet/clip models to include in image based on model type
RUN if [ "$MODEL_TYPE" = "sdxl" ]; then \
      wget -q -O models/checkpoints/sd_xl_base_1.0.safetensors https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors && \
      wget -q -O models/vae/sdxl_vae.safetensors https://huggingface.co/stabilityai/sdxl-vae/resolve/main/sdxl_vae.safetensors && \
      wget -q -O models/vae/sdxl-vae-fp16-fix.safetensors https://huggingface.co/madebyollin/sdxl-vae-fp16-fix/resolve/main/sdxl_vae.safetensors; \
    fi

RUN if [ "$MODEL_TYPE" = "sd3" ]; then \
      wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/checkpoints/sd3_medium_incl_clips_t5xxlfp8.safetensors https://huggingface.co/stabilityai/stable-diffusion-3-medium/resolve/main/sd3_medium_incl_clips_t5xxlfp8.safetensors; \
    fi

RUN if [ "$MODEL_TYPE" = "flux1-schnell" ]; then \
      wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/unet/flux1-schnell.safetensors https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors && \
      wget -q -O models/clip/clip_l.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors && \
      wget -q -O models/clip/t5xxl_fp8_e4m3fn.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors && \
      wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/vae/ae.safetensors https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors; \
    fi

RUN if [ "$MODEL_TYPE" = "flux1-dev" ]; then \
      wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/unet/flux1-dev.safetensors https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors && \
      wget -q -O models/clip/clip_l.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors && \
      wget -q -O models/clip/t5xxl_fp8_e4m3fn.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors && \
      wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/vae/ae.safetensors https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/ae.safetensors; \
    fi

RUN if [ "$MODEL_TYPE" = "flux1-dev-fp8" ]; then \
      wget -q -O models/checkpoints/flux1-dev-fp8.safetensors https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors; \
    fi

RUN if [ "$MODEL_TYPE" = "z-image-turbo" ]; then \
      wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/qwen_3_4b.safetensors https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors && \
      wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/diffusion_models/z_image_turbo_bf16.safetensors https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors && \
      wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/vae/ae.safetensors https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors && \
      wget -q --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/model_patches/Z-Image-Turbo-Fun-Controlnet-Union.safetensors https://huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union/resolve/main/Z-Image-Turbo-Fun-Controlnet-Union.safetensors; \
    fi

# Optional stage for users who explicitly want a model-bundled image.
FROM base AS final-with-models

COPY --from=downloader /comfyui/models /comfyui/models

# Stage 3: Final image (model-free)
FROM base AS final

# Intentionally do not copy models into the default image. This keeps a normal
# `docker build .` as a simple ComfyUI setup with no model downloads.
