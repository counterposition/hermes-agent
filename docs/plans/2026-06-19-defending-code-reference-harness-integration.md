# Incorporating Anthropic's Defending Code Reference Harness into Hermes Agent

**Source repo:** [anthropics/defending-code-reference-harness](https://github.com/anthropics/defending-code-reference-harness)
**Status:** Canonical plan, reconciled 2026-06-09 from the original architecture note + an
adversarial review (Opus 4.8). Supersedes the standalone `.tmp_vuln_pipeline_plan_review_prompt.md`
scratch prompt, whose refined plan is folded in here.

---

## Bottom line

Do **not** merge the harness wholesale into Hermes core. Split it into:

1. **A Hermes-native security skill pack** (`optional-skills/security/`) — the static,
   read/write half of the harness. Immediate value, fully runnable on this macOS dev box.
   It needs exactly one tiny generic core addition if we want real enforcement: a
   read-only repo toolset containing `read_file` + `search_files` only. No core loop changes.
2. **An opt-in operator-CLI plugin** (`plugins/vuln_pipeline/`) — a thin wrapper that
   *orchestrates* the upstream autonomous pipeline out of process. It registers a
   `hermes vuln-pipeline` CLI command and **no model tools**. Absence of model tools is
   not, by itself, a security boundary when an agent has the terminal tool, so dangerous
   subcommands must also fail closed outside an explicit operator context. The plugin never
   owns the dangerous execution machinery; upstream's `bin/vp-sandboxed` keeps it.
3. **(Conditional) a Hermes-native runner** — only if Phase 2 earns its keep *and* we can
   match upstream's sandbox guarantees. Treat this as a gated maybe, not the destination.
   **Phase 2 is the likely steady state.**

The repo is a **reference implementation** — Apache-2.0, explicitly *"not maintained and not
accepting contributions,"* C/C++/ASAN-focused, Linux + gVisor-dependent, and it drives its
own subagents internally. That must not become load-bearing Hermes core.

**The single most important constraint:** the autonomous pipeline runs code it is *actively
trying to crash*. Its entire safety case is **gVisor (`runsc`) syscall isolation + egress
restricted to the model API**. Hermes has neither today. Any path that swaps Hermes' own
execution layer underneath the pipeline (i.e. Phase 3) silently weakens that boundary unless
it explicitly reproduces it. Phases 1–2 are designed to never touch it.

---

## What we're integrating (verified upstream facts)

The repo has two qualitatively different surfaces:

**A. Interactive Claude Code skills** (`.claude/skills/`) — mostly read/write only, no
execution: `quickstart`, `threat-model`, `vuln-scan`, `triage`, and `/patch` on static
findings. Vulnerability candidates come from *static* review (nothing is built or run), so
expect more false positives on non-canary targets. `/customize` is mixed: it edits harness
code and runs validation commands. Treat customize execution as Phase 2/3 territory; Phase 1
may include only a planning-only customization skill that writes instructions/artifacts and
never validates by running target code.

**B. The autonomous pipeline** (`harness/` + `bin/vp-sandboxed`) — a seven-stage loop that
**executes target code**:

1. **Build** — compile target into a Docker image with ASAN.
2. **Recon** — lightweight agent proposes input-parsing subsystem partitions (`focus_areas`).
3. **Find** — N agents in parallel, each in an isolated container, craft malformed inputs
   until ASAN crashes 3/3 times.
4. **Verify (grade)** — separate grader reproduces crashes in a fresh container; only the
   PoC crosses over.
5. **Dedupe (judge)** — judge agent deduplicates against already-reported bugs.
6. **Report** — structured exploitability analysis (primitive class, reachability, severity).
7. **Patch** — patch agent writes a fix; grader re-validates: builds, PoC no longer crashes,
   tests pass, find agent can't bypass.

Key properties, all verified against the README:

- **Refuses to run outside a gVisor sandbox** unless `--dangerously-no-sandbox` is passed.
- **Each agent's egress is restricted to the Claude API.**
- **C/C++ memory bugs via ASAN**, but the shape is generic and portable via `/customize`
  (answer: what signals a finding, what a PoC looks like, how the target builds).
- Invoked as `bin/vp-sandboxed run <target> --model <id> --runs 3 --parallel --stream` and
  `bin/vp-sandboxed patch results/<target>/<timestamp>/ --model <id>`.
- Upstream explicitly endorses the operator/dashboard pattern we're building:
  *"ask Claude Code to launch the pipeline and watch the run for you."*
- README warns: *"Autonomous triage and patching are still open issues... budget real
  engineering time."*

---

## Core principle: no core loop changes for Phases 1–2

The original note proposed several "good core additions" (plugin capability metadata like
`dangerous`/`requires_docker`, a headless JSONL runner, a sandbox abstraction). **The
reconciled plan deliberately needs none of those for Phases 1–2.** Gating happens through
plugin-local config + hard in-plugin preflights, not new agent-loop surfaces. This keeps the
work inside the plugin/skill boundary, avoids rebase pain on the fork, and honors the
non-goal of never editing `run_agent.py`, `cli.py`, `gateway/run.py`, or the core loop.

One narrow exception is worth making for Phase 1 enforcement: add a generic `repo-read`
toolset in `toolsets.py` containing only `read_file` and `search_files`. Current Hermes
`file` is **not** read-only (`write_file` and `patch` are included), and `search` means web
search, not file search. Without this tiny toolset addition, static-skill read-only behavior
is merely prose and must not be described as enforced.

- Do **not** rely on `platforms: [linux]` in `plugin.yaml` as the Phase 2 safety gate unless
  the loader is changed to parse/enforce it. Current general plugin loading does not make
  that field the hard boundary. Keep Linux gating inside the plugin preflight/command path.
- Risk gating is plugin-local defaults plus optional `vuln_pipeline.*` config + hard
  preflight, not a `dangerous:` manifest flag and not a `DEFAULT_CONFIG` requirement.
- A headless runner is only a Phase 3 concern, and is deferred.

### The security boundary (read this before touching Phase 3)

The autonomous pipeline's safety rests on two properties Hermes does **not** have:

| Upstream guarantee | Hermes today |
|---|---|
| Each agent in a **gVisor (`runsc`)** container — syscall-level interception | `tools/environments/docker.py` gives `cap-drop ALL`, `no-new-privileges`, PID/resource limits. Solid hardening for *trusted* workloads. **No gVisor.** |
| **Egress restricted to the model API only** | Containers get default networking. **No egress allowlist.** |

`cap-drop ALL` is not gVisor. The threat model here is running code engineered to crash and
potentially carrying exploit payloads; gVisor exists precisely because kernel-syscall
container escapes are the risk that capability-dropping does not fully address. A naive
"Hermes-native" runner that reuses the existing Docker environment would run adversarial code
with **weaker isolation and unrestricted egress** than upstream — silently. That is why
Phase 2 keeps `bin/vp-sandboxed` and Phase 3 is gated on reproducing both properties first.

---

## Hermes ground truth (verified inventory)

What the implementation actually builds on (checked against the codebase, not assumed):

| Capability | Status | Evidence / note |
|---|---|---|
| Operator-only CLI plugin (no model tools) | ✅ Template exists | `plugins/teams_pipeline/` — `register_cli_command()` in `__init__.py`, subparsers in `cli.py`, logic in submodules, zero tools. Copy this shape. |
| `optional-skills/security/` category | ✅ Exists | Already holds `web-pentest`, `oss-forensics`, `sherlock`, `1password`. New skills slot in; cross-reference to avoid overlap. |
| Constrained subagents | ✅ Works, but needs a read-only toolset | `delegate_task(toolsets=[...])`; `role="leaf"` blocks re-delegation and strips `terminal`/`execute_code`/etc. However current `file` includes `write_file`/`patch`, so static skills need a new `repo-read` toolset (`read_file`, `search_files`) before this is real enforcement. |
| Existing security plugin | ✅ `plugins/security-guidance/` | Hooks `pre_tool_call` + `transform_tool_result`; warns on `eval`/`system()`/`subprocess(shell=True)`/etc. in `write_file`/`patch`. **Will fire on our own PoCs/patches** — handle it. |
| Docker execution layer | ⚠️ Exists, no gVisor | `tools/environments/docker.py` — hardened but not gVisor; no egress allowlist. Reusable only if Phase 3 adds both. |
| Agent background jobs | ✅ Works, not plugin state | `terminal(background=True, notify_on_complete=True)` returns a tool-session `session_id`; useful for agents, but not a durable `hermes vuln-pipeline status <run-id>` registry. The plugin must own its own run registry. |
| Headless structured (JSONL) runner | ❌ None | `batch_runner.py` writes JSONL to disk only; no stdout stream. `tui_gateway/server.py` *does* speak newline-delimited JSON-RPC over stdio, but is built for TUI/WebSocket clients. Phase 3 concern. |
| Plugin `dangerous`/`requires_docker` metadata | ❌ None | Not needed — gate via plugin defaults + preflight. `requires_env:` exists; `platforms:` may appear in manifests but is not a verified hard loading gate here, so self-gate. |

---

## Phase 1 — Hermes-native security skill pack

**Do this first.** Highest value-to-risk ratio, the only phase fully runnable/testable on
this macOS box. The static skills themselves are leaf additions; real read-only enforcement
requires the tiny generic `repo-read` toolset addition described above.

Port the static `.claude/skills` into `optional-skills/security/` with collision-resistant
names:

```text
optional-skills/security/
  defending-code-quickstart/SKILL.md
  defending-code-threat-model/SKILL.md
  defending-code-vuln-scan/SKILL.md
  defending-code-vuln-triage/SKILL.md
  defending-code-vuln-patch/SKILL.md
  defending-code-customize-plan/SKILL.md   # optional; planning-only, no validation runs
```

Rules:

- **Translate Claude tool names to Hermes tools:** `Read`→`read_file`, `Glob`/`Grep`→
  `search_files`, `Write`→`write_file`/`patch`, `Task`→`delegate_task`,
  `AskUserQuestion`→`clarify`. Static scan/triage skills must not translate `Bash` to
  `terminal`; any validation command belongs only in the planning-only customize artifact or
  the Phase 2 operator flow.
- **Enforce the static constraint — don't just request it.** Hermes skills are
  *instructional, not permission sandboxes* (there is no `allowed-tools` enforcement). So
  "static `/vuln-scan` must not execute target code" is prose a model can ignore. Make the
  actual review run through `delegate_task(toolsets=["repo-read"])`, where `repo-read` is a
  new generic toolset containing only `read_file` and `search_files`. Do **not** use
  `toolsets=["file", "search"]`: current `file` can write and patch, and `search` is web
  search. This is the **primary** mechanism for static scan/triage.
  `defending-code-vuln-patch` may have the parent write inert diffs under `PATCHES/` after
  reviewing read-only child output; read-only child agents should produce patch plans/diff
  text, not write files.
- **Naming:** use the namespaced skill names above. Do **not** introduce global `/patch`,
  `/triage`, `/threat-model`, or `/vuln-scan` — too collision-prone with existing surfaces.
- **Authoring standards (HARDLINE, per CLAUDE.md):** `description` ≤ 60 chars ending in a
  period, modern section order (`## When to Use` → `## Prerequisites` → `## How to Run` →
  `## Quick Reference` → `## Procedure` → `## Pitfalls` → `## Verification`), scripts in
  `scripts/`, references in `references/`. `author` credits the human first.
- **Parallel review** uses `delegate_task` batch mode (bounded by
  `delegation.max_concurrent_children`, default 3).
- **Keep artifact names compatible** with upstream where useful, so handoff to Phase 2 is
  clean: `THREAT_MODEL.md`, `VULN-FINDINGS.{json,md}`, `TRIAGE.{json,md}`, `PATCHES/`.
- **Tests** at `tests/skills/test_<skill>_skill.py` — frontmatter/guardrail checks
  (description length, tool-name hygiene, no execution in static skills). Stdlib + pytest +
  mock only; no network. Run via `scripts/run_tests.sh`.

---

## Phase 2 — Optional harness-wrapper plugin (operator CLI)

Add a bundled, opt-in plugin that lets Hermes act as the **operator/dashboard** for the
upstream pipeline without owning the execution. Modeled on `plugins/teams_pipeline/`.

```text
plugins/vuln_pipeline/
  plugin.yaml          # kind: standalone; no provides_tools; platforms is advisory only
  __init__.py          # register_cli_command("vuln-pipeline", ...)
  cli.py               # argparse subcommands; dispatch + operator confirmation gates
  runner.py            # subprocess/Popen wrapper around bin/vp-sandboxed
  results.py           # artifact parser → compact JSON summaries
  state.py             # plugin-owned run registry under get_hermes_home()
  README.md
```

CLI surface (operator-facing, not model-callable as a tool; dangerous subcommands still
self-gate because agents with `terminal` can invoke CLIs):

```text
hermes vuln-pipeline setup       # validate Linux / Docker / gVisor; locate harness checkout
hermes vuln-pipeline run <target> [--runs N] [--parallel] [--stream]
hermes vuln-pipeline status <run-id>
hermes vuln-pipeline report <run-id>
hermes vuln-pipeline patch <run-id>
hermes vuln-pipeline dedup <run-id>
```

Hard requirements:

- **No model tools at launch.** Operator CLI only. *Later*, a single **read-only**
  `vuln_pipeline_summary` tool (status/report only) may be added so the agent can narrate
  findings as they land — matching upstream's "explain findings as they come" UX. The
  run/patch/execute actions stay out of model tools forever. Because a Hermes agent with the
  `terminal` tool can still invoke `hermes vuln-pipeline ...`, the dangerous subcommands
  (`run`, `patch`, and any future execution-capable action) must also enforce an operator
  gate: require a real local operator context, read a typed confirmation phrase from
  `/dev/tty` when possible, refuse obvious non-interactive/Hermes tool-subprocess contexts,
  and never treat `--yes` alone as sufficient for first-run or no-sandbox execution. This is
  a practical prompt-injection/accident guard, not a cryptographic boundary against someone
  who already has arbitrary local shell access.
- **Wrap, don't vendor or rewrite.** `runner.py` shells out to an external harness checkout
  whose path comes from plugin-local config (`vuln_pipeline.harness_dir`) or env. Do not copy
  the harness into the tree.
- **Pin and enforce the checkout SHA** (per dependency-pinning policy — Git URLs pin to a
  40-char SHA). Store a `harness_commit` in plugin config or a plugin constant, verify
  `git -C <harness_dir> rev-parse HEAD` in `setup` and before every `run`/`patch`, and record
  the SHA in the run manifest. Refuse mismatches unless the operator passes an explicit
  unsupported-SHA override plus the typed confirmation phrase. Unmaintained-and-pinned is a
  *stability upside*: artifact schemas won't move under you.
- **Always prefer `bin/vp-sandboxed`; refuse `--dangerously-no-sandbox` by default.**
  Overriding requires plugin config `allow_no_sandbox: true`, an explicit CLI flag, and the
  typed operator confirmation phrase. Never silently fall back to plain `docker run`.
- **Preflight is hard-fail:** v1 is Linux-local only; remote workers are future work. Check
  Linux host, Docker present, Docker runtime registration includes gVisor `runsc`, upstream
  sandbox setup/self-test passes where available, no broad home-dir mounts, no
  credential-bearing path mounts, target config exists, artifact dir selected, and user
  confirms authorized defensive scope. Make egress allowlist evidence-based: inspect the
  upstream sandbox policy/wrapper arguments and, where practical in an integration test, run
  a positive model-API reachability probe and a negative non-allowlisted egress probe from
  inside the sandbox. On macOS, fail gracefully with: *run this inside a Linux VM or CI Linux
  runner* — Docker Desktop on macOS is not a Linux host with a registered gVisor runtime.
- **Long runs use a plugin-owned run registry**, not Hermes agent background-process state.
  `runner.py` starts `bin/vp-sandboxed` with `subprocess.Popen`, writes stdout/stderr to a
  log file, stores `{run_id, pid, started_at, harness_dir, harness_sha, target, command,
  results_dir, log_path, status}` under `get_hermes_home()/vuln_pipeline/runs/`, and
  `status` polls the PID/log/artifacts. Do not use `terminal(background=True)` or `process`
  `session_id`s as the durable plugin status mechanism.
- **Compose with `security-guidance`.** The `vuln-patch` skill and any patch artifacts will
  contain exactly the patterns that plugin warns on (`system()`, `eval`, `subprocess(shell=
  True)`). Decide deliberately: scope `security-guidance` to skip the pipeline's artifact
  dirs, or accept the warning noise. Document the choice.

**macOS caveat for this repo:** only Phase 1 is locally runnable. Phase 2's runner is
**unit-tested with a mocked subprocess** here; "done" means green unit tests locally plus a
real smoke/integration run on a CI Linux runner or dedicated Linux VM. Remote worker support
is not v1; do not imply it is shippable until designed and tested separately.

---

## Phase 3 — Hermes-native runner (conditional, security-gated)

The original note framed this as *"the correct long-term design."* The reconciled position:
**it is a gated maybe, and Phase 2 is the likely terminal state.** Two preconditions, both
hard, must be met before any work starts:

1. **Sandbox parity.** A Hermes-native runner must reproduce upstream's guarantees —
   **gVisor (`runsc`) isolation + egress allowlisted to the model API only** — added to the
   environment layer (`tools/environments/docker.py` or a new `gvisor.py` backend) and
   *proven* equivalent. Without this, going native means running exploit code with weaker
   isolation than the wrapper it replaces. This is the bulk of the work and the only reason
   the phase is risky.
2. **Loose coupling in upstream.** The pipeline drives its find/grade/report subagents
   internally — the README implies via Claude Code's `stream-json` + session-ID resume,
   **which we have not verified at the source level.** Before committing, read
   `harness/agents/` and confirm the agent-spawning seam is loose enough to swap. If it's
   tight, "add a runner implementation" actually means forking and rewriting an *unmaintained*
   third-party pipeline's guts — the exact fork-drift burden this whole plan avoids. The
   elegant `AgentRunner` interface below is only worth building if the coupling is loose:

```python
class AgentRunner:
    def run(self, prompt: str, *, model: str, tools: list[str], sandbox: SandboxSpec,
            transcript_path: Path, max_turns: int, system_prompt: str | None) -> AgentResult: ...
    def resume(self, session_id: str, *, transcript_path: Path) -> AgentResult: ...
```

On the headless contract: a native runner would most likely drive the **existing**
`tui_gateway` JSON-RPC-over-stdio surface rather than build a brand-new `hermes run-agent
--jsonl`. The gap is smaller than "build a whole new runner," but it is still real work and
strictly downstream of precondition (1).

If both preconditions hold, the domain phases (recon/find/verify/dedupe/report/patch) stay
the same; only the runner swaps. If they don't, **stop at Phase 2** — Hermes as operator,
upstream as executor, is a perfectly good and honest terminal state.

---

## Phase 4 — Packaging (if it proves broadly useful)

- Publish as a bundled plugin + skill pack; docs under Hermes security workflows.
- Keep the autonomous half disabled by default and Linux-gated.
- Preserve Apache-2.0 attribution and the pinned SHA.
- Tests for artifact parsing, preflight refusal, no-sandbox refusal, and credential-policy
  behavior.

---

## Configuration

Prefer plugin-local defaults so Phase 2 does not require editing `DEFAULT_CONFIG`. The plugin
may read an optional `vuln_pipeline:` section from `config.yaml` if present, but absence of
that section should behave as the safe defaults below:

```yaml
vuln_pipeline:
  harness_dir: ""            # path to the pinned upstream checkout
  harness_commit: ""         # required 40-char SHA once setup has pinned a checkout
  require_sandbox: true      # refuse to run without gVisor
  allow_no_sandbox: false    # must be flipped AND confirmed to override
  default_model: ""          # model id passed to bin/vp-sandboxed
  max_parallel: 3
  runs_dir: ""               # optional; default get_hermes_home()/vuln_pipeline/runs
```

If this later graduates from bundled experimental plugin to first-class Hermes workflow, then
promote the defaults into `DEFAULT_CONFIG` deliberately. Do not smuggle that core config edit
into Phase 2 while also claiming zero core changes.

**Secrets stay in `.env` only** — `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`. Never
store keys in `config.yaml`. The runner must never log env secrets.

If any run state is stored under the Hermes home, use `get_hermes_home()` — never literal
`~/.hermes` (profile rule). Prefer indexing/summarizing the harness's own
`results/<target>/<timestamp>/` **in place** over copying potentially large/sensitive PoC
corpora to a second root.

---

## Artifact contract

The pipeline writes under `results/<target>/<timestamp>/`. `results.py` parses and emits
compact JSON summaries from:

```text
reports/manifest.jsonl
report.json
patch_result.json
found_bugs.jsonl
run_*/result.json
```

⚠️ **The README names these files but does not document their schemas.** Derive the actual
shapes from real sample outputs captured at the pinned SHA (run the `canary`/`drlibs`
targets on a Linux worker once, save the outputs as fixtures). Write the parser
**defensively** — tolerate missing/extra fields and minor drift; never assume a schema you
only saw in a filename.

---

## Test plan

- **Skills:** frontmatter/guardrail tests (≤60-char description, tool-name hygiene,
  namespaced skill names, static scan/triage skills don't reference execution tools, static
  review examples use `delegate_task(toolsets=["repo-read"])` rather than `file`/`search`,
  and patch writes only inert artifacts under `PATCHES/`). Add a small toolset test proving
  `repo-read` contains exactly `read_file` and `search_files`.
- **Plugin CLI:** argparse parser tests for every subcommand; tests that Linux/macOS gating is
  enforced in command/preflight code rather than assumed from `plugin.yaml`.
- **Runner (mocked subprocess):** verify `--dangerously-no-sandbox` is refused by default,
  `bin/vp-sandboxed` is preferred, unsupported harness SHA is refused, non-interactive or
  agent-like invocation of dangerous subcommands is refused, no env secret is ever logged,
  plugin-owned run registry files are written/read correctly, and result parsing handles
  golden fixtures + malformed/partial artifacts.
- **No live Docker / gVisor / network** in unit tests. Integration runs happen on a Linux
  worker/CI only.
- Run everything via `scripts/run_tests.sh`.

---

## Non-goals

- Do **not** edit `run_agent.py`, `cli.py`, `gateway/run.py`, or the core loop. The only
  Phase 1 core edit allowed is the generic `repo-read` toolset in `toolsets.py`; if that is
  rejected, remove the enforcement claim and use parent-mediated file excerpts instead.
- Do **not** make autonomous scanning a default, model-callable core tool.
- Do **not** rely on "no model tools" as the only barrier; dangerous CLI subcommands must
  also self-gate because terminal-enabled agents can invoke CLIs.
- Do **not** auto-mount the Hermes repo or `~/.hermes` into attack containers.
- Do **not** silently fall back to plain Docker when gVisor is absent.
- Do **not** add in-tree memory providers or MCP-specific integrations as part of this.
- Do **not** expose run/patch actions as model tools — operator CLI only, with preflight and
  confirmation gates.

---

## Open questions to resolve before Phase 3

1. **How tightly is `harness/agents/` coupled to Claude Code?** Read the source. Loose seam →
   `AgentRunner` is viable. Tight seam → stay at Phase 2.
2. **Can `tools/environments/docker.py` gain a gVisor backend + egress allowlist** without a
   disproportionate maintenance cost, and can we *prove* parity with upstream's sandbox?
3. **Is there demand** for native execution at all, or is the operator/dashboard role
   (Phase 2) sufficient in practice? Don't build Phase 3 speculatively.

---

## Verdict

> **Implement Phase 1 now** (Hermes-native static security skills, enforced via a new
> generic `repo-read` toolset plus constrained `delegate_task`, fully local). **Build Phase 2
> as specified** (opt-in operator-CLI plugin wrapping `bin/vp-sandboxed`, enforced SHA pin,
> defensive parser, plugin-owned run registry, hard Linux/gVisor/egress preflight, no model
> tools, and dangerous subcommands gated against accidental terminal-tool invocation). **Treat
> Phase 3 as a security-gated maybe** — it requires reproducing gVisor + egress isolation and
> confirming loose upstream coupling first, and Phase 2 is a legitimate terminal state.
>
> This fits Hermes' architecture, keeps core-loop/rebase risk low, preserves provider
> agnosticism for the static skills while honestly leaving the autonomous wrapper
> Anthropic-harness-shaped, and — most importantly — keeps the real security boundary
> (gVisor + egress) intact by leaving dangerous execution with the upstream harness that was
> built around it.
