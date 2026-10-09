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

### Published: `momensirri/comfy-api-worker:v07`

Pushed on 2026-10-09 from `main` at `c8199ef`, digest
`sha256:ec9bea2c80ee31af78b40edf9a05846f43b340534ffdafdfecd08adf20f4462a`,
644 MB compressed. The earlier tags stay on Docker Hub for rollback. The root
target now defaults to the next free tag, `v08`.

Against `v06`, a log filter that ended is replaced, instead of leaving ComfyUI
on a pipe that nothing reads:

- `start.sh` runs `redact_log.py --keep-running`. The keeper holds the pipe and
  runs the filter under it, so ComfyUI's output waits in the pipe while no
  filter runs, and no write of ComfyUI fails for it.
- A filter that ended is reported in the worker's log, in a line that begins
  `worker-comfyui: the log filter ended`, and replaced by one that drops the
  word it starts in: that word may be the rest of a query string.
- Filters that end three times in a row within ten seconds of their start are
  left out for a minute, ComfyUI's output dropped meanwhile, and then tried
  again. Nothing is passed on unfiltered.

The handler and the Python packages are those of `v06`.

Checked on a workstation, in the container without a GPU:

- A probe node writes a signed link through `logging`, `print`, the error
  stream and a chained aiohttp `ClientResponseError`. Every one is in the
  container's log with the location and without the signature. The PID file
  names ComfyUI in both `SERVE_API_LOCALLY` branches, and a keeper and one
  filter run.
- After KILL to the filter a replacement runs, ComfyUI answers, and the jobs
  that fail on `v05` complete: a node that flushes what it prints, prints 20 kB
  at once or shows a `tqdm` bar. ComfyUI's lines keep arriving with the link
  cut, and one line says what happened.
- With ComfyUI itself killed, keeper and filter end and the handler reports
  that ComfyUI cannot be reached. With a filter file that cannot run, the
  container exits with status 1 and says why. With `COMFY_LOG_REDACT=false` the
  links are in the log whole and neither keeper nor filter runs.
- The 43 graph variants of the 32 AZ-AI provider and region models, and the
  seven jobs read by the AZ-AI backend's validator, as for `v03`.
- The files in the image are the repository's at `c8199ef`, and the filter,
  `start.sh` and the handler read back from the registry are the same files.
  `test_input.json` differs by its line ends only: the checkout on the build
  workstation has CRLF there, and `v06` holds the same file.
- The service's 139 unit and contract tests pass in the image.

Checked with these files in the container of `v01`, before the build: a filter
that cannot start any more is left out while jobs keep completing, and is back
within its minute once it can start again.

Not checked: a RunPod endpoint, a provider job, a kill by the kernel for
memory, and the GPU image, where a sampler's `tqdm` bar is what a lost filter
would break.

Known in `v07`: when keeper and filter are both ended, one after the other, the
worker is where `v05` is after its filter ended. A line the filter was reading
at the moment it ended can be lost.

### Earlier: `momensirri/comfy-api-worker:v06`

Digest
`sha256:f0c97b5e873f5890df490504c44f8d7636eb1a8ac8d9a0409d9b60c55b57423a`,
644 MB compressed, built on 2026-10-08. This entry was written on 2026-10-09
from the registry and the image, not by the session that built it: the tag was
on Docker Hub while the root target still named `v06` as the next free tag.

Read from the image: its handler, `start.sh` and the filter are the
repository's at `d97d8d7`, and its Python packages are those of `v07`. Against
`v05` that is the handler that keeps another job's text files out of a job's
answer, as the [job contract](handler-contract.md) describes it. The log filter
is that of `v05`.

Not known here: what was checked on this tag before it was pushed.

Known in `v06`: what is known in `v05` about a filter that is ended with KILL.

### Earlier: `momensirri/comfy-api-worker:v05`

Pushed on 2026-10-08 from commit `5c47ba6`, digest
`sha256:c862d71496dd102b8d2d439312a381f12938bac9d07a1075c3a08e61a5de752e`,
644 MB compressed.

Against `v04`, the log filter stays for as long as ComfyUI writes:

- It ignores INT, TERM, HUP and QUIT and keeps reading when its own output can
  no longer be written. It ends at the end of its input only.
- `start.sh` tries the filter before ComfyUI is given its pipe and does not
  start ComfyUI when the filter does not work.
- It writes until every byte is taken, passes on everything up to the last byte
  that ends a query string, so a progress line no longer waits for the next
  line end, and does not search output that holds no question mark.

The handler is unchanged, and the Python packages are those of `v02`.

Checked on a workstation, in the container without a GPU:

- The probe node of `v04`: its six signed links are in the container's log with
  the location and without the signature, the PID file names ComfyUI, and one
  filter process runs.
- After INT, TERM and HUP sent to the filter itself it is still running and
  ComfyUI answers. With a filter file that cannot run, the container exits with
  status 1 and says why. `v04` fails both.
- With `COMFY_LOG_REDACT=false` the six links are in the log whole and no filter
  runs.
- The 43 graph variants of the 32 AZ-AI provider and region models, and the
  seven jobs read by the AZ-AI backend's validator, as for `v03`.
- The files in the image are the repository's at `5c47ba6`, and the filter and
  `start.sh` read back from the registry are the same files.
- The service's 101 unit and contract tests pass in the image.

Checked on a RunPod CPU endpoint with an R2 bucket, on 2026-10-08, without a
provider node:

- Two jobs that load a clip and save it, the clip given inline and by link, both
  completed on a worker of this tag. Each result is one `video/mp4` object,
  accepted by the AZ-AI backend's validator.
- A graph that ComfyUI refuses, naming an input file whose name ends like a
  signed link with a made-up signature: ComfyUI's own line in the worker's log
  has the query string cut. The handler's lines repeat ComfyUI's refusal as it
  is, name included; a graph the backend sends holds no link.
- With `COMFY_LOG_LEVEL` not set, ComfyUI's own `INFO` and `DEBUG` lines are in
  the log, and no line holds a signed link of the bucket.

Not checked: a provider job on this tag.

Known in `v05`: the filter cannot refuse KILL. When it is ended that way,
ComfyUI keeps running, the PID file is right and the handler reaches it, but
ComfyUI's own output is gone from the log from then on with no line that says
so, and a node that flushes what it prints, prints 20 kB at once or shows a
`tqdm` progress bar fails with `[Errno 32] Broken pipe`. Checked on a
workstation on 2026-10-08, in the container of `v05` with a probe node added.
Read in ComfyUI 0.39.1 and not run: its provider nodes do none of the three,
and its samplers show a `tqdm` bar. `v07` closes it.

### Earlier: `momensirri/comfy-api-worker:v04`

Pushed on 2026-10-08 from commit `9925c6d`, digest
`sha256:679164ecd3408f6f26dda06fec2efb02df0da79c9d3f2963f6fa15bbca3b7b06`,
644 MB compressed.

Against `v03`, ComfyUI's own output goes through `src/redact_log.py`, which
cuts the query string out of every URL. ComfyUI's provider nodes log the signed
links they are given: the first provider job on `v03` left
`ByteDance task succeeded, image URL: …` in the worker's log, with the
provider's link to the result, valid for a day. `COMFY_LOG_REDACT=false` passes
the output on as it is. The handler is unchanged, and the Python packages are
those of `v02`.

Checked on a workstation, in the container without a GPU:

- A probe node wrote a signed link the ways ComfyUI and its nodes do: through
  `logging` at `INFO` and `WARNING`, with `print`, to the error stream, as a
  progress line that ends in a carriage return, and inside a traceback. The
  container's log holds all six with the location and without the signature.
- The PID file names ComfyUI itself, and one filter process runs. With
  `COMFY_LOG_REDACT=false` the same six links are in the log whole and no filter
  runs.
- The 43 graph variants of the 32 AZ-AI provider and region models pass
  ComfyUI's validation and stop at the provider node without a key, and the
  seven jobs listed for `v03` give the same results, each read by the AZ-AI
  backend's validator.
- The files in the image are the repository's at `9925c6d`, and the handler and
  the filter read back from the registry are the same files.
- The service's 96 unit and contract tests pass in the image.

It ran on a RunPod endpoint for an hour on 2026-10-08, where two clip jobs
completed and ComfyUI's own line about a refused graph came out with its query
string cut. Not checked: a provider job on it.

Known in `v04`: a signal sent to the filter itself, or its output closing, ends
the filter while ComfyUI keeps running and then writes to a closed pipe, and a
filter that cannot run is not noticed at start.

### Earlier: `momensirri/comfy-api-worker:v03`

Pushed on 2026-10-07 from `main` at `92d1215`, digest
`sha256:31c1a4f6dc15e9566106e046e84cd157e38178ceecdd29e15dfd3ae566746acc`,
644 MB compressed.

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

One provider job followed on the same endpoint, sent by the AZ-AI backend with
a Comfy key: a Seedream 5.0 Flash image edit with its input given by link. It
completed after 34.6 s of execution, and the backend accepted and imported the
result. The worker's log of that job holds the provider's signed result link,
written by ComfyUI's own node, which `v04` cuts. On `v03`,
`COMFY_LOG_LEVEL=WARNING` among an endpoint's variables keeps ComfyUI from
writing that line.

Not checked: a provider job that fails, a model that takes a clip with a key,
and how much memory a CPU worker needs under larger provider results.

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
