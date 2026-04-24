"""Tests for the Phase A.3 ratchet substrate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepagents_cli.ratchet import QualitySnapshot, Ratchet, Violation


@pytest.fixture
def ratchet(tmp_path: Path) -> Ratchet:
    return Ratchet(harness_dir=tmp_path / ".harness")


# ---------------------------------------------------------------------------
# Violation type
# ---------------------------------------------------------------------------


def test_violation_identity_is_rule_plus_subject() -> None:
    v1 = Violation(rule="scope.x", subject="/app/a.py", reason="r", first_seen="t")
    v2 = Violation(rule="scope.x", subject="/app/a.py", reason="r2", first_seen="t2")
    v3 = Violation(rule="scope.y", subject="/app/a.py", reason="r", first_seen="t")
    assert v1.identity() == v2.identity()
    assert v1.identity() != v3.identity()


# ---------------------------------------------------------------------------
# Ratchet — empty state
# ---------------------------------------------------------------------------


def test_fresh_ratchet_has_no_violations(ratchet: Ratchet) -> None:
    assert ratchet.load_violations() == []


def test_fresh_ratchet_has_no_snapshots(ratchet: Ratchet) -> None:
    assert ratchet.load_quality_history() == []


def test_missing_harness_dir_is_ok(tmp_path: Path) -> None:
    # Explicitly use a dir that doesn't exist; load_* must not raise.
    r = Ratchet(harness_dir=tmp_path / "does-not-exist" / ".harness")
    assert r.load_violations() == []
    assert r.load_quality_history() == []


# ---------------------------------------------------------------------------
# Ratchet — recording violations
# ---------------------------------------------------------------------------


def test_record_returns_is_new_true_for_unseen(ratchet: Ratchet) -> None:
    v, is_new = ratchet.record("scope.out", "/app/a.py", "wrote outside scope")
    assert is_new is True
    assert v.rule == "scope.out"
    assert v.subject == "/app/a.py"
    assert v.first_seen  # ISO timestamp present


def test_record_second_time_returns_is_new_false(ratchet: Ratchet) -> None:
    v1, is_new1 = ratchet.record("scope.out", "/app/a.py", "first")
    v2, is_new2 = ratchet.record("scope.out", "/app/a.py", "second")
    assert is_new1 is True
    assert is_new2 is False
    # The first_seen timestamp is preserved on the existing entry
    assert v2.first_seen == v1.first_seen
    # The old reason wins — we don't overwrite
    assert v2.reason == "first"


def test_record_persists_across_instances(ratchet: Ratchet, tmp_path: Path) -> None:
    ratchet.record("scope.out", "/app/a.py", "r")
    # New instance pointing at the same dir sees the recorded violation
    r2 = Ratchet(harness_dir=tmp_path / ".harness")
    violations = r2.load_violations()
    assert len(violations) == 1
    assert violations[0].subject == "/app/a.py"


def test_is_existing_violation(ratchet: Ratchet) -> None:
    assert ratchet.is_existing_violation("scope.out", "/app/a.py") is False
    ratchet.record("scope.out", "/app/a.py", "r")
    assert ratchet.is_existing_violation("scope.out", "/app/a.py") is True
    assert ratchet.is_existing_violation("scope.out", "/other.py") is False


def test_multiple_rules_on_same_subject_tracked_separately(ratchet: Ratchet) -> None:
    _, new1 = ratchet.record("scope.out", "/app/a.py", "r1")
    _, new2 = ratchet.record("arch.forbidden", "/app/a.py", "r2")
    assert new1 is True
    assert new2 is True
    assert len(ratchet.load_violations()) == 2


# ---------------------------------------------------------------------------
# Ratchet — quality snapshots
# ---------------------------------------------------------------------------


def test_append_and_load_snapshot(ratchet: Ratchet) -> None:
    snap = QualitySnapshot(
        taken_at="2026-04-24T12:00:00+00:00",
        total_loc=1234,
        large_files=3,
        forbidden_imports=0,
        test_coverage_pct=87.5,
        notes="run-010 baseline",
    )
    ratchet.append_snapshot(snap)
    history = ratchet.load_quality_history()
    assert len(history) == 1
    assert history[0].total_loc == 1234
    assert history[0].test_coverage_pct == 87.5


def test_snapshots_append_in_order(ratchet: Ratchet) -> None:
    ratchet.append_snapshot(QualitySnapshot(taken_at="2026-04-24T12:00:00+00:00", total_loc=100))
    ratchet.append_snapshot(QualitySnapshot(taken_at="2026-04-24T13:00:00+00:00", total_loc=110))
    history = ratchet.load_quality_history()
    assert [s.total_loc for s in history] == [100, 110]


def test_snapshot_with_none_coverage_roundtrips(ratchet: Ratchet) -> None:
    ratchet.append_snapshot(QualitySnapshot(taken_at="2026-04-24T12:00:00+00:00"))
    history = ratchet.load_quality_history()
    assert history[0].test_coverage_pct is None


# ---------------------------------------------------------------------------
# Corrupt-file tolerance
# ---------------------------------------------------------------------------


def test_corrupt_violations_file_is_treated_as_empty(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "violations.json").write_text("{{ not json")
    r = Ratchet(harness_dir=hd)
    # Must not raise; must return empty list
    assert r.load_violations() == []


def test_non_list_violations_payload_is_empty(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "violations.json").write_text('{"wrong": "shape"}')
    r = Ratchet(harness_dir=hd)
    assert r.load_violations() == []


def test_malformed_entries_are_skipped(tmp_path: Path) -> None:
    hd = tmp_path / ".harness"
    hd.mkdir()
    (hd / "violations.json").write_text(
        json.dumps(
            [
                {"rule": "ok", "subject": "/a", "reason": "r", "first_seen": "t"},
                {"this": "is garbage"},
                {"rule": "ok2", "subject": "/b", "reason": "r", "first_seen": "t"},
            ]
        )
    )
    r = Ratchet(harness_dir=hd)
    violations = r.load_violations()
    assert {v.subject for v in violations} == {"/a", "/b"}


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def test_write_is_atomic_no_tmp_leftover(ratchet: Ratchet) -> None:
    ratchet.record("scope.out", "/a", "r")
    # After a successful write the .tmp sibling should be gone
    tmp = ratchet.violations_path.with_suffix(".json.tmp")
    assert not tmp.exists()
    assert ratchet.violations_path.exists()


# ---------------------------------------------------------------------------
# Integration: ScopeEnforcement rejection → Ratchet.record
# ---------------------------------------------------------------------------


def test_scope_enforcement_records_via_ratchet(tmp_path: Path) -> None:
    """The recorder callback is the integration point between
    ScopeEnforcementMiddleware and Ratchet. This test proves the
    contract without needing the agent runtime."""
    from unittest.mock import Mock

    from langchain_core.messages import ToolMessage

    from deepagents_cli.policy import TaskPolicy
    from deepagents_cli.scope_enforcement import ScopeEnforcementMiddleware

    ratchet = Ratchet(harness_dir=tmp_path / ".harness")
    policy = TaskPolicy(task_type="docs", allowed_paths=("docs/**",))

    def record_violation(tool: str, path: str, reason: str) -> None:
        ratchet.record(rule=f"scope.{reason.split()[0].lower()}", subject=path, reason=reason)

    m = ScopeEnforcementMiddleware(policy=policy, violation_recorder=record_violation)

    req = Mock()
    req.tool_call = {
        "name": "write_file",
        "args": {"path": "/src/naughty.py"},
        "id": "tc-1",
    }

    def handler(_req: object) -> ToolMessage:
        msg = ToolMessage(content="ok", name="write_file", tool_call_id="tc-1")
        return msg

    m.wrap_tool_call(req, handler)

    violations = ratchet.load_violations()
    assert len(violations) == 1
    assert violations[0].subject == "/src/naughty.py"
    assert "scope." in violations[0].rule
