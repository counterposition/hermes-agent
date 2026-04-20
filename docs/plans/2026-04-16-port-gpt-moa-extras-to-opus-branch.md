# Port selected GPT-5.4 MoA additions onto the Opus MoA branch

**Date:** 2026-04-16
**Status:** Ready to implement
**Target branch:** `hermes-agent-moa-claude-opus` (worktree: `/Users/me/c/mine/hermes-agent-moa-claude-opus`, tip commit `613b5564`)
**Source worktree (read-only):** `/Users/me/c/mine/hermes-agent-moa-gpt-5.4-high` (branch `feat/moa-routing-gpt-5.4-high`)

## Context for a fresh agent

Two agents independently implemented the [2026-04-13 MoA provider-routing ADR](2026-04-13-moa-provider-routing-adr.md). The Opus branch is the one we're shipping — it's committed, it correctly threads `reasoning_config` through the Anthropic adapter (a hard prerequisite the ADR called out), it clamps Codex `minimal→low` before send, it guards against the caller's `temperature` clobbering the forced `temperature=1` that Anthropic thinking requires, and it explicitly accepts `custom:<name>` providers.

The GPT-5.4 branch missed the Anthropic adapter fix (leaving `reasoning_config=None` hardcoded in `_AnthropicCompletionsAdapter.create`, so `provider: anthropic` silently drops reasoning), but it produced two artifacts worth taking:

1. A **user-facing reference doc** wired into the website sidebar.
2. A **parity test** structure that compares MoA's `_reasoning_kwargs` output against `AIAgent._build_api_kwargs` for OpenRouter/Nous/Copilot/AI-Gateway routes.

This document is that port list. Do the work on the Opus worktree. Leave the GPT worktree alone.

## Tasks

### 1. Port the user-facing reference doc

**Source:** `/Users/me/c/mine/hermes-agent-moa-gpt-5.4-high/website/docs/reference/mixture-of-agents.md` (115 lines)

**Destination:** `/Users/me/c/mine/hermes-agent-moa-claude-opus/website/docs/reference/mixture-of-agents.md`

Copy verbatim, then make these changes to match the Opus implementation:

1. Update the example roster (lines 16–34 of the source) so it matches the default roster the Opus branch actually ships. Opus updated the defaults: `google/gemini-3.1-pro-preview` (not `gemini-3-pro-preview`), and the fourth reference model is `x-ai/grok-4.3` (not `deepseek/deepseek-v3.2`). See [`hermes_cli/config.py`](../../hermes_cli/config.py) `DEFAULT_CONFIG["moa"]` in the Opus worktree for the authoritative list.

2. Under **Reasoning per model** (around line 76), confirm the Anthropic bullet reads "threaded into the Anthropic adapter so it emits a `thinking` block" — this is true on the Opus branch, so keep it. Also add a bullet above it that mentions the Codex `minimal→low` clamp ("Codex's Responses API doesn't accept `minimal`; MoA transparently maps it to `low` before send") so users aren't surprised when they set `reasoning: minimal` on a Codex entry and see `low` in their billing logs.

3. Add a short note under **Reasoning per model** calling out the OpenRouter silent-accept blind spot by name — the ADR specifically asks for this at section "Known blind spot: silent-accept providers". One sentence is enough: "OpenRouter proxies to many backends that silently accept and drop `extra_body.reasoning` without error; if a reasoning setting seems inert, cross-check against provider-side logs or billing."

### 2. Wire the doc into the website sidebar

**File:** `/Users/me/c/mine/hermes-agent-moa-claude-opus/website/sidebars.ts`

Add `'reference/mixture-of-agents'` to the reference section, immediately after `'reference/toolsets-reference'`. The GPT branch's edit is the exact shape to copy — see its diff in the GPT worktree:

```
 'reference/tools-reference',
 'reference/toolsets-reference',
+'reference/mixture-of-agents',
 'reference/mcp-config-reference',
```

### 3. Extend the AIAgent parity test

**Destination file:** `/Users/me/c/mine/hermes-agent-moa-claude-opus/tests/tools/test_mixture_of_agents_tool_provider_routing.py`

**Source:** `/Users/me/c/mine/hermes-agent-moa-gpt-5.4-high/tests/tools/test_mixture_of_agents_tool_provider_routing.py`, specifically:
- The `agent_for_parity` fixture (GPT source lines 76–90).
- The `_agent_reasoning_view` helper (GPT source lines 443–448).
- The `test_reasoning_translation_matches_main_agent` parametrized test (GPT source lines 451–527).
- The `_make_tool_defs` helper (GPT source lines 17–28) used by the fixture.

Port those pieces into the Opus test file, but do **not** port GPT's module-scope `from run_agent import AIAgent` import literally. In this repo the `tests/conftest.py` autouse fixture sets `HERMES_HOME` at test runtime; importing `run_agent` at module import time can initialize logging against the real `~/.hermes` before that fixture runs. Keep the parity fixture hermetic by importing `AIAgent` lazily inside the fixture (or otherwise patching logging / `HERMES_HOME` before importing `run_agent`).

Put the parity block after the existing `test_reasoning_kwargs_copilot_remap_branches` test so it sits next to the standalone translation tests.

#### Extend beyond GPT's coverage

GPT's `_agent_reasoning_view` returns `{}` for `anthropic` and `openai-codex`, which silently skips those paths. That's the blind spot that let GPT's own Anthropic-adapter bug slip through. Close it:

1. **For `openai-codex`**: do **not** extend the `_build_api_kwargs` parity helper by looking for `reasoning_config`. That compares different abstraction layers:
   - MoA's `_reasoning_kwargs("openai-codex", ...)` returns adapter-facing kwargs (`{"reasoning_config": ...}`).
   - `AIAgent._build_api_kwargs()` in Codex mode returns Responses API payload fields (`reasoning`, `include`, etc.), not `reasoning_config`.

   Instead, add a small adapter-level test, e.g. `test_codex_adapter_receives_reasoning_config_from_moa_shape`, that:
   - Calls `moa._reasoning_kwargs("openai-codex", "gpt-5.4", {"enabled": True, "effort": "high"})` and asserts the MoA side returns `{"reasoning_config": {"enabled": True, "effort": "high"}}`.
   - Instantiates `_CodexCompletionsAdapter` with a mock client whose `responses.stream()` records the outbound kwargs.
   - Calls `.create(model=..., messages=..., reasoning_config={"enabled": True, "effort": "high"})`.
   - Verifies the adapter emitted the Codex Responses payload shape (`reasoning={"effort": "high", "summary": "auto"}` and `include=["reasoning.encrypted_content"]`).
   - Adds a `minimal` variant if you want to pin the existing `minimal→low` clamp in the adapter.

   Keep the main parity block limited to the providers where `AIAgent._build_api_kwargs()` and `moa._reasoning_kwargs()` are intentionally comparable today (OpenRouter / Nous / Copilot / AI-Gateway).

2. **For `anthropic`**: `AIAgent._build_api_kwargs` does not emit a ready-to-send reasoning kwarg for Anthropic in the same way — the reasoning translation happens inside `_AnthropicCompletionsAdapter.create` via `build_anthropic_kwargs`. So a pure `_build_api_kwargs`-vs-`_reasoning_kwargs` diff won't work.

   Instead, add a second parity-style test: `test_anthropic_adapter_receives_reasoning_config_from_moa_shape`. It should:
   - Build the MoA kwargs by calling `moa._reasoning_kwargs("anthropic", "claude-opus-4-6", {"enabled": True, "effort": "high"})`.
   - Assert the result is `{"reasoning_config": {"enabled": True, "effort": "high"}}`.
   - Instantiate `_AnthropicCompletionsAdapter` with a mock client, call `.create(model=..., messages=..., reasoning_config={"enabled": True, "effort": "high"})`, and verify that `build_anthropic_kwargs` received `reasoning_config={"enabled": True, "effort": "high"}` (not `None`). This guards against the exact regression the GPT branch shipped.
   - Use `monkeypatch` to stub `agent.anthropic_adapter.build_anthropic_kwargs` and `normalize_anthropic_response` — see Opus's existing `test_anthropic_adapter_does_not_clobber_thinking_temperature` (at the bottom of the Opus test file) for the stub pattern.

### 4. Port the init-failure edge case test

**Source:** `/Users/me/c/mine/hermes-agent-moa-gpt-5.4-high/tests/tools/test_mixture_of_agents_tool_provider_routing.py` lines 617–629, `test_reference_model_init_failure_is_reported_as_model_failure`.

Port verbatim except:
- Replace `AsyncMock(side_effect=RuntimeError(...))` with a plain lambda that raises — Opus's `resolve_provider_client` path is synchronous, so `AsyncMock` would add ceremony that doesn't match Opus's call pattern. Check `_run_reference_model_safe` in the Opus worktree: it calls `resolve_provider_client` synchronously and expects a `(client, resolved_model)` tuple back.
- The test asserts the friendly error message mentions "could not be initialized". Opus's current path returns a message matching `f"{model}: no credentials configured for provider {provider!r}"` when the client is `None`, and re-raises otherwise. So the GPT test as written won't pass against Opus — adapt the assertion to whichever error text the Opus `_run_reference_model_safe` actually produces when `resolve_provider_client` raises. Read [`tools/mixture_of_agents_tool.py`](../../tools/mixture_of_agents_tool.py) `_run_reference_model_safe` around the client resolution call first, then decide whether to wrap it in a `try/except` that reports the exception as a soft failure (returning `(model, err_msg, False)`) or to assert the current re-raise behavior. The ADR's guidance ("that model is marked as failed and the MoA proceeds with remaining successful responses") argues for wrapping — failure to resolve a client is a per-model failure, not an MoA-wide crash. If you add the wrap, keep the change minimal: one `try/except Exception` around the `resolve_provider_client` call, returning the `(model, f"{model} could not be initialized: {exc}", False)` triple on any error.

## What NOT to port from the GPT branch

Deliberately skip these — they're either unnecessary complexity or incompatible with the Opus shape:

- **`_get_openrouter_client` legacy compatibility shim.** Not in the ADR; the Opus branch routes everything through `resolve_provider_client` and doesn't need the shim.
- **`_maybe_await` wrapper + `inspect` import.** `resolve_provider_client` is synchronous in this codebase; awaiting its sync return is dead code.
- **`_canonical_reasoning_config` re-normalizer.** Opus's `_reasoning_kwargs` branches directly on `reasoning_config` without re-normalizing. Keep Opus's cleaner shape.
- **`_coerce_runtime_model_entry`.** Redundant with `_load_moa_config`'s normalization; Opus's runtime receives already-normalized dicts.
- **GPT's `_create_completion_with_fallbacks` two-level retry.** Opus's `_handle_unsupported_param` + shared `_build_api_params` pattern keeps the sticky-cache consultation in one place. Don't introduce a second retry layer.
- **GPT's `check_moa_requirements` doing `resolve_provider_client` probes.** Opus uses `_provider_has_credentials`, which is a lightweight env/auth-file probe with no client construction. Opus's shape is faster and fine for preflight. Keep it.
- **The `reasoning` raw-string field on normalized entries.** GPT carries `reasoning` (the raw config string) alongside `reasoning_config` (the parsed dict). Opus keeps only the parsed dict and derives the display label via `_effort_label(entry)`. Don't add the raw field back.

## Validation

After porting, run in the Opus worktree:

```bash
cd ~/c/mine/hermes-agent-moa-claude-opus
uv run pytest tests/tools/test_mixture_of_agents_tool_provider_routing.py tests/tools/test_mixture_of_agents_tool.py -x --tb=short
```

Expected: all tests pass, with roughly 4–5 new tests added on top of the existing 42: the hermetic `AIAgent` parity fixture / helper, the OpenRouter/Nous/Copilot/AI-Gateway parity block, one Codex adapter test (optionally split into a second `minimal→low` clamp test), one Anthropic adapter regression test, and one init-failure regression test.

Also verify the docs build:

```bash
cd ~/c/mine/hermes-agent-moa-claude-opus/website
npm run build
```

Expected: the build succeeds and `reference/mixture-of-agents` appears in the reference sidebar.

## Commit

Single commit on the Opus branch (do not amend `613b5564`):

- Subject: `docs: port MoA reference page and AIAgent parity test`
- Body: one paragraph describing the three additions (reference doc + sidebar, AIAgent parity test extended to cover Anthropic/Codex adapters, init-failure regression test) and crediting the GPT-5.4 branch as the source.

Do not squash with `613b5564`. The implementation commit and the docs/parity-test commit are separately reviewable.
