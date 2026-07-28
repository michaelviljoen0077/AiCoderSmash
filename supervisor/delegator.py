"""The Delegator core (spec section 2).

A small local function-calling model (qwen3-coder via Ollama) that classifies
each task into a capability tier and reads results back to the user. It never
writes project code itself. If the local Ollama daemon is down, a keyword
heuristic keeps the switchboard operational.
"""

import json
import re
import urllib.error
import urllib.request

from .settings import Settings

CLASSIFY_SYSTEM = """You are the Delegator of an AI developer-tool switchboard.
Classify the user's coding task into exactly one complexity tier:

  1 = architectural planning, multi-file refactors, long-horizon reasoning, design/migration work
  2 = standard production engineering: implement a feature, fix a bug, integrate a component
  3 = boilerplate, single-function completions, renames, docstrings, tiny surgical edits

Respond with ONLY a JSON object: {"tier": <1|2|3>, "reason": "<one short sentence>"}"""

READBACK_SYSTEM = (
    "You are the Delegator reading a worker engine's output back to the user. "
    "Summarize what was done or answered in at most three sentences. "
    "Do not add advice or new information."
)

TIER1_HINTS = re.compile(
    r"architect|design|plan|migrat|refactor (the|entire|whole)|restructure|"
    r"from scratch|end.to.end|overhaul|strategy",
    re.IGNORECASE,
)
TIER3_HINTS = re.compile(
    r"boilerplate|docstring|rename|typo|one.?liner|single function|autocomplete|"
    r"stub|getter|setter|snippet|comment",
    re.IGNORECASE,
)


class Delegator:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _chat(self, system: str, user: str, timeout: int = 60) -> str | None:
        payload = json.dumps(
            {
                "model": self.settings.delegator_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.settings.ollama_host}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return (body.get("message") or {}).get("content", "").strip() or None
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
            return None

    def online(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"{self.settings.ollama_host}/api/tags", timeout=3
            ):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def classify(self, prompt: str) -> tuple[int, str]:
        """Return (tier, reason). Uses the local model, else a heuristic."""
        raw = self._chat(CLASSIFY_SYSTEM, prompt)
        if raw:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    tier = int(parsed.get("tier", 2))
                    if tier in (1, 2, 3):
                        return tier, parsed.get("reason", "classified by delegator")
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
        return self._heuristic(prompt)

    @staticmethod
    def _heuristic(prompt: str) -> tuple[int, str]:
        if TIER3_HINTS.search(prompt) or len(prompt) < 60:
            return 3, "heuristic: small surgical task (delegator model offline)"
        if TIER1_HINTS.search(prompt):
            return 1, "heuristic: architectural keywords (delegator model offline)"
        return 2, "heuristic: default production tier (delegator model offline)"

    def read_back(self, task: str, output: str) -> str | None:
        """Optional short summary of a worker's output. None if unavailable."""
        if not output or len(output) < 400:
            return None
        clipped = output[:4000]
        return self._chat(
            READBACK_SYSTEM, f"Original task:\n{task}\n\nWorker output:\n{clipped}"
        )
