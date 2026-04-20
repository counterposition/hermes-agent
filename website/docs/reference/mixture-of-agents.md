---
title: Mixture of Agents (MoA)
---

Mixture of Agents (MoA) runs several reference models in parallel, then sends their responses to a separate aggregator model that synthesizes the final answer.

Use it for genuinely hard prompts where diversity helps: multi-step reasoning, architecture tradeoffs, difficult debugging, complex coding tasks, or synthesis across viewpoints.

Configuration lives under `moa:` in `config.yaml`.

## Shipped default config

```yaml
moa:
  enabled: true
  reference_models:
    - model: "anthropic/claude-opus-4.6"
      provider: "openrouter"
    - model: "google/gemini-3.1-pro-preview"
      provider: "openrouter"
    - model: "openai/gpt-5.4-pro"
      provider: "openrouter"
    - model: "x-ai/grok-4.3"
      provider: "openrouter"
  aggregator_model:
    model: "anthropic/claude-opus-4.6"
    provider: "openrouter"
  reference_temperature: 0.6
  aggregator_temperature: 0.4
```

This is the fallback roster Hermes ships with when your `moa:` block is absent.
`min_successful_references` is omitted by default and resolves to `2` for this
four-model roster.

Notes:

- `enabled: false` disables the tool without deleting the roster.
- String shorthand is allowed for convenience:

```yaml
moa:
  reference_models:
    - "anthropic/claude-opus-4.6"
  aggregator_model: "anthropic/claude-opus-4.6"
```

Those shorthand entries default to `provider: openrouter`.

## Provider routing

Each entry chooses its own provider.

That lets you mix:

- OpenRouter for breadth and reliability
- OpenAI Codex for GPT-5 via a Codex subscription
- Anthropic direct for native Claude routing
- Other configured providers supported by Hermes

Preflight is roster-aware: Hermes checks the providers actually referenced by
your MoA config. A Codex-only roster does not require `OPENROUTER_API_KEY`,
while a mixed roster needs credentials for every provider it touches.

If a provider/model pairing is not in the known provider catalog, Hermes logs a warning but does not block the call. This is intentional: catalogs can lag behind newly released model slugs.

## Reasoning per model

Each entry may optionally set `reasoning`.

Accepted values are the same values Hermes accepts elsewhere for reasoning effort, plus `none` to disable reasoning explicitly for that entry.

Behavior:

- omitted / `null` / `""` => do not send any reasoning parameter; use the provider default
- `none` => send an explicit reasoning-disabled config when supported
- invalid value => load-time config error

Reasoning is translated per provider:

- OpenRouter / Nous / AI Gateway => `extra_body.reasoning`
- Copilot / Copilot ACP => `extra_body.reasoning` with effort normalization to the provider-supported set
- OpenAI Codex => `reasoning_config` threaded into the Codex Responses adapter. Codex's Responses API doesn't accept `minimal`; MoA transparently maps it to `low` before send, so a `reasoning: minimal` entry on a Codex slot will show up as `low` in your billing logs.
- Anthropic => `reasoning_config` threaded into the Anthropic adapter so it emits a `thinking` block
- Custom OpenAI-compatible endpoints => `reasoning_effort`

:::note Silent-accept providers
OpenRouter proxies to many backends that silently accept and drop `extra_body.reasoning` without error; if a reasoning setting seems inert, cross-check against provider-side logs or billing.
:::

Unsupported parameters are learned lazily. If a provider rejects `temperature` or reasoning parameters with an `unsupported_parameter` error, Hermes retries without that parameter and remembers the result for the rest of the process.

## Operational notes

- The aggregator is the final answer. If the aggregator fails, the whole MoA call fails.
- `min_successful_references` is validated against the roster size.
- When the key is omitted, Hermes uses an adaptive default: `2` for multi-model rosters, `1` for single-model rosters.

## `/model` and `/reasoning_effort` do not cascade into MoA

MoA has its own roster and per-entry reasoning configuration.

Important:

- `/model` changes the main agent model, not the MoA reference roster or aggregator
- `/reasoning_effort` changes the main agent loop, not the `moa.*.reasoning` entries

If you want MoA to track your main model or reasoning preferences, update both places.

## Codex warning

Routing one or more MoA slots through `openai-codex` can burn through a Codex plan quickly.

Because MoA fans out in parallel, routing multiple reference models through Codex multiplies consumption against the plan's daily cap.

If the aggregator itself is Codex-routed and fails, the whole MoA tool fails.

## Related docs

- [Tools Reference](./tools-reference.md)
- [Toolsets Reference](./toolsets-reference.md)
- [Providers](../integrations/providers.md)
