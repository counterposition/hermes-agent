---
name: defending-code-threat-model
description: Build a static security threat model.
version: 1.0.0
author: Anthropic, ported by Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: security
    tags: [security, threat-modeling, static-analysis]
---

# Defending Code Threat Model

## When to Use

Use this skill when the user asks to threat model a codebase, map attack surface, identify assets and trust boundaries, or prepare scope for a later static vulnerability scan.

## Prerequisites

- A local repository or design artifact the user is authorized to review.
- Optional owner availability for interview mode.
- `delegate_task` available for code-grounded review with `toolsets=["repo-read"]`.
- Parent file-writing capability for `THREAT_MODEL.md`.

## How to Run

Supported modes:

- `interview`: gather owner input and produce `THREAT_MODEL.md`.
- `bootstrap`: derive a draft from static code review and any supplied local reports.
- `bootstrap-then-interview`: generate a code-grounded draft, then refine it with the owner.

If the mode is omitted, ask whether an owner is available. Prefer `bootstrap-then-interview` when both an owner and a local checkout are available.

## Quick Reference

- Output file: `THREAT_MODEL.md`
- Static child review: `delegate_task(..., toolsets=["repo-read"], role="leaf")`
- Core questions: what are we working on, what can go wrong, what will we do, did we do enough
- Downstream users: `defending-code-vuln-scan` and triage reviewers

## Procedure

1. Confirm the target directory exists and is in scope.
2. State the static boundary: no target execution, no exploit validation, no live service probing.
3. For `bootstrap`, spawn bounded read-only child reviews. Use one child per major subsystem or trust boundary:

```python
delegate_task(
    tasks=[
        {
            "goal": "Identify entry points, trust boundaries, assets, and threat classes for this subsystem.",
            "context": "Target: <target>. Static review only. Produce concise threat rows with file references.",
            "toolsets": ["repo-read"],
            "role": "leaf",
        }
    ]
)
```

4. For `interview`, use `clarify` for missing owner context instead of guessing.
5. Synthesize `THREAT_MODEL.md` with these sections:
   - Scope and assumptions.
   - Assets.
   - Entry points and trust boundaries.
   - Threats with actor, surface, asset, impact, likelihood, status, and evidence.
   - Deprioritized cases.
   - Open questions.
   - Recommended mitigations.
6. Re-read the artifact before responding and summarize the top five threats.

## Pitfalls

- A threat is not a one-line bug. If fixing one source line makes the item disappear, record it as vulnerability evidence rather than as the threat itself.
- Do not import network-only advisory claims unless the user supplied them or explicitly asks for public advisory research.
- Do not let bootstrap children write files; parent writes the final artifact.
- Do not validate threats by running target code.

## Verification

- `THREAT_MODEL.md` exists and includes scope, assets, trust boundaries, threat rows, open questions, and mitigations.
- Each code-grounded threat cites files or functions reviewed by a `repo-read` child.
- The response labels unresolved owner assumptions separately from code-backed findings.
