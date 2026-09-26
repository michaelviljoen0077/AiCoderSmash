"""Worker engine adapters (spec sections 1, 5).

Three adapter families:
  * CLIEngine    — subprocess-driven tools (Claude Code, Copilot, Codex, Cursor)
  * OllamaEngine — models served through the local Ollama daemon, including
                   Ollama Cloud models (signed-in) and the emergency local model
  * ManualEngine — Antigravity: no headless API, so the supervisor prepares a
                   handoff brief and the file watcher captures the results

Every adapter enforces cwd = PROJECT_ROOT_DIR and raises RateLimitError when a
route hits its quota so the waterfall can cascade.
"""

import http.client
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .errors import EngineError, EngineUnavailable, RateLimitError
from .ledger import EXHAUSTED, THROTTLED, iso_in
from .settings import Settings

RATE_LIMIT_PATTERN = re.compile(
    r"rate.?limit|usage limit|quota|too many requests|\b429\b|limit reached"
    r"|out of (free )?(credits|messages|requests)|allowance",
    re.IGNORECASE,
)


_REACHABLE_TTL = 5.0
_reachable_cache: dict[str, tuple[float, bool]] = {}
_reachable_lock = threading.Lock()


def ollama_reachable(host: str) -> bool:
    """Whether the Ollama daemon answers, cached briefly.

    The dashboard polls status every few seconds and several engines share one
    daemon, so without a cache a down daemon costs a 3s timeout per check.
    """
    now = time.monotonic()
    with _reachable_lock:
        hit = _reachable_cache.get(host)
        if hit and now - hit[0] < _REACHABLE_TTL:
            return hit[1]
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3):
            ok = True
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        ok = False
    with _reachable_lock:
        _reachable_cache[host] = (time.monotonic(), ok)
    return ok


@dataclass
class EngineResult:
    route: str
    output: str
    status: str = "ok"  # "ok" | "manual"
    changed_files: set = field(default_factory=set)


class BaseEngine:
    def __init__(self, route: str, settings: Settings):
        self.route = route
        self.settings = settings
        self.config = settings.engine_config(route)
        self.writes_code = bool(self.config.get("writes_code", False))

    def available(self) -> bool:
        raise NotImplementedError

    def run(self, prompt: str, tier: int) -> EngineResult:
        raise NotImplementedError

    def _reset_estimate(self) -> str | None:
        minutes = int(self.config.get("default_reset_minutes", 60))
        return iso_in(minutes) if minutes > 0 else None


class CLIEngine(BaseEngine):
    def available(self) -> bool:
        template = self.config.get("command") or []
        return bool(template) and shutil.which(template[0]) is not None

    def _build_command(self, prompt: str, tier: int) -> list[str]:
        template = self.config.get("command") or []
        models_by_tier = self.config.get("models_by_tier") or {}
        model = models_by_tier.get(str(tier))

        args: list[str] = []
        skip_next = False
        for i, part in enumerate(template):
            if skip_next:
                skip_next = False
                continue
            if "{model}" in part:
                if model is None:
                    continue
                part = part.replace("{model}", model)
            # Drop a flag whose value placeholder has no model to fill.
            if (
                model is None
                and i + 1 < len(template)
                and "{model}" in template[i + 1]
            ):
                skip_next = True
                continue
            part = part.replace("{prompt}", prompt)
            args.append(part)

        # Resolve the executable so npm .cmd shims work on Windows.
        resolved = shutil.which(args[0])
        if resolved:
            args[0] = resolved
        return args

    def run(self, prompt: str, tier: int) -> EngineResult:
        if not self.available():
            raise EngineUnavailable(self.route, "CLI binary not found on PATH")
        cmd = self._build_command(prompt, tier)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.settings.project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.engine_timeout,
            )
        except subprocess.TimeoutExpired:
            raise EngineError(self.route, "engine timed out")
        except OSError as e:
            raise EngineUnavailable(self.route, f"failed to launch: {e}")

        combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        if proc.returncode != 0:
            if RATE_LIMIT_PATTERN.search(combined):
                status = EXHAUSTED if "usage limit" in combined.lower() else THROTTLED
                raise RateLimitError(
                    self.route,
                    f"rate/usage limit detected (exit {proc.returncode})",
                    resets_at=self._reset_estimate(),
                    status=status,
                ) from None
            raise EngineError(
                self.route,
                f"exit {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:400]}",
            )
        return EngineResult(self.route, (proc.stdout or "").strip())


class OllamaEngine(BaseEngine):
    def available(self) -> bool:
        return ollama_reachable(self.settings.ollama_host)

    def run(self, prompt: str, tier: int) -> EngineResult:
        if not self.available():
            raise EngineUnavailable(self.route, "Ollama daemon not reachable")
        model = self.config.get("model")
        if not model:
            raise EngineUnavailable(self.route, "no model configured")
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.settings.ollama_host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(
                req, timeout=self.settings.engine_timeout
            ) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:400]
            if e.code == 429 or RATE_LIMIT_PATTERN.search(detail):
                raise RateLimitError(
                    self.route,
                    f"HTTP {e.code}: {detail}",
                    resets_at=self._reset_estimate(),
                ) from None
            raise EngineError(self.route, f"HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, OSError, TimeoutError,
                http.client.HTTPException) as e:
            raise EngineError(self.route, f"request failed: {e}") from None
        except ValueError as e:
            raise EngineError(self.route, f"invalid response: {e}") from None

        message = body.get("message") if isinstance(body, dict) else None
        content = ((message or {}).get("content") or "").strip()
        if not content:
            raise EngineError(self.route, f"empty response from {model}")
        return EngineResult(self.route, content)


class ManualEngine(BaseEngine):
    """Antigravity: prepare a handoff brief; results are captured by the watcher."""

    def available(self) -> bool:
        return True

    def run(self, prompt: str, tier: int) -> EngineResult:
        handoff_dir = self.settings.project_root / self.config.get(
            "handoff_dir", ".switchboard/handoff"
        )
        handoff_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        brief = handoff_dir / f"antigravity-{stamp}.md"
        brief.write_text(
            f"# Antigravity Handoff Brief ({stamp})\n\n"
            f"Tier: {tier}\n\n"
            f"## Task\n\n{prompt}\n",
            encoding="utf-8",
        )
        return EngineResult(
            self.route,
            f"Handoff brief written to {brief}. Open this project in Antigravity "
            "and run the brief; the supervisor's file watcher will capture the "
            "resulting changes into the memory bank.",
            status="manual",
        )


_TYPES = {"cli": CLIEngine, "ollama": OllamaEngine, "manual": ManualEngine}


def build_engines(settings: Settings) -> dict[str, BaseEngine]:
    engines: dict[str, BaseEngine] = {}
    for route, cfg in settings.engines.items():
        cls = _TYPES.get(cfg.get("type", "cli"), CLIEngine)
        engines[route] = cls(route, settings)
    return engines
