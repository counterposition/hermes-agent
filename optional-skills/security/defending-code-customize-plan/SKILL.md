---
name: defending-code-customize-plan
description: Plan safe harness customization only.
version: 1.0.0
author: Anthropic, ported by Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: security
    tags: [security, harness-customization, planning]
---

# Defending Code Customize Plan

## When to Use

Use this skill when the user wants to adapt the reference harness idea to another language, target shape, vulnerability class, or detection signal, but only wants a Phase 1 planning artifact.

## Prerequisites

- User authorization and a clear target domain.
- Optional local reference checkout or design notes.
- `delegate_task` available for read-only source or documentation review with `toolsets=["repo-read"]`.
- Parent file-writing capability for planning artifacts only.

## How to Run

Interview the user, inspect only local docs or source with read-only children, and write a customization plan. Do not validate the plan by executing target code.

## Quick Reference

- Output: `CUSTOMIZE-PLAN.md`
- read-only review: `delegate_task(..., toolsets=["repo-read"], role="leaf")`
- Planning axes: target shape, input modality, detection signal, isolation boundary, dedupe signature, report format, patch-verification signal
- Execution handoff: Phase 2 operator workflow or a separately approved implementation task

## Procedure

1. Ask what the user wants to find, who will run the workflow, and what output matters.
2. Ask targeted follow-ups for missing axes:
   - Vulnerability class.
   - Target shape.
   - Detection signal.
   - Proof-of-concept shape.
   - Isolation boundary.
   - Dedupe signature.
   - Report structure.
   - Patch verification signal.
3. If a local reference checkout or existing target exists, use a read-only child to inspect it:

```python
delegate_task(
    goal="Map which local harness or target files appear domain-specific and which are generic.",
    context="Path: <path>. Static planning only. Do not modify files or execute code.",
    toolsets=["repo-read"],
    role="leaf",
)
```

4. Create `CUSTOMIZE-PLAN.md` with:
   - Goal and scope.
   - Domain assumptions.
   - Required inputs and target layout.
   - Detection and grading oracle.
   - Reporting schema.
   - Files or prompts likely to change.
   - Safety requirements.
   - Unknowns and validation plan for a later phase.
5. Stop at the plan. If the user asks for implementation or validation, explain that it is outside Phase 1.

## Pitfalls

- Do not rewrite harness code from this skill.
- Do not validate by running sample targets.
- Do not weaken sandbox assumptions. If execution will happen later, preserve the requirement for a real isolation boundary and constrained egress.
- Do not promise that a static-only plan proves the customized workflow works.

## Verification

- `CUSTOMIZE-PLAN.md` exists and is explicitly labeled planning-only.
- Any local inspection uses `toolsets=["repo-read"]`.
- The plan lists unresolved validation work instead of claiming it has been performed.
