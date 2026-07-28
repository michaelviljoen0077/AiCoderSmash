"""Interactive entry point: python -m supervisor [--project <dir>]"""

import argparse
import sys

from .settings import Settings
from .waterfall import Switchboard

BANNER = """\
=====================================================
  AI SWITCHBOARD & CONCURRENCY SUPERVISOR  (v9.1)
=====================================================
Type a task and the Delegator will route it.
Commands:
  /status                 engine + quota ledger overview
  /tier <1|2|3> <task>    force a capability tier
  /route <engine> <task>  force a specific engine
  /help                   show this help
  /quit                   exit
"""


def print_status(board: Switchboard):
    delegator_state = "online" if board.delegator.online() else "OFFLINE (heuristic mode)"
    print(f"\nDelegator ({board.settings.delegator_model}): {delegator_state}")
    print(f"Project root: {board.settings.project_root}")
    print(f"Memory bank:  {board.settings.claude_md}\n")
    print(f"{'ROUTE':<26} {'INSTALLED':<11} {'QUOTA':<11} RESETS AT")
    for route, installed, entry in board.status():
        print(
            f"{route:<26} {'yes' if installed else 'no':<11} "
            f"{entry.get('status', '?'):<11} {entry.get('resets_at') or '-'}"
        )
    print()


def print_report(report):
    print(f"\n[delegator] tier {report.tier} — {report.tier_reason}")
    for line in report.attempts:
        print(f"[waterfall] {line}")
    if report.ok:
        print(f"\n===== {report.result.route} =====")
        print(report.result.output or "(no textual output)")
        if report.result.changed_files:
            print(f"\nFiles changed: {', '.join(sorted(report.result.changed_files))}")
        if report.read_back:
            print(f"\n[delegator read-back] {report.read_back}")
    else:
        print("\nAll routes failed or are exhausted. See the trail above; "
              "check /status for quota resets.")
    print()


def main():
    parser = argparse.ArgumentParser(prog="supervisor")
    parser.add_argument(
        "--project", default=None,
        help="Absolute path of the project folder the fleet works on "
             "(default: config/settings.json project_root)",
    )
    parser.add_argument("--ui", action="store_true",
                        help="Launch the web dashboard instead of the terminal REPL")
    parser.add_argument("--port", type=int, default=8787,
                        help="Web dashboard port (default 8787)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't auto-open the dashboard in a browser")
    args = parser.parse_args()

    settings = Settings(project_root=args.project)
    board = Switchboard(settings)

    if args.ui:
        from .webui import serve
        serve(board, port=args.port, open_browser=not args.no_browser)
        return

    print(BANNER)
    print_status(board)

    try:
        while True:
            try:
                line = input("switchboard> ")
            except EOFError:
                break
            # Piped input on Windows can carry a UTF-8 BOM on the first line,
            # either decoded (﻿) or as raw bytes misread via cp1252.
            for bom in ("﻿", "\xef\xbb\xbf"):
                if line.startswith(bom):
                    line = line[len(bom):]
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line == "/help":
                print(BANNER)
                continue
            if line == "/status":
                print_status(board)
                continue
            if line.startswith("/tier "):
                parts = line.split(maxsplit=2)
                if len(parts) < 3 or parts[1] not in ("1", "2", "3"):
                    print("usage: /tier <1|2|3> <task>")
                    continue
                print_report(board.submit(parts[2], tier=int(parts[1])))
                continue
            if line.startswith("/route "):
                parts = line.split(maxsplit=2)
                if len(parts) < 3:
                    print("usage: /route <engine> <task>")
                    continue
                if parts[1] not in board.engines:
                    print(f"unknown engine; options: {', '.join(board.engines)}")
                    continue
                print_report(board.submit(parts[2], tier=2, route=parts[1]))
                continue
            if line.startswith("/"):
                print("unknown command; /help for options")
                continue
            print_report(board.submit(line))
    except KeyboardInterrupt:
        pass
    finally:
        board.shutdown()
        print("Switchboard shut down.")


if __name__ == "__main__":
    sys.exit(main())
