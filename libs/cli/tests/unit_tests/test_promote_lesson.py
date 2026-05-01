"""Tests for promote-lesson automation (M6 + Meta-Harness Integration 1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepagents_cli.promote_lesson import (
    PromotionProposal,
    promote_from_trial,
    propose,
    propose_and_stage,
    stage_proposal,
)
from deepagents_cli.trace_analyzer import TraceInsight


def _insight(**overrides: object) -> TraceInsight:
    defaults: dict[str, object] = {
        "category": "missing_context",
        "confidence": "medium",
        "summary": "summary text",
        "evidence": ("e1", "e2"),
        "proposed_promotion": "do the thing",
    }
    defaults.update(overrides)
    return TraceInsight(**defaults)  # type: ignore[arg-type]


def _strategies(proposals: tuple[PromotionProposal, ...]) -> set[str]:
    return {p.strategy for p in proposals}


# ---------------------------------------------------------------------------
# propose — multi-candidate fan-out
# ---------------------------------------------------------------------------


def test_propose_returns_tuple() -> None:
    proposals = propose(_insight())
    assert isinstance(proposals, tuple)
    assert len(proposals) >= 1
    assert all(isinstance(p, PromotionProposal) for p in proposals)


def test_missing_context_yields_strategy_distinct_variants() -> None:
    proposals = propose(_insight(category="missing_context"))
    strategies = _strategies(proposals)
    # Two strategies: edit a rule vs add an example
    assert "rule_edit" in strategies
    assert "example_file" in strategies
    # Variants must target different paths (otherwise reviewers
    # can't pick between them meaningfully)
    targets = {p.target_path for p in proposals}
    assert len(targets) == len(proposals)


def test_missing_rule_yields_rule_and_test_variants() -> None:
    proposals = propose(_insight(category="missing_rule"))
    strategies = _strategies(proposals)
    assert "rule_edit" in strategies
    assert "companion_test" in strategies


def test_missing_tool_yields_policy_and_backlog_variants() -> None:
    proposals = propose(_insight(category="missing_tool"))
    strategies = _strategies(proposals)
    assert "policy_relaxation" in strategies
    assert "tool_proposal" in strategies
    # The architectural variant has no single target file
    backlog = next(p for p in proposals if p.strategy == "tool_proposal")
    assert backlog.target_path is None


def test_missing_example_yields_narrative_and_golden_variants() -> None:
    proposals = propose(_insight(category="missing_example"))
    strategies = _strategies(proposals)
    assert "narrative_example" in strategies
    assert "golden_test" in strategies


def test_model_capability_limit_yields_single_variant() -> None:
    """Low-signal category — multi-candidate fan-out would just be noise."""
    proposals = propose(_insight(category="model_capability_limit"))
    assert len(proposals) == 1
    assert proposals[0].strategy == "known_limits_note"


def test_unknown_category_falls_back_with_manual_review_strategy() -> None:
    insight = _insight(category="missing_context")
    object.__setattr__(insight, "category", "newly_invented_bucket")
    proposals = propose(insight)
    assert len(proposals) == 1
    assert proposals[0].strategy == "manual_review"
    assert "Uncategorized" in proposals[0].title


# ---------------------------------------------------------------------------
# Confidence + body shape — apply to every variant
# ---------------------------------------------------------------------------


def test_every_variant_carries_insight_confidence() -> None:
    for category in (
        "missing_context",
        "missing_rule",
        "missing_tool",
        "missing_example",
        "model_capability_limit",
    ):
        proposals = propose(_insight(category=category, confidence="high"))
        for p in proposals:
            assert p.confidence == "high"


def test_every_variant_has_standard_body_sections() -> None:
    proposals = propose(_insight())
    for p in proposals:
        for section in (
            "## Insight",
            "## Proposed action",
            "## Suggested edit",
            "## Confidence",
            "## Evidence",
        ):
            assert section in p.body, f"strategy={p.strategy} missing {section}"


def test_every_variant_includes_evidence_bullets() -> None:
    proposals = propose(_insight(evidence=("foo", "bar")))
    for p in proposals:
        assert "- foo" in p.body
        assert "- bar" in p.body


def test_confidence_note_matches_level_across_strategies() -> None:
    high_props = propose(_insight(confidence="high"))
    low_props = propose(_insight(confidence="low"))
    for p in high_props:
        assert "auto-apply" in p.body
    for p in low_props:
        assert "triage hint" in p.body


# ---------------------------------------------------------------------------
# stage_proposal
# ---------------------------------------------------------------------------


def test_stage_proposal_creates_pending_promotions_dir(tmp_path: Path) -> None:
    proposal = propose(_insight())[0]
    path = stage_proposal(proposal, harness_dir=tmp_path / ".harness")
    assert path.exists()
    assert path.parent.name == "pending-promotions"


def test_staged_file_includes_strategy_in_header_and_filename(tmp_path: Path) -> None:
    proposal = propose(_insight(category="missing_context"))[0]
    path = stage_proposal(
        proposal,
        harness_dir=tmp_path / ".harness",
        trial_id="trial-123",
    )
    text = path.read_text()
    assert "# Promotion proposal" in text
    assert proposal.strategy in text  # header contains the strategy tag
    assert proposal.strategy in path.name  # filename does too
    assert "trial-123" in text


def test_stage_filename_layout_includes_category_strategy_trial(
    tmp_path: Path,
) -> None:
    proposal = propose(_insight(category="missing_rule"))[0]
    path = stage_proposal(
        proposal,
        harness_dir=tmp_path / ".harness",
        trial_id="task-xyz",
    )
    # Expected shape: <timestamp>-missing_rule-<strategy>-task-xyz.md
    assert path.name.endswith(f"-missing_rule-{proposal.strategy}-task-xyz.md")


def test_stage_proposal_sanitizes_trial_id_slashes(tmp_path: Path) -> None:
    proposal = propose(_insight())[0]
    path = stage_proposal(
        proposal,
        harness_dir=tmp_path / ".harness",
        trial_id="runs/abc/xyz",
    )
    assert "/" not in path.name
    assert "runs_abc_xyz" in path.name


# ---------------------------------------------------------------------------
# propose_and_stage — multi-variant
# ---------------------------------------------------------------------------


def test_propose_and_stage_writes_one_file_per_variant(tmp_path: Path) -> None:
    proposals, paths = propose_and_stage(
        _insight(category="missing_example"),
        harness_dir=tmp_path / ".harness",
        trial_id="t1",
    )
    assert isinstance(proposals, tuple)
    assert isinstance(paths, tuple)
    # Length parity is the contract — one staged file per proposal
    assert len(proposals) == len(paths) == 2
    for path in paths:
        assert path.exists()
    # Distinct filenames (strategy distinguishes them)
    assert len({p.name for p in paths}) == len(paths)


def test_propose_and_stage_returns_empty_paths_when_no_variants(
    tmp_path: Path,
) -> None:
    """Defensive: an unknown category still returns at least one variant
    via the fallback renderer; staged_paths should match length."""
    insight = _insight()
    object.__setattr__(insight, "category", "totally_new_category")
    proposals, paths = propose_and_stage(
        insight,
        harness_dir=tmp_path / ".harness",
        trial_id="t1",
    )
    assert len(proposals) == len(paths) == 1


# ---------------------------------------------------------------------------
# promote_from_trial — multi-variant return shape
# ---------------------------------------------------------------------------


def _make_failed_trial(tmp_path: Path, *, kind: str) -> Path:
    """Create a minimal trial dir extract_signals can read."""
    trial = tmp_path / "trial-xyz"
    (trial / "agent").mkdir(parents=True)

    if kind == "arch":
        (trial / "result.json").write_text(
            json.dumps({"verifier_result": {"rewards": {"reward": 0.0}}}),
        )
        traj = {
            "steps": [{"step_id": 1}],
            "final_metrics": {"total_prompt_tokens": 1000, "total_completion_tokens": 200},
        }
        (trial / "agent" / "trajectory.json").write_text(
            json.dumps(traj) + "\nArch-lint violation\n",
        )
    elif kind == "dump":
        (trial / "result.json").write_text(
            json.dumps({"verifier_result": {"rewards": {"reward": 0.0}}}),
        )
        traj = {
            "steps": [{"step_id": i} for i in range(3)],
            "final_metrics": {"total_prompt_tokens": 10_000, "total_completion_tokens": 35_000},
        }
        (trial / "agent" / "trajectory.json").write_text(json.dumps(traj))
    elif kind == "hang":
        (trial / "result.json").write_text(
            json.dumps({"verifier_result": {"rewards": {"reward": 0.0}}}),
        )
        traj = {
            "steps": [],
            "final_metrics": {"total_prompt_tokens": 0, "total_completion_tokens": 0},
        }
        (trial / "agent" / "trajectory.json").write_text(json.dumps(traj))
    return trial


def test_promote_from_trial_arch_rejection_stages_two_variants(tmp_path: Path) -> None:
    trial = _make_failed_trial(tmp_path, kind="arch")
    result = promote_from_trial(trial)
    assert result is not None
    proposals, staged = result
    assert {p.category for p in proposals} == {"missing_rule"}
    # missing_rule → 2 strategies
    assert len(proposals) == 2
    assert _strategies(proposals) == {"rule_edit", "companion_test"}
    for path in staged:
        assert path.exists()


def test_promote_from_trial_dump_pattern_stages_example_and_golden(tmp_path: Path) -> None:
    trial = _make_failed_trial(tmp_path, kind="dump")
    result = promote_from_trial(trial)
    assert result is not None
    proposals, _ = result
    assert _strategies(proposals) == {"narrative_example", "golden_test"}


def test_promote_from_trial_skips_low_conf_model_limit(tmp_path: Path) -> None:
    trial = _make_failed_trial(tmp_path, kind="hang")
    assert promote_from_trial(trial) is None


def test_promote_from_trial_missing_dir_returns_none(tmp_path: Path) -> None:
    assert promote_from_trial(tmp_path / "nonexistent") is None


def test_promote_from_trial_default_harness_dir(tmp_path: Path) -> None:
    trial = _make_failed_trial(tmp_path, kind="arch")
    result = promote_from_trial(trial)
    assert result is not None
    _proposals, staged = result
    for path in staged:
        assert path.is_relative_to(trial)
        assert path.parent.parent.name == ".harness"


def test_promote_from_trial_explicit_harness_dir(tmp_path: Path) -> None:
    trial = _make_failed_trial(tmp_path, kind="arch")
    explicit = tmp_path / "custom-harness"
    result = promote_from_trial(trial, harness_dir=explicit)
    assert result is not None
    _proposals, staged = result
    for path in staged:
        assert path.is_relative_to(explicit)
