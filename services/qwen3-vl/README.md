# Qwen3-VL-32B prompt worker

A RunPod serverless worker that serves **Qwen3-VL-32B-Instruct** for writing video prompts from a user's idea and up to four images. Its first use is the prompt helper for Seedance 2.5.

It is RunPod's own vLLM worker ([runpod-workers/worker-vllm](https://github.com/runpod-workers/worker-vllm)) with AZ-AI's settings and a small entrypoint. The job handler and the OpenAI routes are the base image's. The instructions that turn an idea into a Seedance prompt are not in the worker: the caller sends them with each request.

State on 2026-10-07: the RunPod endpoint `Qwen3-VL` runs `momensirri/qwen3-vl-32b:v07` (digest `sha256:563c9a26dcd70fb732b46978bb98130ba8295c46149a7a5bd7455909682bcbcd`, a public repository), and the 32B model passes every check below there. Nothing calls it yet: the backend has no prompt helper. See [What was checked](#what-was-checked).

**Since 2026-10-07 15:17 UTC the endpoint runs the 4-bit model on RTX 5090 cards.** At the owner's request the GPU pool was changed from `ADA_48_PRO` to `ADA_32_PRO`, and four variables make the `v07` image load `RedHatAI/Qwen3-VL-32B-Instruct-NVFP4` (`MODEL_NAME`, `MODEL_REVISION`, and `HF_HOME` and `HUGGINGFACE_HUB_CACHE` pointing at the container disk, which was raised to 40 GB). To go back: pool `ADA_48_PRO`, no variables.

| | 8-bit model on the 48 GB cards | 4-bit model on the RTX 5090 |
| --- | --- | --- |
| Smoke checks | all five pass | all five pass |
| Speed of one request | 21 tokens a second | 67 tokens a second |
| The example prompt request, warm | 6.4 to 7.7 s | 2.2 to 2.5 s |
| GPU memory | 34.2 of 44.4 GiB; context cache 30,288 tokens | 21.4 of 31.4 GiB; context cache 32,432 tokens |
| Fresh worker | 74 s (L40S), 188 s (Blackwell slice), with the built-in caches | 327 s, the only start so far: 68 s downloading the weights, 49 s compiling, 110 s capturing CUDA graphs |
| Price in RunPod's catalog | $1.75 an hour | $1.58 an hour |

- **The answers.** Four requests (one image with three ideas, one of them in Arabic, and one request without an image) were sent to both at temperature 0. The 4-bit answers follow the ideas as the 8-bit ones do; neither set is visibly better. They are in `.local/qwen3-vl/answers-8bit.json` and `answers-4bit.json` of the workspace. Nobody has compared them on the owner's own frames.
- **The fresh start is not prepared for this model.** The endpoint's Model field still names the 8-bit weights, which only the console can change, so a fresh worker downloads 22 GB; and the caches inside `v07` belong to the 8-bit model. vLLM uses `FlashInferCutlassNvFp4LinearKernel` here.
- **Availability.** From 15:33 to 16:03 UTC no 5090 was available to the endpoint and a request waited in the queue. The 4-bit model would also fit the 48 GB cards; that has not been tried.
- Everything below describes the 8-bit model on the 48 GB cards unless it says otherwise.

| Tag | What it is |
| --- | --- |
| `v01` | The first release (2026-10-06) |
| `v02` | Can send its compile cache to storage. Superseded: its cache is not marked with the GPU generation |
| `v03` | Chooses a built-in compile cache by GPU generation, and keeps a Hugging Face token off the command line and out of the log. No cache inside |
| `v04`, `v05` | `v03` with the compiled model for compute capability 12.0, then for 8.9 as well |
| `v06` | Keeps the DeepGEMM kernels next to the compiled model. The caches of `v05` inside |
| `v07` | `v06` with the DeepGEMM kernels for 12.0. **In use** |

## What the image contains

| Part | Value |
| --- | --- |
| Base image | `runpod/worker-v1-vllm:v2.28.0`, pinned by digest: vLLM 0.30.0, CUDA 13.0 |
| Model | `Qwen/Qwen3-VL-32B-Instruct-FP8`, Qwen's own 8-bit weights, Apache 2.0, 35.5 GB |
| Revision | `4bf2c2f39c37c0fede78bede4056e1f18cdf8109`, the repository's `main` (unchanged since 2025-10-22) |
| Settings | [config/qwen3-vl-32b.yaml](config/qwen3-vl-32b.yaml), copied to `/etc/azai/` |
| Entrypoint | [scripts/start.py](scripts/start.py): starts the base image's worker, keeps links and a Hugging Face token out of its log and its command line, gives vLLM the compile cache that fits the worker's GPU, and can send a worker's compile cache to storage |
| Compile caches | One per GPU generation, from `compile-cache/*.tar.gz`, made on the endpoint on 2026-10-07: the compiled model for compute capability 8.9 (L40S), and the compiled model and the DeepGEMM kernels for 12.0 (RTX PRO 6000 Blackwell) |

**The weights are not in the image.** A 35 GB layer cannot be uploaded from this workstation in reasonable time, and RunPod keeps the weights on its hosts when the endpoint's **Model** field names them.

## Settings

| Setting | Value | Reason |
| --- | --- | --- |
| Served model name | `qwen3-vl-32b` | What callers pass as `model`; it stays the same when the weights change |
| Context | 16,384 tokens | Instructions, four images, the idea and the answer fit; one full context is 4 GiB of GPU memory |
| Images per request | 4, no video | First frame, last frame and references |
| Image size | at most 1,310,720 pixels (1,280 tokens) | A larger image is reduced. The model's own limit is 16,384 tokens for one image |
| Answer length | at most 4,096 tokens | Applies whatever `max_tokens` a request asks for |
| Requests at a time | 8 per worker | `max-num-seqs` and `MAX_CONCURRENCY` |
| GPU memory share | 0.95 | The weights are 33.1 GiB of a 48 GB card |
| Image links | no redirects, 15 seconds, 32 MB | A link is fetched by the worker |
| Log level of the RunPod SDK | `INFO` | At its default the SDK writes every answer to the worker log |

An environment variable named after a vLLM flag overrides the file, for example `MAX_MODEL_LEN=8192` on the endpoint.

## Cold start

A request to an endpoint with no running worker waits for a worker. Measured on the endpoint on 2026-10-07, from sending the request to its answer:

| Case | Without caches | With the caches (`v07`) |
| --- | --- | --- |
| A fresh worker on an L40S | 122 s | 74 s |
| A fresh worker on an RTX PRO 6000 Blackwell slice | 275 s | 188 s |
| A worker RunPod could bring back (FlashBoot), 4 and 5.5 minutes after it stopped | about 2 s | about 2 s |

These are times on a host that already holds the image. A worker placed on a new host waits for RunPod to fetch the image first: one request waited 399 seconds, of which the worker's own start was 80.

- **What a fresh start consists of on an L40S** (110 seconds from vLLM's start to ready, without caches): about 35 seconds of imports and set-up, 10 seconds loading the 34 GiB of weights from RunPod's copy, and 60 seconds starting the engine, of which 40 seconds are compiling the model. With the cache the engine starts in 15 seconds.
- **The compile caches remove the compiling.** vLLM compiles the model at every start unless it finds what an earlier start left in its cache folder, and a serverless worker starts from the image each time, so the folders are built into the image. [compile-cache/README.md](compile-cache/README.md) says how they are made.
- **One cache per GPU generation.** vLLM picks a different kernel for these weights on each generation (`MarlinFP8ScaledMMLinearKernel` on the L40S, `DeepGemmFp8BlockScaledMMKernel` on the Blackwell card), its cache is not named after the GPU, and it uses what it finds without checking. The entrypoint therefore reads the GPU's compute capability and gives vLLM only the folders made on that generation.
- **The Blackwell slices have more to prepare.** DeepGEMM compiles its kernels at run time; with them built in, its warm-up fell from 26 seconds to under one. What remains is 91 seconds of capturing CUDA graphs (4 seconds on the L40S). The log shows the compiled model's Triton kernels being rebuilt there: the cache names kernel files under `/tmp/torchinductor_root/triton`, a folder that is not kept. Keeping it is the next step if these slices matter. Both generations generate at the same speed, 21 tokens a second.
- **RunPod's `L40, L40S, 6000 Ada` group hands out these Blackwell slices as well** (`NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb`). On 2026-10-07 the L40S cards were often unavailable and the slices served.
- **vLLM's optimisation level 0 is not used.** It skips compiling and CUDA graphs, but with the 2B model on this workstation it generated three times slower (30 against 93 tokens a second) for the same start time as the compile cache gives.
- **Not starting at all is the larger gain.** An idle timeout of 60 seconds keeps the worker for a user's next prompt, and the application can send a first small request when a user opens the video tool.

## Build

```bash
docker buildx bake
```

This builds `momensirri/qwen3-vl-32b:v07`, with the compile caches that are in `compile-cache/`. The build parses the settings with vLLM's own parser, so a misspelled key or a value of the wrong type fails here. The image is the base image (31.5 GB unpacked, 9 GB to download once) plus a few kilobytes.

To publish, sign in to Docker Hub as `momensirri` and run the next command. Docker Hub takes the base layers from RunPod's repository, so only the few small layers of this image are uploaded.

```bash
docker buildx bake --push
```

Set `RELEASE_VERSION` to a new tag for every release: a push to an existing tag replaces it.

## RunPod endpoint

Create the endpoint in the RunPod account of the other AZ-AI endpoints, so that the backend's one `RUNPOD_API_KEY` reaches it.

1. **Container image:** `momensirri/qwen3-vl-32b:v07`.
2. **GPU:** 48 GB. Put `L40, L40S, 6000 Ada` first ($0.00053 a second in RunPod's price list on 2026-10-06). `A6000, A40` ($0.00034 a second) can be the second choice. vLLM runs these weights with the same weight-only kernel on both groups (the endpoint's log and the RTX 3060's say so), so the difference is the cards' own speed, which has not been measured for the cheaper group.
3. **CUDA version:** 13.0. The base image requires it.
4. **Model:** `Qwen/Qwen3-VL-32B-Instruct-FP8`. RunPod's documentation says it then starts workers on hosts that already hold the weights and does not bill the download.
5. **Container disk:** 60 GB, which also holds the weights when a host has no cached copy.
6. **Workers:** 0 active, 1 or 2 at most to begin with. Set the idle timeout to about 60 seconds: RunPod's default of 5 seconds stops the worker between two prompts of the same user.
7. **Environment variables:** none are required. `ALLOWED_MEDIA_DOMAINS=<storage host>` makes the worker fetch links from that host only. **Do not set `HF_TOKEN`:** the model is public, and `v01` and `v02` write the token into the worker log (the base image passes it to vLLM as `--hf-token` and logs the command). From `v03` the token stays off the command line and out of the log.

**A change of the image or of a variable is a new release, and workers of earlier releases keep answering** until RunPod has replaced them. On 2026-10-07 workers three releases old still answered an hour after a change. RunPod's v2 API lists each worker with its release, image and GPU (`api.runpod.io/v2/serverless/<id>/workers`) and streams a worker's log (`.../workers/<worker>/logs`); read them before trusting a measurement.

**An RTX 5090 cannot run these weights.** They take 34 GiB of GPU memory and the card has 32 GB. A 4-bit version of the same model is about 21 GB and would fit: `RedHatAI/Qwen3-VL-32B-Instruct-NVFP4` (the 4-bit format of the Blackwell cards) or `QuantTrio/Qwen3-VL-32B-Instruct-AWQ`. Neither has been tried here; it would be a second endpoint with its own settings and caches, and the prompts would have to be compared with the 8-bit ones. In RunPod's catalog on 2026-10-07 the 5090 is its own serverless pool (`ADA_32_PRO`) at $1.58 an hour against $1.75 for the group in use, and was the easier one to get.

Then run the checks against it:

```bash
RUNPOD_API_KEY=... node scripts/smoke.mjs --endpoint <endpoint id>
```

The first request includes the cold start. Send one real request with an image of your own:

```bash
RUNPOD_API_KEY=... node scripts/smoke.mjs --endpoint <endpoint id> --request examples/seedance-first-frame.json --image path/to/frame.jpg
```

[examples/seedance-first-frame.json](examples/seedance-first-frame.json) holds example instructions for a first-frame prompt. They are there to try the endpoint with; the product's own instructions belong in the backend.

## Calling the worker

Send a job to `/runsync` or `/run`. The body of `openai_input` is an OpenAI chat completion request:

```json
{
  "input": {
    "openai_route": "/v1/chat/completions",
    "openai_input": {
      "model": "qwen3-vl-32b",
      "max_tokens": 400,
      "messages": [
        { "role": "system", "content": "..." },
        {
          "role": "user",
          "content": [
            { "type": "image_url", "image_url": { "url": "https://<signed link to the image>" } },
            { "type": "text", "text": "The user's idea" }
          ]
        }
      ]
    }
  }
}
```

- **The answer** is `output[0].choices[0].message.content`, and `output[0].usage` has the token counts.
- **A refused request** ends as `FAILED` on RunPod, and the job's `error` is one string that holds the worker's message. A local worker ends it as `COMPLETED` with `output[0].error.message` in place of `choices`. A caller has to handle both.
- **Error messages can contain the link that was sent.** vLLM names the link it could not fetch. Do not log such a message or show it to a user.
- An image can be a link or a `data:` address. Links keep the job small; RunPod limits the size of a request.
- Sampling defaults come from the model: temperature 0.7, top-p 0.8, top-k 20.
- The same endpoint also answers OpenAI clients at `https://api.runpod.ai/v2/<endpoint id>/openai/v1`.

## Test on this workstation

The 32B model needs a 48 GB GPU. `Qwen/Qwen3-VL-2B-Instruct-FP8` has the same architecture and the same weight format and fits the RTX 3060, so the image is tested with it. From Git Bash:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --name azai-qwen3vl-test --gpus all --shm-size=4g -p 127.0.0.1:8080:8080 -v azai-qwen3vl-hf:/runpod-volume -e MODEL_NAME=Qwen/Qwen3-VL-2B-Instruct-FP8 -e MODEL_REVISION=46485250d8854c0a9be4f1adbc67ca47e5bb6fa5 -e GPU_MEMORY_UTILIZATION=0.7 momensirri/qwen3-vl-32b:v07 --rp_serve_api --rp_api_host 0.0.0.0 --rp_api_port 8080
```

When the log says `Uvicorn running on http://0.0.0.0:8080`, run in a second terminal:

```bash
node scripts/smoke.mjs --url http://localhost:8080
```

The Docker volume `azai-qwen3vl-hf` keeps the 2B weights (3.5 GB) between runs. The unit tests of the entrypoint need only Python:

```bash
python -m unittest discover -s tests
```

## What was checked

On 2026-10-06, on this workstation (RTX 3060 12 GB), with the 2B model in the image built from this folder:

- **The build**, and its settings check: a misspelled key and a value of the wrong type each fail it.
- **Start-up:** vLLM 0.30.0 takes every setting of the file, and an environment variable overrides one.
- **Requests:** text, an image sent inline, an image sent as a link, and the example request with a real image.
- **Limits:** a 2048x2048 image came to 1,242 prompt tokens; a fifth image, a video, a link that redirects and a link to a local file were refused; a request for 9,000 tokens stopped at 4,096. The worker answered normally after each refusal.
- **`ALLOWED_MEDIA_DOMAINS`:** a link on the named host was fetched, another host was refused, an inline image still worked.
- **The log:** no answer text, and a link that could not be fetched appears without its query string.
- **8-bit weights on an older GPU:** the RTX 3060 is of the same generation as the A6000 and A40. vLLM loaded the weights with its weight-only kernel and answered correctly.
- Twenty-two unit tests of the entrypoint.
- **A Hugging Face token, on 2026-10-07:** with `HF_TOKEN` set, the command the worker starts has no `--hf-token` and the token appears nowhere in the log.
- **The compile cache, on 2026-10-07:** a worker with `COMPILE_CACHE_UPLOAD_URL` set sent its cache, in a folder named 8.6 after the RTX 3060's compute capability, to a stand-in for storage; the link's query string stayed out of the log; and a fresh container of an image built with that file took the cache, loaded the compiled model in 0.5 seconds (33 seconds without) and passed the smoke checks. The image also builds without any file.
- **The push:** the published image was read back from Docker Hub and has the entrypoint and settings of the tested one.

On the same day, on the RunPod endpoint (one 48 GB GPU of the `L40, L40S, 6000 Ada` group, CUDA 13.0), with the 32B model:

- **The model loads and answers** with the settings of this folder, unchanged.
- **The five smoke checks pass:** text, an image sent inline, an image sent as a link, the image size cap (1,242 prompt tokens for 2048x2048) and the refusal of a fifth image.
- **Cold start:** the first request ever was answered after 377 seconds, which includes RunPod fetching the image. A request to the stopped worker a minute later was answered after 156 seconds.
- **The example request** with a real image: a usable prompt of 123 tokens in 8.2 seconds. The same with the idea written in Arabic: an English prompt that followed the idea, in 7.8 seconds.
- **Eight requests at once** were all answered within 9.0 seconds, each in 6 to 8 seconds of execution.
- **Speed of one request:** 600 tokens in 28.6 seconds, 21 tokens a second.
- **The Model field** names the model with the pinned revision (the owner's screenshot of the endpoint, 2026-10-07).
- **Memory, from the worker's log:** of 44.4 GiB on the card, 34.2 GiB are the weights and 7.4 GiB are left for the KV cache, which is 30,288 tokens, 1.85 times one full context.
- **`v02`, `v03`, `v05` and `v07`** each passed the smoke checks on the endpoint on 2026-10-07.
- **The compile cache on an L40S, `v06` and `v07`:** the worker logged that it uses the cache built in for compute capability 8.9, vLLM loaded the compiled model directly, compiling took 0.5 seconds (39.8 without) and the engine started in 15 seconds (60 without). The request was answered after 74 seconds (122 without).
- **The caches on a Blackwell slice, `v07`:** the worker took the compiled model and the DeepGEMM kernels built in for 12.0; compiling took 0.44 seconds (45.65 without), the DeepGEMM warm-up under one second (26 without), and the engine started in 113 seconds (205 without). The request was answered after 188 seconds (275 without, 256 and 223 with the compiled model alone).
- **A worker brought back by FlashBoot** answered in 1.8 and 2.3 seconds, 5.5 and 4 minutes after it had stopped.
- **No token on the command line:** the `v03` to `v05` workers log `vllm serve` without `--hf-token`.
- **Speed on a Blackwell slice:** 600 tokens in 28.5 seconds, 21 tokens a second, as on the L40S.

## What was not checked

- **The quality of the prompts** beyond two samples of one image. Nobody has judged them against Seedance 2.5 results.
- **Requests near the limits:** four real photographs in one request, and a context close to 16,384 tokens.
- **The `A6000, A40` group** with the 32B model. The endpoint does not list it.
- **The 8.9 cache on an L40 or an RTX 6000 Ada.** It was made and used on L40S cards. That the other two take it follows from their equal compute capability; no worker has started on one.
- **The rest of the Blackwell start.** The 91 seconds of graph capture were read from one log; keeping the Triton kernel folder has not been tried.
- **How long RunPod keeps a stopped worker ready to bring back.** Two requests, 4 and 5.5 minutes after a stop, were answered in about 2 seconds.
- **More than one worker,** and a day of real use.

If Qwen ever adds a commit to the model repository, RunPod's cached copy and the pinned revision may differ, and a worker would then download the pinned weights at each cold start. Set the new commit in the Dockerfile and rebuild.
