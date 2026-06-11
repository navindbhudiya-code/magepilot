"""A tiny terminal spinner for long model calls. No-op when output isn't a TTY (pipes/tests)."""
import itertools
import sys
import threading
import time


class Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, msg: str = "thinking"):
        self.msg = msg
        self._stop = threading.Event()
        self._t = None

    def __enter__(self):
        if sys.stderr.isatty():
            self._t = threading.Thread(target=self._run, daemon=True)
            self._t.start()
        return self

    def _run(self):
        for c in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            sys.stderr.write(f"\r{c} {self.msg}… ")
            sys.stderr.flush()
            time.sleep(0.1)
        sys.stderr.write("\r" + " " * (len(self.msg) + 6) + "\r")
        sys.stderr.flush()

    def __exit__(self, *a):
        self._stop.set()
        if self._t:
            self._t.join(timeout=0.3)
