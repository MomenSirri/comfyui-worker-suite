# The job contract of the ComfyUI handlers

Three services of this suite run a ComfyUI graph for one RunPod job:
`generic-comfyui`, `ltx25` and `image-workflows`. Each has its own handler. This
page states what all three must take and answer, so that they can change
separately without drifting apart. The prompt worker (`qwen3-vl`) is not a
ComfyUI worker and has its own job format in its README.

The contract is the one the AZ-AI backend relies on. `tests/handler_contract`
holds it as tests, and each service runs them against its own handler.

## What a job holds

`input.workflow` is the ComfyUI graph, in API format. It is queued as sent.

`input.images` is a list of input files. Each entry has a `name`, which is the
file name the graph refers to, and the file itself in one of three ways:

| Entry | Meaning |
| --- | --- |
| `{ "name", "image": "<base64>" }` | the bytes inline; a data-URI prefix is allowed |
| `{ "name", "image": "<https link>" }` | a link, which the worker downloads |
| `{ "name", "url": "<https link>" }` | a link, which the worker downloads |

`data` is read like `image`. A clip is one more entry of `input.images`, by
link; `generic-comfyui` and `ltx25` take one, `image-workflows` takes images
only. A RunPod request is limited to 10 MB, so inline delivery carries a few
megabytes at most.

`input.comfy_org_api_key`, when sent, is handed to ComfyUI for its provider API
nodes and is never logged.

A downloaded input is limited by `INPUT_DOWNLOAD_MAX_BYTES` (256 MiB by default
in `generic-comfyui` and `ltx25`, 50 MiB in `image-workflows`). At most five
redirects are followed, with their bodies left unread, and a response with a
`Content-Encoding` is refused: both could otherwise bring more into the worker's
memory than the limit allows. One read has a timeout. The whole download has
none of its own; a server that keeps sending a little is ended by the endpoint's
execution timeout.

## What a job answers

A job that produced its file answers with it under the key of its kind:

```json
{
  "images": [
    { "filename": "ComfyUI_00001_.png", "type": "s3_url", "data": "https://…" }
  ]
}
```

- A video is under `videos`, in the same form. `generic-comfyui` and `ltx25`
  add `media_type` to each entry and `success`, `prompt_id` and credit figures
  beside the lists.
- `type` is `s3_url` when the endpoint has its bucket variables, and `data` is
  then a presigned link. Without a bucket, `type` is `base64` and `data` holds
  the bytes. The AZ-AI backend reads links only.
- A stored video has a video content type. The object name ends with the file's
  extension, which is how a reader tells an image from a video.
- A file a loader node was given is not a result. `LoadVideo` reports its input
  clip as an output of type `input`; no handler stores or returns it.
- A file that two nodes report is stored and returned once. A file without
  bytes is not a result.
- `image-workflows` also answers `status: "success"` and `message`, the list of
  the same links. That was its whole answer in earlier releases, and it is kept
  for callers that still read it. Without a bucket this sends each inline
  result twice.

A job that failed answers with `error`, a sentence, and may add `details`.
RunPod reports such a job as `FAILED`. This covers a node that raised, an input
link that could not be fetched or is too large, a result that could not be
fetched or stored, a graph stopped at `WORKFLOW_EXECUTION_TIMEOUT_S`, and a
bucket whose variables are incomplete. The last one is checked before the graph
runs: with `BUCKET_ENDPOINT_URL` set and a key missing, the RunPod SDK would
write the result to the worker's disk and return that path as if it were the
link.

Two cases differ between the handlers:

- A graph that ran to its end and left no file. `image-workflows` fails the
  job. `generic-comfyui` and `ltx25` complete it with empty lists and
  `status: "success_no_outputs"`, because a graph may be run for its side
  effects; a caller that expects a file reads that status as a failure.
- A job that has files and also met an error. `generic-comfyui` completes it,
  with the files, the messages under `errors` and `success: false`. `ltx25`
  fails it.

## What a worker keeps to itself

A signed link is a credential for as long as it is valid.

- The handler sets `RUNPOD_LOG_LEVEL=INFO` before it loads the RunPod SDK,
  unless the variable has a value. At `DEBUG`, its default, the SDK logs each
  job's whole answer.
- A failed download reports its kind (`HTTP 403`, a timeout), never the link.
- The message of a failed node is logged and returned with the query string of
  every URL in it cut.
- The line that reports an upload names the object without its signature.
- In `generic-comfyui`, ComfyUI's own output goes through a filter that cuts the
  query string out of every URL (`src/redact_log.py`, started by `start.sh`).
  ComfyUI's provider nodes log the signed links they are given, for example the
  link to a finished task's result, and a handler cannot filter what ComfyUI
  writes. The other two services do not run provider nodes and have no filter.

## How the AZ-AI backend reads an answer

| Job | Reading | Accepted |
| --- | --- | --- |
| Upscale, Enhancement | `output.images` | exactly one entry with `data`, `filename` and `type: "s3_url"` |
| Studio (image or video) | `output.videos`, `images`, `files`, `message` | exactly one link whose file name has an extension of the tool's kind; an entry named `azai_in_…` is ignored |

The link must be on the storage endpoint the backend is configured with, inside
the bucket it accepts worker results from. With no usable file, a Studio job
counts as failed when the answer has `success: false`, an `error`, or a `status`
other than `success`, `ok` or `completed`. An answer the backend cannot read is
worse than a failed job: nothing completes and nothing is refunded until the job
reaches its age limit.

An endpoint that serves the backend therefore needs all three bucket variables,
with `BUCKET_ENDPOINT_URL` as the backend's storage endpoint over https and the
bucket as its path. A worker cannot check this for itself: with another
address, or with no bucket at all, it answers in good faith with links or inline
bytes that the backend does not accept.

Which delivery a Studio worker gets is set in the backend's `STUDIO_WORKERS`
(`"input"`: `base64`, `url` or `image-url`). All three handlers take all three.

## Each handler against the contract

| | `generic-comfyui` | `ltx25` | `image-workflows` |
| --- | --- | --- | --- |
| Behind | the Studio's provider models | the Studio's LTX models | Upscale, Enhancement, the Studio's FLUX.2 Klein modes |
| Images inline or by link | yes | yes | yes |
| Clips, video results | yes | yes | no |
| Run-time limit (`WORKFLOW_EXECUTION_TIMEOUT_S`) | 1200 s | 1200 s | off unless set |
| Recycles the worker after a failed job | no | yes | yes (`REFRESH_WORKER_ON_FAILURE`) |

`ltx25` also takes its named modes (`mode`, `parameters`, `media`), described in
its own `docs/API.md`; a job with a `workflow` and no `mode` follows this page.

## Checking a handler

```bash
cd services/<service>
python -m unittest tests.test_backend_contract
```

The cases need `requests` and `websocket-client`; the case that reads the RunPod
SDK's log level is skipped where the SDK is not installed. ComfyUI, the input
links and the bucket are stand-ins (`tests/handler_contract/fake_comfy.py`).
`tests/handler_contract/backend.py` is a port of the backend's own checks and
changes when they change. The hosted check runs the cases for all three
services.

The cases do not start ComfyUI and do not build an image. A changed handler
reaches an endpoint only through a new image tag.
