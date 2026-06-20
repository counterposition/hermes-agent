---
name: defending-code-vuln-patch
description: Draft inert patches for verified findings.
version: 1.0.0
author: Anthropic, ported by Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: security
    tags: [security, patch-planning, static-analysis]
---

# Defending Code Vulnerability Patch

## When to Use

Use this skill when the user wants candidate fixes for findings that have been triaged, preferably from `TRIAGE.json`.

## Prerequisites

- `TRIAGE.json`, `VULN-FINDINGS.json`, or a compatible findings file.
- A local repository path for source context.
- `delegate_task` available for read-only patch planning with `toolsets=["repo-read"]`.
- Parent file-writing capability limited to `PATCHES/` and optional local patch-state notes.

## How to Run

Inputs:

- `<findings>`: required findings source.
- `--repo <path>`: target repository for context.
- `--top N`: patch only the highest ranked N static positives.
- `--id <id>`: patch one finding.
- `--fresh`: ignore existing patch notes.

This Phase 1 skill never edits target source and never applies a diff. It writes inert patch artifacts for human review.

## Quick Reference

- Output directory: `PATCHES/`
- Per-finding files: `PATCHES/<id>/patch.diff` and `PATCHES/<id>/patch_result.json`
- Summary files: `PATCHES.md`, `PATCHES.json`
- read-only planners and reviewers: `toolsets=["repo-read"]`

## Procedure

1. Parse arguments and locate findings.
2. Prefer `TRIAGE.json` and filter to `verdict == "true_positive"` unless the user explicitly asks for draft fixes against untriaged candidates.
3. Resolve cited files under `--repo`. Skip findings without source context.
4. For each selected finding, spawn a read-only patch planner:

```python
delegate_task(
    goal="Draft an inert unified diff for one verified security finding without modifying the repository.",
    context="Finding: <finding>. Repo: <repo>. Static source review only. Return diff text and rationale.",
    toolsets=["repo-read"],
    role="leaf",
)
```

5. Spawn an independent read-only reviewer for each proposed diff:

```python
delegate_task(
    goal="Review an inert candidate security patch from source context and finding details.",
    context="Finding: <finding>. Candidate diff: <diff>. Static review only. Judge whether the diff addresses the root cause and risks regressions.",
    toolsets=["repo-read"],
    role="leaf",
)
```

6. Parent writes artifacts only under `PATCHES/`. Do not write into `--repo`.
7. Include `verified: "static_review_only"` in `patch_result.json`; do not imply executable validation.
8. Create `PATCHES.md` and `PATCHES.json` with selected ids, verdicts, reviewer notes, and apply instructions for a human.

## Pitfalls

- Do not run apply, format, test, build, fuzz, or reproduction commands.
- Do not edit target source files from this skill.
- Do not trust untriaged findings without warning the user.
- Do not suppress security-guidance warnings by weakening the patch artifact; warnings on vulnerable patterns may be useful.

## Verification

- No target source file is modified.
- All writes are under `PATCHES/`.
- Each patch has a read-only planner result and a read-only reviewer result.
- The final summary says patches are inert static candidates requiring human review and executable validation elsewhere.
