# Visual Explainer Policy

Interactive visual explainers are valuable only when **operating** them changes
understanding. Otherwise they are decorative artifacts that train skimming.
Read this whole file before building any visual for a learning session.

## When visuals help

Use a visual when the concept has shape:

- Geometry, topology, vector spaces, transforms
- Dynamic systems, feedback loops, queues, control
- Algorithms with state transitions (search, sort, gradient descent, backprop, consensus, scheduling)
- Probability and statistics (sampling, Bayes updates, distributions, confidence intervals)
- Tradeoff surfaces (precision/recall, bias/variance, latency/quality/cost)
- Systems-architecture flows and failure modes
- Physics / chemistry / biology mechanisms

## When they usually don't

- Simple definitions
- Verbal distinctions with no spatial or dynamic structure
- Topics where a table, analogy, or worked example already suffices
- Pure procedure, notation, quick confirmations — a sentence does the job

## Core rules

1. **One relationship per visual.** Show one relationship, one step, one
   comparison — not the finished picture — and let the question ask for what's
   missing.
2. **Always pair with a prediction question.** "Predict what happens as this
   goes to zero, then drag it." The learner's hand is on the parameter, not
   yours — that is why the interactive beats the static version.
3. **Never animate the whole mechanism.** "Here's the entire process,
   animated" is the answer in prettier clothes; it bypasses thinking exactly
   as thoroughly as typing out the solution.
4. **Never decorate every turn.** A visual that isn't carrying the concept is
   decoration, and decoration teaches the learner to skim.
5. **Never an unguided sandbox** — narrated and prompted, always — EXCEPT as a
   deliberate session-end release. The three-act arc: motivating question →
   guided interaction → sandbox at the end.

## Required structure for any interactive explainer

Every interactive explainer must have all seven:

1. A one-sentence learning goal.
2. Controls that map to *conceptual* variables, not cosmetic sliders.
3. A prediction prompt before interaction.
4. A challenge ("set the parameters so that ...").
5. A reflection question after.
6. Reset and presets.
7. **Verification** — open it in a browser, check the console, and test the
   primary controls before claiming it works. Never ship an unopened artifact.

If the artifact is meant to be consumed → a file. If it is meant to be
operated → an interactive widget or artifact the learner can reopen and
iterate on.

## Explainer tiers → Hermes paths

| Tier | Output | Use when | Hermes path |
|---|---|---|---|
| Static diagram | SVG/HTML | relationships, taxonomy, pipeline, anatomy | delegate to the `concept-diagrams` skill |
| Interactive simulator | TUI widget or HTML/p5.js with controls + tasks | parameters or dynamics matter | `tui-widgets` in TUI/dashboard sessions; `p5js` for a browser artifact |
| Animated explainer | Manim video | a math/algorithm sequence benefits from temporal staging | `manim-video` skill, on request only |

The visual is still a scaffold for the turn it appears in: it pairs with one
focused question — not a caption, a question — and the explanatory prose and
the prompt to think stay in your own words around it.
