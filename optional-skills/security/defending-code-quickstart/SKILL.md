---
name: defending-code-quickstart
description: Orient static defending-code review work.
version: 1.0.0
author: Anthropic, ported by Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: security
    tags: [security, static-analysis, defending-code]
---

# Defending Code Quickstart

## When to Use

Use this skill when the user wants an orientation for the static defending-code workflow, asks how to start, or wants a guided Day 1 pass that stays inside Hermes Phase 1.

## Prerequisites

- A local repository or target directory the user is authorized to review.
- `delegate_task` available so read-only review can be constrained with `toolsets=["repo-read"]`.
- `write_file` or `patch` available only for parent-authored artifacts such as `THREAT_MODEL.md`, `VULN-FINDINGS.md`, `TRIAGE.md`, or files under `PATCHES/`.

## How to Run

Ask which path the user wants:

- Orientation only.
- Static Day 1 workflow: threat model, vulnerability scan, triage.
- A question about the workflow.

If the user chooses the Day 1 workflow, invoke these skills in order:

1. `defending-code-threat-model bootstrap <target>`
2. `defending-code-vuln-scan <target>`
3. `defending-code-vuln-triage VULN-FINDINGS.json --repo <target>`

Pause between stages and show the artifact that was produced.

## Quick Reference

- Static threat map: `THREAT_MODEL.md`
- Static findings: `VULN-FINDINGS.json` and `VULN-FINDINGS.md`
- Static triage: `TRIAGE.json` and `TRIAGE.md`
- Candidate patch artifacts: `PATCHES/`
- read-only child reviews: `delegate_task(..., toolsets=["repo-read"], role="leaf")`

## Procedure

1. Confirm the target path and authorization scope.
2. State that Phase 1 is static only: it reads code and writes review artifacts, but it does not execute target code or validate exploits.
3. For any source inspection, delegate to a child agent with `toolsets=["repo-read"]`:

```python
delegate_task(
    goal="Inspect the target repository and summarize entry points, trust boundaries, and likely security-sensitive components.",
    context="Target: <target>. Static review only. Do not execute code or modify files.",
    toolsets=["repo-read"],
    role="leaf",
)
```

4. Route work to the namespaced skills instead of generic command names.
5. After each stage, read back the artifact summary and ask whether to continue.
6. For execution-verified work, explain that it is outside Phase 1 and requires the later operator-controlled harness wrapper.

## Pitfalls

- Do not introduce generic `/patch`, `/triage`, `/threat-model`, or `/vuln-scan` names.
- Do not let a child reviewer inherit broad file tools; use `repo-read`.
- Do not present static findings as verified exploits.
- Do not run build, install, test, fuzz, or reproduction commands against the target.

## Verification

- The quickstart points only to `defending-code-*` skill names.
- Any delegated source review uses `toolsets=["repo-read"]`.
- The final handoff names the static artifacts and clearly labels them as candidates.
