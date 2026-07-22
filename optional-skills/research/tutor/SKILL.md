---
name: tutor
description: Teach concepts via diagnosis, practice, and spaced review.
version: 1.0.0
author: Harish Kukreja, adapted from Anthropic's learn skill (Apache-2.0)
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [learning, tutoring, education, socratic, study, visualization, fsrs]
    category: research
    related_skills: [concept-diagrams, tui-widgets, p5js, manim-video]
    config:
      - key: knowledge_base.path
        description: Markdown directory for closure notes (e.g. an Obsidian vault)
        prompt: Knowledge base directory (optional)
      - key: knowledge_base.wikilinks
        description: Use [[wikilinks]] in agent companion notes
        default: false
---

# Tutor Skill

Run a bounded tutoring loop: diagnose, teach one concept at a time, require recall, grade honestly, and schedule spaced review through a deterministic FSRS script. The goal is not to answer the learner's question but to make them able to answer it — this time and next time. This skill does NOT do source-grounded teaching from user documents or scenario/roleplay practice (v1 is concept tutoring only), and it never does the learner's graded work for them.

## When to Use

- "Teach me X", "explain how X works", "quiz me on X", "I'm rusty on X"
- Confusion signals: "won't stick", "keep mixing these up", "not getting it"
- Reviewing previously studied topics ("what's due?", "let's review")

Do NOT use for: tasks (coding, writing, lookup), personal troubleshooting, opinion prompts, or one-off factual questions with no learning intent.

## Prerequisites

- `terminal` toolset (runs the state script via `uv run`).
- Optional: `knowledge_base.path` skill config — a markdown directory (e.g. an Obsidian vault) for closure notes. When unset, skip the knowledge-base bridge silently.
- Optional: Honcho memory tools for qualitative learner preferences. The skill works without them.

## How to Run

All learner state lives in profile-scoped JSON files under `$HERMES_HOME/state/skills/tutor/`, outside the replaceable skill package. It is owned exclusively by `scripts/tutor_state.py`. Never edit these files directly, and never do scheduling arithmetic yourself — you supply verdicts; the script owns the schedule.

```bash
# Hermes substitutes this token with the active profile's installed skill path.
uv run python "${HERMES_SKILL_DIR}/scripts/tutor_state.py" <verb> [args]
```

Every verb prints JSON except `summary` (human-readable). Errors exit nonzero with one-line JSON `{"error": "..."}`. Flags for testing: `--data-dir DIR` overrides the state directory; `--now ISO8601` overrides the clock.

## Quick Reference

| Intent | Command |
|---|---|
| Session open: due reviews + last-session report | `tutor_state.py summary [topic]` |
| List topics | `tutor_state.py topics` |
| Start a topic (idempotent) | `tutor_state.py start <topic> --title T [--goal G] [--friction low\|medium\|high]` |
| List a topic's concepts (ALWAYS before adding) | `tutor_state.py concepts <topic>` |
| Add a concept | `tutor_state.py add-concept <topic> <id> --pointer "..." [--force]` |
| Record a graded check (immediately, every time) | `tutor_state.py rate <topic> <id> --verdict correct\|partial\|misconception\|unclear [--transfer] [--misconception-note "..."]` |
| Due concepts, interleaved across topics | `tutor_state.py due [topic]` |
| Log a session | `tutor_state.py log-session <topic> --covered id,id --struggled id,id` |
| Store statistics | `tutor_state.py stats [topic]` |

Verdict → FSRS rating (fixed in the script; you never pick a rating): correct → Good; correct with `--transfer` → Easy; partial → Hard; misconception → Again; unclear → Again. `--transfer` is only accepted with `correct` — the script rejects it otherwise. Usage errors (bad verb, flag, or choice) also come back as one-line JSON with exit code 2.

## Procedure

### Session open

Run the `summary` verb via `terminal` before anything else. If reviews are due, open with them — "3 reviews due on transformers; last time you were stuck on KV-cache" — before starting new material. When several topics have due concepts, follow the `due` verb's round-robin order and interleave across topics rather than blocking through one. Each due concept carries a `review_pointer`: a topic pointer to paraphrase a FRESH question from, never a verbatim question to repeat.

### 0. Contract

For a new topic, establish goal (intuition / problem-solving / exam prep / implementation), friction level, and timebox with at most ONE light calibrating question — `clarify` is fine here. If the learner doesn't engage, default to friction medium and move on. Never run a questionnaire. Then `start <topic>` (idempotent — safe to re-run).

**Friction dial** (learner can change it anytime; "just explain" is obeyed, not argued with):

| Level | Behavior |
|---|---|
| low | Explain first, then one quick check. |
| medium | Diagnose first, then teach/check in short loops. **Default.** |
| high | Mostly guided questions and hints; full solution only when the learner asks twice or is clearly stuck. |

**Earned escape hatch:** after a genuine attempt, give the full solution on request at any friction level. Distinguish **impatient** (engaged, has the pieces, wants speed — narrow the question, keep them doing the last step) from **genuinely stuck** (same wrong idea repeated, silence, shutdown — do the first step yourself, then rebuild with them driving). Time pressure that appears only AFTER questions started is impatience; a deadline stated up front is a real constraint — answer directly and offer depth later.

### 1. Diagnostic probe

One question that reveals the current mental model: explain, predict, or solve a tiny example. Not "rate yourself beginner/intermediate/advanced". Fluent expert phrasing calibrates the level you teach at, not whether you diagnose.

### 2. Map

From the diagnostic, infer level and likely misconceptions, and state a short path of 3–5 concept nodes. Run the `concepts` verb FIRST and reuse existing concept IDs — never mint a near-duplicate. Then `add-concept` for genuinely new nodes with a paraphrasable `--pointer`. If the script refuses with `near_duplicate` (exit 2), use the existing ID; pass `--force` only when the concepts are truly distinct.

### 3. Microcycle (per concept)

A. **Activate/predict** — one targeted question or mini-problem; attempt-first when the topic warrants.
B. Learner answers.
C. **Grade explicitly**: correct / partial / misconception / unclear, against a stated rubric.
D. **Feedback** — affirm what was right, name the specific gap or misconception.
E. **Smallest useful intervention** — a narrowing hint before an explanation; explanation short enough that the learner's next action is nearby.
F. **Self-explanation** — ask them to restate WHY it works.
G. **One NEW retrieval/transfer check** — not isomorphic to the explanation just given.

Run the `rate` verb IMMEDIATELY after each grading — never batch ratings at session end (sessions end by interruption, not ritual). Pass `--transfer` only when the learner demonstrably applied the idea to a new case. Record caught misconceptions with `--misconception-note` on `partial` or `misconception` verdicts. A later `correct` verdict for that concept resolves its active misconception notes automatically.

For problem practice, derive the solution privately FIRST — `execute_code` or scratch reasoning — before posing the problem, so grading is grounded in a verified answer, not your priors.

**Grading rules.** Free-text recall is the default check; use `clarify` choice buttons only for opening elicitation, low-stakes quick checks, and which-way-next decisions. When your grading confidence is low, SAY SO and probe with a follow-up instead of bluffing. On a failed check: dialogic re-teach from a different angle, then re-check with a PARAPHRASED question — verbatim repeats train pattern-matching. One question per turn, every turn carries a scaffold, no praise inflation: verdicts are specific and earned ("this trips most people up" beats "great question!").

### 4. Optional visual

Only when the concept has shape — spatial, dynamic, causal, probabilistic, algorithmic, or parameter-sensitive. One relationship per visual, paired with a prediction question; never the whole mechanism animated, never decoration every turn. Full policy, required structure, and the tier table (`concept-diagrams` / `tui-widgets` / `p5js` / `manim-video`) live in [references/visual-explainer-policy.md](references/visual-explainer-policy.md) — read it before building any visual.

### 5. Closure

Stop probing once the learner demonstrates transfer — say so plainly. Then:

1. Three-bullet summary: covered, misconceptions fixed, still weak.
2. **Knowledge-base bridge** (skip silently if `knowledge_base.path` is unset):
   - FIRST prompt the learner to write their own note in their knowledge base. This is the generation effect doing pedagogical work — their paraphrase beats your prose. Not admin; do not skip or offer to do it for them.
   - THEN write an agent companion note via `write_file` into `<knowledge_base.path>/concepts/agent/` (create the directory if missing), with YAML frontmatter (`author: hermes-agent`, `topic`, `date`), a session summary, misconceptions fixed, and 3 retrieval questions. Filename rules: exactly `<topic-slug> (agent).md`, derived from the topic slug only — never from learner-supplied text, never containing `/`, `\`, or `..` — and the resolved path must stay inside `concepts/agent/`. If the file already exists, APPEND a new dated section; never truncate or rewrite prior content. Strictly additive — NEVER edit human-authored notes or write anywhere else in the knowledge base. Use `[[wikilinks]]` only when `knowledge_base.wikilinks` is true.
3. Run `log-session` with `--covered` and `--struggled` concept IDs.

### Honcho (optional)

When Honcho memory tools are available, consult them for preferred analogy domains, depth tolerance, and frustration signals, and persist durable meta-observations ("prefers mechanical analogies") there — not topic mastery, which belongs to the FSRS store. The skill works fully without Honcho.

## Pitfalls

Condensed from the anti-pattern catalog — the full 18 items with evidence and fixes are in [references/anti-patterns.md](references/anti-patterns.md).

- **Answer-on-demand during practice** — the crutch effect; hints first, withhold final answers during exercises (escape hatch: earned reveal after a genuine attempt).
- **Rigid Socratic persistence** — the schoolmarm trap drives abandonment; honor the friction dial and "just explain".
- **Question spam / empty turns** — one question per turn, every turn carries a scaffold.
- **Heavy up-front elicitation** — one calibrating question with a default fallback, never a questionnaire.
- **Lecture-dump then quiz** — checks are event-triggered after each hard part, woven into short turns.
- **Multiple-choice as primary assessment** — free recall by default; `clarify` buttons only for the three sanctioned uses.
- **Verbatim re-quizzing** — always paraphrase from `review_pointer`; repeats train pattern-matching.
- **"Makes sense" as evidence** — it isn't; require retrieval or self-explanation before moving on.
- **Unguided sandbox visuals / whole-mechanism animations** — one relationship + prediction question; sandbox only as a session-end release.
- **Praise inflation** — elicit reasoning instead of cheerleading; verdicts specific and earned.
- **Being the mastery tracker yourself** — your in-context mastery estimates are unreliable; the script's store is the only tracker. Never estimate due dates or "adjust" the schedule.
- **Probing past demonstrated transfer** — burns goodwill; end the session.
- **Dumping learning state into global `memory`** — topic state belongs in the profile-scoped tutor store (and Honcho for qualitative preferences), not global memory, unless it's a stable user preference.

## Verification

Round-trip the script against a throwaway state dir (examples assume a POSIX shell):

```bash
D=$(mktemp -d)
uv run python "${HERMES_SKILL_DIR}/scripts/tutor_state.py" start demo --title "Demo" --data-dir "$D"
uv run python "${HERMES_SKILL_DIR}/scripts/tutor_state.py" add-concept demo test-concept --pointer "why demo works" --data-dir "$D"
uv run python "${HERMES_SKILL_DIR}/scripts/tutor_state.py" rate demo test-concept --verdict correct --data-dir "$D"
uv run python "${HERMES_SKILL_DIR}/scripts/tutor_state.py" due --data-dir "$D" --now 2099-01-01T00:00:00Z
```

Expect: `start` returns JSON with `"created": true` (and `false` on re-run); `rate` prints `due_at`/`rating`/`stability` and `$D/demo.json` exists with a one-entry `review_log`; `due` with the far-future `--now` lists the concept. `summary` should print human-readable text, not JSON.

From a repo checkout, run the test suite:

```bash
scripts/run_tests.sh tests/skills/test_tutor_skill.py
```

Agent-level check: a real session should show `summary` run at session open, a `rate` call after every graded check (not batched), and — when `knowledge_base.path` is set — a companion note under `concepts/agent/` that did not touch any human-authored file.
