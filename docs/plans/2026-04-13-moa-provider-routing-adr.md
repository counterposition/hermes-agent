# ADR: Multi-Provider Routing for Mixture-of-Agents Tool

**Status:** Proposed (revised 2026-04-15 after adversarial review)  
**Date:** 2026-04-13  
**Scope:** `tools/mixture_of_agents_tool.py`, `hermes_cli/config.py`, minimal additions to `agent/auxiliary_client.py` (Codex/Anthropic adapter `reasoning_config` threading).

**Revision notes (2026-04-15):** Adversarial review identified (a) a stale reasoning table that silently dropped Nous/Copilot/OpenRouter-gating, (b) a broken direct-OpenAI-via-`custom` escape-hatch claim for GPT-5/o-series, and (c) shallow config validation that let model/provider drift silently degrade MoA. Fixes: rewrote the reasoning-kwarg table to mirror actual main-agent behavior and added a parity test to enforce ongoing sync; scoped GPT-5 direct-OpenAI out of this landing and documented the limitation; added `moa.enabled` kill switch, adaptive `min_successful_references` default, and non-blocking catalog-drift warnings.

**Revision notes (2026-04-15, pt. 2):** Second-pass review surfaced under-specified semantics that would let two implementors diverge. Resolved inline (no ambiguities deferred): kill-switch response shape, disposition of pre-existing `reference_models`/`aggregator_model` entry-point params, exact upstream source locations (with file:line) for the OpenRouter gate / Copilot dynamic support / xhigh clamp / Nous predicate, the `base_url` arg removed from `_reasoning_kwargs` as unused, catalog-drift test normalization rule, Codex warning-key contents, `emit_warnings=False` scope, and `debug_call_data["parameters"]` entry shape under per-entry provider.

**Revision notes (2026-04-15, pt. 3):** Codex adversarial review flagged four errors in pt. 2: (a) the Copilot normalization spec stopped at `xhigh→high` and silently ignored the main agent's full remap (`minimal→low`, else `medium`, else first supported); (b) the provider table dropped `ai-gateway` into "all others" even though upstream treats `ai-gateway.vercel.sh` as unconditionally reasoning-capable; (c) the `_deep_merge` clarification was backwards — empty-dict overrides on dict-typed defaults recurse and preserve the default rather than overwriting it, so `aggregator_model: {}` silently inherits the default instead of tripping validation; (d) the kill-switch response's `success: true` + echoed prompt is fail-open, mis-signalling an unsolved prompt as a solved MoA answer to any caller that doesn't branch on the additive `disabled` flag. All four fixed in this revision.

## Context

The Mixture-of-Agents (MoA) tool currently hardcodes its model roster and routes all API calls — both reference models and the aggregator — through a single OpenRouter client. The model list and aggregator are module-level constants:

```python
REFERENCE_MODELS = [
    "anthropic/claude-opus-4.6",
    "google/gemini-3-pro-preview",
    "openai/gpt-5.4-pro",
    "deepseek/deepseek-v3.2",
]
AGGREGATOR_MODEL = "anthropic/claude-opus-4.6"
```

This creates three problems:

### 1. No provider flexibility

Every reference model must be reachable via OpenRouter. Users who have direct subscriptions or cheaper credit pools elsewhere (e.g., an OpenAI Codex plan, direct Anthropic API access, Google Cloud credits) cannot route models through their preferred provider. The tool pays per-token OpenRouter markups even when a cheaper path exists.

### 2. No model customization without code changes

The reference model list is a Python constant. Changing a model requires editing source code and restarting Hermes. This is unacceptable for users on forks or long-lived custom builds — the models should track what's current and cost-effective without a code change. The existing docstring even acknowledges this: "To customize the MoA setup, modify the configuration constants at the top of this file."

### 3. Stale model defaults

The hardcoded list includes models that are no longer state-of-the-art (e.g., `gemini-3-pro-preview` has been superseded by `gemini-3.1-pro-preview`) and includes models with extreme per-token pricing (`gpt-5.4-pro`). Without config support, every user inherits the same defaults regardless of their cost sensitivity or provider access.

## Decision

Make the MoA model roster, provider routing, and operational parameters configurable via `config.yaml` under a new `moa` key. Each model entry specifies an explicit provider so the tool can route through the cheapest or most-credited path per model, and optionally a per-model reasoning effort so users can dial each model's thinking budget independently.

### Config schema

```yaml
moa:
  enabled: true              # kill switch; set false to disable MoA without deleting config
  reference_models:
    - model: "anthropic/claude-opus-4.6"
      provider: "openrouter"
      reasoning: "high"
    - model: "google/gemini-3.1-pro-preview"
      provider: "openrouter"
      reasoning: "medium"
    - model: "gpt-5.4"
      provider: "openai-codex"
      reasoning: "xhigh"
    - model: "deepseek/deepseek-v3.2"
      provider: "openrouter"
      # reasoning omitted → provider default
  aggregator_model:
    model: "anthropic/claude-opus-4.6"
    provider: "openrouter"
    reasoning: "high"
  reference_temperature: 0.6
  aggregator_temperature: 0.4
  min_successful_references: 2   # default adapts: 2 when ≥2 reference models, else 1
```

### Per-model reasoning effort

Each reference or aggregator entry accepts an optional `reasoning` field. Valid values are the same set accepted by `/reasoning_effort` and `agent.reasoning_effort` (defined in `hermes_constants.VALID_REASONING_EFFORTS`, parsed via `hermes_constants.parse_reasoning_effort`). Do not list them inline in this ADR — the constants module is the source of truth and listing them here invites drift.

- Omitted (key absent), `reasoning: null`, or `reasoning: ""` in YAML → no reasoning parameter is sent, and the provider's default thinking budget applies. All three forms are treated identically; users frequently write `null` or `""` to mean "unset" and a hard error there would be hostile. This is distinct from `"none"` (the string), which explicitly disables reasoning (`{"enabled": False}`).
- Invalid string → `ValueError` at load time, listing `VALID_REASONING_EFFORTS` **plus the string `"none"`** (since `"none"` is a valid input accepted by `parse_reasoning_effort` but is not a member of the tuple — the error message must name it explicitly or users won't discover it).
- Detection logic: the helper treats `entry.get("reasoning")` as "unset" when it's `None`, the empty string, or whitespace-only. For any other value, call `parse_reasoning_effort(value)` — a `None` return becomes a hard `ValueError`. (Note: `parse_reasoning_effort` itself returns `None` for empty input, but the MoA helper handles the empty case upstream so the only way to reach a `None` return is a genuine unrecognized string.)
- The parsed `reasoning_config` dict (`{"enabled": True, "effort": "high"}` or `{"enabled": False}`) is threaded through `_run_reference_model_safe` / `_run_aggregator_model`. How it reaches the wire depends on provider — see "Reasoning kwarg translation" below. Providers that don't support a given level surface an HTTP 400 which triggers the sticky unsupported-parameter fallback (see "Sticky fallback lifecycle").

### Reasoning kwarg translation

**This is a prerequisite implementation task, not an incidental detail.** The current MoA tool calls `client.chat.completions.create(..., extra_body={"reasoning": {"enabled": True, "effort": "xhigh"}})` — OpenRouter-specific. The main loop's `reasoning_config` is translated inside `run_agent.py` per-provider (Responses API for OpenAI/Codex, `thinking` block for Anthropic, `extra_body.reasoning` for OpenRouter). The MoA tool bypasses `run_agent.py` entirely, so something has to do that translation.

**Chosen approach: one local helper in `tools/mixture_of_agents_tool.py`, kept in parity with the main agent via a test.** Add `_reasoning_kwargs(provider: str, resolved_model: str, base_url: str, reasoning_config: dict | None) -> dict` that returns a dict to merge into `api_params`. A local helper beats "extract a shared module and refactor `run_agent.AIAgent._build_api_kwargs` to call it" because `run_agent.py` is the upstream hot spot — a persistent patch inside `_build_api_kwargs` would add a fresh merge-conflict surface on every rebase. That motivation survives.

What does not survive is the earlier claim that the local copy is "~20 lines and stable." The main agent's per-provider behavior has non-trivial gating that our helper must mirror. **Exact upstream sources (so implementors don't reverse-engineer from test bodies):**

- **OpenRouter family predicate**: `AIAgent._supports_reasoning_extra_body` at [run_agent.py:6368](run_agent.py:6368). Prefix allowlist: `deepseek/`, `anthropic/`, `openai/`, `x-ai/`, `google/gemini-2`, `qwen/qwen3`. **Duplicate inline** in `_reasoning_kwargs` — cannot import: it's an instance method keyed on `self.base_url`/`self.model`, and the Nous check (`"nousresearch" in base_url`) is braided into the same function. Lift the prefix list and the OpenRouter-match logic into a pure helper in the MoA module (`_openrouter_family_supports_reasoning(resolved_model: str) -> bool`); the parity test in step 5 holds it in sync with `_supports_reasoning_extra_body`.
- **Copilot dynamic support**: `hermes_cli.models.github_model_reasoning_efforts(model_id, catalog=None, api_key=None) -> list[str]` at [hermes_cli/models.py:1616](hermes_cli/models.py:1616). Returns the catalog's `capabilities.supports.reasoning_effort` list. **Import directly** — it's a module-level pure function, so no duplication and no drift surface. Empty list means "reasoning not supported" → emit no kwarg.
- **Full Copilot effort normalization** (not just `xhigh→high`): inline at [run_agent.py:6422-6430](run_agent.py:6422), inside `_github_models_reasoning_extra_body`. The complete cascade, which `_reasoning_kwargs` must mirror verbatim:
  1. `xhigh` with `high` in `supported_efforts` → `high`.
  2. Otherwise, if the requested effort is not in `supported_efforts`:
     - `minimal` with `low` in `supported_efforts` → `low`;
     - else if `medium` in `supported_efforts` → `medium`;
     - else `supported_efforts[0]`.
  3. Otherwise (requested effort is in `supported_efforts`) → passthrough.
  The upstream cascade also applies when `reasoning_config` is absent (defaulting to `"medium"`), but MoA's "reasoning unset" branch already short-circuits before translation, so that default-medium path doesn't apply to us. Still, the non-`xhigh` remaps are load-bearing: `parse_reasoning_effort` accepts `"minimal"`, so a user writing `reasoning: minimal` against a Copilot model advertising `[low, medium, high]` must be remapped to `low` — not dropped and not passed through. **Duplicate inline** in `_reasoning_kwargs` (still ~8 lines, not worth extracting upstream) and expand the parity test matrix to include at least one `minimal` and one "requested-effort-not-in-supported" case against a Copilot model to cover this cascade. Without these cases the `xhigh`-only test would pass while the implementation silently diverged from upstream on the other unsupported efforts.
- **Nous unconditional reasoning**: the upstream path detects Nous via `"nousresearch" in base_url` inside `_supports_reasoning_extra_body`. MoA doesn't need that heuristic — `provider` is explicit in the config entry, so `provider == "nous"` is the gate directly. No upstream reference needed; the parity test just asserts MoA's Nous branch produces the same kwargs as the main agent's Nous route.

The local copy is ~50-60 lines, not ~20, and the maintenance burden is real: when upstream rotates a gated model family or adds a Copilot reasoning model, the copy drifts silently unless disciplined.

The discipline is enforced by a **parity test** (see step 5) that runs a fixed set of `(provider, model, effort)` tuples through both `AIAgent._build_api_kwargs` and `_reasoning_kwargs` and asserts the wire kwargs match. When either side changes, the test fails in CI on the next rebase — in a file we own, with no upstream merge conflict. Total rebase cost: the parity test only, and only when the underlying behavior actually changed.

Per-provider behavior:

Valid providers are those accepted by `resolve_provider_client` / enumerated in `hermes_cli.models.list_available_providers()`, which now derives from `CANONICAL_PROVIDERS` (commit 204e9190), strengthening the "fail at load time with a valid id" check: openrouter, nous, openai-codex, copilot, copilot-acp, gemini, huggingface, zai, kimi-coding, kimi-coding-cn, minimax, minimax-cn, kilocode, anthropic, alibaba, qwen-oauth, xiaomi, opencode-zen, opencode-go, ai-gateway, deepseek, arcee, xai, custom. There is no `openai` provider id. Direct-OpenAI access via `provider: custom` + `OPENAI_BASE_URL=api.openai.com` works for chat-completions-compatible models (GPT-4o, older slugs) but is **not a supported route for GPT-5/o-series in this landing** — `_needs_codex_wrap` (auxiliary_client.py:1305-1326) only auto-wraps into the Responses API when `"codex"` is in the model name, so a GPT-5.x call through `custom` hits `/v1/chat/completions` and is rejected upstream with `unsupported_api_for_model`. Users who want GPT-5 should route via `openrouter` or `openai-codex`. A one-line extension to `_needs_codex_wrap` to detect the `gpt-5`/`o\d` pattern on `api.openai.com` would unlock this path and is a reasonable follow-up, but is out of scope here.

| Provider | `reasoning_config` empty / `None` | `{"enabled": False}` | `{"enabled": True, "effort": X}` |
|---|---|---|---|
| `openrouter` | no kwarg | if model family supports reasoning: `extra_body={"reasoning":{"enabled":False}}`; else no kwarg | if model family supports reasoning: `extra_body={"reasoning":{"enabled":True,"effort":X}}`; else no kwarg. The "supports reasoning" predicate mirrors the main agent's OpenRouter gate — see `test_reasoning_not_sent_for_unsupported_openrouter_model` (minimax-family excluded) and `test_reasoning_sent_for_supported_openrouter_model` (qwen-family included) for the expected shape. |
| `nous` | no kwarg | `extra_body={"reasoning":{"enabled":False}}` | `extra_body={"reasoning":{"enabled":True,"effort":X}}` — same wire format as OpenRouter. The main agent sends reasoning to Nous unconditionally (see `test_reasoning_sent_for_nous_route`); MoA does the same. |
| `copilot` / `copilot-acp` | no kwarg | if model supports reasoning: `extra_body={"reasoning":{"enabled":False}}`; else no kwarg | if model supports reasoning: `extra_body={"reasoning":{"effort": remap(X)}}`; else no kwarg. `remap(X)` is the full cascade from `_github_models_reasoning_extra_body` ([run_agent.py:6422-6430](run_agent.py:6422)): `xhigh→high` if supported; else for any `X` not in supported_efforts, `minimal→low`, else `medium`, else `supported_efforts[0]`; passthrough only when `X` is already in supported_efforts. See `test_reasoning_sent_for_copilot_gpt5`, `test_reasoning_xhigh_normalized_for_copilot`, `test_reasoning_omitted_for_non_reasoning_copilot_model` for the reference behavior; parity matrix must also cover `minimal` and at least one other unsupported-effort case. |
| `openai-codex` | no kwarg | `reasoning_config={"enabled":False}` kwarg (adapter skips Responses `reasoning`) | `reasoning_config={"enabled":True,"effort":X}` kwarg (adapter emits Responses `reasoning={"effort":X, "summary":"auto"}`) |
| `anthropic` | no kwarg | no thinking block | `reasoning_config={"enabled":True,"effort":X}` kwarg → `AnthropicAuxiliaryClient` emits `thinking` block via `build_anthropic_kwargs` |
| `custom` | no kwarg | no kwarg | `reasoning_effort=X` unconditionally — if the model doesn't support it, the runtime reasoning-unsupported fallback (see "Sticky fallback lifecycle") learns and strips on retry. GPT-5/o-series via `custom` + `api.openai.com` is out of scope in this landing (see provider-list note above). |
| `ai-gateway` | no kwarg | `extra_body={"reasoning":{"enabled":False}}` | `extra_body={"reasoning":{"enabled":True,"effort":X}}` — same wire format as OpenRouter/Nous, sent unconditionally. Upstream's `_supports_reasoning_extra_body` ([run_agent.py:6377-6378](run_agent.py:6377)) short-circuits to `True` for any `ai-gateway.vercel.sh` base URL, so MoA must also treat `provider: ai-gateway` as reasoning-capable unconditionally. Parity matrix must include at least one `ai-gateway` case so this row doesn't silently drift back to "all others". |
| all others (gemini, zai, arcee, kimi-coding-cn, xai, kimi-coding, minimax, minimax-cn, kilocode, alibaba, qwen-oauth, xiaomi, opencode-zen, opencode-go, deepseek, huggingface) | no kwarg | no kwarg | no kwarg — reasoning not threaded through the wrapper for these providers in this landing. Document as a known limitation; users setting `reasoning` on an unsupported provider get provider-default silently (the silent-accept blind spot). Adding per-provider support is a follow-up. |

**Codex/Anthropic wrapper work is a hard prerequisite, not a "verify" step.** Confirmed by reading the current code:

1. `_CodexCompletionsAdapter.create` (auxiliary_client.py:289-298) builds `resp_kwargs` and comments: "the Codex endpoint does NOT support max_output_tokens or temperature — omit to avoid 400 errors." It **already silently drops `temperature`**, so we do *not* need to seed Codex into `_TEMPERATURE_UNSUPPORTED` — there is no 400 to detect. It does **not** read `reasoning_config` from kwargs. Add: pull `reasoning_config` from kwargs, and when `{"enabled": True, "effort": X}`, set `resp_kwargs["reasoning"] = {"effort": X, "summary": "auto"}` and `resp_kwargs["include"] = ["reasoning.encrypted_content"]` (mirroring `run_agent.py:5993-5994`). When `{"enabled": False}`, emit no `reasoning` key.
2. `_AnthropicCompletionsAdapter.create` (auxiliary_client.py:508-516) hardcodes `reasoning_config=None` when building Anthropic kwargs. Change to `reasoning_config=kwargs.get("reasoning_config")`. `build_anthropic_kwargs` already accepts `{"enabled": True, "effort": X}` directly and does the effort→budget_tokens translation itself (see anthropic_adapter.py:1315-1333) — no duplicate translation needed.
3. No separate async work is required: `_AsyncCodexCompletionsAdapter.create` (auxiliary_client.py:459-461) and `_AsyncAnthropicCompletionsAdapter.create` (around line 571) are `asyncio.to_thread(self._sync.create, **kwargs)` passthroughs, so fixing the sync adapters automatically covers the async path that MoA uses.

Scope these wrapper fixes to the minimum needed for MoA. If Anthropic reasoning support turns out to require a larger adapter refactor, **scope `provider: anthropic` with reasoning out of the initial landing** — keep `provider: anthropic` without reasoning working, document reasoning as a follow-up, don't block the routing change.

**`_reasoning_kwargs` emits at most one wire kwarg**, selected by provider: `extra_body.reasoning` (openrouter, nous, copilot, copilot-acp — gated by the per-provider predicate above), `reasoning_effort` (custom), `reasoning_config` (openai-codex, anthropic), or nothing (providers in the "all others" row, or any provider where the gate says "not supported"). The choice of `reasoning_config` as the Codex/Anthropic kwarg matches the existing `run_agent.py` convention and keeps the adapter changes minimal — both paths already think in those terms internally.

**Signature:** `_reasoning_kwargs(provider: str, resolved_model: str, reasoning_config: dict | None) -> dict`. An earlier draft included `base_url: str` by analogy with the main agent's `_supports_reasoning_extra_body`, which dispatches on base URL. MoA doesn't need it — `provider` is explicit per entry, so the openrouter-vs-nous branch is decided directly from `provider`, not the URL. Dropping the arg removes a dead param from the call site.

**Known blind spot: silent-accept providers.** Detection requires an HTTP 400. Providers (or OpenAI-compatible proxies in front of them) that accept unknown parameters and silently discard them produce no error signal, so a user setting `reasoning: high` against such a model gets the provider's default with no warning. **OpenRouter is the notable case**: it proxies to many backends (DeepSeek, Gemini via some routes, legacy chat models) that don't support `extra_body.reasoning`, and OpenRouter forwards the call without surfacing an error. This is undetectable without a server-side probe (comparing token counts, response latencies, or a canary prompt), which we explicitly do not implement — the cost/complexity isn't justified for what is a configuration error on the user's part. Mitigation: the `logger.info` line per reference call echoes the configured effort label, so users investigating an unexpected response can cross-check against provider-side logs or billing to confirm the parameter was honored. Call this out **by name** (OpenRouter) in the MoA reference page so users know where to look when a reasoning setting seems inert.

Reasoning is intentionally per-model rather than a global `moa.reasoning_effort`. The point of MoA is diversity; forcing every model to the same effort level defeats that, and the user's cost preferences may differ per provider (a Codex-plan GPT-5.4 call is "free" at `xhigh`, while an OpenRouter pay-per-token Opus at `xhigh` is not).

### Routing mechanism

Each model entry's `provider` field is passed to `resolve_provider_client()` from `agent/auxiliary_client.py` — the same provider router used by Hermes' main agent loop and delegation system. This gives us:

- **Auth resolution:** Each provider uses the correct API key from `.env` (`OPENAI_API_KEY` for Codex, `OPENROUTER_API_KEY` for OpenRouter, etc.)
- **API format adaptation:** The Codex/Responses API is handled transparently via `CodexAuxiliaryClient`, so a Codex model (e.g. `gpt-5.4`) through `openai-codex` works without special-casing
- **Client caching:** `resolve_provider_client()` handles internal client reuse, so we don't create redundant connections
- **Async support:** The existing `async_mode=True` flag on `resolve_provider_client` returns an `AsyncOpenAI`-compatible client

### Default behavior

When `moa` is absent from config or when an entry omits `provider`, the tool falls back to OpenRouter routing. This preserves backward compatibility — existing deployments with `OPENROUTER_API_KEY` continue working unchanged. `moa.enabled` defaults to `True` when absent, so users inherit MoA behavior without a config migration.

### Fallback chain for individual models

If a reference model's provider fails (auth error, rate limit, model unavailable after retries), that model is marked as failed and the MoA proceeds with remaining successful responses, just as it does today. The `min_successful_references` threshold still governs whether the overall MoA call fails or proceeds with partial results.

### Aggregator failure behavior

If the aggregator call fails after its own retries, the entire MoA invocation raises — there is no fallback to a reference response, and no degraded mode. This matches current behavior (the aggregator is the tool's output) and is called out here because users routing the aggregator through a flaky subscription-backed provider will see the whole tool fail, not a partial result.

**Mitigation:** The MoA reference page and config comment recommend keeping the aggregator on the most-reliable, highest-quota provider the user has (typically OpenRouter on a funded account, or a direct Anthropic/OpenAI key with headroom). Reference models are where subscription-backed routing pays off; the aggregator is where reliability matters. The unified Codex warning (see Risks) mentions aggregator failure as part of its message when an aggregator is Codex-routed, so there's no separate warning mechanism.

A fallback-aggregator config field (e.g. a second-choice aggregator invoked on primary failure) was considered and rejected: it doubles the aggregator token cost on every failure path, and the failure mode it protects against (subscription outage) is better solved by the user changing their primary aggregator than by silently paying for a backup.

## Implementation Plan

### 1. Add `moa` defaults to `hermes_cli/config.py`

Insert a `moa` subtree into `DEFAULT_CONFIG` with the current hardcoded values as defaults. The default `reference_models` list stays OpenRouter-only for backward compatibility.

**No `_config_version` bump.** The loader at `hermes_cli/config.py:2273` already does `_deep_merge(DEFAULT_CONFIG, user_config)`, so a missing `moa` key transparently picks up defaults on every load. Version bumps are reserved for changes that must rewrite user files (renames, schema moves); this change is purely additive.

Scoping the diff to a single dict insertion also minimizes rebase conflicts against upstream edits to `DEFAULT_CONFIG`.

### 2. Refactor `tools/mixture_of_agents_tool.py`

**Keep the module-level constants** (`REFERENCE_MODELS`, `AGGREGATOR_MODEL`, `REFERENCE_TEMPERATURE`, `AGGREGATOR_TEMPERATURE`, `MIN_SUCCESSFUL_REFERENCES`) as fallback defaults. Upstream actively maintains these; leaving them in place lets upstream's rotations flow in via merge unchanged.

**Add one helper, `_load_moa_config(emit_warnings: bool = False) -> dict`, near the top of the file.** `emit_warnings` gates the one-time-per-process Codex rate-limit warnings described below — preflight passes `False`, the entry point passes `True`.

- Calls `hermes_cli.config.load_config()` and reads `config.get("moa", {})`.
- Falls back field-by-field to the existing module constants for any key the user omitted.
- Normalizes each reference-model entry to `{model, provider, reasoning_config}`; if `provider` is missing on an entry, defaults to `"openrouter"`. Reasoning handling is specified separately below.
- **Accepts string shorthand** for `aggregator_model` and for reference-model entries: a bare string (e.g. `aggregator_model: "anthropic/claude-opus-4.6"` or `- "gpt-5.4"` in the list) is expanded to `{model: <str>, provider: "openrouter"}`. Users will reach for this by analogy with the main-loop `model:` field; rejecting it would be a needless papercut. Dict form is required to set `provider` or `reasoning`.
- Validates shape: after applying fallbacks and string-shorthand expansion, `reference_models` must be a non-empty list and `aggregator_model` must be a dict with a `model` key. An empty list or `aggregator_model: null` in YAML (present but empty) bypasses the field-by-field fallback because the key is technically set, so the helper checks these explicitly and raises `ValueError` with a targeted message ("moa.reference_models must be a non-empty list" / "moa.aggregator_model must specify a 'model' field"). Otherwise an empty roster would reach the fanout and crash in `asyncio.gather` / `max()` with an opaque error.
- Validates providers: unknown `provider` values raise `ValueError` listing valid ids from `hermes_cli.models.list_available_providers()`. Fail at load time, not at first API call.
- **Soft-validates model/provider pairing: non-blocking warning only.** For each resolved entry, call `hermes_cli.models.provider_model_ids(provider)`. If the returned list is **non-empty** and the entry's `model` (post-normalization) is not in it, `logger.warning("MoA: model %r not in %s catalog — may fail at call time", model, provider)`. Do **not** raise: `provider_model_ids` returns `[]` for providers without a `/models` endpoint (OAuth-only, direct-API, `custom`), static catalogs lag behind just-released slugs, and legitimate MoA use cases include beta models and fine-tunes not in the catalog. Hard-failing here would bite real users. The warning alone gives drift visibility; the preflight `check_moa_requirements` + aggregator failure still protect correctness at invocation time.
- Validates reasoning: the helper first checks `entry.get("reasoning") is None` — this single branch covers both the key being absent and `reasoning: null` in YAML, and in both cases `reasoning_config` is `None` (no kwarg at the API boundary). Only for a non-`None` value does it call `hermes_constants.parse_reasoning_effort(value)`. `parse_reasoning_effort` returns `None` for unrecognized inputs; in that branch the helper raises `ValueError` listing `VALID_REASONING_EFFORTS`. YAML `null` is a first-class "unset" marker rather than a typo.
- **Validates `enabled` kill switch**: `moa.enabled` is an optional bool, default `True`. When `False`, the helper still parses and validates the rest of the config (so toggling back on is an immediate-effect operation, not a "now go fix all the errors that accumulated while disabled" experience), and returns the config dict with `enabled=False` set. The entry point short-circuits on `enabled=False` by returning the user-prompt passthrough response and logging one line at INFO. `check_moa_requirements` returns `False` when `enabled=False` so the tool is also hidden from the preflight-gated menu. Kill switch reasons: emergency rollback when a provider adapter drifts, A/B testing MoA off vs on, and temporarily disabling without losing the roster config.
- Validates `min_successful_references`: must be an int with `1 <= min_successful_references <= len(reference_models)`. A value outside that range raises `ValueError` at load time. **Default is adaptive**: when the key is absent, use `min(2, len(reference_models))` — i.e., `2` for rosters of ≥2, `1` for a single-model roster. Previous default was `1` unconditionally; bumping to `2` on typical rosters means a single silent reference failure no longer masks itself as a successful MoA run. Users on cost-sensitive rosters who want the old behavior set `min_successful_references: 1` explicitly. `0` and values exceeding the roster size both fail fast (unreachable-success and nonsense-input respectively).
- Returns a single dict consumed by the entry point.

All new logic lives in this one function. Rough size: `_load_moa_config` itself is ~50-70 lines (shape validation, provider/reasoning/enabled/min_successful_references checks, soft catalog warning, string-shorthand expansion). The companion `_reasoning_kwargs` adds another ~50-60 lines for the per-provider translation matrix (see "Reasoning kwarg translation" above). Both live in the MoA tool module, which upstream rarely touches.

**Replace the `_get_openrouter_client()` call sites with per-model provider routing:**

- `_run_reference_model_safe()` changes signature from `model: str` to `model_entry: dict` (`{model, provider, reasoning_config}`). Inside, call `resolve_provider_client(provider, model=model, async_mode=True)` from `agent/auxiliary_client.py` to get a properly configured client. Translate `reasoning_config` into the right wire kwargs via `_reasoning_kwargs(provider, resolved_model, reasoning_config)` (see "Reasoning kwarg translation" above) and merge into `api_params` before calling `chat.completions.create`. The retry loop and error handling are unchanged, plus the new reasoning-unsupported 400 fallback described below.
- `_run_aggregator_model()` accepts a dict and resolves its client and reasoning kwargs the same way.
- Prerequisite wrapper verification (Codex, Anthropic) is called out in the translation section above — any required wrapper fixes land *before* the MoA changes use that provider.
- Add one `logger.info("MoA reference %s via %s (reasoning=%s)", model, provider, effort_label)` line per call for observability across providers; `effort_label` is the raw string from config or `"default"` when absent.

Model ID normalization is **not** re-implemented here — `resolve_provider_client` already calls `_normalize_resolved_model` (auxiliary_client.py:1382), which routes through `hermes_cli.model_normalize.normalize_model_for_provider`. A user who writes `anthropic/claude-opus-4.6` under `provider: anthropic` gets the slug translated to `claude-opus-4-6` for free.

**Update `mixture_of_agents_tool()` entry point:**

- `cfg = _load_moa_config(emit_warnings=True)` at the top. Call-time load so profile switches take effect without a restart. Verified: `hermes_cli.config.load_config()` at `hermes_cli/config.py:2253` has no module-level cache — it reads from `get_config_path()` on every invocation and `get_config_path()` resolves against the current `HERMES_HOME` (which profile switches already update). So profile switches naturally propagate; no cache-bypass is needed. If a future refactor adds caching, the cache must key on the resolved config path so the profile-switch path remains correct — that constraint belongs in the refactor's review, not here. Per-call disk I/O is acceptable: MoA invocations are expensive (N parallel LLM calls), so a single YAML read is noise. The `emit_warnings` flag is what gates Codex warnings (see below); preflight passes `emit_warnings=False`. (`/model` intentionally does *not* cascade into MoA — see note below.)
- **Short-circuit on `enabled=False` — fail closed, not fail open.** Immediately after loading, if `cfg["enabled"] is False`, `logger.info("MoA disabled via config; short-circuiting without calling any model")` once and return the JSON string of:
  ```python
  {
      "success": False,
      "response": "",                    # empty — no model produced an answer
      "models_used": {
          "reference_models": [],
          "aggregator_model": "",
      },
      "error": "MoA disabled via moa.enabled=false",
  }
  ```
  An earlier draft used `success: True` with `response: user_prompt` plus an additive `disabled: True` flag. That was fail-open: any caller that read `response` without branching on the flag would treat the unsolved user prompt as a successful MoA answer — the wrong failure mode for a tool whose contract is synthesis. Under the fail-closed shape, callers branching on `success` route to their normal error path, and the `error` string is distinct enough from a real failure (auth, network, no references succeeded) that operators can tell the difference in logs. The existing error-response shape (`tools/mixture_of_agents_tool.py:391-399`) already has `{success: False, response, models_used, error}`, so this reuses the contract rather than inventing a third state. `check_moa_requirements` returning `False` when `enabled=False` already hides the tool from preflight-gated menus in most code paths, so this short-circuit is defense-in-depth for callers that reach the tool by other routes (direct import, SDK consumer, delegation). Fast path preserved: no provider-client construction, no LLM calls, no accidental billing.
- Read `ref_models = cfg["reference_models"]`, `agg = cfg["aggregator_model"]`.
- Pass model dicts through to the internal functions.

**Simplify temperature handling:** delete the `model.lower().startswith('gpt-')` heuristic. Always pass `temperature` on first attempt; on HTTP 400, inspect the error and fall back per the detection rules below.

**Reasoning-unsupported fallback mirrors temperature.** Both live behind a shared detection + sticky-cache mechanism so the interactions are well-defined rather than two independent string-matchers racing each other.

**Unsupported-parameter detection (shared by temperature and reasoning):**

1. **Prefer structured fields.** OpenAI-compatible error bodies expose `error.code == "unsupported_parameter"` and `error.param` (e.g. `"temperature"`, `"reasoning"`). Match on those first; they are stable across model rotations.
2. **Fall back to substring matching** only when structured fields are absent (some providers wrap errors). Substring sets: for temperature, `temperature` + one of `not supported | unsupported | does not support`; for reasoning, any of `reasoning`, `thinking`, `extended_thinking` with the same "unsupported" cues. Gemini/Anthropic wording variants belong in this table.
3. **Precedence and single-param retries.** Structured `error.param` is authoritative — drop exactly that parameter, record it, and retry. On the substring path, always drop **exactly one** parameter per retry, even if both families' substrings appear in the message. A 400 body like `"reasoning_effort requires temperature=1"` mentions both words but only one is the actual cause; dropping both would populate a false-positive entry in the other sticky cache. Tie-break order when both substrings match: drop `reasoning` first (newer feature, more likely to be the culprit on legacy models), retry, and only drop `temperature` if the next call 400s with the temperature substring. Worst case is three round-trips per `(provider, resolved_model)` to reach steady state — still bounded, and subsequent calls pay nothing. The N-retry pathology the earlier draft worried about is already bounded by there being only two parameters in play.
4. **No match → don't fall back.** Re-raise as a normal error. Silently swallowing unrelated 400s would mask real bugs.

**Sticky fallback lifecycle.** Two process-level dicts — `_TEMPERATURE_UNSUPPORTED: dict[tuple[str, str], bool]` and `_REASONING_UNSUPPORTED: dict[tuple[str, str], bool]` — keyed by `(provider, resolved_model)` where `resolved_model` is the post-normalization slug returned as the second tuple element from `resolve_provider_client()` (auxiliary_client.py:1318). Keying on the resolved slug — not the user-written one — means a user who writes `anthropic/claude-opus-4.6` under `provider: anthropic` and a user who writes the pre-normalized `claude-opus-4-6` share one cache entry instead of populating two duplicates that each cold-400. Same-model/different-provider still diverges correctly because `provider` is part of the key. Entries live for the process lifetime; a user who fixes their config or a provider that re-enables a parameter recovers on the next Hermes restart. Hermes sessions are typically short enough that process-lifetime stickiness is fine and a TTL would add complexity without clear benefit. This covers the case where a user configures `reasoning: high` against a model that doesn't accept a reasoning parameter at all (e.g. a legacy chat-only model routed through OpenRouter).

**Concurrency note.** MoA fans out via `asyncio.gather`, so multiple coroutines can observe a cold 400 and write to the same sticky cache entry concurrently. This is safe without a lock: writes are idempotent (`True` for the same key), and Python dict single-key assignment is atomic under the GIL. Tests that assert cache contents must serialize the mock (e.g. single-model roster, or `asyncio.Lock` inside the mock client) — otherwise ordering of per-parameter population across retries is non-deterministic.

**No seeding.** An earlier draft specified a module-import-time seed that pre-populated `_TEMPERATURE_UNSUPPORTED` for gpt-5/o-series slugs. On closer inspection the seed was unworkable: `provider_model_ids("custom")` returns `[]` (no catalog to enumerate), and seeding off `provider_model_ids("openrouter")` would be wrong because OpenRouter normalizes `temperature` for those slugs — a seed there would disable a supported parameter. GPT-5/o-series direct-OpenAI via `custom` is separately out of scope for this landing (see provider-list note), so the scenario the seed was trying to optimize doesn't even reach the parameter-fallback layer: the request is rejected at `unsupported_api_for_model` before it ever negotiates `temperature`.

Rely on the runtime fallback for any future `custom`-routed model that rejects `temperature` or `reasoning_effort`. First use pays one extra HTTP round-trip to learn, then every subsequent call in the process skips the rejected kwarg. On an MoA invocation that takes 30-60s end-to-end, a one-time ~500ms learning cost is noise. The `gpt-` prefix heuristic being deleted is not replaced by anything; the runtime cache is the correctness mechanism.

**Leave `tools/openrouter_client.py` untouched** even though this tool stops using it for the hot path. Upstream still uses it, and the module-constant fallbacks are all OpenRouter-routed, so the file remains load-bearing.

**Leave `get_available_models()` and `get_moa_configuration()` returning the module constants.** They're introspection helpers used by the `__main__` demo block and external callers; making them config-aware is scope creep (they'd need to do disk I/O, handle ValueError, etc.). A one-line docstring addendum noting "reflects fallback defaults, not the resolved roster" is enough. If a future need arises, add a separate `get_resolved_moa_configuration()` that calls `_load_moa_config` — but not as part of this change.

**Fix `check_moa_requirements` to reflect actual config.** The current check validates `OPENROUTER_API_KEY` only, which is wrong once users route some or all models elsewhere.

The registry's `check_fn` contract is strictly `() -> bool` (see `tools/registry.py:131`; exceptions are caught and coerced to `False`). There is no rich-error return channel. New behavior:

1. Call `_load_moa_config(emit_warnings=False)` inside a `try/except ValueError`. If it raises (invalid provider, invalid reasoning string, empty `reference_models`, `min_successful_references` out of range, etc.), `logger.error("MoA config invalid: %s", exc)` so the reason is visible in startup logs, and return `False`. Do not let the exception propagate.
2. `emit_warnings=False` suppresses the Codex warnings on the preflight path; those are reserved for actual invocations so users don't see them out of context during startup or tool-menu rendering.
3. **If `cfg["enabled"] is False`, return `False`** — preflight-gated menus hide the tool so users don't invoke it by accident while the kill switch is flipped. The short-circuit in the entry point handles the case where someone calls the tool anyway (direct code path, SDK consumer, etc.), so this is defense-in-depth rather than the only line of protection.
4. On successful load: collect the set of distinct providers in use across reference models and the aggregator.
5. For each provider, delegate to the same auth-presence check that `resolve_provider_client` uses internally (e.g. per-provider env-var lookup via `hermes_cli.models`). On any missing credential, `logger.error` listing *every* missing provider credential (not just the first) and return `False`.
6. All credentials present → return `True`.

If the resolved config uses only Codex + Anthropic, OpenRouter's absence is not an error. The OpenRouter-specific check stays reachable for callers that still want it, but `check_moa_requirements` itself becomes config-aware. This is a one-time fix that must land with the routing change; deferring it would ship a broken preflight.

**Update `requires_env` on the registry binding** (`tools/mixture_of_agents_tool.py:559`) from `["OPENROUTER_API_KEY"]` to `[]`. The list is now config-dependent and can't be expressed statically; `check_moa_requirements` is the source of truth. Leaving `OPENROUTER_API_KEY` hardcoded would cause non-OpenRouter-only configs to show a spurious missing-env warning in the tool menu.

### 3. Tool schema and existing per-invocation parameters

**Current signature** (`tools/mixture_of_agents_tool.py:233-237`):
```python
async def mixture_of_agents_tool(
    user_prompt: str,
    reference_models: Optional[List[str]] = None,
    aggregator_model: Optional[str] = None,
) -> str
```
`reference_models` and `aggregator_model` already exist as per-invocation overrides — they are **not hypothetical**. The original framing ("we could add...") was wrong. We need an explicit disposition:

**Decision: keep both params, typed as today (`List[str]` / `str`), and treat every entry as OpenRouter-routed.** When either is provided:
- String entries are expanded to `{model: <str>, provider: "openrouter", reasoning_config: None}` — identical to the string-shorthand rule in `_load_moa_config`.
- The config-loaded roster is overridden *only in the slot that was passed*. Passing `reference_models=[...]` does not touch the aggregator; passing `aggregator_model="..."` does not touch references. This matches the "partial override" mental model callers already have.
- `provider` and `reasoning` cannot be set via these params in this landing. Adding a richer per-invocation override would require a list-of-dicts schema on the tool, which adds context cost on every tool call. Defer unless a clear use case appears.
- `moa.enabled: false` short-circuits **before** these overrides are consulted. A disabled MoA does not run arguments you passed it.
- The catalog-drift soft-validation and Codex plan warning *also* apply to override paths — overrides go through the same normalization pipeline, not around it.

**Why not remove them?** They're part of the current tool's public surface and may be used by SDK consumers or delegation callers. Removing would be a silent breaking change. Keeping them typed as-is, with an OpenRouter-only semantic, preserves every existing call site.

**Why not error?** Erroring on provided overrides would also be a breaking change, and the OpenRouter-only fallback matches the pre-refactor behavior exactly.

Tool *schema* exposed to the model: continue to expose only `user_prompt`, per the existing schema. Internal callers that pass the other params keep working; the model is not taught a larger schema it would pay tokens for.

Note that `/model` changes the main agent's model but does **not** affect MoA's reference roster or aggregator — those are governed solely by the `moa` config block (or the `reference_models` / `aggregator_model` override params above). Worth calling out in docs so users don't expect `/model anthropic/claude-opus-4.6` to cascade into MoA.

### 4. Update `hermes_cli/tools_config.py`

One-line tweak to the `moa` toolset description noting that models are configurable under `moa:` in `config.yaml`. The interactive menu doesn't need to edit list-of-dicts — YAML is the config path.

### 5. Tests

Add `tests/tools/test_mixture_of_agents_tool_provider_routing.py` as a **new file** (new files never conflict on rebase).

Tests below are split into **core contract** (load-bearing behavioral guarantees; refactors must preserve them) and **implementation detail** (useful regression guards that may be rewritten if the implementation changes shape). The split exists so future maintainers know which tests to update carefully vs. which can be regenerated.

**Core contract:**

- Absent `moa` key → `_load_moa_config()` returns the module-constant defaults (backward-compat contract).
- Missing `provider` field → defaults to `"openrouter"`.
- Unknown `provider` → `ValueError` at load time.
- **Kill switch**: `moa.enabled: false` → `_load_moa_config` still parses and validates the rest of the block (so invalid-but-disabled configs still raise `ValueError` — errors aren't deferred), but the returned dict has `enabled=False`. Entry point short-circuits, logs one INFO line, and returns **the fail-closed error shape**: `{success: False, response: "", models_used: {reference_models: [], aggregator_model: ""}, error: "MoA disabled via moa.enabled=false"}`. Assert `success is False`, `response == ""`, `error` contains `"moa.enabled=false"`, and `models_used` is zeroed. `check_moa_requirements` returns `False`. Default when `enabled` key absent is `True`.
- **Empty-dict override detection for `aggregator_model`**: config with `moa: {aggregator_model: {}}` → `ValueError` at load time, via the raw-YAML re-read path (not via the merged-dict path, which inherits the default and masks the typo). Assert the message names the field. Analogous case for `reference_models` items of shape `{}`.
- **Model-catalog soft validation**: entry with a model not in `provider_model_ids(provider)` logs a warning but does **not** raise, when the catalog is non-empty. Entry against a provider where `provider_model_ids` returns `[]` (OAuth/direct-API/`custom`) produces no warning — silent pass. Assert via caplog.
- String shorthand: `aggregator_model: "foo/bar"` expands to `{model: "foo/bar", provider: "openrouter"}`; a bare string in the `reference_models` list expands the same way.
- Empty `reference_models: []` → `ValueError` at load time, with a message naming the field.
- `aggregator_model: null` or `aggregator_model: {}` → `ValueError` at load time, with a message naming the field.
- `min_successful_references` out of range (`0`, or greater than `len(reference_models)`) → `ValueError` at load time.
- **Adaptive default for `min_successful_references`**: roster of 3 with no explicit value → resolved `min_successful_references == 2`; single-model roster → `1`. User-set `min_successful_references: 1` on a 3-model roster stays `1` (explicit beats default).
- Missing `reasoning` field, `reasoning: null`, and `reasoning: ""` all produce `reasoning_config == None` and the per-provider reasoning kwargs are *absent* from the final `api_params` (assert via `call_args.kwargs` on the mocked client). Single parametrized test covering all three forms.
- Invalid `reasoning: "extreme"` → `ValueError` at load time, and the error message contains both every member of `VALID_REASONING_EFFORTS` and the literal string `"none"`.
- Per-provider reasoning translation (parametrized over providers):
  - `openrouter` + supported-family model (e.g. `qwen/qwen3.5-plus-02-15`) + `reasoning: "xhigh"` → `extra_body={"reasoning": {"enabled": True, "effort": "xhigh"}}`.
  - `openrouter` + supported-family model + `reasoning: "none"` → `extra_body={"reasoning": {"enabled": False}}`.
  - `openrouter` + unsupported-family model (e.g. `minimax/minimax-m2.5`) + `reasoning: "high"` → **no reasoning kwarg emitted**. Gate mirrors main agent's predicate.
  - `nous` + `reasoning: "medium"` → `extra_body={"reasoning": {"enabled": True, "effort": "medium"}}` unconditionally; parity with `test_reasoning_sent_for_nous_route`.
  - `copilot` + `gpt-5.4` + `reasoning: "xhigh"` → `extra_body={"reasoning": {"effort": "high"}}` (xhigh clamped to high).
  - `copilot` + `gpt-4.1` + `reasoning: "high"` → **no reasoning kwarg emitted** (non-reasoning model).
  - `copilot` + `gpt-5.4` (supports `[low, medium, high]`) + `reasoning: "minimal"` → `extra_body={"reasoning": {"effort": "low"}}` — mirrors upstream's `minimal→low` remap ([run_agent.py:6425-6426](run_agent.py:6425)). Required case; without it the `xhigh`-only test would pass on a broken-remap implementation.
  - `copilot` + a Copilot model that supports only, say, `[medium, high]` + `reasoning: "minimal"` → `extra_body={"reasoning": {"effort": "medium"}}` — the `medium` branch of the cascade. Use a real Copilot model slug whose catalog advertises that support set, or monkeypatch `github_model_reasoning_efforts` in-test.
  - `ai-gateway` + `reasoning: "high"` → `extra_body={"reasoning": {"enabled": True, "effort": "high"}}` unconditionally; `ai-gateway` + `reasoning: "none"` → `extra_body={"reasoning": {"enabled": False}}`. Parity with [run_agent.py:6377-6378](run_agent.py:6377) where the base URL short-circuits to reasoning-supported. Required case; without it ai-gateway could silently regress to the "all others" bucket.
  - `custom` + `reasoning: "high"` → `reasoning_effort="high"` unconditionally. Models that reject the kwarg rely on the runtime fallback; the test asserts the kwarg is present on first attempt, not that it survives a 400.
  - `openai-codex` + `reasoning: "high"` → client receives kwargs that `CodexAuxiliaryClient` translates into the Responses-API `reasoning={"effort":"high", ...}`. **This translation must be asserted directly in the auxiliary-client test suite** (not only at the MoA boundary) so a main-loop regression doesn't silently break MoA and vice versa.
  - `anthropic` + `reasoning: "high"` → `AnthropicAuxiliaryClient` emits a `thinking` block; same cross-suite assertion requirement.
- **Parity test with main agent (load-bearing).** For each `(provider, model, effort)` tuple in a fixed matrix — one supported and one unsupported per gated provider (`openrouter`, `copilot`) plus one case per straightforward provider (`nous`, `openai-codex`, `anthropic`, `custom`) — construct a minimal `AIAgent` via the existing test fixtures and capture the kwargs that `_build_api_kwargs` produces, then call `_reasoning_kwargs(provider, model, base_url, reasoning_config)` and assert the emitted wire kwargs match. The matrix lives at the top of the test file as a module constant so adding a case is a one-line change. This is the discipline that makes the local reasoning copy sustainable: if upstream changes the OpenRouter gate, the Copilot supported-model list, or the `xhigh`-clamp rule, this test fails on the next rebase in a file we own. Keep the matrix small (≤10 cases) — it asserts the shape of parity, not exhaustive coverage.
- Each default in `DEFAULT_CONFIG["moa"]` is present in `hermes_cli.models.provider_model_ids(<provider>)`. Public accessor only — do **not** import `_PROVIDER_MODELS`. When upstream rotates the catalog, this fails loudly instead of silently shipping a bad default.
- The module-constant fallbacks (`REFERENCE_MODELS`, `AGGREGATOR_MODEL`) are each present in `provider_model_ids("openrouter")`. Separate rot surface from `DEFAULT_CONFIG["moa"]`.
- `check_moa_requirements` is config-aware: all-Codex config with `OPENROUTER_API_KEY` unset but `OPENAI_API_KEY` set → check passes; all-OpenRouter config with `OPENROUTER_API_KEY` unset → check fails naming OpenRouter specifically.
- `check_moa_requirements` surfaces loader errors: a malformed `moa` block (e.g. `reasoning: "extreme"`) → preflight returns a failure with the `ValueError` message, does **not** raise.

**Implementation detail (regression guards):**

- Mocked `resolve_provider_client` is called once per reference model with the configured provider.
- Model-ID normalization: `{model: "anthropic/claude-opus-4.6", provider: "anthropic"}` routes through the normalizer and produces a native slug at the API boundary.
- Mixed per-model reasoning: two reference entries with different `reasoning` levels produce two client calls with distinct `reasoning_config` kwargs.
- Reasoning-unsupported fallback: mocked client raises a 400 containing `reasoning` on first call; retry without `reasoning_config` succeeds and the sticky cache is populated so a second invocation skips the kwarg.
- Sticky-cache key isolation: same model slug via two different providers populates two distinct entries; a fallback learned for one provider does not affect the other.
- Structured `error.param` precedence: 400 with `error.code == "unsupported_parameter"` and `error.param == "temperature"` drops only temperature on retry, even if the message text also mentions `reasoning`.
- Dual-unsupported 400 on substring path: first retry drops `reasoning` only (tie-break order). If the retry also 400s with the temperature substring, the second retry drops `temperature`. Assert sticky caches populated per-parameter per-round, not in a single step. Complement with a false-positive guard: 400 substring-matching both families where the *second* retry succeeds — `_REASONING_UNSUPPORTED` populated, `_TEMPERATURE_UNSUPPORTED` *not* populated.
- Cache keying uses resolved model: `provider: anthropic` with user-written slug `anthropic/claude-opus-4.6` and `claude-opus-4-6` populate the same sticky-cache entry (assert via `call_args` and cache introspection).
- Runtime temperature fallback: `custom`-routed gpt-5 slug with temperature → first call 400s, retry without `temperature` succeeds, sticky cache populated; second call to the same slug skips `temperature` entirely (assert via `call_args` and cache introspection).
- Codex warning is one-time-per-config-shape: two MoA invocations against the same Codex-routed roster log the warning exactly once (assert via caplog).
- Codex warning re-fires on shape change: switch from reference-only Codex to reference+aggregator Codex between invocations → warning fires a second time with the aggregator-failure note.
- Codex-aggregator mention: when the aggregator is Codex-routed, the warning message includes the aggregator-failure note; reference-only Codex configs do not.
- `emit_warnings=False` suppresses Codex warnings: `_load_moa_config(emit_warnings=False)` with a Codex-routed entry logs nothing.

Do not modify `tests/tools/test_mixture_of_agents_tool.py` unless necessary.

### 6. Update docs

- Add a tool reference page under `website/docs/` for MoA (none exists today) showing the config.yaml shape, explaining provider routing, and calling out that `/model` does not cascade into MoA. Also document that `/reasoning_effort` only affects the main agent loop — MoA entries each have their own `reasoning` field, so users who want MoA to track the main-loop effort must update both.
- Rewrite the module docstring: `config.yaml` as primary, constants as fallback, multi-provider routing. Drop the "modify the configuration constants at the top of this file" sentence.
- Config comment (under `_COMMENTED_SECTIONS` in `config.py`) should include a one-line warning: routing multiple reference models through Codex multiplies consumption against the plan's daily cap.

### Clarifications (from 2026-04-15 pt. 2 review)

These resolve specific semantic ambiguities so two implementors can't diverge. Grouped by topic.

**Catalog-drift test normalization.** The drift test iterates each entry in `DEFAULT_CONFIG["moa"]["reference_models"] + [DEFAULT_CONFIG["moa"]["aggregator_model"]]`, calls `normalize_model_for_provider(entry["model"], entry["provider"])` ([hermes_cli/model_normalize.py:294](hermes_cli/model_normalize.py:294)), and asserts the result is in `provider_model_ids(entry["provider"])`. For `provider: openrouter` entries like `anthropic/claude-opus-4.6`, the normalizer is an identity and the OpenRouter catalog returns namespaced slugs — they match directly. For `provider: anthropic` entries (if added to defaults later), the normalizer strips the `anthropic/` prefix and replaces `.` with `-` ([hermes_cli/model_normalize.py:370-374](hermes_cli/model_normalize.py:370)) so `anthropic/claude-opus-4.6` becomes `claude-opus-4-6` before lookup. Without this normalization the test would false-fail on Anthropic-direct defaults. The same normalization rule also applies to the second drift test (module-constant fallbacks against `provider_model_ids("openrouter")`).

**`aggregator_model: {}` silently inherits the default — pre-merge detection required.** `_deep_merge` ([hermes_cli/config.py:2386-2403](hermes_cli/config.py:2386)) **does** recurse when both sides are dicts, including when `override[key] == {}`. Walking the call for `{"moa": {"aggregator_model": {}}}` against the default:
1. Top level: both sides contain `moa` as a dict → recurse.
2. Inside `moa`: both sides contain `aggregator_model` as a dict → recurse.
3. Inside `aggregator_model`: `override = {}`, so the `for key, value in override.items()` loop never executes, and the function returns `base.copy()` — the unmodified default.

Consequence: `aggregator_model: {}` and the unset case are **indistinguishable** after the merge. The shape validator running against the merged dict sees a well-formed default and does not raise, so a real config typo goes undetected and the user gets the default aggregator they did not ask for.

Fix: validate against the **raw user subtree** for empty-dict overrides, before trusting the merged result. Two acceptable approaches:

- *Preferred:* re-read the YAML at `hermes_cli.config.get_config_path()` inside `_load_moa_config` (the cost is one extra disk read per MoA invocation, noise compared to the N parallel LLM calls), inspect `raw.get("moa", {})`, and raise if `aggregator_model` is present and equal to `{}`, or if any entry in `reference_models` is present and equal to `{}`. Then discard the raw read and continue with the merged config for everything else.
- *Alternative:* thread a `raw_user_config` accessor out of `hermes_cli.config.load_config()` alongside the merged dict, and do the same check. Cleaner long-term but requires an upstream-touching signature change; prefer the re-read unless the signature change is happening anyway.

Other empty-override cases — `reference_models: []` (list, not dict, overwrites via the else branch; already caught by the shape validator), `moa: {}` (dict, recurses, returns default — which is the desired behavior since the whole point is that an absent or empty `moa` block inherits defaults), `reference_models` items where the item is a non-dict (caught as type error) — are fine as specified. Only dict-typed fields with dict-typed defaults (`aggregator_model` and any future dict-typed additions) need the pre-merge check.

**Codex warning key contents.** `codex_routed_models` is `frozenset(entries where entry["provider"] == "openai-codex")` and **includes the aggregator's model slug when the aggregator is Codex-routed**. `aggregator_is_codex` is a parallel bool whose purpose is (a) selecting the warning message variant (the reference-only variant omits the aggregator-failure note; the aggregator-is-Codex variant includes it) and (b) ensuring the cache key re-fires when the aggregator slot transitions into or out of Codex between profile switches — even if the reference-side frozenset happens to be identical across the transition. Without the bool, a user who moves the aggregator from OpenRouter to Codex while keeping the same reference roster would never see the aggregator-failure warning. Example keys:
- Roster references `gpt-5.4` via `openai-codex`, aggregator on OpenRouter → `(frozenset({"gpt-5.4"}), False)`.
- Same references, aggregator now Codex-routed to `gpt-5.4` → `(frozenset({"gpt-5.4"}), True)` — distinct cache entry, warning re-fires with the aggregator-failure note.

**`emit_warnings=False` scope.** Suppresses **both** the Codex plan-consumption warnings and the catalog-drift soft-validation warnings. Both are operational-guidance logs keyed to user action; neither is appropriate during preflight (`check_moa_requirements`) or startup, where the user hasn't opted into MoA for this turn. Shape validation and hard errors (unknown provider, invalid `reasoning` string, out-of-range `min_successful_references`, empty `reference_models`, missing `aggregator_model.model`) are *not* gated by this flag — they always run and always raise, because they represent unusable config rather than operational caution.

**`debug_call_data["parameters"]` under per-entry provider.** The current tool captures flat slugs ([tools/mixture_of_agents_tool.py:276-293](tools/mixture_of_agents_tool.py:276)): `reference_models: List[str]`, `aggregator_model: str`. Post-refactor, each entry carries a provider, so update the capture to:
```python
debug_call_data["parameters"] = {
    "user_prompt": user_prompt[:200],
    "reference_models": [{"model": e["model"], "provider": e["provider"], "reasoning": effort_label(e)} for e in ref_models],
    "aggregator_model": {"model": agg["model"], "provider": agg["provider"], "reasoning": effort_label(agg)},
    "reference_temperature": cfg["reference_temperature"],
    "aggregator_temperature": cfg["aggregator_temperature"],
    "min_successful_references": cfg["min_successful_references"],
}
```
Where `effort_label(entry)` returns the raw string from config or `"default"` when `reasoning_config is None`. Flat string formats would hide routing info from the debug pane, which is the whole reason to touch this dict. This is a regression-guard-level test concern, not a core contract, so add it to the implementation-detail test list.

**Existing `reference_models` / `aggregator_model` entry-point params** — resolved in section 3 above. They stay, typed as today, with OpenRouter-only semantics and a partial-override model. Not deprecated, not removed, not errored.

### Light-touch rebase strategy

To keep this enhancement easy to carry forward on top of upstream changes, the implementation is constrained to:

1. **Never modify module-level constants** in `tools/mixture_of_agents_tool.py`. They remain the fallback defaults, so upstream rotations merge cleanly.
2. **All new logic in two helpers** (`_load_moa_config`, `_reasoning_kwargs`) inside the MoA module. Upstream won't touch new functions in new spots.
3. **One insertion into `DEFAULT_CONFIG`**, no migration logic.
4. **All tests in a new file**, not edits to existing tests.
5. **Leave `tools/openrouter_client.py` alone** so upstream's usage stays intact.
6. **Do not modify `run_agent.py` or `agent/auxiliary_client.py` beyond the minimum.** The `_CodexCompletionsAdapter` and `_AnthropicCompletionsAdapter` changes described in "Reasoning kwarg translation" are the only upstream edits. Everything else (reasoning translation, gate predicates, clamping) lives in the local `_reasoning_kwargs` helper and is held to parity by the parity test in step 5 — that's the trade we accept in return for not carrying a persistent patch on `_build_api_kwargs`.

## Consequences

### Positive

- **Cost optimization:** Users route expensive models through subscriptions (Codex plan) and cheap models through pay-per-token providers (OpenRouter) based on their actual cost structure.
- **No code changes needed for model swaps:** Updating the model roster is a config edit and restart — no fork maintenance.
- **Leverages existing infrastructure:** `resolve_provider_client()` already handles auth, base URLs, API format adaptation, and client caching. No new provider management code.
- **Backward compatible:** Absent config defaults to current OpenRouter-only behavior. No breakage for existing users.
- **Provider diversity:** Reference models can deliberately span providers, reducing single-provider outage risk.
- **Emergency rollback:** `moa.enabled: false` disables MoA without requiring users to delete their roster. Flip back on to resume. Useful when a provider adapter drifts and the tool starts returning degraded results before the parity test catches it.
- **Safer default success threshold:** `min_successful_references` adapts to the roster size (`2` for rosters ≥2, `1` for single-model). A single silent reference failure no longer masks itself as a successful MoA run — which was the whole point of routing diversity.

### Negative

- **Config complexity:** The `moa` section is more complex than a flat model list. Users need to understand the provider field. Mitigated by sensible defaults and documentation.
- **Async client multiplicity:** Instead of one shared OpenRouter client, we may hold several provider clients simultaneously. This is already the norm for the rest of Hermes (delegation, vision, main loop) and `resolve_provider_client()` manages connection pooling internally.
- **Parity-test maintenance burden:** The local `_reasoning_kwargs` mirrors non-trivial main-agent behavior (OpenRouter model-family gate, Copilot dynamic support + effort clamp, Nous unconditional reasoning). When upstream changes any of those, the parity test fails on the next rebase and we update the local copy. This is explicit, localized, and preferable to carrying a patch on `run_agent.AIAgent._build_api_kwargs`, but it is a real ongoing cost — not the "~20 lines stable" handwave the first draft claimed.

### Risks

- **Codex plan rate limits:** Codex subscriptions have request/day caps that OpenRouter doesn't impose. Because MoA fans out to N reference models in parallel on every invocation, routing even one reference through Codex means each MoA call consumes a Codex request, and an afternoon of agentic use can exhaust a weekly allowance. Per-call, the existing `MIN_SUCCESSFUL_REFERENCES=1` default and retry logic degrade gracefully to partial results. Cumulative cost is the real concern — mitigated by (a) a doc warning in the config comment and (b) a one-time-per-(config-shape) `logger.warning` when any `moa` entry (reference or aggregator) specifies `provider: openai-codex`. The message notes the plan-consumption implication and, if the aggregator itself is Codex-routed, also notes that aggregator failure fails the whole tool. Gated by `emit_warnings=True` on `_load_moa_config()` so preflight stays quiet. Enforced by a module-level `_codex_warning_seen: set[tuple[frozenset[str], bool]]` keyed on `(frozenset(codex_routed_models), aggregator_is_codex)`; a profile switch that changes which slots are Codex-routed triggers a fresh warning so users don't miss the aggregator-failure nuance when switching profiles mid-session. Not code-enforced with a hard daily counter — that would add state that doesn't fit the tool's stateless design.
- **Stale catalog defaults:** Module-constant fallbacks and `DEFAULT_CONFIG["moa"]` reference specific model slugs that can be rotated out of `hermes_cli/models.py`. Mitigation: the test in step 5 asserts each default exists in the catalog, so upstream rotations break tests before they ship bad defaults.

## Alternatives Considered

### A. Per-invocation tool parameters only (no config.yaml)

Expose `reference_models` and `aggregator_model` as tool parameters that the model fills in per call. This would let the agent choose models dynamically, but:
- Increases every tool call's token cost (larger schema)
- The agent may not know the user's cost preferences or provider access
- No way to set a persistent default — every invocation requires full specification

Rejected in favor of config-as-primary, parameters-as-optional-future.

### B. Environment variable overrides

Use env vars like `MOA_REFERENCE_MODELS` to configure the roster. This works for simple cases but:
- Can't express per-model provider routing (lists of dicts are awkward in env vars)
- Doesn't align with Hermes' config.yaml convention
- No interactive setup support

Rejected.

### C. Keep OpenRouter-only, just make model list configurable

The simplest change: read models from config but always route through OpenRouter. This solves the stale-defaults problem but not the provider-routing problem. A user with a Codex subscription still pays OpenRouter markups for OpenAI models.

Rejected because it leaves cost optimization on the table, and the marginal implementation cost of per-model provider routing is small (we're calling `resolve_provider_client` which already exists).