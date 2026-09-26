"""Configuration loading for the AI Switchboard."""

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
LEDGER_PATH = BASE_DIR / "quota_ledger.json"


class Settings:
    def __init__(self, project_root: str | None = None):
        with open(CONFIG_DIR / "settings.json", encoding="utf-8") as f:
            self._raw = json.load(f)
        with open(CONFIG_DIR / "capabilities.json", encoding="utf-8") as f:
            self.capabilities = json.load(f)

        root = project_root or self._raw.get("project_root", ".")
        self.project_root = Path(root).resolve()
        self.ollama_host = self._raw.get("ollama_host", "http://localhost:11434")
        self.delegator_model = self._raw.get("delegator_model", "qwen3.5:4b")
        self.claude_md = self.project_root / self._raw.get("claude_md", "CLAUDE.md")
        self.watcher_interval = float(self._raw.get("watcher_interval_seconds", 1.5))
        self.engine_timeout = int(self._raw.get("engine_timeout_seconds", 900))
        self.engines = self._raw.get("engines", {})

    def engine_config(self, route: str) -> dict:
        return self.engines.get(route, {})

    def routes_for_tier(self, tier: int) -> list[str]:
        entry = self.capabilities.get(str(tier))
        if not entry:
            raise ValueError(f"No capability entry for tier {tier}")
        return entry["routes"]

    def tier_name(self, tier: int) -> str:
        return self.capabilities.get(str(tier), {}).get("name", f"Tier {tier}")
