# Project Specification: "AI Switchboard & Concurrency Supervisor" (v9.1)

> Revision of v9.0. Same architecture and intent; integration mechanisms corrected to
> match how the underlying tools actually work as of mid-2026. Changes from v9.0 are
> marked **[CHANGED]** with a rationale.

---

## 1. Unified Fleet Architecture & Tool Alignment

The system structures your pre-paid premium developer tools into **six worker engines**
on a single structural tier, coordinated by a local, lightweight Ollama instance acting
as the central **Overseer/Delegator**.

The Supervisor can trigger these engines independently, swap them on the fly, or chain
them sequentially:

1. **Claude Code** — Local CLI agent. Driven headlessly via `claude -p "<prompt>" --output-format json`.
   Uses native terminal authentication (subscription login).
2. **GitHub Copilot** — Driven via the GitHub Copilot CLI in programmatic mode.
   Uses native `gh` login authentication.
3. **Google Antigravity** — Agentic IDE. **[CHANGED]** There is no supported headless
   API or CLI for submitting tasks programmatically. Antigravity is a
   **manual-dispatch rung**: the Supervisor prepares the task brief (prompt + injected
   shared memory) into a handoff file and notifies you to run it in the IDE; the
   file-system watcher captures the results when Antigravity writes them.
4. **Ollama Cloud** — Premium hosted models via Ollama's own cloud
   (e.g. `deepseek-v3.1:671b-cloud`), authenticated with your Ollama account sign-in.
   **[CHANGED]** v9.0 routed this "via your Copilot infrastructure pipeline" — that
   pathway does not exist. Ollama Cloud and Copilot are independent waterfall rungs.
5. **Cursor** — Local workspace composer, driven via the `cursor-agent` CLI in
   non-interactive mode. Uses the local app session account.
   **[CHANGED]** Rules are injected via `.cursor/rules/` (the `.cursorrules` file is
   deprecated).
6. **Codex** — OpenAI Codex CLI, driven via `codex exec` for surgical, single-function
   completions. **[CHANGED]** v9.0's "custom proxy module targeting the Codex execution
   port" is replaced with the supported headless CLI, which achieves the same
   fire-and-forget insertion pipeline.

---

## 2. The Delegator Core (Ollama Setup)

Instead of a heavy model hogging hardware while idle, the entry-point chat interface
runs a highly optimized, fast, local function-calling model.

- **Model selection:** `qwen3-coder` 7B-class (or 14B-class if VRAM allows), running
  natively via Ollama.
- **Role:** The single "middleman" interface. It parses user prompts, analyzes task
  context, cross-references the quota ledger, selects the best worker engine, formats
  the payload, and reads back the final results. It never writes project code itself.

---

## 3. Definitive Model Registry Index

The Delegator uses a `capabilities.json` configuration file to match task complexity to
the correct model generation. **[CHANGED]** Model IDs corrected to real, current
identifiers; per-route model choice is constrained to what each subscription actually
exposes.

### Tier 1 — Architectural Planning & Long-Horizon Reasoning (High Complexity)
- **Claude fleet:** `claude-fable-5` (flagship agent tier) or `claude-opus-4-8`
  (deep reasoning engine).
- **Google (Antigravity):** Gemini 3 Pro family.
- **Ollama Cloud:** `deepseek-r1` full-size cloud variant / large Llama-class models.
- **Cursor / Copilot:** routed to the strongest Claude model the plan exposes
  (target: `claude-fable-5`; actual availability is plan-dependent).

### Tier 2 — Production Engineering & System Integration (Medium Complexity)
- **Claude fleet:** `claude-sonnet-5` (the standard daily coding engine).
- **Google (Antigravity):** Gemini 3 Flash family.
- **Cursor Composer:** native internal workspace-context models.

### Tier 3 — Boilerplate Generation & Surgical Injection (Low Complexity)
- **Claude fleet:** `claude-haiku-4-5`.
- **Google (Antigravity):** Gemini Flash-Lite family.
- **Codex:** high-throughput autocomplete logic blocks and single-function completions.

**[REMOVED]** `gemini-omni`, `gemini-3.1-pro`, `gemini-3.5-flash` — not real model IDs.

---

## 4. Account Consumption & "Waterfall" Logic

To maximize the value of flat-rate accounts before touching metered tiers, the
Supervisor implements a cascading economic waterfall driven by `quota_ledger.json`:

```
┌────────────────────────────────────────────────────────┐
│           CHOOSE CLAUDE MODEL FOR THE TASK             │
└───────────────────────────┬────────────────────────────┘
                            │
              [Check Quota Balance Ledger]
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ 1. CLAUDE CODE ALLOWANCE      │ (Native subscription)  │
└───────────────────────────┬────────────────────────────┘
                            │ If allowance dry
                            ▼
┌────────────────────────────────────────────────────────┐
│ 2. GITHUB COPILOT CHANNEL     │ (Flat subscription)    │
└───────────────────────────┬────────────────────────────┘
                            │ If throttled / blocked
                            ▼
┌────────────────────────────────────────────────────────┐
│ 3. OLLAMA CLOUD (DIRECT)      │ (Premium hosted cloud) │
└───────────────────────────┬────────────────────────────┘
                            │ If remote outage / rate limit
                            ▼
┌────────────────────────────────────────────────────────┐
│ 4. CRITICAL LOCAL SELF-HOSTED SCALE-UP                 │
│    Delegator signals Ollama to load Qwen3-Coder 32B    │
│    (Absolute emergency net — always available)         │
└────────────────────────────────────────────────────────┘
```

**[CHANGED]** Rung 3 is Ollama Cloud accessed directly via Ollama sign-in, not via
Copilot.

### 4.1 Rate-Limit Ledger Management — Reactive, Not Polled

**[CHANGED]** v9.0 said the Supervisor "dynamically polls" each platform's quota. None
of these subscription products expose a queryable remaining-allowance API. The ledger
is therefore maintained **reactively**:

1. The Supervisor attempts the highest-priority active route.
2. If the route returns a rate-limit / exhaustion error (detected per-tool from exit
   codes and error output), the Supervisor marks the route `exhausted` or `throttled`
   in the ledger and records the reset time — taken from the error message when the
   tool provides one, otherwise estimated from the subscription's known reset cycle.
3. Routes past their `resets_at` timestamp are automatically re-flagged `active` and
   re-tried on next use.

No API keys are stored; every route uses its tool's own native authentication.

```json
{
  "claude_code_native":   { "status": "exhausted", "resets_at": "2026-07-16T00:00:00Z" },
  "github_copilot_claude":{ "status": "active",    "resets_at": null },
  "ollama_cloud":         { "status": "throttled", "resets_at": "2026-07-15T10:30:00Z" },
  "cursor_composer":      { "status": "active",    "resets_at": null },
  "codex_endpoint":       { "status": "active",    "resets_at": null }
}
```

---

## 5. Direct Tool Interaction: Cursor & Codex Integration

The Python Supervisor treats **Cursor** and **Codex** as native, programmable endpoints
running alongside the other terminal CLI tools.

### 5.1 Cursor Integration (The Workspace Composer Hook)
- **Mechanism:** The Supervisor executes the `cursor-agent` CLI in non-interactive
  mode, injecting the project's state rules into `.cursor/rules/supervisor.mdc` before
  spinning up workspace automation. **[CHANGED]** from the deprecated `.cursorrules`.
- **Context capture:** The Supervisor boots a background file-system watcher. When
  Cursor saves modified code files, the Supervisor intercepts the changes and
  auto-updates the central memory bank.

### 5.2 Codex Integration (The Precision Injection Interface)
- **Mechanism:** **[CHANGED]** The Supervisor invokes `codex exec` (headless mode)
  rather than a custom proxy to a local port. Codex remains the dedicated layer for
  instant logic insertion.
- **Pipeline:** When the Delegator identifies a task requiring minor programmatic
  modification (rather than a full multi-file agent workflow), it targets Codex, passes
  the adjacent block-prefix context, receives the completion, and writes it without
  user intervention.

---

## 6. Concurrency Supervision & Project Integrity Guardrails

To safely run all six worker tools on the same project folder without file collisions
or context blindness, the Python Supervisor executes a rigid lock-step process:

1. **Strict path alignment:** Every spawned execution command enforces its working
   directory (`cwd`) onto the absolute path of the targeted `PROJECT_ROOT_DIR`.
2. **Shared memory injection:** The Supervisor extracts state variables from the root
   `CLAUDE.md` and prepends them as high-priority instructions to the active prompt.
3. **The mutex lock:** A `threading.Lock()` gates code-writing operations. If any
   engine is mid-rewrite, incoming tasks from other systems wait in an isolated,
   sequential holding queue.
4. **Auto-logging updates:** When an active tool finishes, the Supervisor captures its
   changes, appends an action-log entry to `CLAUDE.md`, and releases the lock for the
   next queued tool.

---

## Appendix A — Summary of v9.0 → v9.1 corrections

| # | v9.0 claim | v9.1 correction | Why |
|---|------------|-----------------|-----|
| 1 | Ollama Online "via Copilot infrastructure pipeline" | Ollama Cloud direct, own sign-in; independent waterfall rung | That pathway does not exist |
| 2 | Supervisor "polls" platform quotas | Reactive ledger: detect rate-limit errors, record reset times | No quota-query APIs exist for these products |
| 3 | Antigravity as programmable endpoint | Manual-dispatch rung with prepared handoff brief + watcher capture | No headless API/CLI |
| 4 | Codex via custom proxy to "execution port" | `codex exec` headless CLI | Supported interface, same outcome |
| 5 | `.cursorrules` injection | `.cursor/rules/` directory | `.cursorrules` deprecated |
| 6 | `claude-opus-4.8`, `gemini-omni`, `gemini-3.5-flash` | `claude-opus-4-8`, real Gemini 3 family names | Correct model IDs |
