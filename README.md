# LessonLoop

**Agents don't remember. Files remember, and recall surfaces the right lesson at the exact moment it matters.**

LessonLoop captures an agent's mistakes (and a user's preferences) as compact cards,
then injects the relevant one **right before the action that would repeat it** — at
minimal token cost, shared across multiple agents and models.

It is **zero-infra at the core**: files + a small Python script + `grep`. No database,
no API key, no vector store required. An optional indexer/LLM curator can be plugged in
later, but nothing depends on them.

---

## Release status

LessonLoop is useful today, but treat it as **beta agent infrastructure**:

- Claude Code and Hermes hook wiring are implemented and idempotent.
- `collection` mode is the safe default: it captures/recalls, but does **not** auto-promote new cards.
- `full` mode enables automatic curation on session start; use it after you trust your cards and backups.
- Run `--dry-run` before installing on a real agent profile to preview hook/config changes without writing files.
- Do not publish private `cards/`, `journal-*.jsonl`, `recall_log-*.jsonl`, or `lessonloop.json` values from your own machine.

---

## Why it's different

| | semantic memory (mem0/letta) | rule files (AGENTS.md/cursor) | **LessonLoop** |
|---|---|---|---|
| capture | per-turn LLM extraction | manual | append-only, zero-model |
| recall | similarity (misfires) | always-loaded (doesn't scale) | **exact-trigger, action-time** |
| curation | none | none | **frequency-gated + compression** |
| cost | ∝ turns | ∝ ruleset size | **∝ how often a mistake recurs** |

The core idea: a mistake's lesson should fire **on the action that's about to repeat it**,
not on prompt similarity. `python app.py` surfaces "use `py`, not the Store stub"; `py app.py`
stays silent. You're warned exactly when you're about to err, and left alone when you're not.

---

## The loop

```
[action fails]
   │  Stop hook
   ▼
 capture ──► journal-<agent>.jsonl          (zero-model, append-only)
                │  (batch: SessionStart, or manual)
                ▼
        harvest ─► refine ─► promote         (cluster → triage → canonical card)
                │
                ▼
            cards/ ──► lessons_index.csv
                │  PreToolUse hook
                ▼
            recall ──► injects the rule before the next matching action
```

A second path handles **user preferences** (`l1: 사용자선호`): they recall at *prompt* time
(domain entry) via a `UserPromptSubmit` hook, not at tool time.

---

## Quick Install (one-liner)

No clone needed. This downloads, extracts, and installs in one step:

```bash
# Public repo (when ready):
curl -fsSL https://raw.githubusercontent.com/chan12392/lessonloop/main/scripts/quick_install.py | python - --agent myagent

# Private repo (requires GitHub token):
export LESSONLOOP_GH_TOKEN="<github-token>"
curl -fsSL https://raw.githubusercontent.com/chan12392/lessonloop/main/scripts/quick_install.py | python - --agent myagent

# With custom paths:
curl -fsSL ... | python - --agent myagent --journal-dir "G:/lessonloop/journals" --cards-dir "G:/lessonloop/cards"
```

The `quick_install.py` script:
- Downloads the latest tarball from GitHub
- Extracts to a temp directory
- Runs `install.py` (wires hooks)
- Optionally copies source to `~/.lessonloop` (add `--keep-source`)

For Windows PowerShell:
```powershell
# env LESSONLOOP_GH_TOKEN=ghp_xxx
iex "& { (iwr https://raw.githubusercontent.com/chan12392/lessonloop/main/scripts/quick_install.py).Content } --agent myagent"
```

---

## Install (Claude Code)

Or clone manually and run:

```bash
# from the lessonloop directory:
python scripts/install.py --agent <name>

# preview without writing hooks/files:
python scripts/install.py --agent <name> --dry-run

# with custom paths (optional, can also be changed later via the skill):
python scripts/install.py --agent <name> \
  --journal-dir "G:/lessonloop/journals" \
  --cards-dir "G:/lessonloop/cards"
```

That wires three hooks into `~/.claude/settings.json`:

- `PreToolUse`  → `recall_hook.py` — inject relevant lessons before Bash/Write/Edit
- `Stop`        → `capture.py` — log objective failures from the transcript (zero-model)
- `UserPromptSubmit` → `pref_recall.py` — inject user-preference cards by domain

Each agent gets its **own** log namespace (`journal-<name>.jsonl`,
`recall_log-<name>.jsonl`), so several agents can share one LessonLoop directory while
their signals stay separate.

### Configuring paths (`scripts/config_set.py`)

By default all files (journals, cards, index) live next to the scripts. If you need
to separate them (e.g. journals on local storage, cards on a synced drive):

```bash
# show current paths
python scripts/config_set.py --show

# set journal capture output path
python scripts/config_set.py --journal-dir "G:/lessonloop/journals"

# set cards/index path (shared across agents)
python scripts/config_set.py --cards-dir "G:/lessonloop/cards"

# reset to defaults (next to scripts)
python scripts/config_set.py --reset
```

Paths are stored in `lessonloop.json` at the repository root. All scripts read from
this single source of truth via `scripts/paths.py`.

### Using the skill (Claude Code)

After installation, a `/lessonloop-config` skill is available to configure paths
from within a session:

```
/lessonloop-config --show
/lessonloop-config --journal-dir "G:/lessonloop/journals"
/lessonloop-config --cards-dir "G:/lessonloop/cards"
```

The skill calls `scripts/config_set.py` under the hood. For Hermes agents,
see "Hermes" section below.

### Sharing logs across machines (`--sync-dir`)

By default `journal-<name>.jsonl` is written next to the scripts (the install ROOT). If
LessonLoop is installed on a **local-only path** (e.g. `AppData`, not a synced folder) but
a collector on another machine needs to read the journal, pass a shared directory:

```bash
python scripts/install.py --agent myagent --sync-dir "C:/Users/me/lessonloop/_sync"
```

`capture.py` then **mirrors** every appended line to `<sync-dir>/journal-<name>.jsonl` as
well (the local copy stays the dedup source of truth). The mirror is **fail-open** — if the
sync path is unavailable it logs to stderr and never blocks the tool. You can also set it
via the `LESSONLOOP_SYNC_DIR` env var.

You can also set or change the sync-dir **at runtime, without reinstalling** — it is stored
in `lessonloop.json` (`sync_dir` key), which `capture.py` reads on every call:

```bash
python scripts/config_set.py --sync-dir "G:/.../lessonloop/_sync"   # set (ABSOLUTE path!)
python scripts/config_set.py --sync-dir ""                          # disable mirror
python scripts/config_set.py --show                                 # verify
```

> **`sync_dir` must be absolute.** A relative path is resolved against the capture process's
> cwd (under Hermes that's `AppData/Local/hermes`), so the mirror lands in the wrong folder.
> `config_set.py` warns when given a relative path; `capture.py` also warns at runtime.

The `/lessonloop-config` skill wraps the same `config_set.py` for use inside a session.

### Modes

- `--mode collection` *(default)* — recall + capture + preference. **No auto-promotion.**
  Raw signals accumulate for later curation. Use this when gathering data across agents.
- `--mode full` — also wires `SessionStart` → `cycle.py`, which on each session start runs
  `harvest → refine → promote --auto` and promotes high-confidence (`ready`) cards
  automatically.

### Uninstall

```bash
python scripts/install.py --agent <name> --uninstall
```

Re-running install is idempotent (it strips any prior LessonLoop hooks first).
Paths resolve from the script location, so it works wherever the directory is mounted.

---

## Build the index

The index (`lessons_index.csv`) is derived from `cards/` and is git-ignored:

```bash
python scripts/build_index.py
```

Run it after adding or editing cards. `recall_hook.py` reads this CSV.

---

## Card shape

```yaml
---
id: <sha1(rule)[:12]>          # natural cross-agent dedup
l1: 기술 | 에이전트행동 | 사용자선호   # technical / behavior / user-preference
l2: <facet, e.g. 인코딩>
trigger: ".bat cp949 chcp"     # literal tokens, exact-matched against the action
rule: <one-line imperative — usually this alone fixes it>
trigger_tools: "Write,Edit"    # OPTIONAL — fire on these tool calls regardless of path
                                # tokens (for tool-usage lessons token matching can't reach,
                                # e.g. "Read before Write/Edit")
enforce: lint | hook | guard | manual
severity: low | medium | high | critical
sources: <count before distillation>
---
## facts   (verbatim technical literals)
## fix     (the exact correction)
## check   (a runnable self-check, when possible)
```

---

## Runtime support

- **Claude Code** — native, via `settings.json` hooks (above).
- **Hermes** — full loop supported (`capture` + `recall` + `pref`). `install.py --runtime hermes`
  wires **three** shell hooks in Hermes's `config.yaml`:

  | hook | script | role |
  | --- | --- | --- |
  | `post_tool_call` | `capture.py` | pipe each tool result (`status`/`error_type`/`error_message`) to the failure journal |
  | `pre_tool_call`  | `recall_hook.py` | technical-card recall → **block** (`{"decision":"block","reason":rule}`); `SCORE_MIN=2.5` (block is forced, so the bar is higher than CC's 1.2) |
  | `pre_llm_call`   | `pref_recall.py` | user-preference recall → **context-inject** (`{"context":rule}`) into the user message |

  `capture.py` / `recall_hook.py` / `pref_recall.py` all auto-detect the runtime from the
  payload (`hook_event_name` / `cwd`) — one codebase, both runtimes. Hermes exposes no
  "allow-with-context" on `pre_tool_call`, so technical-card recall is a forced block (the
  agent sees the rule as a tool error and adapts the next attempt); user-preference recall
  uses the soft `pre_llm_call` inject, matching CC's `UserPromptSubmit`.

  `install.py --runtime hermes` also **auto-registers** `skills/external_dirs` in your
  `config.yaml` (line-insert only, comments preserved, idempotent, no `pyyaml`), so the
  `/lessonloop-config` skill is usable after a Hermes restart. If the `hooks:` block is
  already non-empty it prints a merge snippet instead of touching it. You can also add it
  by hand:

  ```yaml
  skills:
    external_dirs:
      - "/home/me/lessonloop/skills"
  ```

  Restart Hermes, then use inside a Hermes session:

  ```
  /lessonloop-config --show
  /lessonloop-config --journal-dir "G:/lessonloop/journals"
  /lessonloop-config --sync-dir "Desktop/lessonloop/_sync"
  ```

  ```bash
  # run ON the Hermes machine:
  python scripts/install.py --agent myagent --runtime hermes --dry-run  # preview first
  python scripts/install.py --agent myagent --runtime hermes            # then install
  # then restart Hermes. Logs go to journal-myagent.jsonl next to the scripts.

  # if Hermes is installed on a local-only path (e.g. AppData) and another
  # machine collects the journals, mirror them to a synced folder:
  python scripts/install.py --agent myagent --runtime hermes \
    --sync-dir "/home/me/lessonloop/_sync"
  ```

  It edits only the `hooks:` line (preserving comments) and sets
  `hooks_auto_accept: true` so hooks run non-interactively; if the `hooks:` block is
  already populated it prints a snippet to merge by hand instead of touching it.
- **OpenClaw** — planned. *Roadmap.*

---

## Using from a chat channel (Telegram, Discord, ...)

LessonLoop runs **inside the agent runtime** as hooks — it is *not* a Telegram/Discord bot.
If you reach the agent through a chat channel, the channel intercepts any `/`-prefixed text
(like `/lessonloop-config`) as **its own bot command** and rejects it with
`Unknown command`. The message never reaches the agent.

**Rule: never use the `/` skill syntax from a channel.** Drop the slash and write a plain
natural-language request — the agent runs the underlying script itself:

```
# ✗ channel rejects this:
/lessonloop-config --sync-dir "C:/Users/me/Desktop/lessonloop/_sync"

# ✓ say this instead (no slash) — works from any channel:
lessonloop sync-dir 을 "C:/Users/me/Desktop/lessonloop/_sync" 로 설정해줘
set lessonloop sync-dir to "C:/Users/me/Desktop/lessonloop/_sync"
```

`sync_dir` **must be an absolute path** — a relative one (e.g. `Desktop/...`) is resolved
against the capture process's cwd (Hermes runs under `AppData/Local/hermes`), which puts the
mirror in the wrong place. The agent should normalize/confirm the absolute path before running
`config_set.py`.

The agent runs `config_set.py --sync-dir "..."`. Because `sync_dir` lives in
`lessonloop.json` (which `capture.py` reads on every call), the change takes effect
**immediately — no hook re-install, no runtime restart**. This is how you configure a
remote agent (e.g. talking to it over Telegram) without touching its machine.

For path edits that *do* require re-wiring hooks (journal-dir / cards-dir at install time,
or the Hermes skill registration), ask the agent to re-run `install.py` itself, then
restart its runtime.

## Status

The core loop (capture → harvest → refine → promote → recall) and both recall paths are
built and working for the supported runtimes. The project is still beta: hook contracts,
card quality gates, and operational docs are being hardened with field tests. The
self-evaluation layer is now implemented:

- **FEEDBACK** ✅ — `feedback.py` joins `recall_log` ↔ `journal` via a shared `action_sig`
  to flag *weak* cards (fired but the same action failed again) and *dead* cards (never
  fired, flagged only — never auto-pruned, since dormant ≠ obsolete). Emits a per-agent
  `.feedback_state-*.json` with `HEALTH = 1 − recurrence-rate`. Runs in `cycle.py` after
  promote and reports weak cards in the session-start notice. Pure `compute()` + I/O split,
  unit-tested (`test_feedback.py`, 19 assertions).
- **Governor** ✅ — `HEALTH` drives a hysteresis mode (`A` build / `B` maintain / hold) with
  a cold-start floor (too few cards or eligible actions → build). Reports only; it does not
  yet auto-tune refine/compact parameters (that lands once more data accumulates).
- **Weak-card escalation** ✅ — `recall_hook` loads the weak set and raises enforcement for
  recurred cards (stronger marker / recurrence count), so warnings that were ignored get louder.
- **Repair (agent self-witness, no API key)** — `repair.py` (default) joins the recurrence
  evidence per weak card and writes `staging/repair-tasks-<agent>.md`. The **agent you're
  already running** reads it plus [`RULE_SPEC.md`](./RULE_SPEC.md) (a prescriptive rewrite
  spec that judges A=rule-weak / B=trigger-overlap / C=already-fixed *before* rewriting, so
  even lower-tier models don't blindly rewrite good rules) and edits `cards/` directly. No key
  needed. `--api` opts into provider-driven auto-rewrite (OpenAI-compatible incl. z.ai/glm, or
  Anthropic) as a convenience.
- **needs_human refiner** — realigning long-tail cards whose subject ≠ incidental tokens
  still needs an understanding agent; the triage seam exists, the auto-realign is maturing.

---

## License

Licensed under the [Apache License, Version 2.0](./LICENSE).

The LessonLoop **code** (scripts, installer, hooks, index builder) is Apache-2.0. Your
`cards/` are your own private data and are **git-ignored by default** — they never leave
your machine unless you choose to share them. See [`examples/cards/`](./examples/cards) for
fully synthetic sample cards (no real data) and [`examples/lessons_index.csv`](./examples/lessons_index.csv)
for the resulting index shape.
