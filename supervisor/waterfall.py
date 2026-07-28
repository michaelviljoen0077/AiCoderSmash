"""Waterfall dispatcher + concurrency guardrails (spec sections 4 and 6).

For a given tier, tries each route in priority order:
  * skips routes flagged in the quota ledger (reactivating any past reset),
  * skips engines whose CLI/daemon is not installed/reachable,
  * on a rate-limit error, flags the route in the ledger and cascades,
  * on success, logs the action to the shared memory bank.

All code-writing work runs under a single mutex; tasks queue sequentially so
no two engines ever rewrite the project at the same time.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .delegator import Delegator
from .engines import BaseEngine, EngineResult, build_engines
from .errors import EngineError, EngineUnavailable, RateLimitError
from .ledger import QuotaLedger
from .memory import MemoryBank
from .settings import LEDGER_PATH, Settings
from .watcher import ChangeCapture


@dataclass
class DispatchReport:
    result: EngineResult | None
    tier: int
    tier_reason: str
    attempts: list[str] = field(default_factory=list)  # human-readable trail
    read_back: str | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None


class Switchboard:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.delegator = Delegator(settings)
        self.memory = MemoryBank(settings.claude_md)
        self.engines: dict[str, BaseEngine] = build_engines(settings)
        self.ledger = QuotaLedger(LEDGER_PATH, list(self.engines.keys()))
        # Spec 6.3: one writer at a time; everything else queues behind it.
        self.write_lock = threading.Lock()
        self._queue = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="switchboard"
        )

    # ---------------------------------------------------------------- public

    def submit(self, prompt: str, tier: int | None = None,
               route: str | None = None) -> DispatchReport:
        """Queue a task and block until it completes (sequential holding queue)."""
        return self.submit_async(prompt, tier, route).result()

    def submit_async(self, prompt: str, tier: int | None = None,
                     route: str | None = None):
        """Queue a task and return the Future (used by the web UI)."""
        return self._queue.submit(self._dispatch, prompt, tier, route)

    def status(self) -> list[tuple[str, bool, dict]]:
        """(route, installed/reachable, ledger entry) for every engine."""
        snap = self.ledger.snapshot()
        return [
            (route, engine.available(), snap.get(route, {}))
            for route, engine in self.engines.items()
        ]

    def shutdown(self):
        self._queue.shutdown(wait=True)

    # --------------------------------------------------------------- internal

    def _dispatch(self, prompt: str, tier: int | None,
                  forced_route: str | None) -> DispatchReport:
        if tier is None:
            tier, reason = self.delegator.classify(prompt)
        else:
            reason = "tier forced by user"
        report = DispatchReport(result=None, tier=tier, tier_reason=reason)

        routes = [forced_route] if forced_route else self.settings.routes_for_tier(tier)
        full_prompt = self.memory.inject(prompt)

        for route in routes:
            engine = self.engines.get(route)
            if engine is None:
                report.attempts.append(f"{route}: unknown route, skipped")
                continue
            if not self.ledger.is_available(route):
                entry = self.ledger.snapshot().get(route, {})
                report.attempts.append(
                    f"{route}: {entry.get('status')} until {entry.get('resets_at')}, skipped"
                )
                continue
            if not engine.available():
                report.attempts.append(f"{route}: not installed/reachable, skipped")
                continue

            try:
                result = self._run_guarded(engine, full_prompt, prompt, tier)
                report.result = result
                report.attempts.append(f"{route}: SUCCESS")
                if result.status == "ok":
                    report.read_back = self.delegator.read_back(prompt, result.output)
                return report
            except RateLimitError as e:
                self.ledger.mark_limited(route, e.status, e.resets_at)
                report.attempts.append(
                    f"{route}: {e.status} ({e.message}), flagged until {e.resets_at}"
                )
            except EngineUnavailable as e:
                report.attempts.append(f"{route}: unavailable ({e.message})")
            except EngineError as e:
                report.attempts.append(f"{route}: failed ({e.message})")

        return report

    def _run_guarded(self, engine: BaseEngine, full_prompt: str,
                     original_task: str, tier: int) -> EngineResult:
        """Run one engine under the write lock with change capture + logging."""
        self._prepare_rules_file(engine)
        with self.write_lock:
            if engine.writes_code:
                with ChangeCapture(
                    self.settings.project_root, self.settings.watcher_interval
                ) as capture:
                    result = engine.run(full_prompt, tier)
                result.changed_files = set(capture.changes)
            else:
                result = engine.run(full_prompt, tier)

            summary = result.output if result.status == "manual" else (
                result.output[:300] or "(no output)"
            )
            self.memory.log_action(
                engine.route, original_task, summary, result.changed_files
            )
        return result

    def _prepare_rules_file(self, engine: BaseEngine):
        """Spec 5.1: inject project state rules for Cursor before it runs."""
        rules_rel = engine.config.get("rules_file")
        if not rules_rel:
            return
        rules_path = self.settings.project_root / rules_rel
        rules_path.parent.mkdir(parents=True, exist_ok=True)
        rules_path.write_text(
            "---\ndescription: Supervisor-injected shared project state\n"
            "alwaysApply: true\n---\n\n" + self.memory.read(),
            encoding="utf-8",
        )
