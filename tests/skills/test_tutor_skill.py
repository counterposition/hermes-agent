"""Tests for optional-skills/research/tutor/scripts/tutor_state.py.

Tests cover the official-skill packaging seam plus state-script behavior and
scheduling invariants — no FSRS math re-derivation or snapshots of intervals
or weights. The script is driven via subprocess against tmp_path data dirs with
--now pinned for determinism.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    REPO_ROOT / "optional-skills" / "research" / "tutor" / "scripts" / "tutor_state.py"
)

T0 = "2026-07-04T12:00:00+00:00"

FSRS_CARD_KEYS = {
    "card_id",
    "state",
    "step",
    "stability",
    "difficulty",
    "due",
    "last_review",
}


def run(args, data_dir, now=T0, expect=0):
    """Run the script; assert exit code; return CompletedProcess."""
    cmd = [sys.executable, str(SCRIPT), *args, "--data-dir", str(data_dir)]
    if now is not None:
        cmd += ["--now", now]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if expect == "nonzero":
        assert proc.returncode != 0, (
            f"expected failure, got rc=0\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    else:
        assert proc.returncode == expect, (
            f"rc={proc.returncode} (wanted {expect}) for {cmd}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc


def run_json(args, data_dir, now=T0, expect=0):
    return json.loads(run(args, data_dir, now=now, expect=expect).stdout)


def topic_file(data_dir, slug):
    return Path(data_dir) / f"{slug}.json"


def read_topic(data_dir, slug):
    return json.loads(topic_file(data_dir, slug).read_text(encoding="utf-8"))


def start_topic(data_dir, topic="trig", title="Trigonometry"):
    return run_json(["start", topic, "--title", title], data_dir)


def add_concept(data_dir, topic, cid, pointer="pointer text", extra=()):
    return run_json(["add-concept", topic, cid, "--pointer", pointer, *extra], data_dir)


def iso_plus(iso, **delta):
    return (datetime.fromisoformat(iso) + timedelta(**delta)).isoformat()


# ── skill integration ───────────────────────────────────────────────────────


class TestSkillIntegration:
    def test_official_source_packages_the_complete_tutor_skill(self):
        from tools.skills_hub import OptionalSkillSource

        source = OptionalSkillSource()
        metadata = source.inspect("official/research/tutor")
        bundle = source.fetch("official/research/tutor")

        assert metadata is not None
        assert metadata.name == "tutor"
        assert metadata.path == "optional-skills/research/tutor"
        assert bundle is not None
        assert {
            "SKILL.md",
            "scripts/tutor_state.py",
            "scripts/_vendor/FSRS_LICENSE",
            "references/anti-patterns.md",
            "references/visual-explainer-policy.md",
        } <= bundle.files.keys()

    def test_tutor_slug_does_not_collide_with_a_core_command(self):
        from hermes_cli.commands import resolve_command

        assert resolve_command("tutor") is None


# ── start ────────────────────────────────────────────────────────────────────


class TestStart:
    def test_start_creates_topic_file(self, tmp_path):
        out = start_topic(tmp_path)
        assert out["created"] is True
        assert out["topic"] == "trig"
        data = read_topic(tmp_path, "trig")
        assert data["schema_version"] == 1
        assert data["title"] == "Trigonometry"
        assert data["concepts"] == []
        assert data["misconceptions"] == []
        assert "learner_model" not in data
        assert "artifacts" not in data

    def test_default_store_is_profile_scoped_and_outside_skill_package(self, tmp_path):
        hermes_home = tmp_path / "profile"
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "start",
                "trig",
                "--title",
                "Trigonometry",
                "--now",
                T0,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "HERMES_HOME": str(hermes_home)},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (hermes_home / "state" / "skills" / "tutor" / "trig.json").is_file()
        assert not (SCRIPT.parents[1] / "data").exists()

    def test_start_is_idempotent(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        before = topic_file(tmp_path, "trig").read_bytes()
        out = run_json(["start", "trig", "--title", "Different Title"], tmp_path)
        assert out["created"] is False
        assert out["topic"] == "trig"
        assert topic_file(tmp_path, "trig").read_bytes() == before

    def test_topic_name_is_normalized(self, tmp_path):
        out = run_json(["start", "Fourier Analysis!", "--title", "Fourier"], tmp_path)
        assert out["topic"] == "fourier-analysis"
        assert topic_file(tmp_path, "fourier-analysis").is_file()


# ── verdict -> rating mapping ────────────────────────────────────────────────


class TestVerdictRatingMapping:
    @pytest.mark.parametrize(
        "verdict,extra,expected_rating",
        [
            ("correct", (), "good"),
            ("correct", ("--transfer",), "easy"),
            ("partial", (), "hard"),
            ("misconception", (), "again"),
            ("unclear", (), "again"),
        ],
    )
    def test_verdict_maps_to_documented_rating(
        self, tmp_path, verdict, extra, expected_rating
    ):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        out = run_json(
            ["rate", "trig", "alpha-one", "--verdict", verdict, *extra], tmp_path
        )
        assert out["rating"] == expected_rating
        log = read_topic(tmp_path, "trig")["concepts"][0]["review_log"]
        assert log[-1]["verdict"] == verdict
        assert log[-1]["rating"] == expected_rating
        assert log[-1]["ts"] == T0

    def test_rating_monotonicity_from_same_state(self, tmp_path):
        """From the SAME pre-review state at the same --now:
        due(Again) <= due(Hard) <= due(Good) <= due(Easy)."""
        base = tmp_path / "base"
        base.mkdir()
        start_topic(base)
        add_concept(base, "trig", "alpha-one")

        cases = [
            ("misconception", ()),  # Again
            ("partial", ()),  # Hard
            ("correct", ()),  # Good
            ("correct", ("--transfer",)),  # Easy
        ]
        due_ats = []
        for i, (verdict, extra) in enumerate(cases):
            clone = tmp_path / f"clone{i}"
            shutil.copytree(base, clone)
            out = run_json(
                ["rate", "trig", "alpha-one", "--verdict", verdict, *extra], clone
            )
            due_ats.append(datetime.fromisoformat(out["due_at"]))
        assert due_ats == sorted(due_ats), f"due dates not monotone: {due_ats}"

    def test_stability_grows_across_successful_reviews(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        now = T0
        stabilities = []
        for _ in range(3):
            out = run_json(
                ["rate", "trig", "alpha-one", "--verdict", "correct"],
                tmp_path,
                now=now,
            )
            stabilities.append(out["stability"])
            # next review: past the new due date
            now = iso_plus(out["due_at"], days=1)
        assert stabilities[0] < stabilities[1] < stabilities[2], stabilities


# ── misconception lifecycle ─────────────────────────────────────────────────


class TestMisconceptions:
    def test_correct_review_resolves_only_that_concepts_active_notes(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        add_concept(tmp_path, "trig", "bravo-two")
        for concept_id in ("alpha-one", "bravo-two"):
            run_json(
                [
                    "rate",
                    "trig",
                    concept_id,
                    "--verdict",
                    "misconception",
                    "--misconception-note",
                    f"wrong model for {concept_id}",
                ],
                tmp_path,
            )

        before = run(["summary", "trig"], tmp_path).stdout
        assert "wrong model for alpha-one" in before
        assert "wrong model for bravo-two" in before

        out = run_json(
            ["rate", "trig", "alpha-one", "--verdict", "correct"],
            tmp_path,
            now=iso_plus(T0, days=1),
        )
        assert out["resolved_misconceptions"] == 1

        misconceptions = read_topic(tmp_path, "trig")["misconceptions"]
        alpha, bravo = misconceptions
        assert alpha["concept_id"] == "alpha-one"
        assert alpha["status"] == "resolved"
        assert alpha["resolved_at"] == iso_plus(T0, days=1)
        assert bravo["concept_id"] == "bravo-two"
        assert bravo["status"] == "active"

        after = run(["summary", "trig"], tmp_path, now=iso_plus(T0, days=1)).stdout
        assert "wrong model for alpha-one" not in after
        assert "wrong model for bravo-two" in after

    @pytest.mark.parametrize("verdict", ["correct", "unclear"])
    def test_misconception_note_rejects_incompatible_verdicts(self, tmp_path, verdict):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        proc = run(
            [
                "rate",
                "trig",
                "alpha-one",
                "--verdict",
                verdict,
                "--misconception-note",
                "wrong model",
            ],
            tmp_path,
            expect="nonzero",
        )
        assert "--misconception-note" in json.loads(proc.stdout)["error"]
        data = read_topic(tmp_path, "trig")
        assert data["concepts"][0]["review_log"] == []
        assert data["misconceptions"] == []

    def test_blank_misconception_note_is_rejected(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        proc = run(
            [
                "rate",
                "trig",
                "alpha-one",
                "--verdict",
                "partial",
                "--misconception-note",
                "   ",
            ],
            tmp_path,
            expect="nonzero",
        )
        assert "non-empty" in json.loads(proc.stdout)["error"]
        assert read_topic(tmp_path, "trig")["concepts"][0]["review_log"] == []


# ── review_log append-only ───────────────────────────────────────────────────


class TestReviewLogAppendOnly:
    def test_log_grows_by_one_and_prior_entries_unchanged(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")

        run_json(["rate", "trig", "alpha-one", "--verdict", "partial"], tmp_path)
        log_after_first = read_topic(tmp_path, "trig")["concepts"][0]["review_log"]
        assert len(log_after_first) == 1
        first_entry_bytes = json.dumps(log_after_first[0], sort_keys=True)

        run_json(
            ["rate", "trig", "alpha-one", "--verdict", "correct"],
            tmp_path,
            now=iso_plus(T0, days=2),
        )
        log_after_second = read_topic(tmp_path, "trig")["concepts"][0]["review_log"]
        assert len(log_after_second) == 2
        assert json.dumps(log_after_second[0], sort_keys=True) == first_entry_bytes


# ── fuzzy concept-id dedup ───────────────────────────────────────────────────


class TestNearDuplicateConceptIds:
    def test_near_duplicate_refused_with_exit_2(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "sin-cos-pairing")
        proc = run(
            ["add-concept", "trig", "sine-cos-pairing", "--pointer", "p"],
            tmp_path,
            expect=2,
        )
        out = json.loads(proc.stdout)
        assert out["near_duplicate"] == "sin-cos-pairing"
        assert "error" in out
        # store unchanged
        ids = [c["id"] for c in read_topic(tmp_path, "trig")["concepts"]]
        assert ids == ["sin-cos-pairing"]

    def test_force_overrides_near_duplicate_check(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "sin-cos-pairing")
        out = add_concept(tmp_path, "trig", "sine-cos-pairing", extra=("--force",))
        assert out["created"] is True
        ids = [c["id"] for c in read_topic(tmp_path, "trig")["concepts"]]
        assert ids == ["sin-cos-pairing", "sine-cos-pairing"]

    def test_distinct_id_unaffected(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "sin-cos-pairing")
        out = add_concept(tmp_path, "trig", "chain-rule")
        assert out["created"] is True

    def test_id_is_normalized_before_matching(self, tmp_path):
        start_topic(tmp_path)
        out = add_concept(tmp_path, "trig", "Sin/Cos  Pairing")
        assert out["id"] == "sin-cos-pairing"

    def test_single_token_refinement_is_not_flagged(self, tmp_path):
        """'python' vs 'python-packaging' are distinct concepts, not dupes."""
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "python")
        out = add_concept(tmp_path, "trig", "python-packaging")
        assert out["created"] is True

    def test_multi_token_subset_is_flagged(self, tmp_path):
        """'gradient-descent-algorithm' decorates 'gradient-descent'."""
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "gradient-descent")
        proc = run(
            ["add-concept", "trig", "gradient-descent-algorithm", "--pointer", "p"],
            tmp_path,
            expect=2,
        )
        assert json.loads(proc.stdout)["near_duplicate"] == "gradient-descent"


# ── due ordering ─────────────────────────────────────────────────────────────


class TestDueInterleaving:
    def test_due_round_robins_across_topics(self, tmp_path):
        run_json(["start", "alpha-topic", "--title", "A"], tmp_path)
        run_json(["start", "beta-topic", "--title", "B"], tmp_path)
        # concepts are created due immediately at --now
        add_concept(tmp_path, "alpha-topic", "alpha-one")
        add_concept(tmp_path, "alpha-topic", "bravo-two")
        add_concept(tmp_path, "beta-topic", "charlie-three")
        add_concept(tmp_path, "beta-topic", "delta-four")

        out = run_json(["due"], tmp_path, now=iso_plus(T0, minutes=1))
        assert len(out) == 4
        assert [e["topic"] for e in out] == [
            "alpha-topic",
            "beta-topic",
            "alpha-topic",
            "beta-topic",
        ]
        assert all(e["review_pointer"] for e in out)

    def test_due_excludes_not_yet_due_and_scopes_to_topic(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        # push it into the future
        out = run_json(
            ["rate", "trig", "alpha-one", "--verdict", "correct", "--transfer"],
            tmp_path,
        )
        future_due = out["due_at"]
        assert run_json(["due", "trig"], tmp_path) == []
        after = iso_plus(future_due, minutes=1)
        assert [e["id"] for e in run_json(["due", "trig"], tmp_path, now=after)] == [
            "alpha-one"
        ]


# ── persistence invariants ───────────────────────────────────────────────────


class TestPersistence:
    def test_file_valid_after_rate_and_fsrs_round_trips(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        run_json(["rate", "trig", "alpha-one", "--verdict", "correct"], tmp_path)

        data = read_topic(tmp_path, "trig")  # parses as valid JSON
        assert data["schema_version"] == 1
        concept = data["concepts"][0]
        fsrs = concept["fsrs"]
        # full py-fsrs Card dict is stored losslessly
        assert FSRS_CARD_KEYS <= set(fsrs)
        assert isinstance(fsrs["stability"], float)
        assert isinstance(fsrs["difficulty"], float)
        assert fsrs["last_review"] == T0
        # concepts verb agrees with the stored card
        views = run_json(["concepts", "trig"], tmp_path)
        assert views[0]["id"] == "alpha-one"
        assert views[0]["due_at"] == fsrs["due"]
        assert views[0]["last_verdict"] == "correct"

    def test_no_leftover_tmp_files(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        run_json(["rate", "trig", "alpha-one", "--verdict", "correct"], tmp_path)
        assert list(Path(tmp_path).glob("*.tmp")) == []


# ── summary + log-session ────────────────────────────────────────────────────


class TestSummaryAndSessionLog:
    def test_summary_mentions_due_concepts_and_last_session(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one", pointer="why sin AND cos")
        run_json(
            [
                "log-session",
                "trig",
                "--covered",
                "alpha-one",
                "--struggled",
                "alpha-one",
            ],
            tmp_path,
        )
        text = run(["summary"], tmp_path, now=iso_plus(T0, minutes=1)).stdout
        assert "alpha-one" in text
        assert "why sin AND cos" in text
        assert "Last session" in text
        assert "trig" in text
        # plain text designed for pasting at session open, not a JSON blob
        with pytest.raises(json.JSONDecodeError):
            json.loads(text)
        assert len(text.strip().splitlines()) <= 20

    def test_log_session_appends_entries(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        add_concept(tmp_path, "trig", "bravo-two")
        run_json(["log-session", "trig", "--covered", "alpha-one"], tmp_path)
        run_json(
            ["log-session", "trig", "--struggled", "bravo-two"],
            tmp_path,
            now=iso_plus(T0, days=1),
        )
        log = read_topic(tmp_path, "trig")["session_log"]
        assert len(log) == 2
        assert log[0]["covered"] == ["alpha-one"]
        assert log[1]["struggled"] == ["bravo-two"]


# ── stats ────────────────────────────────────────────────────────────────────


class TestStats:
    def test_stats_reflect_store_contents(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        add_concept(tmp_path, "trig", "charlie-three")
        run_json(["rate", "trig", "alpha-one", "--verdict", "correct"], tmp_path)
        out = run_json(["stats"], tmp_path)
        assert out["topics"] == 1
        assert sum(out["concepts_by_status"].values()) == 2
        assert out["total_reviews"] == 1
        assert out["next_due"] is not None


# ── errors ───────────────────────────────────────────────────────────────────


class TestErrors:
    def test_unknown_topic_is_json_error(self, tmp_path):
        proc = run(["concepts", "no-such-topic"], tmp_path, expect="nonzero")
        out = json.loads(proc.stdout)
        assert "error" in out
        assert "no-such-topic" in out["error"]

    def test_unknown_concept_is_json_error(self, tmp_path):
        start_topic(tmp_path)
        proc = run(
            ["rate", "trig", "ghost-concept", "--verdict", "correct"],
            tmp_path,
            expect="nonzero",
        )
        assert "error" in json.loads(proc.stdout)

    def test_corrupt_topic_file_is_json_error_not_traceback(self, tmp_path):
        start_topic(tmp_path)
        topic_file(tmp_path, "trig").write_text("{{{not json", encoding="utf-8")
        proc = run(["concepts", "trig"], tmp_path, expect="nonzero")
        out = json.loads(proc.stdout)
        assert "error" in out
        assert "Traceback" not in proc.stderr

    def test_invalid_now_is_json_error(self, tmp_path):
        proc = run(["topics"], tmp_path, now="not-a-date", expect="nonzero")
        assert "error" in json.loads(proc.stdout)

    def test_empty_store_reads_are_safe_and_create_nothing(self, tmp_path):
        data_dir = tmp_path / "never-created"
        assert run_json(["topics"], data_dir) == []
        assert run_json(["due"], data_dir) == []
        assert not data_dir.exists()


# ── usage errors honor the JSON contract ─────────────────────────────────────


class TestUsageErrorsAreJson:
    """argparse failures must produce one-line JSON on stdout, exit 2."""

    def assert_usage_json(self, proc):
        assert proc.returncode == 2
        out = json.loads(proc.stdout)
        assert "usage error" in out["error"]
        assert "usage" in out

    def test_invalid_verb(self, tmp_path):
        self.assert_usage_json(run(["no-such-verb"], tmp_path, expect=2))

    def test_invalid_friction_choice(self, tmp_path):
        self.assert_usage_json(
            run(
                ["start", "x", "--title", "T", "--friction", "extreme"],
                tmp_path,
                expect=2,
            )
        )

    def test_invalid_verdict_choice(self, tmp_path):
        self.assert_usage_json(
            run(["rate", "trig", "c", "--verdict", "wrongish"], tmp_path, expect=2)
        )

    def test_unknown_flag(self, tmp_path):
        self.assert_usage_json(run(["topics", "--bogus"], tmp_path, expect=2))

    def test_missing_required_title(self, tmp_path):
        self.assert_usage_json(run(["start", "x"], tmp_path, expect=2))


# ── input validation ─────────────────────────────────────────────────────────


class TestInputValidation:
    def test_transfer_rejected_for_noncorrect_verdicts(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        proc = run(
            ["rate", "trig", "alpha-one", "--verdict", "partial", "--transfer"],
            tmp_path,
            expect="nonzero",
        )
        out = json.loads(proc.stdout)
        assert "--transfer" in out["error"]
        # nothing was recorded
        assert read_topic(tmp_path, "trig")["concepts"][0]["review_log"] == []

    def test_blank_pointer_rejected(self, tmp_path):
        start_topic(tmp_path)
        proc = run(
            ["add-concept", "trig", "alpha-one", "--pointer", "   "],
            tmp_path,
            expect="nonzero",
        )
        assert "pointer" in json.loads(proc.stdout)["error"]
        assert read_topic(tmp_path, "trig")["concepts"] == []

    def test_log_session_rejects_unknown_concept_ids(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        proc = run(
            ["log-session", "trig", "--covered", "alpha-one,ghost-idea"],
            tmp_path,
            expect="nonzero",
        )
        assert "ghost-idea" in json.loads(proc.stdout)["error"]
        assert read_topic(tmp_path, "trig")["session_log"] == []


# ── path safety ──────────────────────────────────────────────────────────────


class TestPathSafety:
    def test_doctored_topic_field_cannot_redirect_writes(self, tmp_path):
        """A valid-JSON state file whose "topic" field says "../evil" must be
        refused, not used as the next write path."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        outside = tmp_path / "evil.json"
        malicious = {
            "schema_version": 1,
            "topic": "../evil",
            "title": "x",
            "goal": "",
            "friction": "medium",
            "concepts": [],
            "misconceptions": [],
            "session_log": [],
        }
        (data_dir / "evil.json").write_text(json.dumps(malicious), encoding="utf-8")
        proc = run(
            ["log-session", "evil", "--covered", ""],
            data_dir,
            expect="nonzero",
        )
        assert "error" in json.loads(proc.stdout)
        assert not outside.exists()

    def test_token_reorder_and_subset_ids_are_flagged(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "sin-cos-pairing")
        # reordering: char ratio 0.73 (below threshold) but same token set
        proc = run(
            ["add-concept", "trig", "cos-sin-pairing", "--pointer", "p"],
            tmp_path,
            expect=2,
        )
        assert json.loads(proc.stdout)["near_duplicate"] == "sin-cos-pairing"
        # subset: "pairing" alone refines an existing concept
        proc = run(
            ["add-concept", "trig", "pairing", "--pointer", "p"],
            tmp_path,
            expect=2,
        )
        assert json.loads(proc.stdout)["near_duplicate"] == "sin-cos-pairing"


# ── write locking ────────────────────────────────────────────────────────────


class TestTopicLock:
    def test_held_lock_blocks_with_json_error(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        lock = Path(tmp_path) / "trig.json.lock"
        lock.write_text("12345", encoding="utf-8")  # fresh lock: held
        proc = run(
            [
                "rate",
                "trig",
                "alpha-one",
                "--verdict",
                "correct",
                "--lock-timeout",
                "0.2",
            ],
            tmp_path,
            expect="nonzero",
        )
        assert "locked" in json.loads(proc.stdout)["error"]
        assert read_topic(tmp_path, "trig")["concepts"][0]["review_log"] == []

    def test_stale_lock_is_taken_over(self, tmp_path):
        import os as _os
        import time as _time

        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        lock = Path(tmp_path) / "trig.json.lock"
        lock.write_text("12345", encoding="utf-8")
        stale = _time.time() - 120  # well past the 30s stale threshold
        _os.utime(lock, (stale, stale))
        out = run_json(["rate", "trig", "alpha-one", "--verdict", "correct"], tmp_path)
        assert out["rating"] == "good"
        assert not lock.exists()  # released after the write


# ── FSRS card fidelity ───────────────────────────────────────────────────────


class TestCardFidelity:
    def test_stored_card_round_trips_exactly_through_vendored_fsrs(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        run_json(["rate", "trig", "alpha-one", "--verdict", "correct"], tmp_path)
        fsrs_dict = read_topic(tmp_path, "trig")["concepts"][0]["fsrs"]
        snippet = (
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from fsrs import Card\n"
            "d = json.loads(sys.argv[2])\n"
            "print(json.dumps(Card.from_dict(d).to_dict() == d))\n"
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                snippet,
                str(SCRIPT.parent / "_vendor"),
                json.dumps(fsrs_dict),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "true"

    def test_card_id_stable_across_reviews(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        card_id_before = read_topic(tmp_path, "trig")["concepts"][0]["fsrs"]["card_id"]
        out = run_json(["rate", "trig", "alpha-one", "--verdict", "correct"], tmp_path)
        run_json(
            ["rate", "trig", "alpha-one", "--verdict", "correct"],
            tmp_path,
            now=iso_plus(out["due_at"], days=1),
        )
        assert (
            read_topic(tmp_path, "trig")["concepts"][0]["fsrs"]["card_id"]
            == card_id_before
        )

    def test_all_prior_log_entries_byte_identical_after_many_reviews(self, tmp_path):
        start_topic(tmp_path)
        add_concept(tmp_path, "trig", "alpha-one")
        now = T0
        prev_serialized: list = []
        for _ in range(4):
            out = run_json(
                ["rate", "trig", "alpha-one", "--verdict", "correct"],
                tmp_path,
                now=now,
            )
            log = read_topic(tmp_path, "trig")["concepts"][0]["review_log"]
            serialized = [json.dumps(e, sort_keys=True) for e in log]
            assert serialized[: len(prev_serialized)] == prev_serialized
            assert len(serialized) == len(prev_serialized) + 1
            prev_serialized = serialized
            now = iso_plus(out["due_at"], days=1)
