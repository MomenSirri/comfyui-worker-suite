import os
import signal
import subprocess
import sys
import threading
import time
import unittest

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(SERVICE_DIR, "src"))
sys.path.append(os.path.abspath(os.path.join(SERVICE_DIR, "..", "..", "tests")))

import redact_log
from handler_contract import load_handler

handler = load_handler(SERVICE_DIR)

FILTER = os.path.join(SERVICE_DIR, "src", "redact_log.py")
SIGNED = "https://cdn.example.com/results/a.png?X-Tos-Signature=secret-signature&X-Tos-Expires=86400"
CUT = b"https://cdn.example.com/results/a.png?[redacted]"
POSIX = os.name == "posix"


class TestRedact(unittest.TestCase):
    def test_provider_node_lines_keep_the_location_and_lose_the_signature(self):
        # As ComfyUI writes them: its level tag is coloured, the message is not.
        lines = {
            "result": f"\x1b[32m[INFO]\x1b[0m ByteDance task succeeded, image URL: {SIGNED}\n",
            "upload": f"[INFO] Uploaded video to Comfy API. URL: {SIGNED}\r\n",
            "traceback": f"aiohttp.ClientResponseError: 403, message='Forbidden', url='{SIGNED}'\n",
        }

        for name, line in lines.items():
            with self.subTest(line=name):
                redacted = redact_log.redact(line.encode()).decode()

                self.assertIn("cdn.example.com/results/a.png?[redacted]", redacted)
                self.assertNotIn("secret-signature", redacted)
                self.assertEqual(redacted[-1], line[-1])

    def test_same_patterns_as_the_handler(self):
        # The handler cuts the same way in the messages it returns. If one of the
        # two is changed, the other has to follow.
        for name in ("_ABSOLUTE_URL_QUERY", "_NAMED_QUERY"):
            with self.subTest(pattern=name):
                ours, theirs = getattr(redact_log, name), getattr(handler, name)

                self.assertEqual(ours.pattern.decode(), theirs.pattern)
                self.assertEqual(ours.flags & ~32, theirs.flags & ~32)  # 32: re.UNICODE, for text only

    def test_same_answers_as_the_handler(self):
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
        data = b"\xff\xfe progress \x00\x80 done?\n"

        self.assertEqual(redact_log.redact(data), data)


class TestSplitReady(unittest.TestCase):
    def test_only_the_word_still_being_written_waits(self):
        ready, rest = redact_log.split_ready(b"first line\nsecond line, half a wo")

        self.assertEqual(ready, b"first line\nsecond line, half a ")
        self.assertEqual(rest, b"wo")

    def test_progress_line_shows_without_waiting_for_the_next_one(self):
        # A progress bar writes a carriage return and then its text.
        ready, rest = redact_log.split_ready(b"\r 50%|#####     | 5/10 [00:01<00:01]")

        self.assertEqual(ready, b"\r 50%|#####     | 5/10 [00:01<")
        self.assertEqual(rest, b"00:01]")

    def test_url_still_being_written_waits_whole(self):
        held = b"image URL: " + SIGNED.encode()[:60]

        self.assertEqual(redact_log.split_ready(held), (b"image URL: ", SIGNED.encode()[:60]))

    def test_long_run_with_nowhere_to_cut_is_passed_on_whole(self):
        # Otherwise one endless word would be held in memory for ever.
        run = b"A" * redact_log.MAX_HELD_BYTES

        self.assertEqual(redact_log.split_ready(run), (run, b""))
        self.assertEqual(redact_log.split_ready(b"start " + run), (b"start " + run, b""))
        self.assertEqual(redact_log.split_ready(run[:-1]), (b"", run[:-1]))


class TestRedactor(unittest.TestCase):
    STREAM = (
        b"got prompt\r\n"
        + f"\x1b[32m[INFO]\x1b[0m ByteDance task succeeded, image URL: {SIGNED}\n".encode()
        + b"\r 50%|#####     | 5/10\r100%|##########| 10/10\n"
        + b'{"url":"' + SIGNED.encode() + b'","ok":true}\n'
        + f"Max retries exceeded with url: /a.mp4?X-Amz-Signature=secret-signature (Caused by X)\n".encode()
        + f"last line without an end {SIGNED}".encode()
    )

    def test_however_the_stream_is_split_the_result_is_the_same(self):
        whole = redact_log.redact(self.STREAM)
        self.assertNotIn(b"secret-signature", whole)
        self.assertEqual(whole.count(CUT), 3)

        for position in range(len(self.STREAM) + 1):
            redactor = redact_log.Redactor()
            result = (
                redactor.feed(self.STREAM[:position])
                + redactor.feed(self.STREAM[position:])
                + redactor.finish()
            )
            self.assertEqual(result, whole, f"split at byte {position}")

    def test_byte_by_byte(self):
        redactor = redact_log.Redactor()
        result = b"".join(redactor.feed(bytes((byte,))) for byte in self.STREAM) + redactor.finish()

        self.assertEqual(result, redact_log.redact(self.STREAM))

    def test_nothing_is_held_back_at_the_end(self):
        redactor = redact_log.Redactor()

        self.assertEqual(redactor.feed(b"half a wo"), b"half a ")
        self.assertEqual(redactor.finish(), b"wo")
        self.assertEqual(redactor.finish(), b"")


class TestFilterProcess(unittest.TestCase):
    """The filter as start.sh runs it: a process between ComfyUI and the log."""

    def start(self, **options):
        process = subprocess.Popen(
            [sys.executable, "-u", FILTER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, **options
        )
        # A filter that stopped reading would block the test that writes to it.
        watchdog = threading.Timer(30, process.kill)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        return process

    def test_stream_comes_out_complete_and_without_a_signature(self):
        padding = b'{"note":"' + b"x" * (2 * redact_log.MAX_HELD_BYTES) + b'","url":"'
        data = (
            b"got prompt\n"
            + f"[INFO] task succeeded, image URL: {SIGNED}\n".encode()
            + padding + SIGNED.encode() + b'"}\n'
            + b"\xff\xfe not text\n"
            + f"last line without an end {SIGNED}".encode()
        )

        result = subprocess.run(
            [sys.executable, "-u", FILTER], input=data, capture_output=True, timeout=60
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(b"secret-signature", result.stdout)
        self.assertEqual(result.stdout, data.replace(SIGNED.encode(), CUT))

    def test_empty_stream_ends_quietly(self):
        result = subprocess.run([sys.executable, "-u", FILTER], input=b"", capture_output=True, timeout=60)

        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))

    @unittest.skipUnless(POSIX, "signals as a container sends them")
    def test_signals_meant_for_the_worker_do_not_end_it(self):
        # If the filter ended before ComfyUI, ComfyUI would write to a closed pipe.
        process = self.start()
        try:
            process.stdin.write(f"before {SIGNED}\n".encode())
            process.stdin.flush()
            self.assertEqual(process.stdout.readline(), b"before " + CUT + b"\n")

            for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT"):
                process.send_signal(getattr(signal, name))
            time.sleep(0.3)
            self.assertIsNone(process.poll(), "the filter ended on a signal")

            process.stdin.write(f"after {SIGNED}\n".encode())
            process.stdin.close()
            self.assertEqual(process.stdout.read(), b"after " + CUT + b"\n")
            self.assertEqual(process.wait(timeout=30), 0)
        finally:
            process.kill()
            process.stdout.close()

    @unittest.skipUnless(POSIX, "a closed pipe as a container has it")
    def test_keeps_reading_when_its_output_can_no_longer_be_written(self):
        process = self.start(stderr=subprocess.PIPE)
        try:
            process.stdout.close()
            # More than a pipe holds: a filter that stopped reading would block this.
            for _ in range(64):
                process.stdin.write(b"x" * 8192 + b"\n")
            process.stdin.flush()
            time.sleep(0.3)
            self.assertIsNone(process.poll(), "the filter ended when its output closed")

            process.stdin.close()
            self.assertEqual(process.wait(timeout=30), 0)
            self.assertEqual(process.stderr.read(), b"")
        finally:
            process.kill()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
