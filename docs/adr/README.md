# Architecture Decision Records (fork-local)

One file per decision, numbered `NNNN-short-slug.md`. This directory is
**fork-only** — upstream does not have it, so it carries zero rebase
surface. Upstream's own ADRs live in `docs/ADR.md` (single append-style
file); do not add fork entries there.

Each ADR states its own `Status:` and may be amended in place as a
decision's lifecycle evolves (e.g. "Proposed upstream as PR #NNN",
"Superseded by 0007"). Record the rationale and forces, not
implementation detail — implementation lives in `docs/plans/` and the
code.

## Index

| ADR | Date | Status | Title |
|-----|------|--------|-------|
| [0001](0001-tinfoil-provider-via-local-verification-proxy.md) | 2026-08-23 | Accepted | Tinfoil as a provider — bundled plugin, local verification proxy, host-managed supervision |
