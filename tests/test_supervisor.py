"""Unit tests for the switchboard. Standard library only, no engines needed."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from supervisor.delegator import Delegator
from supervisor.engines import BaseEngine, CLIEngine, EngineResult
from supervisor.errors import RateLimitError
from supervisor.ledger import ACTIVE, THROTTLED, QuotaLedger, iso_in
from supervisor.memory import MemoryBank, _trim_action_log
from supervisor.settings import Settings
from supervisor.watcher import ChangeCapture
from supervisor.waterfall import Switchboard


class HeuristicTests(unittest.TestCase):
    def tier(self, prompt):
        return Delegator._heuristic(prompt)[0]

    def test_substrings_do_not_trigger_tier1(self):
        self.assertEqual(self.tier(
            "Fix the bug where designated users cannot log in to the dashboard page"), 2)
        self.assertEqual(self.tier(
            "Write an explanation of how the login flow works in this app please"), 2)

    def test_short_architectural_task_is_tier1(self):
        self.assertEqual(self.tier("design the api"), 1)

    def test_surgical_keywords_are_tier3(self):
        self.assertEqual(self.tier("add docstrings to every public function in utils.py"), 3)

    def test_short_generic_task_is_tier3(self):
        self.assertEqual(self.tier("bump the version"), 3)


class ClassifyParsingTests(unittest.TestCase):
    def test_think_block_is_ignored(self):
        d = Delegator(Settings(project_root=tempfile.mkdtemp()))
        body = {"message": {"content":
                '<think>maybe {"tier": 3}?</think>{"tier": 1, "reason": "big"}'}}
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = json.dumps(body).encode()
        with mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(d.classify("anything"), (1, "big"))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.json"

    def test_reactivates_after_reset(self):
        ledger = QuotaLedger(self.path, ["a"])
        ledger.mark_limited("a", THROTTLED, "2000-01-01T00:00:00Z")
        self.assertTrue(ledger.is_available("a"))
        self.assertEqual(ledger.snapshot()["a"]["status"], ACTIVE)

    def test_stays_limited_before_reset(self):
        ledger = QuotaLedger(self.path, ["a"])
        ledger.mark_limited("a", THROTTLED, iso_in(60))
        self.assertFalse(ledger.is_available("a"))

    def test_tolerates_garbage_file(self):
        self.path.write_text('["not", "a", "dict"]', encoding="utf-8")
        ledger = QuotaLedger(self.path, ["a"])
        self.assertTrue(ledger.is_available("a"))


class MemoryTests(unittest.TestCase):
    def test_inject_keeps_only_recent_entries(self):
        bank = MemoryBank(Path(tempfile.mkdtemp()) / "CLAUDE.md")
        for i in range(30):
            bank.log_action("r", f"task {i}", "done", {"f.py"})
        injected = bank.inject("do it")
        self.assertIn("task 29", injected)
        self.assertNotIn("task 9 ", injected)
        self.assertIn("10 older entries omitted", injected)
        self.assertTrue(injected.endswith("TASK:\ndo it"))

    def test_trim_is_noop_when_short(self):
        text = "# X\n\n## Action Log\n\n- one\n"
        self.assertEqual(_trim_action_log(text, 20), text)


class WatcherTests(unittest.TestCase):
    def test_detects_changes_and_skips_ignored_dirs(self):
        root = Path(tempfile.mkdtemp())
        (root / "keep.txt").write_text("a")
        (root / "gone.txt").write_text("a")
        (root / "node_modules").mkdir()
        with ChangeCapture(root, interval=0.05) as cap:
            time.sleep(0.02)
            (root / "new.py").write_text("x")
            (root / "gone.txt").unlink()
            (root / "node_modules" / "dep.js").write_text("x")
        self.assertIn("new.py", cap.changes)
        self.assertIn("gone.txt (deleted)", cap.changes)
        self.assertNotIn(str(Path("node_modules") / "dep.js"), cap.changes)
        self.assertNotIn("keep.txt", cap.changes)


class CommandTemplateTests(unittest.TestCase):
    def make(self, cfg):
        settings = Settings(project_root=tempfile.mkdtemp())
        settings.engines = {"x": cfg}
        return CLIEngine("x", settings)

    def test_model_flag_dropped_without_model(self):
        eng = self.make({"command": ["tool", "-p", "{prompt}", "--model", "{model}"]})
        self.assertEqual(eng._build_command("hi", 2)[1:], ["-p", "hi"])

    def test_model_filled_by_tier(self):
        eng = self.make({"command": ["tool", "--model", "{model}", "{prompt}"],
                         "models_by_tier": {"1": "big"}})
        self.assertEqual(eng._build_command("hi", 1)[1:], ["--model", "big", "hi"])


class FakeEngine(BaseEngine):
    def __init__(self, route, settings, behaviour):
        super().__init__(route, settings)
        self.behaviour = behaviour

    def available(self):
        return True

    def run(self, prompt, tier):
        return self.behaviour(self.route)


class WaterfallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch("supervisor.waterfall.LEDGER_PATH", self.tmp / "ledger.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.board = Switchboard(Settings(project_root=str(self.tmp / "proj")))
        self.addCleanup(self.board.shutdown)
        self.board.delegator.read_back = lambda *a: None

    def use(self, **behaviours):
        self.board.engines = {
            r: FakeEngine(r, self.board.settings, b) for r, b in behaviours.items()
        }
        self.board.settings.capabilities = {"2": {"routes": list(behaviours)}}

    def test_crashing_engine_cascades(self):
        def boom(route):
            raise KeyError("oops")
        self.use(a=boom, b=lambda r: EngineResult(r, "fine"))
        report = self.board.submit("task", tier=2)
        self.assertTrue(report.ok)
        self.assertEqual(report.result.route, "b")
        self.assertIn("crashed", report.attempts[0])

    def test_rate_limit_without_reset_is_not_flagged_forever(self):
        def limited(route):
            raise RateLimitError(route, "429", resets_at=None)
        self.use(a=limited)
        self.assertFalse(self.board.submit("task", tier=2).ok)
        self.assertTrue(self.board.ledger.is_available("a"))

    def test_rate_limit_with_reset_is_flagged(self):
        def limited(route):
            raise RateLimitError(route, "429", resets_at=iso_in(30))
        self.use(a=limited)
        self.board.submit("task", tier=2)
        self.assertFalse(self.board.ledger.is_available("a"))


if __name__ == "__main__":
    unittest.main()
