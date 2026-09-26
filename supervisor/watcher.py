"""Background file-system change capture (spec section 5.1).

Polling-based (no third-party dependency): snapshot the project tree before an
engine runs, watch for mtime changes while it works, and report the touched
files so the supervisor can record them in the memory bank.
"""

import os
import threading
from pathlib import Path

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".switchboard", ".cursor", "dist", "build",
}
IGNORE_FILES = {"quota_ledger.json"}


class ChangeCapture:
    def __init__(self, root: Path, interval: float = 1.5):
        self.root = Path(root)
        self.interval = interval
        self.changes: set[str] = set()
        self._baseline: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _scan(self) -> dict[str, float]:
        snap: dict[str, float] = {}
        # os.walk lets us prune ignored dirs instead of descending into e.g.
        # node_modules on every poll.
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            for name in filenames:
                if name in IGNORE_FILES:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    snap[os.path.relpath(full, self.root)] = os.stat(full).st_mtime
                except OSError:
                    continue
        return snap

    def _diff(self):
        current = self._scan()
        for path, mtime in current.items():
            if self._baseline.get(path) != mtime:
                self.changes.add(path)
        for path in self._baseline:
            if path not in current:
                self.changes.add(f"{path} (deleted)")
        self._baseline = current

    def _watch(self):
        while not self._stop.wait(self.interval):
            self._diff()

    def __enter__(self):
        self._baseline = self._scan()
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 2)
        self._diff()
        return False
