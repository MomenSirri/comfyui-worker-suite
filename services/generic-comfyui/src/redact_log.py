"""Cut the query string out of every URL in a stream of log output.

ComfyUI's provider nodes log the links they are given: the link to the result of
a finished task, the link an input was uploaded to. Those links are signed, and
whoever can read a worker's log could open the file behind one for as long as the
link is valid. start.sh passes ComfyUI's output through this filter, so the
location stays readable and the signature never reaches the log.

    python -u main.py ... > >(python -u /redact_log.py) 2>&1

ComfyUI writes into this process. If it ended first, every later write of
ComfyUI would fail, so nothing short of the end of its input ends it: signals
meant for the worker are ignored here, and when the output can no longer be
written the input is still read to its end. It decodes nothing.
"""

import os
import re
import signal

# The same two patterns as _redact_url_queries in handler.py, on bytes.
# tests/test_redact_log.py holds the two to the same patterns and answers.
_ABSOLUTE_URL_QUERY = re.compile(
    rb"""\b((?:https?|wss?)://[^\s"<>?]{1,2048})\?[^\s"<>]*""", re.IGNORECASE
)
_NAMED_QUERY = re.compile(rb"""\?[^\s"<>=?]{1,256}=[^\s"<>]*""")

# The bytes neither pattern reads across: white space, a double quote, an angle
# bracket. Output cut after one of them cannot leave half a query string behind.
_QUERY_ENDS = b' \t\n\r\x0b\x0c"<>'

# A run without any of those bytes is passed on once it is this long.
MAX_HELD_BYTES = 64 * 1024


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

    def __init__(self):
        self._held = b""

    def feed(self, chunk):
        """Take the next piece; return what is ready to be written."""
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


def main(source=0, target=1):
    for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_IGN)

    redactor = Redactor()
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

    while True:
        try:
            chunk = os.read(source, 65536)
        except OSError:
            break
        if not chunk:
            break
        emit(redactor.feed(chunk))
    emit(redactor.finish())


if __name__ == "__main__":
    main()
