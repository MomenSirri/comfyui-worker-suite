import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

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
# Where the processes under another one can be listed.
PROC = POSIX and os.path.isdir("/proc/self")


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

    def test_started_mid_stream_drops_the_word_it_starts_in(self):
        # A filter that takes over from one that ended may start inside a query
        # string, where nothing marks what follows as part of a link.
        redactor = redact_log.Redactor(mid_stream=True)

        result = redactor.feed(f"ture=secret-signature&X-Tos-Expires=86400' next {SIGNED}\n".encode())

        self.assertEqual(result + redactor.finish(), b" next " + CUT + b"\n")

    def test_word_a_mid_stream_filter_starts_in_may_arrive_in_pieces(self):
        redactor = redact_log.Redactor(mid_stream=True)

        self.assertEqual(redactor.feed(b"ture=secret-"), b"")
        self.assertEqual(redactor.feed(b"signature"), b"")
        self.assertEqual(redactor.feed(b"&more=1\nnext line\n"), b"\nnext line\n")
        self.assertEqual(redactor.feed(b"a=1 b\n"), b"a=1 b\n")

    def test_mid_stream_word_that_never_ends_is_dropped_whole(self):
        redactor = redact_log.Redactor(mid_stream=True)

        self.assertEqual(redactor.feed(b"ture=secret-signature"), b"")
        self.assertEqual(redactor.finish(), b"")

    def test_wherever_a_mid_stream_filter_starts_nothing_of_a_query_string_comes_out(self):
        for position in range(len(self.STREAM) + 1):
            rest = self.STREAM[position:]
            redactor = redact_log.Redactor(mid_stream=True)

            result = redactor.feed(rest) + redactor.finish()

            ends = [rest.find(bytes((end,))) for end in b' \t\n\r\x0b\x0c"<>']
            first_end = min((end for end in ends if end >= 0), default=len(rest))
            self.assertEqual(result, redact_log.redact(rest[first_end:]), f"started at byte {position}")
            for part in (b"secret-signature", b"X-Tos-Expires", b"86400"):
                self.assertNotIn(part, result, f"started at byte {position}")


class TestMain(unittest.TestCase):
    """The filter's exit status: the keeper reads 0 as the end of ComfyUI's output."""

    def setUp(self):
        self.source, self.source_end = os.pipe()
        self.target, self.target_end = os.pipe()
        self.open_ends = {self.source, self.source_end, self.target, self.target_end}
        self.addCleanup(lambda: [os.close(end) for end in self.open_ends])

    def close(self, end):
        self.open_ends.remove(end)
        os.close(end)

    def test_status_is_0_at_the_end_of_the_input(self):
        os.write(self.source_end, f"image URL: {SIGNED}\n".encode())
        self.close(self.source_end)

        status = redact_log.main(source=self.source, target=self.target_end)

        self.close(self.target_end)
        self.assertEqual((status, os.read(self.target, 65536)), (0, b"image URL: " + CUT + b"\n"))

    def test_status_is_not_0_when_the_input_cannot_be_read(self):
        # Taken for the end of the input, it would end the keeper while ComfyUI still writes.
        # Nothing can be read from the end of a pipe that is for writing.
        self.assertEqual(redact_log.main(source=self.source_end, target=self.target_end), 1)


class TestRunFilter(unittest.TestCase):
    def test_filter_runs_on_the_keeper_s_input_and_is_told_when_it_starts_mid_stream(self):
        with mock.patch.object(redact_log.subprocess, "call", return_value=0) as call:
            statuses = redact_log.run_filter(7, False), redact_log.run_filter(7, True)

        first, second = call.call_args_list
        self.assertEqual(statuses, (0, 0))
        self.assertEqual(first.args[0], [sys.executable, "-u", FILTER])
        self.assertEqual(second.args[0], [sys.executable, "-u", FILTER, "--mid-stream"])
        self.assertEqual((first.kwargs, second.kwargs), ({"stdin": 7}, {"stdin": 7}))

    def test_filter_that_cannot_be_started_is_one_that_ended(self):
        # Whatever keeps a filter from starting, the keeper has to stay on the pipe.
        for error in (OSError("no more processes"), TypeError("no interpreter")):
            with self.subTest(error=error):
                with mock.patch.object(redact_log.subprocess, "call", side_effect=error):
                    self.assertEqual(redact_log.run_filter(7, False), redact_log.NOT_STARTED)


@unittest.skipUnless(POSIX, "a pipe can be waited for")
class TestDiscardFor(unittest.TestCase):
    def test_drops_what_is_written_and_says_the_input_is_still_open(self):
        source, source_end = os.pipe()
        self.addCleanup(os.close, source)
        self.addCleanup(os.close, source_end)
        os.write(source_end, b"written while no filter runs\n")
        started = time.monotonic()

        still_open = redact_log.discard_for(source, 0.3)

        self.assertTrue(still_open)
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        os.write(source_end, b"next")
        self.assertEqual(os.read(source, 65536), b"next")

    def test_says_when_the_input_ended(self):
        source, source_end = os.pipe()
        self.addCleanup(os.close, source)
        os.write(source_end, b"the last of ComfyUI's output\n")
        os.close(source_end)
        started = time.monotonic()

        still_open = redact_log.discard_for(source, 30)

        self.assertFalse(still_open)
        self.assertLess(time.monotonic() - started, 10)


class TestKeepRunning(unittest.TestCase):
    """The keeper's decisions, with the filters it runs replaced by how each one ends."""

    def keep_running(self, runs, input_ends_while_discarded=False, notices_writable=True):
        """Run the keeper over filters that each last some seconds and end with a status."""
        source, source_end = os.pipe()
        notices, notices_end = os.pipe()
        os.close(source_end)
        self.addCleanup(os.close, source)
        self.addCleanup(os.close, notices)
        runs = list(runs)
        now = [0.0]
        self.started = []
        self.pauses = []
        self.discards = []

        def run(run_source, mid_stream):
            self.assertEqual(run_source, source)
            self.started.append(mid_stream)
            seconds, status = runs.pop(0)
            now[0] += seconds
            return status

        def discard(discard_source, seconds):
            self.assertEqual(discard_source, source)
            self.discards.append(seconds)
            now[0] += seconds
            return not input_ends_while_discarded

        result = redact_log.keep_running(
            source=source,
            # Nothing can be written to the end of a pipe that is for reading.
            notices=notices_end if notices_writable else notices,
            run=run,
            clock=lambda: now[0],
            pause=self.pauses.append,
            discard=discard,
        )

        os.close(notices_end)
        self.notices = os.read(notices, 65536).decode().splitlines()
        self.assertEqual(runs, [], "the keeper stopped before the last filter")
        return result

    def test_ends_when_the_filter_reaches_the_end_of_its_input(self):
        result = self.keep_running([(5, 0)])

        self.assertEqual((result, self.started, self.notices, self.pauses), (0, [False], [], []))

    def test_filter_that_ended_is_started_again_mid_stream_and_the_log_says_so(self):
        result = self.keep_running([(3600, -9), (5, 0)])

        self.assertEqual((result, self.started, self.pauses), (0, [False, True], []))
        self.assertEqual(len(self.notices), 1)
        self.assertTrue(self.notices[0].startswith("worker-comfyui: the log filter ended with status -9"))
        self.assertIn("started again", self.notices[0])

    def test_filter_that_keeps_ending_at_once_is_left_out_for_a_while_and_then_tried_again(self):
        # A keeper that kept trying at once would leave ComfyUI waiting on a full
        # pipe; one that never tried again would cost the worker its log for good.
        limit = redact_log.MAX_QUICK_ENDS

        result = self.keep_running([(0, 1)] * limit + [(5, 0)])

        self.assertEqual((result, self.started), (0, [False] + [True] * limit))
        self.assertEqual(self.discards, [redact_log.DISCARD_SECONDS])
        self.assertEqual(len(self.notices), limit)
        self.assertIn(f"ended {limit} times in a row, last with status 1", self.notices[-1])
        self.assertIn("ComfyUI keeps running", self.notices[-1])
        self.assertIn(f"discarded for {redact_log.DISCARD_SECONDS} seconds", self.notices[-1])
        self.assertIn("tried again", self.notices[-1])
        # It waits before another try at once, and not before leaving the filter out.
        self.assertEqual(len(self.pauses), limit - 1)

    def test_quick_ends_are_counted_afresh_after_the_filter_was_left_out(self):
        limit = redact_log.MAX_QUICK_ENDS

        result = self.keep_running([(0, 1)] * (2 * limit) + [(5, 0)])

        self.assertEqual((result, len(self.started)), (0, 2 * limit + 1))
        self.assertEqual(self.discards, [redact_log.DISCARD_SECONDS] * 2)
        self.assertEqual(len(self.notices), 2 * limit)

    def test_ends_when_the_input_ends_while_it_is_discarded(self):
        limit = redact_log.MAX_QUICK_ENDS

        result = self.keep_running([(0, 1)] * limit, input_ends_while_discarded=True)

        self.assertEqual((result, len(self.started), self.discards), (0, limit, [redact_log.DISCARD_SECONDS]))

    def test_ends_far_apart_are_never_given_up(self):
        ends = 4 * redact_log.MAX_QUICK_ENDS

        result = self.keep_running([(redact_log.QUICK_END_SECONDS, -9)] * ends + [(5, 0)])

        self.assertEqual((result, len(self.started), len(self.notices), self.pauses), (0, ends + 1, ends, []))

    def test_quick_ends_count_only_in_a_row(self):
        almost = [(0, 1)] * (redact_log.MAX_QUICK_ENDS - 1)

        result = self.keep_running(almost + [(3600, 1)] + almost + [(5, 0)])

        self.assertEqual(result, 0)

    def test_notice_that_cannot_be_written_does_not_end_the_keeper(self):
        result = self.keep_running([(3600, -9), (5, 0)], notices_writable=False)

        self.assertEqual((result, self.started, self.notices), (0, [False, True], []))


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

    def test_filter_started_mid_stream_drops_the_word_it_starts_in(self):
        result = subprocess.run(
            [sys.executable, "-u", FILTER, "--mid-stream"],
            input=f"ture=secret-signature&more=1 next {SIGNED}\n".encode(),
            capture_output=True,
            timeout=60,
        )

        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b" next " + CUT + b"\n", b""))

    def test_option_it_does_not_know_is_refused(self):
        # Passing the stream on under a mistyped option would look like a filter that runs as asked.
        result = subprocess.run(
            [sys.executable, "-u", FILTER, "--keep-runing"],
            input=f"{SIGNED}\n".encode(),
            capture_output=True,
            timeout=60,
        )

        self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.assertIn(b"--keep-runing", result.stderr)

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


class TestKeeperProcess(unittest.TestCase):
    """The keeper as start.sh runs it: `redact_log.py --keep-running` on ComfyUI's output."""

    COMMAND = [sys.executable, "-u", FILTER, "--keep-running"]

    def start(self):
        # Its own session, so that the filter under it can be ended together with it.
        keeper = subprocess.Popen(
            self.COMMAND,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        def stop():
            try:
                os.killpg(keeper.pid, signal.SIGKILL)
            except OSError:
                pass
            keeper.wait()
            for stream in (keeper.stdin, keeper.stdout, keeper.stderr):
                stream.close()

        # A keeper that stopped passing output on would block the test that reads it.
        watchdog = threading.Timer(30, stop)
        watchdog.start()
        self.addCleanup(stop)
        self.addCleanup(watchdog.cancel)
        return keeper

    @staticmethod
    def state_and_parent(pid):
        """The state letter and the parent of a process as /proc has them, or None when it is gone."""
        try:
            with open(f"/proc/{pid}/stat") as stat:
                state, parent = stat.read().rsplit(")", 1)[1].split()[:2]
        except (OSError, ValueError):
            return None
        return state, int(parent)

    def wait_until(self, reached, what):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            found = reached()
            if found:
                return found
            time.sleep(0.02)
        self.fail(what)

    def filter_under(self, keeper, other_than=None):
        """The PID of the filter the keeper runs, waiting for it to be there."""

        def running():
            for name in os.listdir("/proc"):
                if name.isdigit() and int(name) != other_than:
                    if self.state_and_parent(name) in (("S", keeper.pid), ("R", keeper.pid)):
                        return int(name)

        return self.wait_until(running, "no filter runs under the keeper")

    def wait_for_state(self, pid, state):
        self.wait_until(
            lambda: (self.state_and_parent(pid) or ("",))[0] == state, f"process {pid} did not reach state {state}"
        )

    def test_stream_comes_out_as_from_the_filter_alone(self):
        data = (
            b"got prompt\n"
            + f"[INFO] task succeeded, image URL: {SIGNED}\n".encode()
            + b"\xff\xfe not text\n"
            + f"last line without an end {SIGNED}".encode()
        )

        result = subprocess.run(self.COMMAND, input=data, capture_output=True, timeout=60)

        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, data.replace(SIGNED.encode(), CUT), b"")
        )

    @unittest.skipUnless(PROC, "the filter under the keeper is found in /proc")
    def test_killed_filter_is_replaced_and_the_log_says_so(self):
        keeper = self.start()
        keeper.stdin.write(f"before {SIGNED}\n".encode())
        keeper.stdin.flush()
        self.assertEqual(keeper.stdout.readline(), b"before " + CUT + b"\n")

        killed = self.filter_under(keeper)
        os.kill(killed, signal.SIGKILL)
        # Fails after a while when no other filter takes the place of the killed one.
        self.filter_under(keeper, other_than=killed)
        self.assertIsNone(keeper.poll(), "the keeper ended with its filter")

        # The filter that took over cannot know whether it starts inside a link.
        keeper.stdin.write(f"ture=secret-signature&more=1 after {SIGNED}\n".encode())
        keeper.stdin.close()
        self.assertEqual(keeper.stdout.read(), b" after " + CUT + b"\n")
        self.assertEqual(keeper.wait(timeout=30), 0)
        notices = keeper.stderr.read().decode().splitlines()
        self.assertEqual(len(notices), 1, notices)
        self.assertIn("the log filter ended with status -9", notices[0])
        self.assertIn("started again", notices[0])

    @unittest.skipUnless(PROC, "the filter under the keeper is found in /proc")
    def test_output_written_while_no_filter_runs_waits_for_the_next_one(self):
        # Without the keeper the pipe would have no reader, and this write would
        # fail with a broken pipe, as ComfyUI's did. The keeper is held still for
        # the write, so that it is certain no filter runs then.
        keeper = self.start()
        killed = self.filter_under(keeper)
        keeper.send_signal(signal.SIGSTOP)
        self.wait_for_state(keeper.pid, "T")
        os.kill(killed, signal.SIGKILL)
        self.wait_for_state(killed, "Z")

        keeper.stdin.write(f"\nwritten with no filter running {SIGNED}\n".encode())
        keeper.stdin.close()
        keeper.send_signal(signal.SIGCONT)

        self.assertEqual(keeper.stdout.read(), b"\nwritten with no filter running " + CUT + b"\n")
        self.assertEqual(keeper.wait(timeout=30), 0)

    @unittest.skipUnless(POSIX, "signals as a container sends them")
    def test_signals_meant_for_the_worker_do_not_end_the_keeper(self):
        keeper = self.start()
        keeper.stdin.write(f"before {SIGNED}\n".encode())
        keeper.stdin.flush()
        self.assertEqual(keeper.stdout.readline(), b"before " + CUT + b"\n")

        for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT"):
            keeper.send_signal(getattr(signal, name))
        time.sleep(0.3)
        self.assertIsNone(keeper.poll(), "the keeper ended on a signal")

        keeper.stdin.write(f"after {SIGNED}\n".encode())
        keeper.stdin.close()
        self.assertEqual(keeper.stdout.read(), b"after " + CUT + b"\n")
        self.assertEqual(keeper.wait(timeout=30), 0)
        self.assertEqual(keeper.stderr.read(), b"")


if __name__ == "__main__":
    unittest.main()
