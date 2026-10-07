# Building and maintaining workers

Run builds from the suite root. Root Bake contexts point into this repository;
the checkout does not require the old sibling projects on the D: drive.

`scripts/build.ps1 -List` lists all targets from `build-catalog.json`. Select a
single target explicitly to preview, build, or publish it. Unqualified root Bake
builds select only `generic-comfyui`. The `image-workflows` group is an explicit
bulk-build option and can require substantial storage and model downloads.

## Image-workflow inputs

The 18 imported configurations retain their existing CUDA, model, dependency,
and tag settings. `-Tag` on the root helper overrides the chosen image's tag for
an independent release. Some original targets have fixed historical tags; use
an explicit tag rather than assuming a global version variable changes them.

Selected FLUX.2 Klein, reference-generation, SeedVR, and enhancement stages COPY
local weights. Supply only the model files required by your chosen target under
`services/image-workflows/models/`, preserving case and filenames. See
`local-model-inputs.json` and the selected Dockerfile stage. These files are
ignored by Git and must be provisioned separately on any CI build runner.

Existing local model folders can be used as the source for this provision step.
Weight distribution and model licenses follow their original providers. A
source-only clone is not a complete bundle of those private/local artifacts.

The imported image-workflow recipes preserve existing authentication behavior:
some downloads consume the `HUGGINGFACE_ACCESS_TOKEN` or `CIVITAI_API_TOKEN`
environment variables through build arguments; the SeedVR targets use a BuildKit
secret for Hugging Face access. Keep credentials out of source control and avoid
sharing plan output containing populated legacy build arguments. Migrating all
legacy download recipes to secret mounts is a separate maintenance improvement.

## Generic worker

`generic-comfyui` selects the source Dockerfile's `final` stage with its CUDA
12.8.1 defaults. It includes the ComfyUI runtime, handler, required startup node
verification, and credit tracker integration. Models are supplied via the
source project's configured volume paths. Its model-free image still runs
through ComfyUI.

## Comfy API worker (CPU)

`comfy-api-cpu` builds `services/generic-comfyui/Dockerfile.cpu`: the generic
worker's handler and job contract on CPU PyTorch, for graphs that only call
ComfyUI's provider API nodes and the core image and video nodes around them.
No such node uses a GPU, so the image runs on a CPU endpoint and on any GPU
endpoint.

| | `generic-comfyui` | `comfy-api-cpu` |
| --- | --- | --- |
| Base | CUDA 12.8.1 runtime | `python:3.12-slim-bookworm` |
| PyTorch | cu128 | 2.10.0 CPU |
| ComfyUI | `latest` unless set | 0.39.1, set by `COMFY_API_COMFYUI_VERSION` |
| Credit tracker node, KJNodes | included | not included |
| Sample workflows, embedded node docs | included | left out (about 575 MB) |
| Start | GPU pre-flight check | `COMFY_DEVICE=cpu`: no check, `--cpu` |
| RunPod SDK log level | `DEBUG` unless set | `RUNPOD_LOG_LEVEL=INFO` |

The build fails when a requirement replaces the CPU PyTorch, when the download
patch no longer fits the ComfyUI release, or when ComfyUI does not register a
node type listed in `services/generic-comfyui/node-lists/comfy-api.txt`. Keep
that list equal to the node types of the graphs the worker is sent.

Without the credit tracker node, `credit_usage` in a result is the handler's own
estimate instead of tracked rows, and without KJNodes a graph cannot use
`SaveStringKJ`.

Endpoint variables: `BUCKET_ENDPOINT_URL` (with the bucket as its path),
`BUCKET_ACCESS_KEY_ID` and `BUCKET_SECRET_ACCESS_KEY` for result links;
`COMFY_ORG_API_KEY` only when jobs do not carry `comfy_org_api_key`. A URL
input is held in memory and limited by `INPUT_DOWNLOAD_MAX_BYTES` (256 MiB).

### Published: `momensirri/comfy-api-worker:v01`

Pushed on 2026-10-07, digest
`sha256:fdc6e3f7773e28933d5c9332ef18626de8ccde93a02f182874123ba883bfdfe0`,
644 MB compressed. Its Python packages, as the build resolved them, are listed in
`services/generic-comfyui/docs/comfy-api-cpu-v01.pip-freeze.txt`; nothing pins
them yet, so a later build can resolve newer ones.

Checked on a workstation, in the container without a GPU:

- ComfyUI 0.39.1 starts on the CPU and serves the 28 listed node types.
- The 32 AZ-AI Studio provider and region models, 43 graph variants built by
  AZ-AI's own builders, pass ComfyUI's validation and stop at the provider node
  with `Unauthorized`, because no Comfy key was sent. No provider was called.
- Through the handler: an image and an MP4 given as `{ name, url }` in
  `input.images` come back as `output.images` and `output.videos`; a link that
  answers 404 fails the job without the link in the answer or the log.
- With the bucket variables pointing at an S3 stand-in, results are stored as
  `<bucket>/<MM-YY>/<job id>/<id>.<ext>` and returned as `s3_url`; the worker
  log holds no signed query string.

Not checked: a job with a Comfy key, a RunPod endpoint of either kind, storage
on R2, and how much memory a CPU worker needs.

## LTX workers

- `ltx25-int8` and `ltx25-bf16` share a workflow catalog; the primary transformer
  and text encoder use different precisions.
- `ltx25-cq-v2` selects a dedicated CQ enhancement bundle and catalog.
- `ltx25-4k` builds the ComfyUI qualification overlay, resolving its full INT8
  base through a named Bake context.

LTX full builds require access to their gated model repositories. Supply
`-HFTokenFile` on the helper or set `HF_TOKEN` privately. Tokens are passed as
BuildKit secrets. All builds target `linux/amd64`. Consult the LTX source docs
for driver, VRAM, host RAM, disk, and validation requirements.

## Prompt worker (Qwen3-VL)

`qwen3-vl` builds `services/qwen3-vl`: RunPod's vLLM worker
(`runpod/worker-v1-vllm`, pinned by tag and digest) with a settings file and a
small entrypoint. It serves Qwen3-VL-32B-Instruct for writing video prompts and
is the one build here without ComfyUI. Its own README has the settings, the
endpoint, the job contract and what was checked.

- The weights are not in the image. A worker downloads the model revision named
  in the Dockerfile from Hugging Face.
- `services/qwen3-vl/compile-cache/*.tar.gz` are vLLM's compile caches, one file
  per GPU generation. They are made on a GPU for one image and are not in version
  control. The image builds without them, and its workers then compile the model
  at every fresh start. Put the files that belong to a release into that folder
  before building it for an endpoint.
- `momensirri/qwen3-vl-32b` has the tags `v01` to `v11`. The root target defaults
  to the next free one, `v12`; the service's own `docker-bake.hcl` still names
  `v11`. Build releases from the suite root.
- The entrypoint's unit tests need only Python:
  `python -m unittest discover -s tests` in `services/qwen3-vl`.

The folder was imported on 2026-10-07 from a working folder that was not under
version control. `scripts/start.py`, `scripts/check-serve-config.py` and
`config/qwen3-vl-32b.yaml` are byte for byte the files inside the published
`v11` (compared through the registry). The service README was not yet brought
up to date for the tags after `v07`: it still names the 8-bit weights as the
default, while the Dockerfile builds the 4-bit ones.

## Updating and releasing

1. Change code, workflows, and dependency pins in the appropriate service folder.
2. Run its lightweight tests and the root catalog validator.
3. Build the selected image with a new release tag.
4. Run representative GPU jobs for its supported workflows.
5. Publish and record the image digest, hardware, and validation result.
6. Update its RunPod deployment, retaining the earlier image for rollback.

The GitHub check workflow validates configuration and selected lightweight tests;
it does not automatically build, publish, or deploy large GPU images.
