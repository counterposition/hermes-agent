---
name: defending-code-vuln-triage
description: Triages static security findings.
version: 1.0.0
author: Anthropic, ported by Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: security
    tags: [security, triage, static-analysis]
---

# Defending Code Vulnerability Triage

## When to Use

Use this skill when the user has raw security findings and wants static verification, deduplication, ranking, owner hints, and a concise engineering backlog.

## Prerequisites

- A findings file or directory, preferably `VULN-FINDINGS.json`.
- A local repository path for cited source locations.
- `delegate_task` available for verifier children constrained to `toolsets=["repo-read"]`.
- Parent file-writing capability for `TRIAGE.json` and `TRIAGE.md`.

## How to Run

Inputs:

- `<findings>`: required JSON, JSONL, Markdown, or findings directory.
- `--repo <path>`: target repository for source verification.
- `--auto`: skip user interview and use conservative defaults.
- `--votes N`: verifier votes per finding, default 3.
- `--fp-rules <file>`: optional local false-positive rules.
- `--fresh`: ignore any prior triage notes.

## Quick Reference

- Outputs: `TRIAGE.json`, `TRIAGE.md`
- read-only verifier: `delegate_task(..., toolsets=["repo-read"], role="leaf")`
- Verdicts: `true_positive`, `false_positive`, `duplicate`, `needs_manual_test`
- Ranking: exploitability and impact derived from source evidence, not scanner severity alone

## Procedure

1. Parse arguments and ask for missing repository or findings paths.
2. Unless `--auto` is set, use `clarify` to gather trust-boundary context, threat priorities, scoring preference, and noise tolerance.
3. Normalize findings into a flat list. Preserve existing ids where possible.
4. Resolve cited files under `--repo`. Findings with no reachable source location become `needs_manual_test` with low confidence; do not let them dedupe real source-backed findings.
5. Run deterministic dedupe by normalized file, category, and nearby line.
6. For semantic dedupe, use one child with `toolsets=["repo-read"]` to compare whether one fix would remove multiple findings.
7. For verification, batch verifier children. Each child must read only the cited source and answer from code evidence:

```python
delegate_task(
    tasks=[
        {
            "goal": "Verify one candidate security finding from static source evidence.",
            "context": "Finding: <finding>. Repo: <repo>. Static review only. Decide true_positive, false_positive, or needs_manual_test with evidence.",
            "toolsets": ["repo-read"],
            "role": "leaf",
        }
    ]
)
```

8. Apply majority vote. If votes split and the user chose recall, keep as `needs_manual_test`; otherwise mark low-confidence false positives only when the source evidence supports that.
9. Rank survivors by exploitability, impact, reachability, and user threat priorities.
10. Create `TRIAGE.json` and `TRIAGE.md`.

## Pitfalls

- Do not execute proof-of-concept inputs or send requests. Static source reading is the only oracle in Phase 1.
- Do not treat "no working exploit was produced" as a false-positive reason.
- Do not dedupe unlocatable findings into located findings.
- Do not let verifier children use broad `file` or web `search` toolsets.

## Verification

- `TRIAGE.json` and `TRIAGE.md` exist and contain consistent ids and verdicts.
- Every true-positive verdict cites source evidence read by a `repo-read` child.
- Duplicates point to a canonical id.
- The final summary separates verified static positives from manual-test items.
