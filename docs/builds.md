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
In both images the handler keeps the RunPod SDK at log level `INFO` unless
`RUNPOD_LOG_LEVEL` is set; at `DEBUG` the SDK logs the signed result links.

### Published: `momensirri/comfy-api-worker:v03`

Pushed on 2026-10-07 from `main` at `92d1215`, digest
`sha256:31c1a4f6dc15e9566106e046e84cd157e38178ceecdd29e15dfd3ae566746acc`,
644 MB compressed. `v02` and `v01` stay on Docker Hub for rollback. The root
target now defaults to the next free tag, `v04`.

Against `v02`, the handler follows the suite's
[job contract](handler-contract.md):

- A job whose graph loads a clip and then fails at a later node fails. The clip
  a loader was given is no longer stored or returned as a result.
- A video, audio or text result is stored with its own content type, and video
  jobs no longer list an error about `animated`.
- `success` is false whenever the job met an error, and a result without bytes
  is refused.
- With `BUCKET_ENDPOINT_URL` set and a key missing, a job is refused before its
  workflow runs.
- An input link is followed through at most five redirects with their bodies
  left unread, and a compressed answer is refused.

Its Python packages are those of `v02`, listed in
`services/generic-comfyui/docs/comfy-api-cpu-v02.pip-freeze.txt`: the layers that
install them came from the build cache. Nothing pins them yet, so a build
without that cache resolves them anew.

Checked on a workstation, in the container without a GPU:

- ComfyUI 0.39.1 starts on the CPU and serves the 28 listed node types.
- The 32 AZ-AI Studio provider and region models, 43 graph variants built by
  AZ-AI's own builders, pass ComfyUI's validation and stop at the provider node
  with `Unauthorized`, because no Comfy key was sent. No provider was called.
- Seven jobs shaped as the AZ-AI backend sends them went through the image's
  handler, and each answer was read by the backend's own validator: an image
  given inline, by link in `url` and by link in `image`; a clip given by link,
  loaded and saved; a provider graph that loads a clip and fails at the provider
  node, which is the case `v02` answers as a success; and a link that answers
  404. The five results are in the bucket once each, the video as `video/mp4`,
  and the two failing jobs failed.
- With the bucket variables pointing at an S3 stand-in, results are stored as
  `<bucket>/<MM-YY>/<job id>/<id>.<ext>` and returned as `s3_url`; the worker
  log holds no signed query string.
- The handler and the other files in the image are the repository's at
  `92d1215`.
- The RunPod SDK in the image stays at `INFO` with `RUNPOD_LOG_LEVEL` blank, and
  goes to `DEBUG` when the variable says so.
- The service's 86 unit and contract tests pass in the image.

Checked on a RunPod CPU endpoint with an R2 bucket, on 2026-10-08, with two
jobs that load a clip and save it, without a provider node:

- The clip given inline, and the clip given by link, the link being the first
  job's result. Both completed on a worker of this tag, after 4.5 s and 1.1 s
  of execution.
- Each answer was read by the AZ-AI backend's validator and accepted, with the
  result inside the bucket the backend takes worker results from.
- Each result is one object, stored through the SDK's multipart uploader with
  the content type `video/mp4`. The clip a job was given did not come back.
- The worker's log holds no signed link.

Not checked: a job with a Comfy key, so no provider node has run on this tag,
and how much memory a CPU worker needs under real provider results.

### Earlier: `momensirri/comfy-api-worker:v02`

Pushed on 2026-10-07 from `main` at `461b0f9`, digest
`sha256:3de258192ec2827c20e421e774a61d09493345688d88b98711ffc0b00e066821`,
644 MB compressed, packages in
`services/generic-comfyui/docs/comfy-api-cpu-v02.pip-freeze.txt`. Against `v01`
it keeps the RunPod SDK at `INFO` by itself and cuts URL query strings from a
failed node's message, from `comfy_credits.details` and from a failed provider
download.

Known in `v02` and `v01`:

- A job whose graph loads a clip and then fails at a later node, for example at
  a provider node, answers `success: true` with the clip it was given as its
  only file. A caller that expects a result cannot tell this from an answer it
  does not understand; the AZ-AI backend would keep such a job pending until
  its age limit instead of failing it at once.
- The answer of every video job lists an error about `animated`, and the clip a
  job was given is stored in the bucket again beside the result.
- A stored video has the content type `image/mp4`.

### Earlier: `momensirri/comfy-api-worker:v01`

Pushed on 2026-10-07, digest
`sha256:fdc6e3f7773e28933d5c9332ef18626de8ccde93a02f182874123ba883bfdfe0`,
644 MB compressed, packages in
`services/generic-comfyui/docs/comfy-api-cpu-v01.pip-freeze.txt`. It keeps the
RunPod SDK at `INFO` only through the image's own `RUNPOD_LOG_LEVEL`, and a
failed download's message can hold the query string of the link.

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

The three ComfyUI handlers are held to one job contract,
[handler-contract.md](handler-contract.md). The check workflow runs each
handler's unit tests and the contract cases of `tests/handler_contract`. A
handler change that passes them still reaches an endpoint only through a new
image tag.
