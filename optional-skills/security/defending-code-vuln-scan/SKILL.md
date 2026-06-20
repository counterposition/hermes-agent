---
name: defending-code-vuln-scan
description: Run static repo-read vulnerability review.
version: 1.0.0
author: Anthropic, ported by Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: security
    tags: [security, vulnerability-scan, static-analysis]
---

# Defending Code Vulnerability Scan

## When to Use

Use this skill when the user asks to statically scan a local codebase for security vulnerabilities or to produce raw findings for later triage.

## Prerequisites

- A local target directory in authorized scope.
- Optional `THREAT_MODEL.md` from `defending-code-threat-model`.
- `delegate_task` available for child reviewers constrained to `toolsets=["repo-read"]`.
- Parent file-writing capability for `VULN-FINDINGS.json` and `VULN-FINDINGS.md`.

## How to Run

Inputs:

- `<target>`: required local directory.
- `--focus <area>`: optional repeatable focus area.
- `--single`: optional sequential review for tiny targets.
- `--extra <file>`: optional local extra vulnerability classes.
- `--no-score`: optional skip of confidence calibration.

Resolve the target, derive focus areas, delegate read-only review, collate findings, and write both JSON and Markdown artifacts.

## Quick Reference

- Output files: `VULN-FINDINGS.json`, `VULN-FINDINGS.md`
- Child review toolset: `toolsets=["repo-read"]`
- Child role: `role="leaf"`
- Next skill: `defending-code-vuln-triage VULN-FINDINGS.json --repo <target>`
- Finding ids: `F-001`, `F-002`, sorted by confidence and severity

## Procedure

1. Confirm the target path and source-file count.
2. Inspect `THREAT_MODEL.md` if present and derive focus areas from entry points, trust boundaries, and threat rows.
3. If no threat model or explicit focus exists, ask a read-only child to inspect repository structure and propose three to ten focus areas:

```python
delegate_task(
    goal="List security review focus areas for the target repository from static source inspection.",
    context="Target: <target>. Static review only. Return focus areas with key files and trust boundaries.",
    toolsets=["repo-read"],
    role="leaf",
)
```

4. Unless `--single` is appropriate, run one child review per focus area in a single batch:

```python
delegate_task(
    tasks=[
        {
            "goal": "Find candidate vulnerabilities for focus area: <focus-area>.",
            "context": "Target: <target>. Static review only. Report plausible exploit paths with file and line evidence. Do not modify files.",
            "toolsets": ["repo-read"],
            "role": "leaf",
        }
    ]
)
```

5. In every child prompt, require: entry source, sink, attacker-controlled data path, trigger condition, concrete impact, and specific fix idea.
6. Drop only explicit `category=none` placeholders. Otherwise keep candidates for triage.
7. If confidence calibration is enabled, spawn one shallow `repo-read` child per finding to score source support from 1 to 10. Do not drop findings in this pass.
8. Create `VULN-FINDINGS.json` with `target`, `scanned_at`, `focus_areas`, `findings`, and `summary`.
9. Create `VULN-FINDINGS.md` with a summary table and one section per finding.

## Pitfalls

- Do not execute target code, install dependencies, start services, fuzz inputs, or reproduce findings.
- Do not use broad `file` or web `search` toolsets for child reviewers.
- Do not report test, fixture, generated, documentation, or notebook examples as production vulnerabilities unless the user says they are in scope.
- Do not claim a finding is verified; this skill produces candidates.

## Verification

- Every child source-review example uses `toolsets=["repo-read"]`.
- `VULN-FINDINGS.json` and `.md` exist and agree on counts and ids.
- Every finding has a file reference or is clearly marked as unlocatable and low confidence.
- The final message says the findings are static candidates for triage.
