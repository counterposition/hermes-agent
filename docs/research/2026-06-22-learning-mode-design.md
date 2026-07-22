# Learning Mode — Design Research (Consolidated)

**Date:** 2026-06-22 EDT
**Updated:** 2026-07-04 — design-review decisions folded in (see "Decision log —
2026-07-04" below); amendments are also applied inline and win over any older
phrasing they contradict.
**Status:** Consolidated research synthesis + design recommendation — this is the
single source of truth for the Hermes learning feature ("learning mode").
**Supersedes:** Earlier broad, evidence-weighted, and capability-survey research
passes, consolidated here.
**Question:** Should Hermes get a learning-mode skill, plugin, or core mode — and
what should the actual learning interaction be?

---

## Verification status & how to read the markers

The two source passes were independently researched. The second pass ran a
5-angle deep-research workflow (search → fetch → claim extraction → adversarial
verification) over 20 sources / ~120 falsifiable claims; its automated
verification phase crashed on an API session limit, so load-bearing claims were
re-verified manually. Every claim below carries a confidence marker so an
overstated source cannot quietly drive a design decision:

- **✅ fetched** — confirmed by direct fetch of the primary source this research.
- **◐ corroborated** — well-known pre-2026 result, corroborated against model
  training knowledge but not re-fetched.
- **▣ anecdote** — blog/reception sourcing only; weighted as a signal, not proof.

The net read after verification: the *direction* (deliberate tutor pattern >
passive answer tool; guardrails matter; persistent review matters) is supported
by multiple independent ✅ sources. The specific effect-size magnitudes are
noisier and should not be quoted as guarantees.

---

## Executive decision

**Build a skill — not a plugin, and not a core Hermes mode.** Back it with a
**deterministic FSRS learner-model script** the skill calls. This is the decided
direction.

The reframing that matters: this is **not** "make Hermes ask questions." It is
"make Hermes run a bounded learning loop" — diagnose → teach one concept →
require active recall / self-explanation → give feedback → adapt → optionally
visualize → summarize and schedule retention.

Why a skill, and why this is the *industry* pattern rather than a compromise:

- Pedagogy lives in the **prompt + content grounding, not the model.** ChatGPT
  Study Mode shipped as a pure system prompt. LearnLM abandoned a separate tutor
  model for "pedagogical instruction following." The Harvard RCT tutor (below)
  was a system prompt encoding learning science + pre-written expert solutions.
  ✅/◐
- A Hermes skill is exactly an instruction payload injected as a **user message**
  — cache-safe, session-scoped, zero core changes. The skill invocation *is* the
  mode (no global toggle, no `/personality` overlay).
- Skills already provide everything the design needs: a slash command, NL
  triggering via the skills index, declared config (`metadata.hermes.config`),
  an injected absolute skill directory for packaged scripts, and a profile-aware
  `HERMES_HOME` for durable state.

### Why not a plugin (this refines the first pass)

The 06-10 pass floated a plugin as a "good v2" for durable state, `/learnmode`
toggles, and `pre_llm_call` hook injection. The 06-11 pass examined that and
concluded **nothing in the design requires plugin machinery**:

- Durable state → a Python script + `$HERMES_HOME/state/skills/tutor/`, not
  plugin tools or mutable files inside the replaceable skill package.
- Mode entry → the skill invocation itself (cache-safe user-message injection),
  not a stateful toggle.
- Context injection → a **session-open ritual** (pull-based: the script reports
  due reviews at the start of a session), *not* a `pre_llm_call` hook that risks
  polluting every turn and mutating cached context.

A plugin would only buy CLI subcommands (`hermes learn stats`) — deferrable, and
partially coverable by `quick_commands`. So: **skill + script first.** Revisit a
plugin only if real use proves we need CLI-surface stats or cross-profile state.

### Why not a core mode

A core mode would touch hot CLI/TUI/gateway/session surfaces and create
rebase/maintenance cost for a fork, with no capability payoff — the learning
interaction needs instruction, tools, and a little state, all reachable from a
skill. Learning should be opt-in at the task/session level, never a global stance.

---

## Decision log — 2026-07-04

Settled in design review (user-confirmed). Folded inline throughout; listed
here so the deltas from the 06-22 text are auditable.

1. **Reviews are Hermes-owned.** The skill's FSRS store is the single source of
   truth for scheduling, and reviews happen conversationally — dialogic
   remediation is the point of an agent tutor. We do NOT delegate scheduling to
   the Obsidian spaced-repetition plugin: verified 2026-07-04, OSR is still
   SM-2-OSR only with FSRS merely "planned" (st3v3nmw/obsidian-spaced-repetition
   #748), which would trip anti-pattern #12. Flashcard export for OSR/Anki stays
   possible later as a *derived view* of the store — never a second scheduler.
2. **Push stays deferred — deliberately.** Pull-only v1 despite the durability
   tension: simpler, and no ambient cron infrastructure to install, clean up,
   and maintain. The source-of-truth decision keeps push cheap later (a cron
   job reading the `due` verb).
3. **Append-only review-event log in the store.** The durable asset is the raw
   review history (concept, timestamp, verdict, rating) — current FSRS
   parameters are derivable from it. This keeps re-fitting weights, switching
   algorithms, and Anki export open forever, for the cost of one JSON array.
4. **Knowledge-base bridge ships in v1** (see "The knowledge-base bridge"):
   one-way, closure-time, human-note-first, agent companion notes clearly
   attributed. Conventions mined from the obsidian-capture ADR 0003; the
   capture branch itself stays parked as a separate initiative (it is NOT
   obviated by llm-wiki — llm-wiki is an agent-owned wiki under `~/wiki`;
   capture is companion notes inside the user's own vault).
5. **Vendor py-fsrs; don't reimplement.** FSRS is ~20 fitted weights plus
   subtle forgetting-curve and same-day handling — exactly the code that looks
   right and schedules wrong. Vendor it into `scripts/` with attribution; tests
   assert scheduling invariants (due dates monotone in rating, stability grows
   on success), not re-derived math.
6. **Deterministic rubric→rating mapping lives in the script.** The LLM chooses
   a rubric verdict (correct / partial / misconception / unclear); the script
   maps it to an FSRS rating via a fixed, conservatively-biased table. The LLM
   never chooses a schedule — grade inflation must not corrupt stability.
7. **Incremental write-back, not closure-only.** State is written after every
   concept microcycle (each `rate` call); closure is a summary pass. Real
   sessions end by abandonment, interrupt, or context compression — not by
   ritual.
8. **Concept-ID reuse is enforced by the script.** The skill must list existing
   concepts before creating any; the script fuzzy-matches near-duplicate IDs so
   cross-session re-derivation ("sin-cos-pairing" vs "sine-cosine-pairs")
   cannot fragment the store.
9. **v1 is one mode: concept tutoring.** Source-grounded teaching and
   scenario/roleplay practice (the other two legitimate modes from the product
   landscape) are additive prose sections later — encoding all three at v1
   bloats the instruction payload.
10. **Renamed to `tutor` / `/tutor` (2026-07-04, post-build).** Upstream
    shipped a built-in core `/learn` slash command on main (skill acquisition:
    "Learn a reusable skill from anything you describe", agent/learn_prompt.py
    + learning_graph, plus `/journey` with alias `learning`). Registry commands
    win slash dispatch over skill commands, so a skill named `learn` would be
    permanently shadowed after the next fork sync. The skill is therefore
    `optional-skills/research/tutor/` with `name: tutor`, description "Teach
    concepts via diagnosis, practice, and spaced review." NL triggering
    ("teach me X") is unaffected; upstream's /learn is an orthogonal feature,
    not a competing tutor.

---

## Headline design implications

1. **A deliberate tutor interaction roughly doubles learning gains** over the
   same LLM used as a passive answer tool (meta-analysis: tutor g = 0.902 vs
   tool g = 0.436). ✅
2. **Durability is where the value concentrates.** Interventions > 8 weeks:
   g = 1.024; 1–4 weeks: g = 0.389. A persistent learner model + review loop is
   the differentiator, not a nicety. ✅
3. **Unguarded answer-giving actively harms learning** (−17% on later unassisted
   exams vs never-exposed controls), and learners cannot self-detect the harm.
   Hint-first guardrails eliminate it. ✅
4. **But rigid Socratic persistence is the #1 documented anti-pattern.** 44.3% of
   human-tutor edits to a deployed LearnLM tutor were *loosening* its pacing;
   locked-down tutors push learners to unconstrained alternatives. Guarded-by-
   default **with an earned escape hatch** is the evidence-backed middle. ✅
5. **Pedagogy lives in the prompt + grounding, not model choice.** (See decision
   above.) ✅/◐
6. **One question per turn, short turns; comprehensiveness accumulates across
   turns.** Verbatim from the UK RCT's effective prompt: "Do not give the student
   the answer." / "Only ask the student one question at a time." ✅
7. **Elicit prior knowledge lightly** — one calibrating question with a default
   fallback, not a mandatory questionnaire. Over-elicitation is a documented
   complaint against Claude's learning style. ◐/▣
8. **Checks must demand recall, not recognition** (free-text first; binary/MC =
   shallow encoding), and a failed check should trigger **dialogue, not a static
   correction** (dialogic remediation 93.0% vs static hints 65.4% second-attempt
   success). ✅
9. **The LLM cannot *be* the learner model.** LLM mastery estimates are
   temporally incoherent (they move the wrong direction; DKT AUC 0.83 vs
   fine-tuned LLM 0.77). Keep quantitative mastery/schedule state in an explicit
   structured store the LLM reads and writes. ✅
10. **Visuals are scaffolds, not rewards.** Guided interactives showing *one*
    relationship with a prediction question attached; unguided sandboxes and
    whole-mechanism animations are anti-patterns. Sandbox belongs at session end. ◐

---

## Evidence base

### A. Recent AI-tutor field evidence (the RCTs)

| Study | Design | Key findings | Status |
|---|---|---|---|
| Harvard intro-physics RCT (Kestin et al., *Sci Reports* 2025) | RCT, authentic course | AI tutor beat expert-led active classroom: 0.63 SD (linear), 0.73–1.3 SD (quantile, ceiling-corrected); median 49 min vs 60-min class; higher engagement. Tutor = learning-science system prompt + part-by-part scaffolding + pre-written expert solutions for grounding. | ◐ |
| Wharton/UPenn Turkey study (Bastani et al., *PNAS* 2025) | RCT, ~1,000 HS math students | GPT Base: +48% practice, **−17% on unassisted exam** (crutch effect). GPT Tutor (teacher solution embedded + hints-only, no final answers): +127% practice, exam harm eliminated (n.s.). Learners didn't perceive the harm. | ◐ |
| UK 5-school RCT (LearnLM + human supervision, arXiv 2512.23633) | 7-week RCT, N=165, Y9–10 math | Transfer +5.5pp vs human tutors (66.2% vs 60.7%). Dialogic remediation 93.0% / human 91.2% / static hints 65.4%. **44.3% of human edits = loosening Socratic pacing.** 74.4% of drafts accepted unedited; 0.1% factual errors. Prompt published verbatim. | ✅ |
| Meta-analysis, 133 studies / k=188 (arXiv 2509.22725) | Meta-analysis, Nov 2022–Mar 2025 | Knowledge gains g = 0.751 pooled. Role moderates: tutor 0.902 / partner 0.842 / passive tool 0.436. Duration: >8 wks 1.024 vs 1–4 wks 0.389. Recommends strategic answer-withholding. | ✅ |
| Nigeria World Bank pilot (GPT-4 after-school) | RCT, 6 weeks | +0.31 SD composite; ≈ 1.5–2 years of business-as-usual schooling; among the most cost-effective interventions recorded. | ◐ |
| Tutor CoPilot (Stanford, 2024) | RCT, 700 tutors / 1,000 students | +4pp mastery (p<0.01); +9pp for weakest tutors. Mechanism: +10pp "explain your thinking" prompts displacing generic encouragement. | ◐ |
| "See Solution" field study (arXiv 2504.11146) | Field study, 885 CS1 students | With optional bypass of Socratic guardrails: 50% never bypassed; 14% bypassed every time; bypassing concentrated in weaker students. Recommends **conditional/earned** solution access; hard lockdown risks abandonment to unconstrained chatbots. | ✅ |
| LLM grading calibration (arXiv 2603.29559) | 7 LLMs, 3 STEM datasets | Free-text grading is imperfect → use selective automation. Self-reported confidence best calibrated (ECE 0.166). Bigger graders better calibrated. | ✅ |
| LLM-as-knowledge-tracer critique (arXiv 2512.23036) | Benchmark on ASSISTments (603k interactions) | DKT AUC 0.83 > LoRA-tuned Llama-3-8B 0.77 > zero-shot 0.69. LLM mastery updates temporally incoherent. Prescription: explicit learner-state store + LLM as interface. | ✅ |
| LearnLM report (arXiv 2412.16429) | Expert preference eval (N=228, 2,360 convs) | "Pedagogical instruction following" replaced the separate tutor model. Experts prefer +31% vs GPT-4o / +11% vs Claude 3.5 / +13% vs Gemini base. Caveat: preference ≠ learning gains; role-played learners. | ◐ |
| IntelliCode (arXiv 2512.18669) | Architecture + simulated learners only | Versioned learner state (mastery+uncertainty, misconceptions, review schedule); single-writer orchestrator; graduated 5-level hint ladder; SM-2 grades derived from telemetry, not self-rating. No human evidence. | ✅ (arch) |

### B. Foundational learning-science principles (the *why*)

These pre-date the AI-tutor wave and explain *why* the loop below is shaped the
way it is. All ◐ (well-established, corroborated).

- **Retrieval practice / practice testing** — among the highest-utility
  techniques (Dunlosky, "Strengthening the Student Toolbox"). Every session must
  include free-recall or transfer checks; never let it end on "that makes sense."
- **Distributed practice / spacing** — high-utility; underpins the review loop
  (Dunlosky; Cepeda et al. meta-analysis). If the learner wants retention, end
  with a review schedule, not a single beautiful explanation.
- **Self-explanation** — meta-analysis weighted mean **g = 0.55** (Bisra et al.
  2018). After explaining, ask the learner to restate *why* a step works; grade
  it and repair gaps.
- **ICAP** (Interactive > Constructive > Active > Passive; Chi & Wylie 2014) —
  prefer tasks where the learner predicts, explains, argues, debugs, or teaches
  back. Keep passive mini-lectures short and purposeful.
- **ITS work, but implementation quality dominates** — median **0.66 SD** over
  conventional instruction across 50 controlled ITS evaluations (Kulik &
  Fletcher 2016), but contingent on test alignment and implementation. Don't
  overclaim.
- **Productive failure / problem-solving-before-instruction** — **g = 0.36**
  favoring attempt-first (Sinha & Kapur 2021). For conceptual topics, ask the
  learner to attempt/predict before the canonical explanation.
- **Pedagogical steering** — default instruction-tuned LLMs optimize "helpful
  assistant," which conflicts with learning; model the interaction as a small
  state machine with student-state classification (Puech et al., StratL 2025).
- **LLMs are not automatically good tutor models** — in TutorGym, none beat
  chance at labeling incorrect actions; next-step correctness only ~52–70%. For
  exact domains, add answer keys / tool verification / source grounding. ✅

---

## Product landscape

| Product | Entry | Pedagogy | Notable |
|---|---|---|---|
| **ChatGPT Study Mode** (Jul 2025) | Toggle / `/study` | Pure system prompt co-written with pedagogy experts: lightweight elicitation w/ default fallback; never-do-the-work rule; **event-triggered checks "after hard parts"**; varied activities incl. teach-back; anti-sycophancy tone | Prompt-only mode fails to hold on weaker models ▣; pacing failures observed ▣ |
| **Claude Learning Mode** (Apr 2025; styles Aug 2025) | Opt-in (Projects, then style dropdown) | Socratic ("How would you approach this?", "What evidence supports your conclusion?") + structured artifacts (guides, templates) as first-class outputs | Deployed campus-wide (Northeastern 50k, LSE) ◐; over-elicitation complaint ▣ |
| **Gemini Guided Learning + LearnLM** (Aug 2025) | Named experience + shareable link | Stepwise decomposition, soft-Socratic, multimodal aids (diagrams, videos, quizzes) | Long-session degradation; rigid persona reads patronizing ▣ |
| **Khanmigo** | Platform-embedded | Strict Socratic default; mastery-system integration | Walled garden; text-only complaint ▣ |
| **NotebookLM Learning Guide** | Upload sources | **Source-grounded** answers w/ citations; flashcards, quizzes, mind maps, audio/video overviews | The strongest pattern here is grounding: teach from the user's docs and cite them ◐ |
| **Eedi + DeepMind constrained tutor** | After a wrong diagnostic | Tutor activates *only* after an incorrect answer; bounded to one construct; distractors mapped to **named misconceptions**; supports one moment of difficulty then returns to the lesson | The clearest counterexample to "general chat tutor" — diagnosis-first design ✅ |
| **Duolingo Max** | In-app | **Scenario practice** (roleplay / video call), feedback on accuracy & complexity; curriculum experts design scenarios | For skills/languages/interviews, the right primitive is roleplay, not explanation ◐ |
| **OpenTutor** (OSS, local-first) | Local app | RAG-grounded answers w/ citations; **Socratic as an option, not always-on**; FSRS 4.5; BKT + cognitive-load module | Closest OSS analog to a personal-agent tutor ✅ |
| **agent-tutor-skill** (OSS, unproven) | `/learn` skill in Claude Code | Markdown learner model under `~/.learn/` (profile + per-topic KBs); Explain→Example→Check→Evaluate→Practice | Convergent independent design; runs FSRS as LLM-executed formulas (**fragile** — don't copy this) ✅ |
| **Anthropic `learn` skill** (Apache-2.0, local at `~/.claude/skills/learn/`) | Skill trigger (explicit + NL) | Diagnose-before-teach; one question + one scaffold per turn; impatient-vs-stuck distinction; toolkit of moves; visuals-as-scaffold; integrity carve-out; anti-pattern list | **Best-curated tutoring-craft prose found; license permits adaptation with attribution** — this is our starting point for the skill body |
| **DeepTutor** (OSS platform) | Full platform | Chat/solve/quiz/research/viz/KBs/memory/autonomous bots | Architectural reference only — Hermes already has the substrate; we need policy + state, not a platform ◐ |

**Coverage gaps** (not verified; none expected to change the architecture):
NotebookLM study internals, Duolingo Birdbrain, Brilliant, Mr. Ranedeer, Khanmigo
primary sources, voice tutors.

**The two strongest takeaways for Hermes:**
1. The Anthropic `learn` skill is Apache-2.0 and is the best tutoring-craft prose
   available — **adapt it (with attribution)** rather than write the pedagogy
   from scratch.
2. Diagnosis-first (Eedi) + source-grounding (NotebookLM) + scenario practice
   (Duolingo) are three distinct, legitimate modes the skill should support.

---

## The interaction model

A good Hermes learning session runs this loop. (Comprehensiveness accumulates
*across* turns — each turn is short, one question at a time.)

```text
0. Contract
   - What do you want to learn?
   - Desired outcome: intuition / problem-solving / exam prep / implementation skill / debate-level nuance?
   - Socratic friction: low / medium / high?
   - Timebox?

1. Diagnostic  (diagnose before teaching — Eedi)
   - Ask the learner to explain, predict, solve a tiny example, or pick among diagnostic options.
   - Prefer a probe that reveals the current mental model and likely misconception, NOT "rate yourself beginner/intermediate/advanced."

2. Map
   - Infer learner state: novice / partial / fluent / advanced.
   - Identify likely prerequisites and misconceptions.
   - State a short path: 3–5 concept nodes.

3. Microcycle per concept
   A. Activate/predict: targeted question or mini-problem (attempt-first when the topic warrants — productive failure).
   B. Learner answers.
   C. Grade explicitly: correct / partial / misconception / unclear.
   D. Feedback: affirm the correct part, name or describe the gap.
   E. Hint or explanation: the smallest useful intervention (hint ladder).
   F. Self-explanation: ask the learner to restate WHY it works.
   G. Retrieval/transfer check: one NEW question, not isomorphic to the explanation.

4. Optional visual explainer  (see policy below)
   - Only when the concept is spatial/dynamic/causal/probabilistic/algorithmic/parameter-sensitive.
   - Must carry a task: "make X happen," "predict what changes," "find the threshold."

5. Closure
   - 3-bullet summary.
   - Misconceptions fixed / still weak.
   - 3 retrieval questions or flashcards.
   - Write learner state back; schedule spaced review (FSRS).
```

### The six design tensions, resolved

**(a) Mode toggle vs always-on.** Every major product is opt-in. For a
self-motivated adult there is no adversarial student, so the bypassability
critique of toggles ▣ is moot. **The skill invocation IS the mode** —
`/tutor <topic>` or natural "teach me X," injected as a user message. No global
toggle.

**(b) Socratic strictness vs answer-giving.** Three layers:
- *During practice/assessment:* withhold final answers, give hints/scaffolds
  (the PNAS −17%-vs-no-harm difference).
- *Pacing:* never rigid — one question per turn, and distinguish **impatient**
  (narrow the question, keep them doing the last step) from **genuinely stuck**
  (give a foothold: do the first step, then rebuild). The 44.3%-loosening finding
  plus the `learn` skill's heuristic ("time pressure appearing only *after*
  questions started is impatience").
- *Escape hatch:* after a genuine attempt, full solution on request
  (conditional/earned access; half of learners never use it; hard bans cause
  abandonment).
- *Pure exposition requests* ("structured overview of X," no assessment) are a
  legitimate destination, not a failure — direct explanation is one of the moves.

**Friction dial (default = Medium):**

| Level | Behavior |
|---|---|
| Low | Explain first, then one quick check. |
| Medium | Diagnose first, then teach/check in short loops. **Default.** |
| High | Mostly guided questions and hints; no full solution until the learner asks twice or is clearly stuck. |

The user can say "just explain" / "less Socratic" and the skill obeys. The point
is learning, not obedience to a pedagogical aesthetic.

**(c) Comprehension-check cadence.** Event-triggered, not fixed-interval: one
check after each hard part / new concept, one question per turn, varied forms
(restate, apply to a new case, predict-before-reveal, teach-back, invent an
example). **Stop checking once the learner demonstrates transfer** — probing past
understanding burns goodwill. Anti-pattern: lecture-dump then quiz; multi-question
walls.

**(d) Free-text vs multiple-choice.** Free recall is the default check (MC =
recognition = shallow encoding). Use the `clarify` tool's choice buttons only for:
opening elicitation, low-stakes quick checks, and "which way next" decisions.
Grade free text in-conversation against an explicit rubric (correct / partial /
incorrect + *what specifically* was right/wrong); **when grading confidence is
low, say so and probe** instead of bluffing (the n=1 analog of selective
automation ✅). Failed check → dialogic re-teach from a different angle (93.0% vs
65.4% ✅), then re-check with a **paraphrased** question (verbatim repeats train
pattern-matching).

**(e) When to generate a visual.** When the concept has shape — a relationship, a
process, a parameter whose effect should be *felt*. See the policy section.

**(f) Praise inflation / honest grading.** Replace generic encouragement with
elicitation of reasoning — the Tutor CoPilot mechanism (+10pp "explain your
thinking" → +4pp mastery ◐). Verdicts are specific and earned; no "Great
question!"; "this trips most people up" beats "anyone can learn this!".
Sycophancy is a documented Study Mode failure ▣.

---

## Visual explainer policy

Interactive visual explainers are valuable only when **operating** them changes
understanding. Otherwise they are decorative artifacts that train skimming.

**Use them for:** geometry/topology/vector spaces/transforms; dynamic systems,
feedback loops, queues, control; algorithms with state transitions (search, sort,
gradient descent, backprop, consensus, scheduling); probability/statistics
(sampling, Bayes updates, distributions, CIs); tradeoff surfaces
(precision/recall, bias/variance, latency/quality/cost); systems architecture
flows and failure modes; physics/chemistry/biology mechanisms.

**Usually don't:** simple definitions; verbal distinctions with no spatial/dynamic
structure; topics where a table, analogy, or worked example suffices.

**Core rules** (from the explorables tradition — Victor ◐, Case ◐, Matuschak
narrated-explorables ◐ — plus the `learn` skill): **one relationship per visual**,
paired with a **prediction question** ("predict what happens as this → 0, then
drag it"); **never** the whole mechanism animated (that is the answer in prettier
clothes); **never** decoration every turn; narrated and prompted, **never an
unguided sandbox** — except as a deliberate session-end release (Case's three-act
arc: motivating question → guided interaction → sandbox).

### Explainer tiers → Hermes paths

| Tier | Output | Use when | Hermes path |
|---|---|---|---|
| Static diagram | SVG/HTML | relationships, taxonomy, pipeline, anatomy | delegate to `concept-diagrams` (`claude-design`) |
| Interactive simulator | TUI widget or HTML/p5.js with controls + tasks | parameters or dynamics matter | `tui-widgets` in TUI/dashboard sessions; `p5js` for a browser artifact |
| Animated explainer | Manim video | a math/algorithm sequence benefits from temporal staging | `manim-video`, on request only |

**Required structure for any interactive explainer:** (1) one-sentence learning
goal; (2) controls that map to *conceptual* variables, not cosmetic sliders;
(3) a prediction prompt before interaction; (4) a challenge ("set the parameters
so that…"); (5) a reflection question after; (6) reset/presets; (7) **verification
— open in browser, check console, test the primary controls** before claiming it
works. If meant to be consumed → a file; if meant to be operated → an
interactive widget or artifact.

---

## The durable learner model — architecture

This is the piece that separates Hermes from the hosted learning modes, and where
the >8-week effect (g = 1.024) lives. Three layers, per the hybrid prescription
(2512.23036 ✅, IntelliCode ✅, OpenTutor ✅, agent-tutor-skill ✅):

**1. Structured store — source of truth, script-managed.** Per-topic state with
per-concept **FSRS** parameters (difficulty, stability, due date), status,
misconception notes, and a short session log. A deterministic Python script owns
**all** reads/writes (atomic, like memento) — **the LLM never does scheduling
arithmetic in its head** (the agent-tutor-skill's LLM-executed FSRS formulas are
the fragile version of this; the KT critique ✅ is why the LLM can't *be* the
tracker). **FSRS, not SM-2/fixed intervals**: same retention with fewer reviews,
and far better behavior under *irregular* review cadence (Anki FAQ ✅) — exactly
the personal-agent reality. Memento's fixed 1/3/7-day ladder is the weakest option.
The FSRS engine is **vendored py-fsrs**, not a reimplementation; the
rubric→rating mapping is a fixed table in the script (the LLM grades, the script
schedules); each concept carries an **append-only review-event log** (the durable
asset — parameters are derivable, history is not); and writes are **incremental**
— after every `rate` call, not only at closure.

**2. Qualitative layer — optional, Honcho.** Preferred analogy domains, depth
tolerance, frustration signals, cross-topic patterns — `honcho_reasoning` to
consult, `honcho_conclude` to persist meta-observations. Soft dependency; the
feature works without it.

**3. Session-continuity ritual — pull-based.** *Open:* the script reports due
reviews + last-session summary ("3 reviews due on transformers; last time you were
stuck on KV-cache"); when several topics are due, **interleave** across topics
rather than blocking through one. *Close:* a summary pass (state has already been
written incrementally per microcycle) plus the knowledge-base bridge — prompt the
learner to write their own note first, then emit the agent companion note (see
"The knowledge-base bridge"). Pull, not push — deferring push is a deliberate
2026-07-04 decision (simplicity, no ambient cron infra to maintain), and the
authoritative store keeps push cheap to add later. **This is why no
`pre_llm_call` hook is needed** — and why prompt caching stays intact.

### Concrete store schema

One topic file lives under `$HERMES_HOME/state/skills/tutor/`. Keeping mutable
state outside the installed skill directory means a force reinstall can replace
the package without deleting the learner's history.

The script reads/writes this; it injects only a *compact summary* into the
conversation, never the raw JSON.

```json
{
  "schema_version": 1,
  "topic": "sinusoidal positional encodings",
  "goal": "implementation-level intuition",
  "friction": "medium",
  "concepts": [
    {
      "id": "sin-cos-pairing",
      "status": "learning",
      "fsrs": { "difficulty": 6.4, "stability": 2.1, "due": "2026-06-25T14:00:00Z" },
      "last_verdict": "partial",
      "review_pointer": "why each frequency uses BOTH sin and cos — paraphrase a fresh question at review time, never repeat verbatim",
      "review_log": [
        { "ts": "2026-06-22T15:12:00Z", "verdict": "partial", "rating": "hard" }
      ]
    }
  ],
  "misconceptions": [
    {
      "id": "sin-cos-pairing-m1",
      "concept_id": "sin-cos-pairing",
      "description": "Thinks each dimension is an arbitrary feature rather than paired sin/cos frequencies",
      "status": "active",
      "observed_at": "2026-06-22T15:12:00Z"
    }
  ],
  "session_log": [
    { "covered": ["why-position-needed"], "struggled": ["sin-cos-pairing"], "ts": "..." }
  ]
}
```

(FSRS parameters replace the first pass's plain `mastery: 0.5` floats — the
scheduler needs difficulty/stability/due, not a single scalar. `review_log` is
append-only and is the durable asset: current FSRS parameters can always be
re-derived from it, which keeps weight re-fitting, algorithm swaps, and Anki
export open. `review_pointer` is a *topic pointer* the LLM paraphrases from at
review time — storing a verbatim question would trip anti-pattern #7.)

---

## The knowledge-base bridge (v1)

At session closure the skill bridges the learning session into the user's own
knowledge base. Strictly **one-way**: learner state never depends on KB content,
and the machine-owned FSRS store never lives in the vault (human edits and
file-sync conflicts would corrupt scheduling state).

1. **Human note first.** The closure ritual prompts the learner to write their
   own note in their KB. This is the generation effect doing pedagogical work —
   the paraphrase-to-learn practice — not an inconvenience. The tutor prompting
   the human to write beats the tutor writing for them.
2. **Agent companion note second.** The agent then writes a clearly-attributed
   companion note: what was covered, misconceptions caught and fixed, and the
   session's retrieval questions. It follows the companion-note conventions
   established during the earlier Obsidian-capture design exploration: a
   dedicated agent subtree (e.g. `concepts/agent/`), strictly additive — never
   edit human-authored notes — with YAML frontmatter marking agent authorship,
   topic, and date. That exploration is not a dependency; only its conventions
   are adopted.
3. **KB-agnostic by construction.** The target is a configured markdown
   directory (skill config, e.g. `knowledge_base.path` under
   `metadata.hermes.config`) plus a wikilink flavor knob. Obsidian is just a
   markdown tree, so plain markdown works everywhere and Obsidian users point
   the path at their vault. No KB-provider ABC until 3+ real backends demand
   one — anything earlier is speculative infrastructure.

A future *read* path — grounding tutoring in the user's existing notes — is an
additive capability on top of this, not a redesign; the one-way constraint is
what keeps it cheap.

---

## Recommended Hermes architecture (component map)

| Need | Hermes mechanism |
|---|---|
| Mode entry | Skill slash command `/tutor <topic>` + skills-index NL triggering; SKILL.md injected as a user message (cache-safe) |
| Pedagogy prose | **Adapt the Apache-2.0 Anthropic `learn` skill** (attribution required) + graft the verified additions: practice guardrails w/ earned escape hatch, retrieval-practice rules, grading rubric + uncertainty honesty, FSRS loop, grounding pattern |
| Elicitation & MC checks | `clarify` tool (≤4 choices + auto-"Other", free-text mode; native pickers on CLI/TUI/desktop) |
| Free-text checks & grading | In-conversation, rubric in skill prose (memento's grade→show-answer→feedback flow, upgraded) |
| Scheduler & learner state | `scripts/tutor_state.py`: **vendored py-fsrs** + topic/concept CRUD + `due` / `rate` / `stats` / `summary` / `concepts` verbs; fixed rubric→FSRS-rating table; append-only review log; JSON under `$HERMES_HOME/state/skills/tutor/`, atomic incremental writes |
| KB note bridge (v1) | Closure ritual: prompt human's own note first, then agent companion note into `knowledge_base.path` (one-way; capture-ADR conventions; wikilink flavor knob) |
| Content grounding | For problem practice, the agent derives the solution privately first (`execute_code` / scratch) — the Harvard/PNAS grounding pattern, adapted |
| Static visuals | Delegate to `concept-diagrams` (SVG/HTML) |
| Interactive explainers | `tui-widgets` in TUI/dashboard sessions; `p5js` for a browser artifact |
| Deep-dive animation | `manim-video`, on request only |
| Qualitative learner profile | Honcho tools (optional) |
| Review reminders | Session-open due-check (pull). Future: macOS notification plan, or cron once a desk delivery path exists |

### Skill authoring specifics

- **Name:** `tutor`, exposed as `/tutor` (renamed from `learn` — see decision
  log #10; upstream's core `/learn` would shadow a skill of that name).
- **Placement:** develop fork-first — `optional-skills/research/tutor/` (use the
  existing `research` category; do not add a new `education/` taxonomy — upstream
  is unlikely to accept a one-skill category, and `extract-skills.py` already maps
  `education` tags → `research`). Symlink to `~/.hermes/skills/` for fast iteration.
  Upstream via `hermes skills install official/research/tutor` once proven.
- **v1 scope:** concept tutoring only (decision log #9). Source-grounded
  teaching and scenario/roleplay practice arrive as additive prose sections
  once the core loop is proven.
- **Frontmatter** (skill-authoring standards apply — **description ≤ 60 chars**,
  one sentence, ends with a period):

```yaml
---
name: tutor
description: Teach concepts via diagnosis, practice, and spaced review.
version: 1.0.0
author: <human contributor first>, adapted from Anthropic's learn skill (Apache-2.0)
license: Apache-2.0
metadata:
  hermes:
    tags: [learning, tutoring, education, socratic, study, visualization, fsrs]
    related_skills: [concept-diagrams, tui-widgets, p5js, manim-video]
    config:
      - key: knowledge_base.path
        description: Markdown directory for closure notes (e.g. an Obsidian vault)
        prompt: Knowledge base directory (optional)
      - key: knowledge_base.wikilinks
        description: Use [[wikilinks]] in agent companion notes
        default: false
---
```

- **Section order** (modern standard): title → 2–3 sentence intro → When to Use →
  Prerequisites → How to Run → Quick Reference → Procedure (the microcycle +
  tension resolutions) → Pitfalls (the anti-pattern catalog) → Verification.
- **Scripts in `scripts/`** (the FSRS state manager — keep it pure-Python so the
  platform audit passes cross-platform). Tests at
  `tests/skills/test_tutor_skill.py` (stdlib + pytest + mock; no live network),
  run via `scripts/run_tests.sh tests/skills/test_tutor_skill.py -q`.
- **Core rules for the skill body:** clarify goal + friction unless obvious; one
  diagnostic question at a time; learner-generated answers before explanation when
  the topic warrants; explanations short enough that the next learner action is
  nearby; free-text over MC unless diagnosing with specific distractors; grade
  explicitly (correct/partial/misconception/unclear); on wrong, name the
  misconception and ask a near-transfer follow-up; visuals only when interaction
  or spatial structure helps; list existing concepts before creating new ones
  (ID reuse, decision log #8); end with retrieval checks + spaced-review
  write-back + the KB bridge (human note first, companion note second).

### Why build this instead of just using Claude/ChatGPT/Gemini learning modes

Durable cross-session learner model + **FSRS reviews** (the >8-week effect); real
tool access during tutoring (run actual code as the worked example); local
interactive widgets and artifacts; and an optional memory layer (Honcho) none
of them have.

---

## Anti-pattern catalog

Merged and deduplicated across both passes; markers indicate evidence strength.

1. **Answer-on-demand during practice** — crutch effect; learners can't self-detect
   the harm. Fix: hints-first, withhold final answers for exercises. ✅
2. **Unyielding Socratic persistence / no escape hatch** — the schoolmarm trap;
   drives abandonment (44.3% loosening; See-Solution). Fix: friction dial + earned
   reveal. ✅
3. **Question spam / multi-question walls / empty turns** (no scaffold). Fix: hint
   ladder, one question per turn, explicit reveal path. ✅/◐
4. **Front-loaded heavy elicitation questionnaires.** Fix: one calibrating question
   with a default fallback. ▣/◐
5. **Lecture-dump then quiz; not pausing for responses.** Fix: event-triggered
   checks woven into short turns. ▣
6. **Recognition-style (binary/MC) checks as primary assessment.** Fix: free-recall
   default. ◐
7. **Verbatim-repeated quiz questions** (trains pattern-matching). Fix: paraphrase
   on re-check. ◐
8. **Illusion of understanding** — learner says "makes sense," tutor moves on. Fix:
   require retrieval or self-explanation. ◐
9. **Unguided sandbox interactives / whole-mechanism animations as "explanations" /
   visual decoration every turn.** Fix: one relationship + prediction; sandbox only
   at session end. ◐
10. **Praise inflation / sycophantic affirmation.** Fix: specific earned verdicts;
    elicit reasoning instead. ▣/◐
11. **LLM-in-context as the mastery tracker** (temporal incoherence). Fix: explicit
    structured store; LLM is the interface, not the tracker. ✅
12. **Fixed intervals or naive SM-2** ("low interval hell") when FSRS exists. Fix:
    FSRS. ✅
13. **Trusting all self-grades equally; bluffing grading confidence.** Fix: rubric +
    say-so when uncertain, then probe. ✅
14. **Probing past demonstrated understanding** (no session end). Fix: stop on
    demonstrated transfer. ◐
15. **Misplaced persistence** — dumping every learning detail into global `memory`.
    Fix: the profile-scoped tutor store + (optional) Honcho, not global memory,
    unless it's a stable user preference. ◐
16. **Overfitting to classroom ethics** — treating all help as cheating. Fix:
    distinguish homework/exam integrity from adult self-directed mastery; keep an
    integrity carve-out, not a blanket refusal. ◐
17. **No grounding when sources matter** — teaching from priors despite
    user-provided docs/code. Fix: source-grounded branch with citations. ◐
18. **No verification of artifacts** — claiming an explainer works without opening
    it. Fix: browser + console check. ◐

---

## Concrete next steps

1. **Scaffold the skill** at `optional-skills/research/tutor/` (or
   `~/.hermes/skills/learn/` symlinked for iteration). Copy the Apache-2.0
   Anthropic `learn` skill body as the base; credit the human contributor first,
   Anthropic second.
2. **Graft the verified additions** into the prose: practice guardrails + earned
   escape hatch, the friction dial, free-text grading rubric + uncertainty honesty,
   the visual-explainer policy, and the FSRS review loop.
3. **Write `scripts/tutor_state.py`** — **vendored py-fsrs** (with attribution) +
   topic/concept CRUD with `due` / `rate` / `stats` / `summary` / `concepts`
   verbs, the fixed rubric→FSRS-rating table, fuzzy concept-ID matching, an
   append-only per-concept review log, and atomic JSON writes under the
   profile-scoped tutor state directory on every `rate` (incremental, not
   closure-only). This is the load-bearing new code; the rest is prose.
4. **Tests** at `tests/skills/test_tutor_skill.py` — exercise scheduling
   *invariants* (due dates monotone in rating; stability grows on success; log
   append-only) and the CRUD verbs against a temp dir; stdlib + pytest + mock
   only. No re-derivation of FSRS math, no change-detector snapshots.
5. **Wire the session-continuity ritual + KB bridge** — open with a due-review
   report (interleave when several topics are due); close with a summary pass
   and the bridge: prompt the human's own note, then write the agent companion
   note under `knowledge_base.path` following capture-ADR conventions.
   Pull-based; no hooks, no cache invalidation.
6. **Validate end-to-end** in a fresh session on a real topic before deciding
   whether to upstream. Defer any plugin (CLI stats) until real use demands it.

---

## Source index

**Product & implementation**
- ChatGPT Study Mode — <https://openai.com/index/chatgpt-study-mode/>
- Anthropic Claude for Education — <https://www.anthropic.com/news/introducing-claude-for-education>
- Google Guided Learning — <https://blog.google/products-and-platforms/products/education/guided-learning/>
- LearnLM product page — <https://cloud.google.com/solutions/learnlm>
- LearnLM technical report — <https://arxiv.org/html/2412.16429v3>
- Khanmigo — <https://www.khanmigo.ai/>
- Khan Academy design post — <https://blog.khanacademy.org/how-we-built-ai-tutoring-tools/>
- NotebookLM students — <https://notebooklm.google/students> · features — <https://blog.google/innovation-and-ai/models-and-research/google-labs/notebooklm-student-features/>
- Eedi Learn — <https://www.eedi.com/learn> · second RCT — <https://www.eedi.com/news/just-launched---our-second-ai-tutor-rct>
- Quizlet Q-Chat (discontinued Jun 2025) — <https://quizlet.com/blog/meet-q-chat>
- Duolingo Max — <https://blog.duolingo.com/duolingo-max/>
- DeepTutor — <https://github.com/HKUDS/DeepTutor>
- OpenTutor — github.com/zijinz456/OpenTutor ✅
- agent-tutor-skill — github.com/Bhala-Srinivash/agent-tutor-skill ✅
- Anthropic `learn` skill (Apache-2.0) — local at `~/.claude/skills/learn/SKILL.md`

**AI-tutor RCTs & evaluations**
- Harvard physics RCT — nature.com s41598-025-97652-6 ◐
- Wharton/Turkey — PNAS 10.1073/pnas.2422633122 ◐
- UK 5-school RCT — arXiv 2512.23633 ✅
- Meta-analysis (133 studies) — arXiv 2509.22725 ✅
- Nigeria World Bank — WPS-11125 ◐
- Tutor CoPilot (Stanford) — NSSA ◐
- See Solution — arXiv 2504.11146 ✅
- Grading calibration — arXiv 2603.29559 ✅
- LLM-as-knowledge-tracer critique — arXiv 2512.23036 ✅
- LearnLM report — arXiv 2412.16429 ◐
- IntelliCode — arXiv 2512.18669 ✅ (architecture)
- Pedagogical steering / StratL — arXiv 2410.03781 ◐
- TutorGym — arXiv 2505.01563 ◐
- LPITutor (RAG/prompt-engineered tutor) — PMC12453719 ◐

**Foundational learning science**
- Dunlosky, "Strengthening the Student Toolbox" — <https://www.aft.org/ae/fall2013/dunlosky> ◐
- Cepeda et al. distributed practice — PubMed 16719566 ◐
- Bisra et al. self-explanation (g = 0.55) — eric.ed.gov/?id=EJ1186664 ◐
- Chi & Wylie ICAP — eric.ed.gov/?id=EJ1044018 ◐
- Kulik & Fletcher ITS meta-analysis (0.66 SD) — journals.sagepub.com/doi/10.3102/0034654315581420 ◐
- Sinha & Kapur productive failure (g = 0.36) — eric.ed.gov/?id=EJ1308129 ◐
- Bastani et al. guardrails / cognitive offloading — PMC12232635 ◐

**Explorables tradition (visual policy)**
- Victor, Explorable Explanations — worrydream.com ◐
- Case, explorables methodology — blog.ncase.me ◐
- Matuschak, Narrated Explorables (Khan Academy) ◐ · andymatuschak.org/prompts ◐
- Anki FSRS FAQ ✅
- Obsidian spaced-repetition plugin algorithm docs (SM-2-OSR only; FSRS "planned", issue #748) — <https://www.stephenmwangi.com/obsidian-spaced-repetition/algorithms/> ✅ (fetched 2026-07-04)

**Hermes fork artifacts (2026-07-04 decisions)**
- Obsidian-capture design exploration — companion-note conventions mined; no runtime dependency
- llm-wiki skill — `skills/research/llm-wiki/SKILL.md` (agent-owned wiki; does NOT cover companion-notes capture)

**Reception anecdata only (▣)**
- leonfurze.com Study Mode first impressions · whytryai.com three-mode comparison · kidsaitools.com Khanmigo review · simonwillison.net Study-Mode prompt analysis

**Hermes docs / local architecture**
- Skills — `website/docs/user-guide/features/skills.md` · Creating skills — `website/docs/developer-guide/creating-skills.md`
- Plugins — `website/docs/user-guide/features/plugins.md` · Build a plugin — `website/docs/guides/build-a-hermes-plugin.md`
- TUI widgets — `skills/productivity/tui-widgets/SKILL.md`
