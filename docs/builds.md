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

## Updating and releasing

1. Change code, workflows, and dependency pins in the appropriate service folder.
2. Run its lightweight tests and the root catalog validator.
3. Build the selected image with a new release tag.
4. Run representative GPU jobs for its supported workflows.
5. Publish and record the image digest, hardware, and validation result.
6. Update its RunPod deployment, retaining the earlier image for rollback.

The GitHub check workflow validates configuration and selected lightweight tests;
it does not automatically build, publish, or deploy large GPU images.
