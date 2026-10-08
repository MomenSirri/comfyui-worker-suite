"""Cut the query string out of every URL in a stream of log output.

ComfyUI's provider nodes log the links they are given: the link to the result of
a finished task, the link an input was uploaded to. Those links are signed, and
whoever can read a worker's log could open the file behind one for as long as the
link is valid. start.sh passes ComfyUI's output through this filter, so the
location stays readable and the signature never reaches the log.

    python -u main.py ... > >(python -u /redact_log.py) 2>&1

The filter must outlive whatever it is given: if it stopped, ComfyUI would be
writing to a closed pipe. It decodes nothing and raises on nothing but the end
of its own output.
"""

import os
import re
import sys

# The same two patterns as _redact_url_queries in handler.py, on bytes.
# tests/test_redact_log.py holds the two to the same answers.
_ABSOLUTE_URL_QUERY = re.compile(
    rb"""\b((?:https?|wss?)://[^\s"<>?]{1,2048})\?[^\s"<>]*""", re.IGNORECASE
)
_NAMED_QUERY = re.compile(rb"""\?[^\s"<>=?]{1,256}=[^\s"<>]*""")

# A line end, or the carriage return that ends a progress line.
_LINE_END = re.compile(rb"[\r\n]")
# What ends a query string. A piece cut here cannot leave half of one behind.
_QUERY_END = re.compile(rb"""[\s"<>]""")

# Output without a line end is passed on once this much of it is held.
MAX_HELD_BYTES = 64 * 1024


def redact(data):
    """The bytes with the query string of every URL replaced by `?[redacted]`."""
    data = _ABSOLUTE_URL_QUERY.sub(rb"\1?[redacted]", data)
    return _NAMED_QUERY.sub(b"?[redacted]", data)


def _last(pattern, data):
    """Where the last match of a one-byte pattern ends, or 0 without one."""
    end = 0
    for match in pattern.finditer(data):
        end = match.end()
    return end


def split_ready(held):
    """
    Split held output into what can be passed on and what has to wait.

    Complete lines are ready. Output that has grown long without a line end is
    cut where a query string cannot continue, so that no piece starts in the
    middle of one; only output with no such place at all is passed on whole.
    """
    cut = _last(_LINE_END, held)
    if cut == 0 and len(held) >= MAX_HELD_BYTES:
        cut = _last(_QUERY_END, held) or len(held)
    return held[:cut], held[cut:]


def filter_stream(source, sink):
    """Copy a file descriptor to a binary file, redacted, until its end."""
    held = b""
    while True:
        chunk = os.read(source, 65536)
        if not chunk:
            break
        ready, held = split_ready(held + chunk)
        if ready:
            sink.write(redact(ready))
            sink.flush()
    if held:
        sink.write(redact(held))
        sink.flush()


if __name__ == "__main__":
    try:
        filter_stream(sys.stdin.fileno(), sys.stdout.buffer)
    except (BrokenPipeError, KeyboardInterrupt):
        pass
