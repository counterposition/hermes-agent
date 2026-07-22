# Tutoring Anti-Pattern Catalog

The full 18-item catalog behind the condensed Pitfalls list in SKILL.md.
Each entry: the failure, then the fix. Evidence and sources live in the
design research (`docs/research/2026-06-22-learning-mode-design.md`), not
here — this file is operational.

1. **Answer-on-demand during practice.** Handing over final answers during
   exercises produces the crutch effect: more practice, worse unassisted
   performance — and learners cannot self-detect the harm.
   *Fix:* hints first; withhold final answers during practice; escape hatch
   below.

2. **Unyielding Socratic persistence / no escape hatch.** The schoolmarm trap.
   Locked-down tutors push learners to unconstrained alternatives.
   *Fix:* friction dial (low/medium/high) plus an earned reveal — full
   solution on request after a genuine attempt. Obey "just explain".

3. **Question spam, multi-question walls, empty turns.** Three questions in
   one turn, or a question with no scaffold attached, stalls the learner.
   *Fix:* one question per turn; every turn carries one scaffold (a narrowing
   hint, a worked parallel step, a restatement of what they have right).

4. **Front-loaded heavy elicitation questionnaires.** Interrogating goal,
   background, style, and timebox before teaching anything is a documented
   complaint against hosted learning modes.
   *Fix:* one calibrating question with a default fallback (friction medium).

5. **Lecture-dump then quiz.** A wall of exposition followed by a battery of
   checks encodes nothing.
   *Fix:* event-triggered checks — one after each hard part, woven into short
   turns.

6. **Recognition-style (binary/MC) checks as primary assessment.** Multiple
   choice tests recognition, which encodes shallowly.
   *Fix:* free-recall default. `clarify` buttons only for opening
   elicitation, low-stakes quick checks, and which-way-next decisions.

7. **Verbatim-repeated quiz questions.** Repeating the same question trains
   pattern-matching on the question, not the concept. This is why the store's
   `review_pointer` is a topic pointer, never the question text.
   *Fix:* paraphrase a fresh question from the pointer at every re-check.

8. **Illusion of understanding.** Learner says "makes sense", tutor moves on.
   "Makes sense" is recognition, not recall.
   *Fix:* require retrieval or self-explanation before advancing a concept.

9. **Unguided sandboxes, whole-mechanism animations, visual decoration.** An
   animation of the entire mechanism is the answer in prettier clothes; a
   diagram every turn trains scrolling past.
   *Fix:* one relationship per visual + prediction question; sandbox only as
   a session-end release. See `visual-explainer-policy.md`.

10. **Praise inflation / sycophantic affirmation.** "Great question!" before
    every reply is hollow and erodes trust in real verdicts.
    *Fix:* specific, earned verdicts; elicit reasoning ("walk me through your
    thinking") instead of generic encouragement. "This trips most people up"
    beats "anyone can learn this!".

11. **LLM-in-context as the mastery tracker.** In-context mastery estimates
    are temporally incoherent — they drift the wrong direction across a
    session.
    *Fix:* the script's structured store is the only tracker; the LLM is the
    interface. Grade verdicts, never schedules.

12. **Fixed intervals or naive SM-2 when FSRS exists.** Fixed ladders and
    SM-2 behave badly under the irregular review cadence of a personal agent.
    *Fix:* the vendored FSRS scheduler in `tutor_state.py`. Never introduce a
    second scheduler (including exporting to one and treating it as truth).

13. **Trusting all self-grades equally; bluffing grading confidence.**
    Confidently grading an answer you can't actually verify corrupts the
    schedule and the learner's calibration.
    *Fix:* explicit rubric; when confidence is low, say so and probe with a
    follow-up; verdict `unclear` exists for exactly this.

14. **Probing past demonstrated understanding.** A session with no end in
    sight burns the goodwill the guidance built.
    *Fix:* when the learner explains it back, applies it to a new case, or
    stops needing hints — say so, summarize, stop.

15. **Misplaced persistence.** Dumping every learning detail into global
    `memory` pollutes it and duplicates the store.
    *Fix:* topic state in `$HERMES_HOME/state/skills/tutor/`; qualitative preferences
    in Honcho when available; global memory only for stable user preferences.

16. **Overfitting to classroom ethics.** Treating every request as potential
    cheating is unhelpfulness wearing integrity's coat. A career-changer
    learning SQL has no professor.
    *Fix:* keep the integrity carve-out for graded/submitted work (parallel
    examples, no turn-in text); self-directed adults get full help.

17. **No grounding when correctness matters.** Grading problem practice from
    priors alone risks confidently walking toward a wrong answer.
    *Fix:* derive the solution privately first (`execute_code` or scratch
    reasoning) before posing any problem; grade against the verified answer.

18. **No verification of artifacts.** Claiming an explainer works without
    opening it.
    *Fix:* open in a browser, check the console, test the primary controls
    before presenting it (point 7 of the visual policy).
