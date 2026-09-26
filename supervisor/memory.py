"""Shared memory bank backed by the project's CLAUDE.md (spec section 6).

Every engine gets the current project state prepended to its prompt, and every
completed job appends an entry to the action log before the write lock is
released — so tool N always knows what tools 1..N-1 just did.
"""

from datetime import datetime, timezone
from pathlib import Path

TEMPLATE = """# Project Memory Bank

## State

(Shared state variables for all AI worker engines. Keep this section short and current.)

## Action Log

"""


LOG_HEADING = "## Action Log"
# Only the newest log entries are injected: the prompt is passed to CLI engines
# as an argument, and Windows caps a command line at ~32K characters.
INJECT_LOG_ENTRIES = 20


def _trim_action_log(memory: str, keep: int) -> str:
    head, sep, log = memory.partition(LOG_HEADING)
    if not sep:
        return memory
    entries: list[list[str]] = []
    for line in log.strip("\n").splitlines():
        if line.startswith("- "):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
    if len(entries) <= keep:
        return memory
    dropped = len(entries) - keep
    kept = "\n".join(line for entry in entries[-keep:] for line in entry)
    return f"{head}{sep}\n\n({dropped} older entries omitted)\n{kept}"


class MemoryBank:
    def __init__(self, claude_md: Path):
        self.path = Path(claude_md)
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(TEMPLATE, encoding="utf-8")

    def read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def inject(self, prompt: str) -> str:
        """Prepend the memory bank as high-priority context to a task prompt."""
        memory = _trim_action_log(self.read(), INJECT_LOG_ENTRIES).strip()
        if not memory:
            return prompt
        return (
            "<shared_project_memory>\n"
            f"{memory}\n"
            "</shared_project_memory>\n\n"
            "The block above is the shared memory bank for this project, "
            "maintained by a supervisor coordinating multiple AI tools. "
            "Respect its state and constraints.\n\n"
            f"TASK:\n{prompt}"
        )

    def log_action(self, route: str, task: str, summary: str, changed_files=None):
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        task_line = task.strip().replace("\n", " ")
        if len(task_line) > 160:
            task_line = task_line[:157] + "..."
        summary_line = summary.strip().replace("\n", " ")
        if len(summary_line) > 300:
            summary_line = summary_line[:297] + "..."
        entry = f"- **{stamp}** `{route}` — task: {task_line}\n  - result: {summary_line}\n"
        if changed_files:
            shown = ", ".join(sorted(changed_files)[:10])
            more = len(changed_files) - 10
            if more > 0:
                shown += f" (+{more} more)"
            entry += f"  - files changed: {shown}\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(entry)
