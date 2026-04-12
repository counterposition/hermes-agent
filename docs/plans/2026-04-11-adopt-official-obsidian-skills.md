# ADR 0001: Adopt official Obsidian skills additively, not as a blunt replacement

Status: Proposed upstream as PR #15848 — carried on the fork as a mirror pending merge (see "Upstream status and fork lifecycle")
Date: 2026-04-11 (lifecycle updated 2026-07-18)
Deciders: Hermes Agent maintainers

## Context

Hermes currently ships a single bundled `obsidian` skill at `skills/note-taking/obsidian/SKILL.md`.

That skill is intentionally broad and easy to trigger, but it is narrow in scope. It mainly documents:
- locating the vault via `OBSIDIAN_VAULT_PATH`
- reading notes
- listing notes
- searching notes
- creating notes
- appending to notes
- basic wikilink syntax

A newer, actively maintained set of Obsidian-related skills exists in `kepano/obsidian-skills`, authored by Steph Ango (`kepano`), the CEO of Obsidian. A local clone is available at `~/c/vendor/obsidian-skills`.

That repository currently provides multiple focused skills:
- `obsidian-markdown`
- `obsidian-cli`
- `obsidian-bases`
- `json-canvas`
- `defuddle`

These official skills cover current Obsidian concepts and workflows that Hermes does not currently capture well, especially:
- Obsidian Flavored Markdown semantics
- properties/frontmatter
- callouts, embeds, and wikilinks in more detail
- Bases (`.base`) files
- JSON Canvas (`.canvas`) files
- Obsidian CLI workflows
- plugin/theme development workflows

At the same time, Hermes's existing bundled `obsidian` skill provides something valuable that the official set does not preserve by itself:
- a simple, generic, filesystem-first entry point for “work with my Obsidian vault” tasks
- an obvious top-level trigger name (`obsidian`)
- a fallback that still works when the running Obsidian app or CLI workflows are not the right tool

Other Hermes materials already refer to the broad `obsidian` skill by name, including:
- `skills/apple/apple-notes/SKILL.md`
- `skills/research/llm-wiki/SKILL.md`
- `website/docs/reference/skills-catalog.md`

A literal replacement of the current `obsidian` skill with only the official focused skills would improve coverage, but would also remove the broad wrapper behavior and degrade discoverability for generic Obsidian requests.

## Decision

Hermes should incorporate official Obsidian skills additively rather than replace the bundled `obsidian` skill outright.

Specifically:

1. Hermes should keep a top-level `obsidian` skill as the broad, generic entry point.
2. Hermes should add Hermes-integrated versions of the official focused skills that are directly about Obsidian authoring, vault management, or app interaction.
3. The initial set should be:
   - `obsidian-markdown`
   - `obsidian-cli`
   - `obsidian-bases`
   - `json-canvas`
4. Skills that serve a broader purpose beyond core Obsidian authoring, vault management, or app interaction should be evaluated independently rather than bundled automatically. In the current upstream set, `defuddle` falls into that category.
5. The top-level `obsidian` skill should explicitly reference the specialized skills and route to them when the task is format-specific or app-specific.

## Rationale

### Why not do a straight replacement?

The current Hermes `obsidian` skill acts as a broad wrapper. Removing it would regress on routing, generic usability, and backward-compatible references to `obsidian` across the existing skill library and docs.

### Why adopt the official skills at all?

The official skills are materially stronger for Obsidian-specific semantics and newer product features.

Relative to the bundled Hermes skill, they provide:
- better coverage of current Obsidian concepts
- more authoritative guidance for Obsidian-specific file formats and workflows
- more granular skill activation based on task type
- current CLI-oriented workflows that exist in the Obsidian app

The Obsidian CLI exposes commands such as `append`, `backlinks`, and `base:create`, which makes the `obsidian-cli` skill directly relevant to Hermes workflows.

### Why keep `defuddle` separate?

`defuddle` is useful, but it is not required to solve the main problem of improving Hermes's Obsidian support.

It is better treated as a separate adoption question because:
- it is not specific to Obsidian note authoring or vault management
- it broadens the scope from “official Obsidian skills” to “related tools by the same author/ecosystem”
- it contains wording oriented toward other agent/tool ecosystems and should be evaluated independently

## Consequences

### Positive

- Hermes gains substantially better support for current Obsidian-specific workflows.
- Hermes preserves the simple high-level `obsidian` entry point that existing users and skills can already reference.
- Hermes avoids a regression in generic vault-management usability.
- The upstream PR can be framed as a quality upgrade rather than a risky removal.

### Negative

- Hermes will now have multiple Obsidian-related skills rather than a single one.
- The top-level wrapper must be kept coherent with the specialized skills.
- The imported skills will likely need Hermes-specific adaptation rather than verbatim vendoring.

### Neutral / operational

- Existing cross-references to `obsidian` should remain valid.
- Documentation should be updated to mention the specialized skills.
- The imported skills should be integrated cleanly into Hermes's existing `skills/` tree rather than vendored wholesale as a nested external repository.

## Upstream PR guidance

If Hermes upstream receives a PR based on this ADR, it should be framed as:
- keeping a generic `obsidian` skill
- adding official focused Obsidian skills
- updating documentation and related-skill references accordingly

It should not be framed as:
- deleting `obsidian` and replacing it with an unrelated set of narrower skills
- importing the external repository wholesale without Hermes-specific adaptation

## Upstream status and fork lifecycle (updated 2026-07-18)

This ADR has moved from proposal to an active upstream contribution.

- **The skills are proposed upstream as NousResearch/hermes-agent PR #15848.** The PR is the
  canonical, reviewed form of the bundle (focused skills modernized to current authoring
  standards, MIT attribution preserved in `skills/note-taking/THIRD_PARTY_NOTICES.md`, a
  stdlib-only `json-canvas/scripts/validate_canvas.py`, and `tests/skills/test_obsidian_skills.py`).
- **The fork carries a mirror of the PR branch `port/obsidian-skills-upstream-v2`.** The carried
  "adopt official Obsidian skills additively" commit holds exactly the PR branch's version of the
  skills (plus this fork-only ADR and the README "What's different in this fork" note, which do not
  ship upstream). The follow-up "reconcile rebased branch with upstream" commit carries the PR's
  per-skill website doc pages and sidebar entries.
- **Re-sync on PR changes.** If reviewers change the PR during review, re-sync the carried commit
  (and the reconcile commit's doc pages) to the updated `port/obsidian-skills-upstream-v2` branch —
  a wholesale content swap into the existing carried commit, not a new commit on top.
- **Drop on merge.** When PR #15848 merges into upstream, drop the carried commit (and the
  obsidian portions of the reconcile commit) on the next rebase — upstream then ships the bundle
  natively and carrying it would duplicate/conflict.

## Non-goals

This ADR does not decide:
- whether the existing Hermes `obsidian` skill should be rewritten to align with current Hermes tool-preference conventions
- whether `defuddle` should be bundled by Hermes
- the exact text of the imported/adapted skill files

Wrapper modernization is intentionally deferred to a separate follow-up initiative so that official-skill adoption can be evaluated on capability coverage rather than being coupled to a broader Hermes-specific cleanup. See ADR 0002.

## Alternatives considered

### Alternative A: Keep the current Hermes skill unchanged

Rejected.

This would preserve simplicity, but it would leave Hermes with weak support for several important modern Obsidian workflows and file formats.

### Alternative B: Replace the current Hermes skill entirely with the official focused skills

Rejected.

This would improve specificity but regress on broad routing, generic usability, and backwards-compatible references to `obsidian`.

### Alternative C: Vendor the external repository wholesale

Rejected.

Hermes should integrate skills into its own structure and conventions, rather than mirror an external repository layout that was designed for broader agent-skill compatibility.

## Follow-up work

1. Add adapted versions of `obsidian-markdown`, `obsidian-cli`, `obsidian-bases`, and `json-canvas` to Hermes.
2. Update the bundled `obsidian` skill to reference those specialized skills and route users to them when the task is specialized, for example:
   - authoring/editing `.md` notes -> `obsidian-markdown`
   - interacting with the running app / Obsidian CLI / plugin development -> `obsidian-cli`
   - editing `.base` files -> `obsidian-bases`
   - editing `.canvas` files -> `json-canvas`
3. Update references in dependent skills and docs.
4. Evaluate `defuddle` separately.
5. Track wrapper modernization separately under ADR 0002.
