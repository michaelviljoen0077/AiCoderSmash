# AI Switchboard & Concurrency Supervisor (v9.1)

A local dispatcher that routes coding tasks across your six pre-paid AI developer
tools, draining flat-rate subscriptions in waterfall order before anything metered,
while a mutex + shared memory bank lets them all work the same project folder safely.

Spec: [SPEC-v9.1.md](SPEC-v9.1.md)

## Requirements

- Python 3.10+ (standard library only — no pip installs needed)
- [Ollama](https://ollama.com) running locally, with the delegator model pulled:
  `ollama pull qwen3-coder:7b`
  (the switchboard still works without it, falling back to heuristic routing)
- Whichever worker CLIs you have, signed in with their own native auth:
  - **Claude Code** — `claude` on PATH (subscription login)
  - **GitHub Copilot CLI** — `copilot` on PATH (`gh` login)
  - **Codex CLI** — `codex` on PATH
  - **Cursor CLI** — `cursor-agent` on PATH
  - **Ollama Cloud** — `ollama signin`, then cloud model tags work through the local daemon

(Antigravity was removed from the default waterfall — its manual handoff broke the
single-interface goal. Re-add it in `config/` if you ever want it back.)

Engines that aren't installed are simply skipped by the waterfall.

## Run

**Web dashboard** (recommended):

```powershell
python -m supervisor --ui --project C:\path\to\your\project
```

Opens `http://127.0.0.1:8787` — fleet status cards, quota ledger, task composer
with tier/route overrides, live dispatch feed with the waterfall trail, and a
shared-memory viewer. Add `--port <n>` or `--no-browser` if needed.

**Terminal REPL**:

```powershell
python -m supervisor --project C:\path\to\your\project
```

Then just type tasks at the `switchboard>` prompt. Useful commands:

| Command | Purpose |
|---|---|
| `/status` | Engine availability + quota ledger overview |
| `/tier 3 <task>` | Force a capability tier (skip delegator classification) |
| `/route codex_endpoint <task>` | Force a specific engine |
| `/quit` | Exit |

## How it works

1. **Delegator** ([supervisor/delegator.py](supervisor/delegator.py)) — a small local
   qwen3-coder model classifies each task into tier 1/2/3 and reads results back.
2. **Capabilities registry** ([config/capabilities.json](config/capabilities.json)) —
   maps each tier to an ordered route list (the waterfall).
3. **Quota ledger** ([supervisor/ledger.py](supervisor/ledger.py), state in
   `quota_ledger.json`) — reactive: a route that returns a rate-limit error is flagged
   `exhausted`/`throttled` with an estimated reset time, then auto-reactivates.
4. **Engines** ([supervisor/engines.py](supervisor/engines.py)) — CLI adapters run each
   tool headlessly with `cwd` locked to the project root; Ollama adapters serve the
   cloud rung and the emergency local Qwen3-Coder 32B net.
5. **Guardrails** ([supervisor/waterfall.py](supervisor/waterfall.py)) — one write lock,
   a sequential task queue, shared-memory injection from `CLAUDE.md`, a polling file
   watcher that captures what each tool changed, and an action log appended after
   every job.

## Tuning

- Engine command templates, per-tier Claude model choice, timeouts, and reset
  estimates live in [config/settings.json](config/settings.json) — adjust them if your
  CLI versions use different flags.
- The waterfall order per tier is [config/capabilities.json](config/capabilities.json).
- Delete `quota_ledger.json` to reset all routes to `active`.
