variable "DOCKERHUB_REPO" {
  default = "momensirribrick"
}

variable "DOCKERHUB_IMG" {
  default = "worker-comfyui"
}

variable "FLUX2_KLEIN_IMG" {
  default = "flux2-klein9b"
}

variable "RELEASE_VERSION" {
  default = "latest"
}

variable "FLUX2_KLEIN_TAG" {
  default = "v04"
}

variable "COMFYUI_VERSION" {
  default = "latest"
}

# Global defaults for standard CUDA 12.6.3 images
variable "BASE_IMAGE" {
  default = "nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04"
}

variable "CUDA_VERSION_FOR_COMFY" {
  default = "12.6"
}

variable "ENABLE_PYTORCH_UPGRADE" {
  default = "false"
}

variable "PYTORCH_INDEX_URL" {
  default = ""
}

variable "HUGGINGFACE_ACCESS_TOKEN" {
  default = ""
}

variable "CIVITAI_API_TOKEN" {
  default = ""
}

variable "KREAMANIA_FP8_SHA256" {
  default = ""
}

variable "ENHANCE_CORE_IMAGE" {
  # Default to local build stage alias; override with a pushed core image to skip heavy rebuilds.
  # Example override:
  # --set enhance.args.ENHANCE_CORE_IMAGE=momensirribrick/general-enhancement:core-v01
  default = "final-enhance-core"
}

group "image-workflows" {
  targets = ["base", "sdxl", "sd3", "flux1-schnell", "flux1-dev", "flux1-dev-fp8", "z-image-turbo", "flux2-klein", "refrence_gen_sdxl_flux2_klein", "base-cuda12-8-1", "flux2-klein-cuda12-8-1", "refrence_gen_sdxl_flux2_klein-cuda12-8-1", "seedvr", "seedvr-cuda12-8-1", "seedvr-runpod-cuda12-8-1", "seedvr-cuda13-0-2", "enhance-core", "enhance"]
}

target "base" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "base"
  platforms = ["linux/amd64"]
  args = {
    BASE_IMAGE = "${BASE_IMAGE}"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = "${CUDA_VERSION_FOR_COMFY}"
    ENABLE_PYTORCH_UPGRADE = "${ENABLE_PYTORCH_UPGRADE}"
    PYTORCH_INDEX_URL = "${PYTORCH_INDEX_URL}"
    MODEL_TYPE = "base"
  }
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-base"]
}

target "sdxl" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final"
  args = {
    BASE_IMAGE = "${BASE_IMAGE}"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = "${CUDA_VERSION_FOR_COMFY}"
    ENABLE_PYTORCH_UPGRADE = "${ENABLE_PYTORCH_UPGRADE}"
    PYTORCH_INDEX_URL = "${PYTORCH_INDEX_URL}"
    MODEL_TYPE = "sdxl"
  }
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-sdxl"]
  inherits = ["base"]
}

target "sd3" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final"
  args = {
    BASE_IMAGE = "${BASE_IMAGE}"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = "${CUDA_VERSION_FOR_COMFY}"
    ENABLE_PYTORCH_UPGRADE = "${ENABLE_PYTORCH_UPGRADE}"
    PYTORCH_INDEX_URL = "${PYTORCH_INDEX_URL}"
    MODEL_TYPE = "sd3"
    HUGGINGFACE_ACCESS_TOKEN = "${HUGGINGFACE_ACCESS_TOKEN}"
  }
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-sd3"]
  inherits = ["base"]
}

target "flux1-schnell" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final"
  args = {
    BASE_IMAGE = "${BASE_IMAGE}"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = "${CUDA_VERSION_FOR_COMFY}"
    ENABLE_PYTORCH_UPGRADE = "${ENABLE_PYTORCH_UPGRADE}"
    PYTORCH_INDEX_URL = "${PYTORCH_INDEX_URL}"
    MODEL_TYPE = "flux1-schnell"
    HUGGINGFACE_ACCESS_TOKEN = "${HUGGINGFACE_ACCESS_TOKEN}"
  }
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-flux1-schnell"]
  inherits = ["base"]
}

target "flux1-dev" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final"
  args = {
    BASE_IMAGE = "${BASE_IMAGE}"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = "${CUDA_VERSION_FOR_COMFY}"
    ENABLE_PYTORCH_UPGRADE = "${ENABLE_PYTORCH_UPGRADE}"
    PYTORCH_INDEX_URL = "${PYTORCH_INDEX_URL}"
    MODEL_TYPE = "flux1-dev"
    HUGGINGFACE_ACCESS_TOKEN = "${HUGGINGFACE_ACCESS_TOKEN}"
  }
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-flux1-dev"]
  inherits = ["base"]
}

target "flux1-dev-fp8" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final"
  args = {
    BASE_IMAGE = "${BASE_IMAGE}"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = "${CUDA_VERSION_FOR_COMFY}"
    ENABLE_PYTORCH_UPGRADE = "${ENABLE_PYTORCH_UPGRADE}"
    PYTORCH_INDEX_URL = "${PYTORCH_INDEX_URL}"
    MODEL_TYPE = "flux1-dev-fp8"
  }
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-flux1-dev-fp8"]
  inherits = ["base"]
}

target "z-image-turbo" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final"
  args = {
    BASE_IMAGE = "${BASE_IMAGE}"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = "${CUDA_VERSION_FOR_COMFY}"
    ENABLE_PYTORCH_UPGRADE = "${ENABLE_PYTORCH_UPGRADE}"
    PYTORCH_INDEX_URL = "${PYTORCH_INDEX_URL}"
    MODEL_TYPE = "z-image-turbo"
  }
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-z-image-turbo"]
  inherits = ["base"]
}

target "base-cuda12-8-1" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "base"
  platforms = ["linux/amd64"]
  args = {
    BASE_IMAGE = "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = ""
    ENABLE_PYTORCH_UPGRADE = "true"
    PYTORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
    MODEL_TYPE = "base"
  }
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-base-cuda12.8.1"]
}


target "flux2-klein" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final-flux2-klein"
  args = {
    BASE_IMAGE = "${BASE_IMAGE}"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = "${CUDA_VERSION_FOR_COMFY}"
    ENABLE_PYTORCH_UPGRADE = "${ENABLE_PYTORCH_UPGRADE}"
    PYTORCH_INDEX_URL = "${PYTORCH_INDEX_URL}"
    MODEL_TYPE = "flux2-klein"
    HUGGINGFACE_ACCESS_TOKEN = "${HUGGINGFACE_ACCESS_TOKEN}"
  }
  tags = ["${DOCKERHUB_REPO}/${FLUX2_KLEIN_IMG}:${FLUX2_KLEIN_TAG}-cuda12.6"]
  inherits = ["base"]
}

# Convenience target for RTX 5090 (CUDA 12.8.1 + cu128 torch wheels)
target "flux2-klein-cuda12-8-1" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final-flux2-klein"

  platforms = ["linux/amd64"]
  args = {
    BASE_IMAGE = "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = ""
    ENABLE_PYTORCH_UPGRADE = "true"
    PYTORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
    MODEL_TYPE = "flux2-klein"
    HUGGINGFACE_ACCESS_TOKEN = "${HUGGINGFACE_ACCESS_TOKEN}"

    LLAMA_CPP_PYTHON_REPO = "JamePeng/llama-cpp-python"
    LLAMA_CPP_PYTHON_TAG  = "v0.3.30-cu128-Basic-linux-20260302"
    LLAMA_CPP_PYTHON_PYTAG = "cp312"
  }

  tags = ["${DOCKERHUB_REPO}/${FLUX2_KLEIN_IMG}:${FLUX2_KLEIN_TAG}"]
  inherits = ["base"]
}

target "refrence_gen_sdxl_flux2_klein" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final-refrence_gen_sdxl_flux2_klein"
  args = {
    BASE_IMAGE = "${BASE_IMAGE}"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = "${CUDA_VERSION_FOR_COMFY}"
    ENABLE_PYTORCH_UPGRADE = "${ENABLE_PYTORCH_UPGRADE}"
    PYTORCH_INDEX_URL = "${PYTORCH_INDEX_URL}"
    MODEL_TYPE = "refrence_gen_sdxl_flux2_klein"
    HUGGINGFACE_ACCESS_TOKEN = "${HUGGINGFACE_ACCESS_TOKEN}"

    LLAMA_CPP_PYTHON_REPO = "JamePeng/llama-cpp-python"
    LLAMA_CPP_PYTHON_TAG  = "v0.3.30-cu128-Basic-linux-20260302"
    LLAMA_CPP_PYTHON_PYTAG = "cp312"
  }
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-refrence_gen_sdxl_flux2_klein"]
  inherits = ["base"]
}

target "refrence_gen_sdxl_flux2_klein-cuda12-8-1" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final-refrence_gen_sdxl_flux2_klein"
  platforms = ["linux/amd64"]
  args = {
    BASE_IMAGE = "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = ""
    ENABLE_PYTORCH_UPGRADE = "true"
    PYTORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
    MODEL_TYPE = "refrence_gen_sdxl_flux2_klein"
    HUGGINGFACE_ACCESS_TOKEN = "${HUGGINGFACE_ACCESS_TOKEN}"

    LLAMA_CPP_PYTHON_REPO = "JamePeng/llama-cpp-python"
    LLAMA_CPP_PYTHON_TAG  = "v0.3.30-cu128-Basic-linux-20260302"
    LLAMA_CPP_PYTHON_PYTAG = "cp312"
  }
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-refrence_gen_sdxl_flux2_klein-cuda12.8.1"]
  inherits = ["base"]
}

target "seedvr" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final-seedvr"
  args = {
    BASE_IMAGE = "${BASE_IMAGE}"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = "${CUDA_VERSION_FOR_COMFY}"
    ENABLE_PYTORCH_UPGRADE = "${ENABLE_PYTORCH_UPGRADE}"
    PYTORCH_INDEX_URL = "${PYTORCH_INDEX_URL}"
    MODEL_TYPE = "seedvr"

    NUNCHAKU_REPO = "nunchaku-ai/nunchaku"
    NUNCHAKU_TAG  = "v1.0.2"
  }
  secret = ["id=huggingface_access_token,env=HUGGINGFACE_ACCESS_TOKEN"]
  tags = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}-seedvr"]
  inherits = ["base"]
}


target "seedvr-cuda12-8-1" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final-seedvr"
  platforms = ["linux/amd64"]
  args = {
    BASE_IMAGE = "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = ""
    ENABLE_PYTORCH_UPGRADE = "true"
    PYTORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
    MODEL_TYPE = "seedvr"

  }
  secret = ["id=huggingface_access_token,env=HUGGINGFACE_ACCESS_TOKEN"]
  tags = ["${DOCKERHUB_REPO}/seedvr:v01"]
  inherits = ["base"]
}


target "enhance" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final-enhance"
  platforms = ["linux/amd64"]
  args = {
    BASE_IMAGE = "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = ""
    ENABLE_PYTORCH_UPGRADE = "true"
    PYTORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
    MODEL_TYPE = "enhance"
    HUGGINGFACE_ACCESS_TOKEN = "${HUGGINGFACE_ACCESS_TOKEN}"
    CIVITAI_API_TOKEN = "${CIVITAI_API_TOKEN}"
    KREAMANIA_FP8_SHA256 = "${KREAMANIA_FP8_SHA256}"
    ENHANCE_CORE_IMAGE = "${ENHANCE_CORE_IMAGE}"

  }
  tags = ["${DOCKERHUB_REPO}/general-enhancement:${RELEASE_VERSION}"]
  inherits = ["base"]
}

target "seedvr-cuda13-0-2" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final-seedvr-qwenvl"
  platforms = ["linux/amd64"]
  args = {
    BASE_IMAGE = "nvidia/cuda:13.0.2-cudnn-runtime-ubuntu24.04"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = ""
    ENABLE_PYTORCH_UPGRADE = "true"
    PYTORCH_INDEX_URL = "https://download.pytorch.org/whl/cu130"
    PYTORCH_VERSION = "2.10.0"
    TORCHVISION_VERSION = "0.25.0"
    TORCHAUDIO_VERSION = "2.10.0"
    MODEL_TYPE = "seedvr"
    NUNCHAKU_WHEEL_URL = "https://github.com/nunchaku-ai/nunchaku/releases/download/v1.2.1/nunchaku-1.2.1+cu13.0torch2.10-cp312-cp312-linux_x86_64.whl"
    QWENVL_COMMIT = "517aed64fae294ca1a906d38e7bcf43ee54afa21"
    QWENVL_LLAMA_CPP_VERSION = "0.3.38"
    QWENVL_LLAMA_CPP_WHEEL_URL = "https://github.com/JamePeng/llama-cpp-python/releases/download/v0.3.38-cu130-Basic-linux-20260504/llama_cpp_python-0.3.38%2Bcu130.basic-cp312-cp312-linux_x86_64.whl"
    QWENVL_LLAMA_CPP_WHEEL_SHA256 = "e838b783fc8b4b8090e7e030a42e5c53af1801ea538ec09bf3b7dc6ea2d0c784"
  }
  secret = ["id=huggingface_access_token,env=HUGGINGFACE_ACCESS_TOKEN"]
  tags = ["${DOCKERHUB_REPO}/seedvr:v05"]
  inherits = ["base"]
}

target "seedvr-runpod-cuda12-8-1" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final-seedvr-qwenvl"
  platforms = ["linux/amd64"]
  args = {
    BASE_IMAGE = "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = ""
    ENABLE_PYTORCH_UPGRADE = "true"
    PYTORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
    PYTORCH_VERSION = "2.10.0"
    TORCHVISION_VERSION = "0.25.0"
    TORCHAUDIO_VERSION = "2.10.0"
    MODEL_TYPE = "seedvr"
    NUNCHAKU_WHEEL_URL = "https://github.com/nunchaku-ai/nunchaku/releases/download/v1.2.1/nunchaku-1.2.1+cu12.8torch2.10-cp312-cp312-linux_x86_64.whl"
    QWENVL_COMMIT = "517aed64fae294ca1a906d38e7bcf43ee54afa21"
    QWENVL_LLAMA_CPP_VERSION = "0.3.49"
    QWENVL_LLAMA_CPP_WHEEL_URL = "https://github.com/JamePeng/llama-cpp-python/releases/download/v0.3.49-cu128-linux-20260831/llama_cpp_python-0.3.49%2Bcu128-cp312-cp312-linux_x86_64.whl"
    QWENVL_LLAMA_CPP_WHEEL_SHA256 = "17e8bfa1bf4f2f9988cdb68fc6d878719427365ff838f95f5c33c682c1b17fed"
  }
  secret = ["id=huggingface_access_token,env=HUGGINGFACE_ACCESS_TOKEN"]
  tags = ["${DOCKERHUB_REPO}/seedvr:v06"]
  inherits = ["base"]
}

target "enhance-core" {
  context = "./services/image-workflows"
  dockerfile = "Dockerfile"
  target = "final-enhance-core"
  platforms = ["linux/amd64"]
  args = {
    BASE_IMAGE = "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04"
    COMFYUI_VERSION = "${COMFYUI_VERSION}"
    CUDA_VERSION_FOR_COMFY = ""
    ENABLE_PYTORCH_UPGRADE = "true"
    PYTORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
    MODEL_TYPE = "enhance"
    HUGGINGFACE_ACCESS_TOKEN = "${HUGGINGFACE_ACCESS_TOKEN}"
    CIVITAI_API_TOKEN = "${CIVITAI_API_TOKEN}"
    KREAMANIA_FP8_SHA256 = "${KREAMANIA_FP8_SHA256}"
  }
  tags = ["${DOCKERHUB_REPO}/general-enhancement:core-${RELEASE_VERSION}"]
  inherits = ["base"]
}

# Independent ComfyUI services; all contexts are contained in this repository.
variable "EXTRA_GENERIC_CONTEXT" {
  default = "./services/generic-comfyui"
}

variable "EXTRA_LTX_CONTEXT" {
  default = "./services/ltx25"
}

variable "EXTRA_REGISTRY" {
  default = "momensirribrick"
}

variable "EXTRA_GENERIC_VERSION" {
  default = "dev"
}

variable "EXTRA_LTX_VERSION" {
  default = "dev"
}

variable "EXTRA_HF_TOKEN_FILE" {
  default = ""
}

# An unqualified invocation builds only the smaller model-free generic worker.
group "default" {
  targets = ["generic-comfyui"]
}

target "generic-comfyui" {
  description = "Model-free ComfyUI worker from the sibling worker-comfyui repository"
  context = "${EXTRA_GENERIC_CONTEXT}"
  dockerfile = "Dockerfile"
  target = "final"
  platforms = ["linux/amd64"]
  tags = ["${EXTRA_REGISTRY}/comfyui-generic:${EXTRA_GENERIC_VERSION}-cu128"]
}

# The Comfy API worker is published under its own account and versioned on its
# own. A push to an existing tag replaces it: set the next free tag per release.
variable "COMFY_API_REGISTRY" {
  default = "momensirri"
}

variable "COMFY_API_VERSION" {
  default = "v08"
}

# A release tag, never `latest`: the provider graphs are checked against it.
variable "COMFY_API_COMFYUI_VERSION" {
  default = "0.39.1"
}

target "comfy-api-cpu" {
  description = "Generic worker on CPU PyTorch for graphs that only call provider API nodes"
  context = "${EXTRA_GENERIC_CONTEXT}"
  dockerfile = "Dockerfile.cpu"
  target = "final"
  platforms = ["linux/amd64"]
  args = { COMFYUI_VERSION = "${COMFY_API_COMFYUI_VERSION}" }
  tags = ["${COMFY_API_REGISTRY}/comfy-api-worker:${COMFY_API_VERSION}"]
}

target "ltx25-int8" {
  description = "Complete LTX 2.5 INT8 generation and enhancement worker"
  context = "${EXTRA_LTX_CONTEXT}"
  dockerfile = "Dockerfile"
  target = "final"
  platforms = ["linux/amd64"]
  args = { MODEL_PROFILE = "int8" }
  secret = EXTRA_HF_TOKEN_FILE != "" ? ["id=hf_token,src=${EXTRA_HF_TOKEN_FILE}"] : ["id=hf_token,env=HF_TOKEN"]
  tags = ["${EXTRA_REGISTRY}/worker-comfyui-ltx25:${EXTRA_LTX_VERSION}-int8"]
}

target "ltx25-bf16" {
  description = "Complete LTX 2.5 BF16 worker with its own model profile"
  inherits = ["ltx25-int8"]
  args = { MODEL_PROFILE = "bf16" }
  tags = ["${EXTRA_REGISTRY}/worker-comfyui-ltx25:${EXTRA_LTX_VERSION}-bf16"]
}

target "ltx25-cq-v2" {
  description = "LTX CQ Enhancer V2 with its dedicated models and workflow catalog"
  inherits = ["ltx25-int8"]
  args = { MODEL_PROFILE = "cq-v2" }
  tags = ["${EXTRA_REGISTRY}/worker-comfyui-ltx25:${EXTRA_LTX_VERSION}-cq-v2"]
}

target "ltx25-4k" {
  description = "ComfyUI 4K qualification overlay on the complete INT8 bundle"
  context = "${EXTRA_LTX_CONTEXT}"
  dockerfile = "Dockerfile.4k"
  platforms = ["linux/amd64"]
  # BuildKit resolves this named context from the INT8 build, without requiring
  # a previously loaded local image. The source Dockerfile remains unchanged.
  contexts = { ltx25-model-base = "target:ltx25-int8" }
  args = { BASE_IMAGE = "ltx25-model-base" }
  tags = ["${EXTRA_REGISTRY}/worker-comfyui-ltx25:${EXTRA_LTX_VERSION}-4k"]
}

# The prompt worker serves a language model with vLLM; it is the one build here
# without ComfyUI. It is published under its own account and versioned on its own.
# The default is the next free tag: v01 to v11 are published, and a push to an
# existing tag replaces it. A build without the files of
# services/qwen3-vl/compile-cache/ (they are not in version control) makes an
# image whose workers compile the model at every fresh start.
variable "QWEN_VL_CONTEXT" {
  default = "./services/qwen3-vl"
}

variable "QWEN_VL_REGISTRY" {
  default = "momensirri"
}

variable "QWEN_VL_VERSION" {
  default = "v12"
}

target "qwen3-vl" {
  description = "Qwen3-VL-32B prompt worker on RunPod's vLLM worker"
  context = "${QWEN_VL_CONTEXT}"
  dockerfile = "Dockerfile"
  platforms = ["linux/amd64"]
  tags = ["${QWEN_VL_REGISTRY}/qwen3-vl-32b:${QWEN_VL_VERSION}"]
}
