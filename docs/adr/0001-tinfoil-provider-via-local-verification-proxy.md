# ADR 0001: Tinfoil as a provider — bundled plugin, local verification proxy, host-managed supervision

- **Status:** Accepted (recorded post-hoc, after implementation)
- **Date:** 2026-08-23
- **Deciders:** Harish Kukreja; plan adversarially reviewed by GPT 5.6 Sol
  before implementation
- **Related:** `docs/plans/2026-08-23-tinfoil-provider.md` (implementation
  plan, rev 2)

## Context

We wanted Tinfoil.sh as an inference provider for its distinguishing
property: confidential inference, where the workload runs in an attested
enclave and the provider cannot observe prompts or outputs. That property
is only real if attestation is actually verified, and Tinfoil's design
places that verification in a proxy process running on the user's own
machine. This makes Tinfoil structurally different from every other
provider we support: its availability depends on the state of the host,
not just on a remote service.

Three forces shaped the integration. First, the project's standing
architecture principle — a narrow core with capability at the edges — and
its designated seam for inference backends, the self-registering provider
plugin. Second, the multi-profile deployment reality: an integration only
visible to one profile's home directory would have to be duplicated by
hand across every profile on the machine. Third, a wish to keep the work
upstreamable rather than accumulating as fork-only divergence.

## Decision

- Integrate Tinfoil as a bundled, in-tree provider plugin using the
  existing provider-profile seam, with no changes to core conversation or
  transport logic beyond two small generic fixes (below). Alternatives
  rejected: a user-directory plugin (would need per-profile duplication
  and cannot be upstreamed) and any core special-casing (the seam exists
  precisely so providers don't touch the waist).
- Treat the locally running verification proxy as the provider endpoint,
  and keep Hermes entirely agnostic to attestation. The proxy owns the
  trust decision; Hermes speaks to it like any other endpoint. Bypassing
  the proxy to reach the cloud service directly was rejected outright —
  it would forfeit the verification that is the entire reason to use this
  provider.
- Run the proxy under host-level service supervision (launchd), managed
  by a documented runbook rather than by new Hermes commands or code.
  Provisioning a third-party binary's lifecycle is a machine concern, not
  product surface; absorbing it would couple the tree to a fast-moving
  external artifact. The supervision itself is not optional polish: the
  manually started proxy died silently during the planning session, and a
  provider whose availability depends on a background process needs a
  supervisor, restart-on-crash, and tolerance for the network being
  unavailable at boot.
- Ship reasoning-effort translation in the first version instead of
  deferring it. The provider documents a strict per-model contract for
  reasoning control and rejects requests that deviate from it. Deferring
  would therefore not have been "wait and see" — it would have meant
  either silently discarding a setting the user actively relies on, or
  hard request failures. The adopted posture: translate for model
  families whose contract we know, omit the control entirely for unknown
  ones, and never guess.
- Where the integration exposed gaps in the shared plugin-provider
  plumbing (a missing default-model fallback; a setup prompt that
  silently discarded input for providers without a base-URL override
  variable), fix them generically for all plugin providers — using
  already-established mechanisms — rather than special-casing Tinfoil.
  The deeper setup-flow persistence rework suggested in review was
  deliberately split out as its own follow-up change rather than allowed
  to expand this one.

## Consequences

- Provider availability is now tied to a host service. The failure mode
  is a local connection error with a clear remediation, surfaced by the
  existing health checks; supervision makes the sad path rare.
- Every profile on the machine can use the provider with a single
  integration; every additional machine needs its own proxy.
- The work cherry-picks cleanly as an upstream contribution, since it
  lives entirely at the sanctioned extension seam plus two generic fixes.
- The pre-implementation adversarial review caught a contract-level error
  (the reasoning deferral) and a silent-input-loss bug that manual
  testing would likely have missed until much later. Treating external
  review as a gate before implementation, not after, is worth repeating.
- Revisit when the provider ships new model families: reasoning control
  is extended family-by-family on evidence, so a new family runs without
  effort control until its contract is confirmed and added.
