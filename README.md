# ComfyUI Worker Suite

Independent ComfyUI workers for image generation, editing, restoration, and video
generation/enhancement on RunPod. Choose one target, build one image, and deploy
that worker independently.

The suite contains **25 build configurations**: the existing 18 image-workflow
configurations, one model-free generic ComfyUI worker and its CPU build for
provider API graphs, four ComfyUI-based LTX variants, and one prompt worker that
serves a language model with vLLM instead of ComfyUI. Build configurations
include runtime foundations, hardware variants, and intermediate images; they
are not 25 different services.

## Repository layout

```text
comfyui-worker-suite/
├── docker-bake.hcl              # Central build catalog; default: generic only
├── build-catalog.json           # Purpose and source folder for every target
├── scripts/
│   ├── build.ps1                # List, preview, build, and push one target
│   └── validate_catalog.py      # Verify catalog and repository-local build plans
├── services/
│   ├── image-workflows/         # Existing 18 Momen configurations
│   ├── generic-comfyui/         # General model-free ComfyUI worker
│   ├── ltx25/                   # INT8, BF16, CQ V2, and ComfyUI 4K
│   └── qwen3-vl/                # Prompt worker: Qwen3-VL-32B on vLLM
├── tests/
│   └── handler_contract/        # The job contract every ComfyUI handler is tested against
├── docs/
│   ├── builds.md                # Build and model-input instructions
│   ├── handler-contract.md      # What a ComfyUI handler takes and answers
│   ├── local-model-inputs.json  # Exact filenames for locally supplied weights
│   └── source-provenance.json   # Source repositories and snapshot commits
└── .github/workflows/check.yml  # Configuration and lightweight source checks
```

Each family keeps its Dockerfiles, handler, workflow graphs, tests, and dependency
configuration together. Service-local READMEs retain source documentation; use
this root README and catalog for suite-wide build commands. This first import
preserves the original image-workflow Dockerfile; further separation of those
18 recipes can be done gradually while preserving their API behavior.

## Build options

| Family | Targets |
| --- | --- |
| Runtime foundations | `base`, `base-cuda12-8-1` |
| Standard image models | `sdxl`, `sd3`, `flux1-schnell`, `flux1-dev`, `flux1-dev-fp8`, `z-image-turbo` |
| FLUX.2 Klein | `flux2-klein`, `flux2-klein-cuda12-8-1` |
| Reference generation | `refrence_gen_sdxl_flux2_klein`, `refrence_gen_sdxl_flux2_klein-cuda12-8-1` |
| SeedVR | `seedvr`, `seedvr-cuda12-8-1`, `seedvr-cuda13-0-2`, `seedvr-runpod-cuda12-8-1` |
| General enhancement | `enhance`, `enhance-core` |
| Generic ComfyUI | `generic-comfyui`, `comfy-api-cpu` |
| ComfyUI LTX | `ltx25-int8`, `ltx25-bf16`, `ltx25-cq-v2`, `ltx25-4k` |
| Prompt worker (vLLM) | `qwen3-vl` |

The existing spelling `refrence_gen...` is retained for compatibility. Every listed
build uses ComfyUI except `qwen3-vl`. Standalone DFR build recipes are excluded
from this suite.

## List and preview

From the repository root in PowerShell:

```powershell
.\scripts\build.ps1 -List
.\scripts\build.ps1 -Target generic-comfyui -Print
.\scripts\build.ps1 -Target ltx25-int8 -Print
```

Previewing does not build images or download weights. It does require Docker's
Buildx CLI. To build and load a generic worker locally:

```powershell
.\scripts\build.ps1 -Target generic-comfyui -Tag momensirribrick/comfyui-generic:v01-cu128
```

For a complete LTX worker, supply a private Hugging Face token file:

```powershell
.\scripts\build.ps1 -Target ltx25-int8 -HFTokenFile C:\secure\hf-token.txt -Tag momensirribrick/worker-comfyui-ltx25:v01-int8
```

Add `-Push` when publishing an image to its registry. Each worker can have its
own release tag and RunPod endpoint. Record the deployed image digest and GPU
validation result for each release.

Linux/macOS can use the same root catalog directly:

```sh
docker buildx bake --list=targets
docker buildx bake generic-comfyui --print
docker buildx bake generic-comfyui --load
```

## Models and validation

Model weights, `.env` files, private tokens, caches, and original Git histories
are excluded from this repository. LTX model manifests retain exact model
revisions and hashes; its full builds download their selected weights. Certain
image-workflow stages require locally supplied LoRAs or model files at the paths
recorded in [local-model-inputs.json](docs/local-model-inputs.json).

Read [builds.md](docs/builds.md) before selecting a model-bundled build. The
generic worker includes ComfyUI but expects models to be supplied separately.

The import is validated through Bake plans and lightweight source tests. These
checks do not establish that every image has been built or every workflow has
passed GPU inference. Historical LTX verification reports remain under its
service documentation and refer to the source project's earlier tests.

## License and origins

The imported source projects use AGPL-3.0. Original license files and attribution
are preserved in each service folder. The root [LICENSE](LICENSE) applies to the
suite. [Source provenance](docs/source-provenance.json) records the snapshot
origins and locally modified source files included in this import.
