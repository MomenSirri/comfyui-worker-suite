import os
import subprocess
import sys
import unittest

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(SERVICE_DIR, "src"))
sys.path.append(os.path.abspath(os.path.join(SERVICE_DIR, "..", "..", "tests")))

import redact_log
from handler_contract import load_handler

handler = load_handler(SERVICE_DIR)

SIGNED = "https://cdn.example.com/results/a.png?X-Tos-Signature=secret-signature&X-Tos-Expires=86400"


class TestRedact(unittest.TestCase):
    def test_provider_node_lines_keep_the_location_and_lose_the_signature(self):
        # As ComfyUI's own nodes write them, colour codes included.
        lines = {
            "result": f"\x1b[32m[INFO]\x1b[0m ByteDance task succeeded, image URL: {SIGNED}\n",
            "upload": f"[INFO] Uploaded video to Comfy API. URL: {SIGNED}\n",
            "traceback": f"aiohttp.ClientResponseError: 403, message='Forbidden', url='{SIGNED}'\n",
        }

        for name, line in lines.items():
            with self.subTest(line=name):
                redacted = redact_log.redact(line.encode()).decode()

                self.assertIn("cdn.example.com/results/a.png?[redacted]", redacted)
                self.assertNotIn("secret-signature", redacted)
                self.assertTrue(redacted.endswith("\n"))

    def test_same_answers_as_the_handler(self):
        # The handler cuts the same way in the messages it returns.
        samples = (
            f"403, message='Forbidden', url='{SIGNED}'",
            f"403 Client Error: Forbidden for url: {SIGNED}",
            "Max retries exceeded with url: /a.mp4?X-Amz-Signature=secret-signature (Caused by Timeout)",
            '{"url":"https://cdn.example.com/a.mp4?sig=secret-signature","error":"quota exceeded"}',
            "404, message='Not Found', url='https://cdn.example.com/a.mp4?secret-signature'",
            "Value not in list: ckpt_name: 'a.safetensors' not in ['b']. Did you mean b? Set x=1.",
            "Pattern (?=abc) did not match, see https://docs.example.com/errors#e1203",
            "got prompt",
            "",
        )

        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(
                    redact_log.redact(sample.encode()).decode(),
                    handler._redact_url_queries(sample),
                )

    def test_bytes_that_are_not_text_pass_through(self):
        data = b"\xff\xfe progress \x00\x80 done\n"

        self.assertEqual(redact_log.redact(data), data)


class TestSplitReady(unittest.TestCase):
    def test_complete_lines_are_ready_and_the_rest_waits(self):
        ready, rest = redact_log.split_ready(b"first\nsecond\nthird without an end")

        self.assertEqual(ready, b"first\nsecond\n")
        self.assertEqual(rest, b"third without an end")

    def test_progress_line_is_ready_at_its_carriage_return(self):
        # A progress bar rewrites its line; it must not wait for the next real line.
        ready, rest = redact_log.split_ready(b" 50%|#####     | 5/10\r 60%|###")

        self.assertEqual(ready, b" 50%|#####     | 5/10\r")
        self.assertEqual(rest, b" 60%|###")

    def test_short_output_without_a_line_end_waits(self):
        self.assertEqual(redact_log.split_ready(b"half a line"), (b"", b"half a line"))

    def test_long_output_without_a_line_end_is_not_cut_inside_a_query_string(self):
        # One long line of compact JSON: the piece must end where a query string
        # cannot continue, or the rest of the signature would follow unredacted.
        padding = b'{"note":"' + b"x" * redact_log.MAX_HELD_BYTES + b'","url":"'
        held = padding + SIGNED.encode()[:60]

        ready, rest = redact_log.split_ready(held)

        self.assertEqual(ready, padding)
        self.assertEqual(rest, SIGNED.encode()[:60])

    def test_long_output_with_nowhere_to_cut_is_passed_on_whole(self):
        held = b"A" * (redact_log.MAX_HELD_BYTES + 10)

        self.assertEqual(redact_log.split_ready(held), (held, b""))


class TestFilterProcess(unittest.TestCase):
    """The filter as start.sh runs it: a process between ComfyUI and the log."""

    def run_filter(self, data):
        return subprocess.run(
            [sys.executable, "-u", os.path.join(SERVICE_DIR, "src", "redact_log.py")],
            input=data,
            capture_output=True,
            timeout=60,
        )

    def test_stream_comes_out_complete_and_without_a_signature(self):
        padding = b'{"note":"' + b"x" * (2 * redact_log.MAX_HELD_BYTES) + b'","url":"'
        data = (
            b"got prompt\n"
            + f"[INFO] task succeeded, image URL: {SIGNED}\n".encode()
            + b" 50%|#####     | 5/10\r100%|##########| 10/10\n"
            + padding + SIGNED.encode() + b'"}\n'
            + b"\xff\xfe not text\n"
            + f"last line without an end {SIGNED}".encode()
        )

        result = self.run_filter(data)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(b"secret-signature", result.stdout)
        self.assertEqual(result.stdout.count(b"a.png?[redacted]"), 3)
        expected = data.replace(SIGNED.encode(), b"https://cdn.example.com/results/a.png?[redacted]")
        self.assertEqual(result.stdout, expected)

    def test_empty_stream_ends_quietly(self):
        result = self.run_filter(b"")

        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))


if __name__ == "__main__":
    unittest.main()
