# Compile caches

At every start vLLM compiles the model and several kinds of kernels are built for the GPU, unless what an earlier start left in the cache folders is found. A serverless worker starts from the image each time, so the folders are built into the image: the build unpacks every `.tar.gz` file of this folder into `/opt/azai/compile-cache/`.

**One file per GPU generation.** A file holds one folder named after a GPU compute capability, for example `12.0` for the RTX 5090 and the RTX PRO 6000 Blackwell, and inside it the cache folders that existed on the worker. All of them are in vLLM's cache root, `~/.cache/vllm`:

| Folder | What it is | Who puts it there |
| --- | --- | --- |
| `torch_compile_cache` | The compiled model | vLLM |
| `triton` | The compiled model's Triton kernels | PyTorch, because `scripts/start.py` names the folder in `TRITON_CACHE_DIR` |
| `nv_compute_cache` | The kernels the NVIDIA driver compiled for the GPU | The driver, because `scripts/start.py` names the folder in `CUDA_CACHE_PATH` |
| `deep_gemm` | The kernels DeepGEMM compiles at run time; only where vLLM uses DeepGEMM (the 8-bit weights on Blackwell cards) | vLLM |

When a worker starts, `scripts/start.py` reads its GPU's capability with `nvidia-smi` and copies the folders of that name to the cache root. A worker on a generation without a file builds everything as before.

vLLM names a compiled model after its version, the model and the settings, but not after the GPU, and it uses what it finds without checking. A cache from another generation is compiled for other kernels, which is why the worker chooses by capability and vLLM is never shown the others.

## Why the worker names two of the folders

- **Triton kernels.** PyTorch decides where they go when the first one is built: under vLLM's compiled-model folder if vLLM has named that folder by then, and under `/tmp/torchinductor_root/triton` otherwise. On Blackwell cards a kernel is built before, so the kernels were outside every kept folder and each start logged `Failed to reload cubin file`. With the folder named up front the warning is gone. It was not what made those starts slow.
- **Kernels the driver compiles.** A kernel that was not built for the GPU's architecture is compiled by the NVIDIA driver when it is first used, and kept in the driver's own cache, by default `~/.nv/ComputeCache`. On Blackwell cards (compute capability 12.0) that is vLLM's attention kernel: a stack sample of the engine every 15 seconds during the capture of CUDA graphs showed it inside `flash_attn_varlen_func` each time, the folder held 74 MB afterwards, and a second start in the same container captured the graphs in 2 seconds where the first took 110. On an L40S nothing is compiled this way and the capture takes 2 seconds.

**The driver's cache only fits the driver it was made with.** A worker on a host with another driver version compiles those kernels again, as it would without the file. The worker logs its driver version next to the capability at every start, and with `Compile cache sent` when a file is made.

The files are not in version control. After a change of the base image, the model or the settings file, the old ones no longer match (vLLM ignores them and compiles) and should be made again. **A file made before `v08` does not fit this entrypoint:** its Triton kernels are inside `torch_compile_cache`, where vLLM no longer looks, so the build refuses a cache without a `triton` folder. The files of `v07` (the 8-bit model) are kept in `.local/qwen3-vl/compile-cache-8bit-v07/` of the workspace for that reason only.

## Making one

1. Build and publish the image.
2. Make a signed `PUT` link for an object in private storage, valid for an hour.
3. Set `COMPILE_CACHE_UPLOAD_URL` on the endpoint to that link and send one request. When vLLM is ready the worker uploads its cache folders and logs `Compile cache sent` with their names, the capability and the driver version.
4. Remove the variable from the endpoint.
5. Download the object to this folder as `<capability>.tar.gz`, for example `12.0.tar.gz`. Do not unpack it.
6. Build and publish the image under a new tag and point the endpoint at it.

**To get a worker on one kind of GPU,** exclude the other types of the pool while the file is made (`gpu.excludedTypes` in RunPod's v2 API, next to `gpu.pools`), and set the worker maximum to 0 and back so that no worker of an earlier release answers. A worker of an earlier release has the variables of its own release: check the release of the worker that answered.

The build log lists the capabilities it found. A start that uses a cache logs `Compile cache: using` with the folder names, then `Directly load AOT compilation from path`, its `torch.compile took` line shows less than a second, no line mentions a cubin file, and on a Blackwell card `Graph capturing finished` shows a few seconds.
