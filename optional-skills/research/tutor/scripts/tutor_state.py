#!/usr/bin/env python3
"""FSRS learner-state store for the Hermes `tutor` skill.

Deterministic CLI that owns ALL scheduling arithmetic: the LLM only ever
supplies a rubric verdict (correct / partial / misconception / unclear);
this script maps it to an FSRS rating via a fixed, conservatively-biased
table and advances the per-concept schedule with the vendored py-fsrs
(v6.3.1, MIT, https://github.com/open-spaced-repetition/py-fsrs)
under ``scripts/_vendor/``.

State: one JSON file per topic under ``--data-dir`` (default:
``<HERMES_HOME>/state/skills/tutor``, created on demand — never at import time),
``schema_version`` 1, atomic writes (tmp file in the same dir +
``os.replace``). Each concept's ``review_log`` is append-only: it is the
durable asset from which FSRS parameters can always be re-derived.

Every verb prints JSON to stdout except ``summary`` (human-readable text).
Errors: nonzero exit + one-line JSON ``{"error": "..."}`` on stdout.

Cross-platform: stdlib + vendored fsrs only; no POSIX-only primitives.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_VENDOR_DIR = str(_SCRIPT_DIR / "_vendor")
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

from fsrs import Card, Rating, Scheduler, State  # noqa: E402

SCHEMA_VERSION = 1


def _default_data_dir() -> Path:
    """Return profile-scoped state outside the replaceable skill package."""
    configured_home = os.environ.get("HERMES_HOME", "").strip()
    hermes_home = (
        Path(configured_home).expanduser()
        if configured_home
        else Path.home() / ".hermes"
    )
    return hermes_home / "state" / "skills" / "tutor"


# Fixed verdict -> FSRS rating table. Conservatively biased: the LLM grades,
# the script schedules — grade inflation must not corrupt stability.
# "correct" with --transfer (learner applied the idea in a new context)
# upgrades to Easy; everything uncertain collapses to Again.
VERDICT_RATINGS = {
    "correct": Rating.Good,
    "partial": Rating.Hard,
    "misconception": Rating.Again,
    "unclear": Rating.Again,
}

# Fuzzy-match threshold for near-duplicate concept ids (decision log #8).
# Character-ratio is complemented by token-set rules: equal or subset token
# sets are near-duplicates regardless of ratio ("sin-cos-pairing" vs
# "cos-sin-pairing" scores only 0.73 but is the same concept reordered).
NEAR_DUPLICATE_RATIO = 0.75

# A single-token proper subset ("pairing" vs "sin-cos-pairing") only counts as
# a near-duplicate above this character-ratio floor; below it, the shorter id
# is treated as a legitimate distinct concept ("python" vs "python-packaging").
SUBSET_SINGLE_TOKEN_RATIO_FLOOR = 0.6

# Cross-process write lock: how long to wait for a held lock, and when a
# leftover lock file from a crashed process counts as stale.
DEFAULT_LOCK_TIMEOUT_SECONDS = 3.0
LOCK_STALE_SECONDS = 30.0

# A Review-state card with at least this much stability (days) counts "solid".
SOLID_STABILITY_DAYS = 30.0

_SUMMARY_MAX_DUE = 8
_SUMMARY_MAX_SESSIONS = 4
_SUMMARY_MAX_MISCONCEPTIONS = 5


class TutorError(Exception):
    """Domain error: rendered as one-line JSON on stdout + nonzero exit."""

    def __init__(self, message: str, exit_code: int = 1, **extra: object) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.payload: dict = {"error": message, **extra}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _slug(text: str) -> str:
    """Normalize an id/topic: lowercase, non-alnum runs -> '-', strip '-'."""
    normalized = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not normalized:
        raise TutorError(f"invalid id/topic {text!r}: normalizes to empty string")
    return normalized


def _parse_now(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc)
    candidate = raw.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise TutorError(f"invalid --now value {raw!r}: expected ISO 8601") from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _topic_path(data_dir: Path, topic: str) -> Path:
    return data_dir / f"{_slug(topic)}.json"


def _require_topic_file(data_dir: Path, topic: str) -> Path:
    path = _topic_path(data_dir, topic)
    if not path.is_file():
        raise TutorError(
            f"unknown topic '{_slug(topic)}': no state file at {path}; "
            "run the 'start' verb first (see 'topics' for existing topics)"
        )
    return path


def _load_topic(data_dir: Path, topic: str) -> dict:
    path = _require_topic_file(data_dir, topic)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TutorError(
            f"corrupt topic file {path}: {exc}; restore it from backup or "
            "move it aside and re-run 'start'"
        ) from None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise TutorError(
            f"unsupported topic file {path}: expected schema_version "
            f"{SCHEMA_VERSION}; move it aside and re-run 'start'"
        )
    # Path safety: the file's own "topic" field decides future write paths, so
    # it must match the slug it was loaded as — a doctored value like
    # "../outside" must never become a write target.
    expected = _slug(topic)
    required_lists = ("concepts", "misconceptions", "session_log")
    invalid_lists = [
        key for key in required_lists if not isinstance(data.get(key), list)
    ]
    problems = []
    if data.get("topic") != expected:
        problems.append(
            f'its "topic" field is {data.get("topic")!r} but the file was '
            f"loaded as {expected!r}"
        )
    if invalid_lists:
        problems.append(f"invalid list field(s): {', '.join(invalid_lists)}")
    if problems:
        raise TutorError(
            f"invalid topic file {path}: {'; '.join(problems)}; refusing to use it — "
            "fix or remove the file"
        )
    return data


def _save_topic(data_dir: Path, data: dict) -> None:
    slug = data.get("topic")
    # Containment: only clean slugs may name a state file, and the write must
    # land inside data_dir (belt-and-braces with the _load_topic validation).
    if not isinstance(slug, str) or slug != _slug(slug):
        raise TutorError(f"refusing to save topic with unsafe slug {slug!r}")
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{slug}.json"
    if path.resolve().parent != data_dir.resolve():
        raise TutorError(f"refusing to write outside data dir: {path}")
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


@contextmanager
def _topic_lock(data_dir: Path, slug: str, timeout: float):
    """Cross-process write lock via exclusive lock-file creation.

    Serializes read-modify-write cycles on one topic so concurrent reviews
    cannot drop review_log entries. os.O_EXCL is atomic on POSIX and Windows.
    A lock older than LOCK_STALE_SECONDS is treated as a crash leftover.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = data_dir / f"{slug}.json.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue  # holder just released it; retry immediately
            if age > LOCK_STALE_SECONDS:
                try:
                    lock.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TutorError(
                    f"topic '{slug}' is locked by another process "
                    f"(lock file {lock}); retry shortly, or delete the lock "
                    "file if no other Hermes process is running"
                ) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _lock_timeout(args: argparse.Namespace) -> float:
    return float(getattr(args, "lock_timeout", DEFAULT_LOCK_TIMEOUT_SECONDS))


def _all_topics(data_dir: Path) -> list[dict]:
    """All topic files, sorted by topic slug. Raises on corrupt files."""
    if not data_dir.is_dir():
        return []
    datasets = []
    for path in sorted(data_dir.glob("*.json")):
        datasets.append(_load_topic(data_dir, path.stem))
    return datasets


def _topics_for(data_dir: Path, topic: str | None) -> list[dict]:
    if topic is not None:
        return [_load_topic(data_dir, topic)]
    return _all_topics(data_dir)


def _due_dt(concept: dict) -> datetime:
    return datetime.fromisoformat(concept["fsrs"]["due"]).astimezone(timezone.utc)


def _status_for_card(card: Card) -> str:
    if card.state == State.Review:
        if card.stability is not None and card.stability >= SOLID_STABILITY_DAYS:
            return "solid"
        return "review"
    return "learning"


def _concept_view(concept: dict) -> dict:
    return {
        "id": concept["id"],
        "status": concept["status"],
        "due_at": concept["fsrs"]["due"],
        "last_verdict": concept.get("last_verdict"),
        "review_pointer": concept.get("review_pointer", ""),
    }


def _find_concept(data: dict, concept_id: str) -> dict:
    normalized = _slug(concept_id)
    for concept in data["concepts"]:
        if concept["id"] == normalized:
            return concept
    known = ", ".join(c["id"] for c in data["concepts"]) or "(none)"
    raise TutorError(
        f"unknown concept '{normalized}' in topic '{data['topic']}'; known: {known}"
    )


def _due_entries(datasets: list[dict], now: datetime) -> list[dict]:
    """Due concepts, round-robin interleaved across topics (sorted by slug),
    within a topic ordered by due_at ascending."""
    buckets = []
    for data in sorted(datasets, key=lambda d: d["topic"]):
        due = [c for c in data["concepts"] if _due_dt(c) <= now]
        due.sort(key=lambda c: (c["fsrs"]["due"], c["id"]))
        if due:
            buckets.append((data["topic"], due))
    entries: list[dict] = []
    index = 0
    while True:
        emitted = False
        for topic, due in buckets:
            if index < len(due):
                concept = due[index]
                entries.append({
                    "topic": topic,
                    "id": concept["id"],
                    "status": concept["status"],
                    "due_at": concept["fsrs"]["due"],
                    "review_pointer": concept.get("review_pointer", ""),
                })
                emitted = True
        if not emitted:
            return entries
        index += 1


def _near_duplicate_of(
    existing_ids: list[str], candidate: str
) -> tuple[str, float] | None:
    """First existing id that is a near-duplicate of candidate, with its ratio.

    Character ratio catches typo-level variants; token-set equality/subset
    catches reorderings ("cos-sin-pairing") and refinements ("attention" vs
    "self-attention") that score below the character threshold.
    """
    cand_tokens = set(candidate.split("-"))
    for existing in existing_ids:
        ratio = difflib.SequenceMatcher(None, existing, candidate).ratio()
        ex_tokens = set(existing.split("-"))
        if ratio >= NEAR_DUPLICATE_RATIO or ex_tokens == cand_tokens:
            return existing, ratio
        # Proper subset = probably a decorated variant of the same concept
        # ("gradient-descent" vs "gradient-descent-algorithm"), but only when
        # the overlap is substantial: a lone shared token with low character
        # similarity ("python" vs "python-packaging") is a legitimate
        # refinement, not a duplicate.
        smaller = min(ex_tokens, cand_tokens, key=len)
        if (smaller == ex_tokens or smaller == cand_tokens) and (
            ex_tokens < cand_tokens or cand_tokens < ex_tokens
        ):
            if len(smaller) >= 2 or ratio >= SUBSET_SINGLE_TOKEN_RATIO_FLOOR:
                return existing, ratio
    return None


def _out(obj: object) -> None:
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _split_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [_slug(part) for part in raw.split(",") if part.strip()]


# ── Verbs ────────────────────────────────────────────────────────────────────


def cmd_topics(args: argparse.Namespace, data_dir: Path, now: datetime) -> None:
    rows = []
    for data in _all_topics(data_dir):
        concepts = data["concepts"]
        rows.append({
            "topic": data["topic"],
            "title": data.get("title", ""),
            "goal": data.get("goal", ""),
            "friction": data.get("friction", "medium"),
            "concept_count": len(concepts),
            "due_now": sum(1 for c in concepts if _due_dt(c) <= now),
        })
    _out(rows)


def _existing_topic_view(data: dict) -> dict:
    return {
        "created": False,
        "topic": data["topic"],
        "title": data.get("title", ""),
        "goal": data.get("goal", ""),
        "friction": data.get("friction", "medium"),
        "concept_count": len(data["concepts"]),
    }


def cmd_start(args: argparse.Namespace, data_dir: Path, now: datetime) -> None:
    slug = _slug(args.topic)
    path = _topic_path(data_dir, slug)
    if path.is_file():
        _out(_existing_topic_view(_load_topic(data_dir, slug)))
        return
    with _topic_lock(data_dir, slug, _lock_timeout(args)):
        if path.is_file():  # lost a creation race: still idempotent
            _out(_existing_topic_view(_load_topic(data_dir, slug)))
            return
        data = {
            "schema_version": SCHEMA_VERSION,
            "topic": slug,
            "title": args.title,
            "goal": args.goal or "",
            "friction": args.friction,
            "concepts": [],
            "misconceptions": [],
            "session_log": [],
        }
        _save_topic(data_dir, data)
    _out({
        "created": True,
        "topic": slug,
        "title": args.title,
        "goal": data["goal"],
        "friction": args.friction,
        "concept_count": 0,
    })


def cmd_concepts(args: argparse.Namespace, data_dir: Path, now: datetime) -> None:
    data = _load_topic(data_dir, args.topic)
    _out([_concept_view(c) for c in data["concepts"]])


def cmd_add_concept(args: argparse.Namespace, data_dir: Path, now: datetime) -> None:
    if not args.pointer.strip():
        raise TutorError(
            "--pointer must be non-empty: it is the seed future review "
            "questions are paraphrased from"
        )
    _require_topic_file(data_dir, args.topic)
    normalized = _slug(args.concept_id)
    with _topic_lock(data_dir, _slug(args.topic), _lock_timeout(args)):
        data = _load_topic(data_dir, args.topic)
        existing_ids = [c["id"] for c in data["concepts"]]
        if normalized in existing_ids:
            raise TutorError(
                f"concept '{normalized}' already exists in topic "
                f"'{data['topic']}'; rate it instead of re-adding"
            )
        if not args.force:
            match = _near_duplicate_of(existing_ids, normalized)
            if match is not None:
                existing, ratio = match
                raise TutorError(
                    f"'{normalized}' is a near-duplicate of existing concept "
                    f"'{existing}' (char ratio {ratio:.2f} / token overlap); "
                    "reuse that id, or pass --force if it is genuinely "
                    "distinct",
                    exit_code=2,
                    near_duplicate=existing,
                )
        card = Card(
            card_id=int(now.timestamp() * 1000) + len(existing_ids),
            due=now,
        )
        concept = {
            "id": normalized,
            "status": "learning",
            "fsrs": card.to_dict(),
            "last_verdict": None,
            "review_pointer": args.pointer,
            "review_log": [],
        }
        data["concepts"].append(concept)
        _save_topic(data_dir, data)
    _out({"created": True, "topic": data["topic"], **_concept_view(concept)})


def cmd_rate(args: argparse.Namespace, data_dir: Path, now: datetime) -> None:
    verdict = args.verdict
    if args.transfer and verdict != "correct":
        raise TutorError(
            "--transfer is only valid with --verdict correct: it upgrades a "
            "correct answer to Easy and has no meaning for other verdicts"
        )
    if args.misconception_note is not None:
        args.misconception_note = args.misconception_note.strip()
        if not args.misconception_note:
            raise TutorError("--misconception-note must be non-empty when provided")
        if verdict not in {"partial", "misconception"}:
            raise TutorError(
                "--misconception-note is only valid with --verdict partial or "
                "misconception"
            )
    _require_topic_file(data_dir, args.topic)
    with _topic_lock(data_dir, _slug(args.topic), _lock_timeout(args)):
        data = _load_topic(data_dir, args.topic)
        concept = _find_concept(data, args.concept_id)
        rating = (
            Rating.Easy
            if (verdict == "correct" and args.transfer)
            else VERDICT_RATINGS[verdict]
        )
        card = Card.from_dict(concept["fsrs"])
        scheduler = Scheduler(enable_fuzzing=False)  # deterministic: no fuzz
        card, _review_log = scheduler.review_card(card, rating, review_datetime=now)
        concept["fsrs"] = card.to_dict()
        concept["last_verdict"] = verdict
        concept["status"] = _status_for_card(card)
        # Append-only review log: the durable asset. Never rewrite prior entries.
        concept["review_log"].append({
            "ts": _iso(now),
            "verdict": verdict,
            "rating": rating.name.lower(),
        })
        resolved_misconceptions = 0
        if verdict == "correct":
            for misconception in data["misconceptions"]:
                if (
                    misconception.get("concept_id") == concept["id"]
                    and misconception.get("status") == "active"
                ):
                    misconception["status"] = "resolved"
                    misconception["resolved_at"] = _iso(now)
                    resolved_misconceptions += 1
        if args.misconception_note:
            misconceptions = data["misconceptions"]
            misconceptions.append({
                "id": f"{concept['id']}-m{len(misconceptions) + 1}",
                "concept_id": concept["id"],
                "description": args.misconception_note,
                "status": "active",
                "observed_at": _iso(now),
            })
        _save_topic(data_dir, data)
    _out({
        "due_at": concept["fsrs"]["due"],
        "rating": rating.name.lower(),
        "stability": card.stability,
        "resolved_misconceptions": resolved_misconceptions,
    })


def cmd_due(args: argparse.Namespace, data_dir: Path, now: datetime) -> None:
    _out(_due_entries(_topics_for(data_dir, args.topic), now))


def cmd_summary(args: argparse.Namespace, data_dir: Path, now: datetime) -> None:
    datasets = _topics_for(data_dir, args.topic)
    if not datasets:
        print("No learning topics yet. Use the 'start' verb to begin one.")
        return
    lines: list[str] = []
    due = _due_entries(datasets, now)
    if due:
        lines.append(f"Due now: {len(due)} concept(s)")
        for entry in due[:_SUMMARY_MAX_DUE]:
            pointer = entry["review_pointer"] or "(no review pointer)"
            lines.append(
                f"  - [{entry['topic']}] {entry['id']} "
                f"(due {entry['due_at'][:10]}): {pointer}"
            )
        if len(due) > _SUMMARY_MAX_DUE:
            lines.append(f"  ... and {len(due) - _SUMMARY_MAX_DUE} more")
    else:
        lines.append("No reviews due right now.")
    shown_sessions = 0
    for data in sorted(datasets, key=lambda d: d["topic"]):
        if not data["session_log"] or shown_sessions >= _SUMMARY_MAX_SESSIONS:
            continue
        last = data["session_log"][-1]
        covered = ", ".join(last.get("covered", [])) or "nothing"
        struggled = ", ".join(last.get("struggled", [])) or "nothing"
        lines.append(
            f"Last session on '{data['topic']}' ({last['ts'][:10]}): "
            f"covered {covered}; struggled with {struggled}"
        )
        shown_sessions += 1
    active = [
        (data["topic"], m)
        for data in sorted(datasets, key=lambda d: d["topic"])
        for m in data["misconceptions"]
        if m.get("status") == "active"
    ]
    if active:
        lines.append("Active misconceptions:")
        for topic, misconception in active[:_SUMMARY_MAX_MISCONCEPTIONS]:
            lines.append(f"  - [{topic}] {misconception['description']}")
    print("\n".join(lines))


def cmd_log_session(args: argparse.Namespace, data_dir: Path, now: datetime) -> None:
    _require_topic_file(data_dir, args.topic)
    with _topic_lock(data_dir, _slug(args.topic), _lock_timeout(args)):
        data = _load_topic(data_dir, args.topic)
        entry = {
            "ts": _iso(now),
            "covered": _split_ids(args.covered),
            "struggled": _split_ids(args.struggled),
        }
        known = {c["id"] for c in data["concepts"]}
        unknown = [
            cid for cid in entry["covered"] + entry["struggled"] if cid not in known
        ]
        if unknown:
            raise TutorError(
                f"unknown concept id(s) in log-session: {', '.join(unknown)}; "
                f"known: {', '.join(sorted(known)) or '(none)'} — add-concept "
                "first, or fix the typo"
            )
        data["session_log"].append(entry)
        _save_topic(data_dir, data)
    _out({"logged": True, "topic": data["topic"], **entry})


def cmd_stats(args: argparse.Namespace, data_dir: Path, now: datetime) -> None:
    datasets = _topics_for(data_dir, args.topic)
    by_status: dict[str, int] = {}
    total_reviews = 0
    next_due: str | None = None
    for data in datasets:
        for concept in data["concepts"]:
            by_status[concept["status"]] = by_status.get(concept["status"], 0) + 1
            total_reviews += len(concept["review_log"])
            due_at = concept["fsrs"]["due"]
            if next_due is None or due_at < next_due:
                next_due = due_at
    _out({
        "topics": len(datasets),
        "concepts_by_status": by_status,
        "total_reviews": total_reviews,
        "next_due": next_due,
    })


# ── CLI ──────────────────────────────────────────────────────────────────────


class _JsonArgumentParser(argparse.ArgumentParser):
    """Usage errors as one-line JSON on stdout (exit 2), per the CLI contract."""

    def error(self, message: str):  # exits; never returns
        print(
            json.dumps(
                {
                    "error": f"usage error: {message}",
                    "usage": self.format_usage().strip(),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir",
        default=argparse.SUPPRESS,
        help=f"state directory (default: {_default_data_dir()})",
    )
    common.add_argument(
        "--now",
        default=argparse.SUPPRESS,
        help="override current time, ISO 8601 (UTC assumed if naive)",
    )
    common.add_argument(  # test hook: shorten lock waits in the suite
        "--lock-timeout",
        type=float,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    # Subparsers inherit _JsonArgumentParser (argparse uses type(parser)), so
    # bad verbs, flags, and choices all honor the JSON error contract.
    parser = _JsonArgumentParser(
        prog="tutor_state.py",
        description="Deterministic FSRS learner-state store for the tutor skill.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("topics", parents=[common], help="list topics")
    p.set_defaults(func=cmd_topics)

    p = sub.add_parser("start", parents=[common], help="create a topic (idempotent)")
    p.add_argument("topic")
    p.add_argument("--title", required=True)
    p.add_argument("--goal", default="")
    p.add_argument("--friction", choices=("low", "medium", "high"), default="medium")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("concepts", parents=[common], help="list a topic's concepts")
    p.add_argument("topic")
    p.set_defaults(func=cmd_concepts)

    p = sub.add_parser("add-concept", parents=[common], help="add a concept")
    p.add_argument("topic")
    p.add_argument("concept_id")
    p.add_argument("--pointer", required=True, help="review pointer text")
    p.add_argument("--force", action="store_true", help="bypass near-duplicate check")
    p.set_defaults(func=cmd_add_concept)

    p = sub.add_parser("rate", parents=[common], help="record a graded review")
    p.add_argument("topic")
    p.add_argument("concept_id")
    p.add_argument("--verdict", required=True, choices=tuple(VERDICT_RATINGS))
    p.add_argument(
        "--transfer",
        action="store_true",
        help="learner transferred the idea to a new context (correct -> Easy)",
    )
    p.add_argument(
        "--misconception-note",
        default=None,
        help="active misconception observed in a partial/misconception answer",
    )
    p.set_defaults(func=cmd_rate)

    p = sub.add_parser("due", parents=[common], help="list due concepts, interleaved")
    p.add_argument("topic", nargs="?", default=None)
    p.set_defaults(func=cmd_due)

    p = sub.add_parser("summary", parents=[common], help="session-open text report")
    p.add_argument("topic", nargs="?", default=None)
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("log-session", parents=[common], help="append a session entry")
    p.add_argument("topic")
    p.add_argument("--covered", default="")
    p.add_argument("--struggled", default="")
    p.set_defaults(func=cmd_log_session)

    p = sub.add_parser("stats", parents=[common], help="aggregate statistics")
    p.add_argument("topic", nargs="?", default=None)
    p.set_defaults(func=cmd_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        now = _parse_now(getattr(args, "now", None))
        data_dir = Path(getattr(args, "data_dir", str(_default_data_dir())))
        args.func(args, data_dir, now)
    except TutorError as exc:
        print(json.dumps(exc.payload, ensure_ascii=False))
        return exc.exit_code
    except Exception as exc:  # actionable JSON, never a stack trace
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
