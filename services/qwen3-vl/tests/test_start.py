import io
import logging
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import start  # noqa: E402

SIGNED = (
    "https://account.r2.cloudflarestorage.com/bucket/prompts/3f1c.png"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=key%2F20261006&X-Amz-Signature=abc123"
)


class RedactLinksTest(unittest.TestCase):
    def test_removes_the_query_string_of_a_link(self) -> None:
        self.assertEqual(
            start.redact_links(f"Failed to fetch media from URL (value={SIGNED})"),
            "Failed to fetch media from URL "
            "(value=https://account.r2.cloudflarestorage.com/bucket/prompts/3f1c.png?[removed])",
        )

    def test_removes_it_inside_quoted_json(self) -> None:
        text = '{"error":{"message":"HTTP 404 (value=%s)","code":422}}' % SIGNED
        redacted = start.redact_links(text)
        self.assertNotIn("X-Amz", redacted)
        self.assertTrue(redacted.endswith('?[removed])","code":422}}'))

    def test_removes_it_from_every_link(self) -> None:
        redacted = start.redact_links(f"{SIGNED} then http://host/a.jpg?token=1 end")
        self.assertEqual(redacted.count("?[removed]"), 2)
        self.assertNotIn("token=1", redacted)
        self.assertTrue(redacted.endswith(" end"))

    def test_leaves_other_text_alone(self) -> None:
        for text in (
            "vLLM is healthy",
            "GET https://huggingface.co/api/models/Qwen/Qwen3-VL-32B-Instruct-FP8 200",
            "Is the answer ready? Yes.",
            "data:image/png;base64,iVBORw0KGgo=",
        ):
            self.assertEqual(start.redact_links(text), text)


class RedactTokenTest(unittest.TestCase):
    def test_masks_a_hugging_face_token(self) -> None:
        self.assertEqual(
            start.redact("Starting vLLM: vllm serve --hf-token hf_AbCdEfGh1234567890 --model Qwen/Model"),
            "Starting vLLM: vllm serve --hf-token hf_[removed] --model Qwen/Model",
        )

    def test_leaves_words_that_only_start_alike(self) -> None:
        for text in ("hf_home is /runpod-volume", "the hf_hub library", "model.safetensors"):
            self.assertEqual(start.redact(text), text)


class TokenOffTheCommandLineTest(unittest.TestCase):
    def test_reserves_the_variable_in_the_base_images_argument_builder(self) -> None:
        builder = types.ModuleType("args_builder")
        builder.RESERVED_ENV_VARS = frozenset({"HOST", "PORT"})
        with mock.patch.dict(sys.modules, {"args_builder": builder}):
            start.keep_token_off_the_command_line()
        self.assertEqual(builder.RESERVED_ENV_VARS, frozenset({"HOST", "PORT", "HF_TOKEN"}))


class LogRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(logging.setLogRecordFactory, logging.getLogRecordFactory())
        start.keep_secrets_out_of_logs()
        self.output = io.StringIO()
        self.logger = logging.getLogger("test_start")
        self.logger.propagate = False
        handler = logging.StreamHandler(self.output)
        self.logger.addHandler(handler)
        self.addCleanup(self.logger.removeHandler, handler)

    def test_a_logged_link_loses_its_query_string(self) -> None:
        # The base image's handler logs a refused request this way.
        self.logger.error("vLLM %s %s returned HTTP %s: %s", "POST", "/v1/chat/completions", 422, SIGNED)
        written = self.output.getvalue()
        self.assertNotIn("X-Amz", written)
        self.assertIn("returned HTTP 422: https://account.r2.cloudflarestorage.com/bucket/prompts/3f1c.png?[removed]", written)

    def test_a_record_whose_arguments_do_not_fit_is_left_to_logging(self) -> None:
        record = logging.getLogRecordFactory()(
            "test_start", logging.ERROR, __file__, 1, "%s and %s", ("one",), None
        )
        self.assertEqual(record.args, ("one",))


class StandInServer(ThreadingHTTPServer):
    """Answers vLLM's health check and takes the upload, on one local port."""

    def __init__(self, upload_status: int = 200) -> None:
        super().__init__(("127.0.0.1", 0), StandInHandler)
        self.upload_status = upload_status
        self.uploads: list[tuple[str, dict[str, str], bytes]] = []


class StandInHandler(BaseHTTPRequestHandler):
    server: StandInServer

    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_PUT(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.uploads.append((self.path, dict(self.headers), body))
        self.send_response(self.server.upload_status)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


class SendCompileCacheTest(unittest.TestCase):
    def serve(self, upload_status: int = 200) -> StandInServer:
        server = StandInServer(upload_status)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def cache_root(self) -> str:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        graph = Path(root.name, "torch_compile_cache", "torch_aot_compile", "abc", "rank_0_0")
        graph.mkdir(parents=True)
        (graph / "model").write_bytes(b"compiled graph")
        Path(root.name, "deep_gemm", "kernels").mkdir(parents=True)
        Path(root.name, "deep_gemm", "kernels", "kernel.so").write_bytes(b"compiled kernel")
        Path(root.name, "triton", "KERNELHASH").mkdir(parents=True)
        Path(root.name, "triton", "KERNELHASH", "triton_fused.cubin").write_bytes(b"triton kernel")
        Path(root.name, "nv_compute_cache", "a", "b").mkdir(parents=True)
        Path(root.name, "nv_compute_cache", "a", "b", "0f1e").write_bytes(b"kernel the driver compiled")
        Path(root.name, "modelinfos").mkdir()
        Path(root.name, "modelinfos", "other.json").write_text("{}")
        return root.name

    def test_sends_the_cache_folder_as_one_tar_file(self) -> None:
        server = self.serve()
        port = str(server.server_address[1])
        with (
            mock.patch.dict(os.environ, {"VLLM_PORT": port, "VLLM_CACHE_ROOT": self.cache_root()}),
            mock.patch.object(start, "gpu_compute_capability", return_value="8.9"),
        ):
            start.send_compile_cache(f"http://127.0.0.1:{port}/bucket/cache.tar.gz?X-Amz-Signature=abc123")

        self.assertEqual(len(server.uploads), 1)
        path, headers, body = server.uploads[0]
        self.assertEqual(path, "/bucket/cache.tar.gz?X-Amz-Signature=abc123")
        self.assertEqual(headers["Content-Length"], str(len(body)))
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            files = {member.name: tar.extractfile(member).read() for member in tar if member.isfile()}
        # Only the cache folders, in one folder named after the GPU's compute capability.
        self.assertEqual(
            files,
            {
                "8.9/torch_compile_cache/torch_aot_compile/abc/rank_0_0/model": b"compiled graph",
                "8.9/deep_gemm/kernels/kernel.so": b"compiled kernel",
                "8.9/triton/KERNELHASH/triton_fused.cubin": b"triton kernel",
                "8.9/nv_compute_cache/a/b/0f1e": b"kernel the driver compiled",
            },
        )

    def test_packs_again_when_a_file_moved_while_it_was_read(self) -> None:
        # Triton writes a kernel under a temporary name and renames it.
        server = self.serve()
        port = str(server.server_address[1])
        pack = start.pack_cache_folders
        attempts = []

        def pack_with_one_failure(archive, capability):
            attempts.append(capability)
            if len(attempts) == 1:
                archive.write(b"the start of an archive")
                raise FileNotFoundError("tmp.pid_1_abc")
            return pack(archive, capability)

        with (
            mock.patch.dict(os.environ, {"VLLM_PORT": port, "VLLM_CACHE_ROOT": self.cache_root()}),
            mock.patch.object(start, "gpu_compute_capability", return_value="8.9"),
            mock.patch.object(start, "pack_cache_folders", side_effect=pack_with_one_failure),
        ):
            start.send_compile_cache(f"http://127.0.0.1:{port}/bucket/cache.tar.gz?X-Amz-Signature=abc123")

        self.assertEqual(attempts, ["8.9", "8.9"])
        self.assertEqual(len(server.uploads), 1)
        with tarfile.open(fileobj=io.BytesIO(server.uploads[0][2]), mode="r:gz") as tar:
            names = sorted(member.name for member in tar if member.isfile())
        self.assertEqual(len(names), 4)

    def test_sends_nothing_when_the_gpu_cannot_be_identified(self) -> None:
        server = self.serve()
        port = str(server.server_address[1])
        with (
            mock.patch.dict(os.environ, {"VLLM_PORT": port, "VLLM_CACHE_ROOT": self.cache_root()}),
            mock.patch.object(start, "gpu_compute_capability", return_value=None),
            self.assertLogs(level=logging.ERROR) as logs,
        ):
            start.send_compile_cache(f"http://127.0.0.1:{port}/bucket/cache.tar.gz?X-Amz-Signature=abc123")

        self.assertEqual(server.uploads, [])
        self.assertIn("compute capability could not be read", "\n".join(logs.output))

    def test_a_refused_upload_is_logged_without_the_signature_and_does_not_raise(self) -> None:
        server = self.serve(upload_status=403)
        port = str(server.server_address[1])
        with (
            mock.patch.dict(os.environ, {"VLLM_PORT": port, "VLLM_CACHE_ROOT": self.cache_root()}),
            mock.patch.object(start, "gpu_compute_capability", return_value="8.9"),
            self.assertLogs(level=logging.ERROR) as logs,
        ):
            start.send_compile_cache(f"http://127.0.0.1:{port}/bucket/cache.tar.gz?X-Amz-Signature=abc123")

        written = "\n".join(logs.output)
        self.assertIn("Compile cache not sent", written)
        self.assertNotIn("abc123", written)

    def test_waiting_for_vllm_gives_up(self) -> None:
        server = self.serve()
        port = str(server.server_address[1])
        server.shutdown()
        server.server_close()
        with mock.patch.dict(os.environ, {"VLLM_PORT": port}):
            self.assertFalse(start.wait_for_vllm(timeout=0.3, interval=0.05))


class GpuComputeCapabilityTest(unittest.TestCase):
    def read(self, stdout: str) -> str | None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
        with mock.patch.object(start.subprocess, "run", return_value=completed) as run:
            capability = start.gpu_compute_capability()
        self.assertEqual(run.call_args.args[0][:2], ["nvidia-smi", "--query-gpu=compute_cap"])
        return capability

    def test_reads_the_capability(self) -> None:
        self.assertEqual(self.read("8.9\n"), "8.9")

    def test_takes_the_first_gpu(self) -> None:
        self.assertEqual(self.read("12.0\n12.0\n"), "12.0")

    def test_is_none_for_an_answer_that_is_not_a_capability(self) -> None:
        for stdout in ("", "[N/A]\n", "Field \"compute_cap\" is not a valid field to query.\n"):
            self.assertIsNone(self.read(stdout))

    def test_is_none_when_the_tool_is_missing_or_fails(self) -> None:
        for error in (FileNotFoundError(), subprocess.CalledProcessError(6, "nvidia-smi"), subprocess.TimeoutExpired("nvidia-smi", 30)):
            with mock.patch.object(start.subprocess, "run", side_effect=error):
                self.assertIsNone(start.gpu_compute_capability())


class GpuDriverVersionTest(unittest.TestCase):
    def read(self, stdout: str) -> str | None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
        with mock.patch.object(start.subprocess, "run", return_value=completed) as run:
            version = start.gpu_driver_version()
        self.assertEqual(run.call_args.args[0][:2], ["nvidia-smi", "--query-gpu=driver_version"])
        return version

    def test_reads_the_version_of_the_first_gpu(self) -> None:
        self.assertEqual(self.read("580.65.06\n580.65.06\n"), "580.65.06")

    def test_is_none_for_an_answer_that_is_not_a_version(self) -> None:
        for stdout in ("", "[N/A]\n", "NVIDIA-SMI has failed\n"):
            self.assertIsNone(self.read(stdout))


class KernelCacheFoldersTest(unittest.TestCase):
    """Triton's kernels and the kernels the NVIDIA driver compiles go to folders that are kept."""

    VARIABLES = {"TRITON_CACHE_DIR": "triton", "CUDA_CACHE_PATH": "nv_compute_cache"}

    def test_names_folders_next_to_vllms_caches(self) -> None:
        with mock.patch.dict(os.environ, {"VLLM_CACHE_ROOT": "/caches/vllm"}):
            for variable in self.VARIABLES:
                os.environ.pop(variable, None)
            start.keep_kernels_with_the_caches()
            for variable, folder in self.VARIABLES.items():
                self.assertEqual(os.environ[variable], os.path.join("/caches/vllm", folder))

    def test_replaces_folders_that_are_not_kept(self) -> None:
        elsewhere = {variable: "/tmp/elsewhere" for variable in self.VARIABLES}
        with mock.patch.dict(os.environ, {"VLLM_CACHE_ROOT": "/caches/vllm", **elsewhere}):
            start.keep_kernels_with_the_caches()
            for variable, folder in self.VARIABLES.items():
                self.assertEqual(os.environ[variable], os.path.join("/caches/vllm", folder))

    def test_every_folder_it_names_is_one_of_the_kept_cache_folders(self) -> None:
        with mock.patch.dict(os.environ, {"VLLM_CACHE_ROOT": "/caches/vllm"}):
            start.keep_kernels_with_the_caches()
            named = [os.environ[variable] for variable in self.VARIABLES]
        for folder in named:
            self.assertEqual(os.path.dirname(folder), "/caches/vllm")
            self.assertIn(os.path.basename(folder), start.CACHE_FOLDERS)

    def test_says_so_when_it_replaces_a_folder(self) -> None:
        with (
            mock.patch.dict(os.environ, {"VLLM_CACHE_ROOT": "/caches/vllm", "CUDA_CACHE_PATH": "/tmp/elsewhere"}),
            self.assertLogs(level=logging.INFO) as logs,
        ):
            start.keep_kernels_with_the_caches()
        written = " ".join(logs.output)
        self.assertIn("CUDA_CACHE_PATH", written)
        self.assertIn("/tmp/elsewhere", written)


class VllmCacheRootTest(unittest.TestCase):
    """The folder has to be the one vLLM works out for itself (vllm/envs.py)."""

    def root(self, variables: dict[str, str]) -> str:
        with mock.patch.dict(os.environ, variables):
            for name in ("VLLM_CACHE_ROOT", "XDG_CACHE_HOME"):
                if name not in variables:
                    os.environ.pop(name, None)
            return start.vllm_cache_root()

    def test_is_in_the_home_folder_by_default(self) -> None:
        self.assertEqual(self.root({}), os.path.join(os.path.expanduser("~"), ".cache", "vllm"))

    def test_follows_the_cache_home(self) -> None:
        self.assertEqual(self.root({"XDG_CACHE_HOME": "/caches"}), os.path.join("/caches", "vllm"))

    def test_takes_the_named_root(self) -> None:
        self.assertEqual(self.root({"VLLM_CACHE_ROOT": "/data/vllm", "XDG_CACHE_HOME": "/caches"}), "/data/vllm")

    def test_expands_the_home_folder_in_the_named_root(self) -> None:
        self.assertEqual(self.root({"VLLM_CACHE_ROOT": "~/vllm"}), os.path.expanduser("~") + "/vllm")


class BuiltInCompileCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.built_in = Path(folder.name, "built-in")
        graph = self.built_in / "8.9" / "torch_compile_cache" / "torch_aot_compile" / "abc" / "rank_0_0"
        graph.mkdir(parents=True)
        (graph / "model").write_bytes(b"compiled on 8.9")
        kernels = self.built_in / "12.0" / "deep_gemm" / "kernels"
        kernels.mkdir(parents=True)
        (kernels / "kernel.so").write_bytes(b"kernel for 12.0")
        (self.built_in / "12.0" / "torch_compile_cache").mkdir()
        (self.built_in / "12.0" / "torch_compile_cache" / "graph").write_bytes(b"compiled on 12.0")
        (self.built_in / "12.0" / "triton" / "KERNELHASH").mkdir(parents=True)
        (self.built_in / "12.0" / "triton" / "KERNELHASH" / "triton_fused.cubin").write_bytes(b"triton kernel for 12.0")
        (self.built_in / "12.0" / "nv_compute_cache").mkdir()
        (self.built_in / "12.0" / "nv_compute_cache" / "index").write_bytes(b"driver cache for 12.0")
        (self.built_in / "12.0" / "other").mkdir()
        self.cache_root = Path(folder.name, "vllm")
        self.target = self.cache_root / "torch_compile_cache"

    def use(self, capability: str | None) -> None:
        with (
            mock.patch.dict(os.environ, {"VLLM_CACHE_ROOT": str(self.cache_root)}),
            mock.patch.object(start, "BUILT_IN_COMPILE_CACHES", str(self.built_in)),
            mock.patch.object(start, "gpu_compute_capability", return_value=capability),
        ):
            start.use_built_in_compile_caches()

    def test_copies_the_cache_made_on_this_gpu_generation(self) -> None:
        self.use("8.9")
        self.assertEqual((self.target / "torch_aot_compile" / "abc" / "rank_0_0" / "model").read_bytes(), b"compiled on 8.9")

    def test_copies_every_cache_folder_of_the_generation_and_nothing_else(self) -> None:
        self.use("12.0")
        self.assertEqual((self.target / "graph").read_bytes(), b"compiled on 12.0")
        self.assertEqual((self.cache_root / "deep_gemm" / "kernels" / "kernel.so").read_bytes(), b"kernel for 12.0")
        self.assertEqual(
            (self.cache_root / "triton" / "KERNELHASH" / "triton_fused.cubin").read_bytes(), b"triton kernel for 12.0"
        )
        self.assertEqual((self.cache_root / "nv_compute_cache" / "index").read_bytes(), b"driver cache for 12.0")
        self.assertEqual(
            sorted(entry.name for entry in self.cache_root.iterdir()),
            ["deep_gemm", "nv_compute_cache", "torch_compile_cache", "triton"],
        )

    def test_says_which_driver_the_worker_has(self) -> None:
        # The driver's own cache only fits the driver version it was made with.
        with (
            mock.patch.object(start, "gpu_driver_version", return_value="580.65.06"),
            self.assertLogs(level=logging.INFO) as logs,
        ):
            self.use("12.0")
        self.assertIn("driver 580.65.06", " ".join(logs.output))

    def test_another_generation_gets_no_cache(self) -> None:
        self.use("8.6")
        self.assertFalse(self.cache_root.exists())

    def test_an_unidentified_gpu_gets_no_cache(self) -> None:
        self.use(None)
        self.assertFalse(self.target.exists())

    def test_a_cache_that_is_already_there_is_left_alone(self) -> None:
        self.target.mkdir(parents=True)
        (self.target / "own").write_text("from an earlier start")
        (self.cache_root / "triton").mkdir()
        (self.cache_root / "triton" / "own").write_text("from an earlier start")
        self.use("12.0")
        self.assertEqual(sorted(entry.name for entry in self.target.iterdir()), ["own"])
        self.assertEqual(sorted(entry.name for entry in (self.cache_root / "triton").iterdir()), ["own"])
        # A folder that was not there is still taken from the image.
        self.assertTrue((self.cache_root / "deep_gemm" / "kernels" / "kernel.so").is_file())


if __name__ == "__main__":
    unittest.main()
