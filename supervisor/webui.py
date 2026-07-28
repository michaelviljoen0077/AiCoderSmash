"""Local web UI for the switchboard.

Standard-library HTTP server exposing the Switchboard over JSON endpoints,
serving the single-page dashboard in web/index.html. Dispatches are async:
POST /api/task returns an id immediately; the page polls until the waterfall
finishes.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .settings import BASE_DIR
from .waterfall import DispatchReport, Switchboard

WEB_DIR = BASE_DIR / "web"


def report_to_dict(report: DispatchReport) -> dict:
    out = {
        "ok": report.ok,
        "tier": report.tier,
        "tier_reason": report.tier_reason,
        "attempts": report.attempts,
        "read_back": report.read_back,
        "result": None,
    }
    if report.result:
        out["result"] = {
            "route": report.result.route,
            "status": report.result.status,
            "output": report.result.output,
            "changed_files": sorted(report.result.changed_files),
        }
    return out


class SwitchboardHandler(BaseHTTPRequestHandler):
    board: Switchboard = None
    tasks: dict = {}
    tasks_lock = threading.Lock()
    counter = [0]

    # -------------------------------------------------------------- plumbing

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200):
        self._send(code, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    # -------------------------------------------------------------- routing

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            page = (WEB_DIR / "index.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
        elif path == "/api/status":
            self._json(self._status())
        elif path == "/api/memory":
            self._json({"memory": self.board.memory.read()})
        elif path.startswith("/api/task/"):
            self._task_state(path.rsplit("/", 1)[-1])
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/task":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            prompt = (body.get("prompt") or "").strip()
        except (json.JSONDecodeError, UnicodeDecodeError):
            prompt = ""
        if not prompt:
            self._json({"error": "empty prompt"}, 400)
            return
        tier = body.get("tier")
        tier = int(tier) if tier in (1, 2, 3, "1", "2", "3") else None
        route = body.get("route") or None
        if route and route not in self.board.engines:
            self._json({"error": f"unknown route {route}"}, 400)
            return

        future = self.board.submit_async(prompt, tier, route)
        with self.tasks_lock:
            self.counter[0] += 1
            task_id = str(self.counter[0])
            self.tasks[task_id] = {"prompt": prompt, "future": future}
        self._json({"id": task_id})

    # -------------------------------------------------------------- handlers

    def _status(self):
        return {
            "project_root": str(self.board.settings.project_root),
            "delegator_model": self.board.settings.delegator_model,
            "delegator_online": self.board.delegator.online(),
            "engines": [
                {
                    "route": route,
                    "installed": installed,
                    "quota": entry.get("status", "?"),
                    "resets_at": entry.get("resets_at"),
                }
                for route, installed, entry in self.board.status()
            ],
        }

    def _task_state(self, task_id: str):
        with self.tasks_lock:
            task = self.tasks.get(task_id)
        if task is None:
            self._json({"error": "unknown task"}, 404)
            return
        future = task["future"]
        if not future.done():
            self._json({"state": "running", "prompt": task["prompt"]})
            return
        try:
            report = future.result()
            self._json({
                "state": "done",
                "prompt": task["prompt"],
                "report": report_to_dict(report),
            })
        except Exception as e:  # dispatch never should raise, but stay honest
            self._json({"state": "error", "prompt": task["prompt"],
                        "error": str(e)})


def serve(board: Switchboard, port: int = 8787, open_browser: bool = True):
    SwitchboardHandler.board = board
    server = ThreadingHTTPServer(("127.0.0.1", port), SwitchboardHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"Switchboard UI running at {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        board.shutdown()
