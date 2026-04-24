"""Tests for the Phase A.1 task-policy layer."""

from __future__ import annotations

import pytest

from deepagents_cli.policy import POLICIES, TaskPolicy, policy_for


# ---------------------------------------------------------------------------
# TaskPolicy validation
# ---------------------------------------------------------------------------


def test_task_policy_is_frozen() -> None:
    p = TaskPolicy(task_type="docs")
    with pytest.raises(Exception):  # noqa: PT011  # frozen dataclass raises FrozenInstanceError
        p.task_type = "feature"  # type: ignore[misc]


def test_task_policy_rejects_bad_approval_level() -> None:
    with pytest.raises(ValueError, match="approval_level"):
        TaskPolicy(task_type="docs", approval_level="whenever")


def test_task_policy_rejects_zero_max_files() -> None:
    with pytest.raises(ValueError, match="max_files_changed"):
        TaskPolicy(task_type="docs", max_files_changed=0)


def test_task_policy_accepts_all_three_approval_levels() -> None:
    for level in ("auto", "on-request", "required"):
        p = TaskPolicy(task_type="x", approval_level=level)
        assert p.approval_level == level


# ---------------------------------------------------------------------------
# Predefined policy shape sanity
# ---------------------------------------------------------------------------


def test_all_predefined_policies_have_unique_task_types() -> None:
    types = [p.task_type for p in POLICIES.values()]
    assert len(types) == len(set(types))


def test_predefined_docs_policy_is_auto_approve_no_tests() -> None:
    p = POLICIES["docs"]
    assert p.approval_level == "auto"
    assert p.require_tests_pass is False
    # Docs shouldn't touch code — allowed paths are markdown/docs only
    assert all(
        glob in p.allowed_paths
        for glob in ("**/*.md", "docs/**")
    )


def test_migration_and_security_require_human_approval() -> None:
    for name in ("migration", "security-fix"):
        assert POLICIES[name].approval_level == "required"


def test_feature_and_refactor_require_plan_and_reviewer() -> None:
    for name in ("feature", "refactor"):
        p = POLICIES[name]
        assert p.require_plan is True
        assert p.require_reviewer is True


def test_fallback_policy_is_conservative() -> None:
    p = POLICIES["unknown"]
    # Reviewer required, small file cap — we don't trust unknown tasks
    assert p.require_reviewer is True
    assert p.max_files_changed <= 15
    assert p.require_tests_pass is True


# ---------------------------------------------------------------------------
# policy_for dispatch
# ---------------------------------------------------------------------------


def test_policy_for_none_returns_fallback() -> None:
    assert policy_for(None).task_type == "unknown"


def test_policy_for_empty_dict_returns_fallback() -> None:
    assert policy_for({}).task_type == "unknown"


def test_policy_for_explicit_task_type_wins() -> None:
    hints = {"task_type": "migration", "phase": "fix"}
    # Explicit beats phase-derived bugfix
    assert policy_for(hints).task_type == "migration"


def test_policy_for_unknown_explicit_type_falls_through() -> None:
    # Unknown explicit type ignored; phase still drives dispatch
    hints = {"task_type": "weird-custom", "phase": "build"}
    assert policy_for(hints).task_type == "feature"


def test_policy_for_fix_phase_is_bugfix() -> None:
    assert policy_for({"phase": "fix", "domain": "python"}).task_type == "bugfix"


def test_policy_for_build_phase_is_feature() -> None:
    assert policy_for({"phase": "build", "domain": "python"}).task_type == "feature"


def test_policy_for_test_phase_is_test_generation() -> None:
    assert policy_for({"phase": "test"}).task_type == "test-generation"


def test_policy_for_examine_phase_is_custom_scoped_docs() -> None:
    p = policy_for({"phase": "examine"})
    # Examine produces a specialized policy, not a named preset
    assert p.task_type == "examine"
    assert p.approval_level == "auto"
    assert p.require_tests_pass is False
    # Writes gated to docs/markdown only
    for glob in p.allowed_paths:
        assert "docs" in glob or glob.endswith(".md")


def test_policy_for_crypto_domain_forces_security_fix() -> None:
    # Crypto domain overrides phase — even a bugfix in crypto land
    # gets the security policy.
    hints = {"phase": "fix", "domain": "crypto"}
    assert policy_for(hints).task_type == "security-fix"


def test_policy_for_crypto_domain_wins_over_build() -> None:
    hints = {"phase": "build", "domain": "crypto"}
    assert policy_for(hints).task_type == "security-fix"


def test_policy_for_missing_phase_and_domain_falls_back() -> None:
    hints = {"complexity": "simple"}
    assert policy_for(hints).task_type == "unknown"


# ---------------------------------------------------------------------------
# End-to-end: classifier output flows into policy selection
# ---------------------------------------------------------------------------


def test_classifier_output_dispatches_correctly() -> None:
    # Use the real classifier to make sure its dict shape is compatible
    from deepagents.prompt import classify

    hints = classify("Fix the failing pytest in test_util.py").as_dict()
    p = policy_for(hints)
    # phase=fix, domain=python → bugfix
    assert p.task_type == "bugfix"
    assert p.require_reviewer is True


def test_classifier_crypto_task_routes_to_security() -> None:
    from deepagents.prompt import classify

    hints = classify("Generate an openssl self-signed cert").as_dict()
    p = policy_for(hints)
    assert p.task_type == "security-fix"
    assert p.approval_level == "required"


def test_classifier_docs_task_routes_to_examine_or_feature() -> None:
    from deepagents.prompt import classify

    # "Document the API" is a write task, should be feature or docs-ish
    hints = classify("Write documentation for the public API").as_dict()
    p = policy_for(hints)
    # phase=build → feature
    assert p.task_type == "feature"
