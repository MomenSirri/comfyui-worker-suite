"""Parse a `vllm serve` config file with vLLM's own argument parser.

A misspelled key, or a value of the wrong type, stops the image build here. Without
this check it stops the worker at its first start, on a paid GPU. Values vLLM judges
only when it starts (a share above 1, a context the GPU cannot hold) still pass.

    python3 check-serve-config.py /etc/azai/qwen3-vl-32b.yaml
"""

import sys

import yaml
from vllm import platforms
from vllm.platforms.cpu import CpuPlatform

# An image build has no GPU, and vLLM refuses to build its parser without a device.
# vLLM's own `bench` command falls back to the CPU platform the same way.
if platforms.current_platform.is_unspecified():
    platforms.current_platform = CpuPlatform()

from vllm.entrypoints.cli.serve import ServeSubcommand  # noqa: E402
from vllm.utils.argparse_utils import FlexibleArgumentParser  # noqa: E402


def main() -> None:
    path = sys.argv[1]

    command = ServeSubcommand()
    parser = FlexibleArgumentParser(prog="vllm")
    command.subparser_init(parser.add_subparsers(dest="subparser"))

    # The worker starts vLLM as `vllm serve <model> ... --config <file>`. The model
    # comes from MODEL_NAME at run time, so any name will do for parsing.
    args = parser.parse_args(["serve", "placeholder/model", "--config", path])
    command.validate(args)

    with open(path) as config_file:
        keys = list(yaml.safe_load(config_file))
    for key in keys:
        print(f"{key}: {getattr(args, key.replace('-', '_'))!r}")
    print(f"{path}: {len(keys)} settings accepted by vLLM")


if __name__ == "__main__":
    main()
