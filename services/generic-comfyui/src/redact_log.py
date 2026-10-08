"""Cut the query string out of every URL in a stream of log output.

ComfyUI's provider nodes log the links they are given: the link to the result of
a finished task, the link an input was uploaded to. Those links are signed, and
whoever can read a worker's log could open the file behind one for as long as the
link is valid. start.sh passes ComfyUI's output through this filter, so the
location stays readable and the signature never reaches the log.

    python -u main.py ... > >(python -u /redact_log.py --keep-running) 2>&1

ComfyUI writes into a pipe. With nothing reading it, every later write of ComfyUI
would fail, a flushed print and a progress bar inside a node among them, so two
processes stand on the pipe. The keeper, started with --keep-running, holds the
pipe and runs the filter; while a filter runs it reads nothing itself. When a
filter ends before its input does, what ComfyUI writes meanwhile waits in the
pipe, and the keeper says so in the log and starts the next filter. Only when
filters end at once, one after the other, does the keeper read the pipe, to drop
what is in it for a minute before it tries again. Both ignore the signals meant
for the worker, and a filter whose output can no longer be written still reads
its input to the end. It decodes nothing.
"""

import os
import re
import select
import signal
import subprocess
import sys
import time

# The keeper, and the filter it starts after another one ended.
KEEP_RUNNING = "--keep-running"
MID_STREAM = "--mid-stream"

# The same two patterns as _redact_url_queries in handler.py, on bytes.
# tests/test_redact_log.py holds the two to the same patterns and answers.
_ABSOLUTE_URL_QUERY = re.compile(
    rb"""\b((?:https?|wss?)://[^\s"<>?]{1,2048})\?[^\s"<>]*""", re.IGNORECASE
)
_NAMED_QUERY = re.compile(rb"""\?[^\s"<>=?]{1,256}=[^\s"<>]*""")

# The bytes neither pattern reads across: white space, a double quote, an angle
# bracket. Output cut after one of them cannot leave half a query string behind.
_QUERY_ENDS = b' \t\n\r\x0b\x0c"<>'
_QUERY_END = re.compile(b"[" + re.escape(_QUERY_ENDS) + b"]")

# A run without any of those bytes is passed on once it is this long.
MAX_HELD_BYTES = 64 * 1024

# A filter that ends within this many seconds of its start ended at once. After
# this many of those in a row the keeper drops the input for a while.
QUICK_END_SECONDS = 10
MAX_QUICK_ENDS = 3
# What the keeper waits after such an end before it tries again.
RETRY_PAUSE_SECONDS = 1
# How long it drops the input before the next filter.
DISCARD_SECONDS = 60

# The exit status of a filter that could not be started, as a shell gives it.
NOT_STARTED = 127


def redact(data):
    """The bytes with the query string of every URL replaced by `?[redacted]`."""
    # Most output holds no question mark at all, and the first pattern is slow
    # on text that is made of URL beginnings.
    if b"?" not in data:
        return data
    data = _ABSOLUTE_URL_QUERY.sub(rb"\1?[redacted]", data)
    return _NAMED_QUERY.sub(b"?[redacted]", data)


def split_ready(held):
    """
    Split held output into what can be passed on and what has to wait.

    Everything up to the last byte that ends a query string is ready, so only the
    word still being written waits, and a progress line shows without waiting for
    the next line. A run that has grown long with no such byte is passed on whole.
    """
    cut = max(held.rfind(bytes((end,))) for end in _QUERY_ENDS) + 1
    if len(held) - cut >= MAX_HELD_BYTES:
        cut = len(held)
    return held[:cut], held[cut:]


class Redactor:
    """Redacts a stream that arrives in pieces of any size."""

    def __init__(self, mid_stream=False):
        self._held = b""
        # A filter that takes over from one that ended may start inside a query
        # string, where nothing marks what follows as part of a link. It drops
        # the word it starts in.
        self._in_first_word = mid_stream

    def feed(self, chunk):
        """Take the next piece; return what is ready to be written."""
        if self._in_first_word:
            end = _QUERY_END.search(chunk)
            if end is None:
                return b""
            chunk, self._in_first_word = chunk[end.start() :], False
        ready, self._held = split_ready(self._held + chunk)
        return redact(ready)

    def finish(self):
        """Return what was still waiting when the stream ended."""
        held, self._held = self._held, b""
        return redact(held)


def _write_all(target, data):
    """Write every byte: one write to a pipe may take only a part of them."""
    view = memoryview(data)
    while view:
        view = view[os.write(target, view) :]


def main(source=0, target=1, mid_stream=False):
    """Pass the input on with its links cut; the exit status, 0 at the end of the input."""
    redactor = Redactor(mid_stream)
    writable = True

    def emit(data):
        nonlocal writable
        if data and writable:
            try:
                _write_all(target, data)
            except OSError:
                # Nothing reads the log any more. Keep reading, so that ComfyUI
                # can keep writing.
                writable = False

    status = 0
    while True:
        try:
            chunk = os.read(source, 65536)
        except OSError:
            # Not the end of the input, so the keeper starts another filter.
            status = 1
            break
        if not chunk:
            break
        emit(redactor.feed(chunk))
    emit(redactor.finish())
    return status


def run_filter(source, mid_stream):
    """Run one filter on the input and wait for it; its exit status."""
    command = [sys.executable, "-u", os.path.abspath(__file__)]
    if mid_stream:
        command.append(MID_STREAM)
    try:
        return subprocess.call(command, stdin=source)
    except Exception:
        # Whatever keeps a filter from starting, the keeper has to stay on the
        # pipe: it tells of the status and tries again.
        return NOT_STARTED


def _say(target, text):
    """Write a line of the worker's own into the log. The keeper works without it."""
    try:
        _write_all(target, f"worker-comfyui: {text}\n".encode())
    except OSError:
        pass


def discard_for(source, seconds):
    """Read the input and drop it for some seconds; False when the input ended meanwhile."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        try:
            readable, _, _ = select.select([source], [], [], remaining)
            if readable and not os.read(source, 65536):
                return False
        except OSError:
            return False


def keep_running(
    source=0, notices=2, run=run_filter, clock=time.monotonic, pause=time.sleep, discard=discard_for
):
    """
    Keep a filter on the input until the input ends; the exit status.

    The input stays open here while no filter runs, so ComfyUI waits with a write
    instead of failing with it. A filter that ended is replaced by one that knows
    it starts mid-stream. Filters that end at once, one after the other, cannot
    be kept running: the keeper then reads the input itself and drops it for a
    while, so that ComfyUI is not left waiting on a full pipe, and tries a filter
    again after that. Nothing unfiltered is ever passed on.
    """
    quick_ends = 0
    mid_stream = False
    while True:
        started = clock()
        status = run(source, mid_stream)
        if status == 0:
            return 0
        mid_stream = True
        quick_ends = quick_ends + 1 if clock() - started < QUICK_END_SECONDS else 0
        if quick_ends >= MAX_QUICK_ENDS:
            _say(
                notices,
                f"the log filter ended {quick_ends} times in a row, last with status {status}."
                f" ComfyUI keeps running; its own output is discarded for {DISCARD_SECONDS} seconds,"
                " then the filter is tried again.",
            )
            if not discard(source, DISCARD_SECONDS):
                return 0
            quick_ends = 0
            continue
        _say(
            notices,
            f"the log filter ended with status {status} and is started again."
            " ComfyUI's output around this line may be incomplete.",
        )
        if quick_ends:
            pause(RETRY_PAUSE_SECONDS)


def _ignore_worker_signals():
    """Leave the signals meant for the worker to the worker: this process ends with its input."""
    for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_IGN)


def run_command_line(arguments):
    """Do what the command line asks for; the exit status."""
    unknown = [argument for argument in arguments if argument not in (KEEP_RUNNING, MID_STREAM)]
    if unknown:
        _say(2, f"redact_log.py does not know {' '.join(unknown)}; it takes {KEEP_RUNNING} or {MID_STREAM}")
        return 2
    _ignore_worker_signals()
    if KEEP_RUNNING in arguments:
        return keep_running()
    return main(mid_stream=MID_STREAM in arguments)


if __name__ == "__main__":
    sys.exit(run_command_line(sys.argv[1:]))
