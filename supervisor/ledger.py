"""Reactive quota ledger (spec section 4.1).

No platform exposes a queryable remaining-allowance API, so the ledger is
maintained reactively: routes start ``active``, get flagged ``exhausted`` /
``throttled`` when an attempt hits a rate-limit error, and auto-reactivate
once their ``resets_at`` timestamp has passed.
"""

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ACTIVE = "active"
EXHAUSTED = "exhausted"
THROTTLED = "throttled"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_in(minutes: int) -> str:
    return (_utcnow() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class QuotaLedger:
    def __init__(self, path: Path, routes: list[str]):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load(routes)

    def _load(self, routes: list[str]):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
            if isinstance(data, dict):
                self._data = {
                    k: v for k, v in data.items()
                    if isinstance(v, dict) and "status" in v
                }
        for route in routes:
            self._data.setdefault(route, {"status": ACTIVE, "resets_at": None})
        self._save()

    def _save(self):
        # Write-then-rename so a crash mid-write can't leave a truncated ledger.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def is_available(self, route: str) -> bool:
        with self._lock:
            entry = self._data.setdefault(route, {"status": ACTIVE, "resets_at": None})
            if entry["status"] == ACTIVE:
                return True
            resets_at = entry.get("resets_at")
            if resets_at:
                try:
                    reset_time = datetime.strptime(
                        resets_at, "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    return False
                if _utcnow() >= reset_time:
                    entry["status"] = ACTIVE
                    entry["resets_at"] = None
                    self._save()
                    return True
            return False

    def mark_limited(self, route: str, status: str, resets_at: str | None):
        with self._lock:
            self._data[route] = {"status": status, "resets_at": resets_at}
            self._save()

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return json.loads(json.dumps(self._data))
