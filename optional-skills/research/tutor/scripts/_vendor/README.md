# Vendored: py-fsrs v6.3.1

Source: https://github.com/open-spaced-repetition/py-fsrs (tag v6.3.1, MIT — see FSRS_LICENSE).
Files are unmodified upstream copies, except that `optimizer.py` is intentionally
omitted (it requires torch/pandas; `from fsrs import Optimizer` will raise —
nothing in this skill uses it). Runtime deps: stdlib + `typing_extensions`
(already present in the Hermes environment).

To update: replace these files from a newer tag, keep this README's version line
current, and re-run `scripts/run_tests.sh tests/skills/test_learn_skill.py`.
