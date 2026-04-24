"""Task policy layer — decides what kind of run is happening.

For every agent run the harness selects exactly one ``TaskPolicy`` based
on the classifier's ``TaskHints``. Downstream middleware (scope
enforcement, reviewer gating, verification pipeline) consults the policy
instead of making their own decisions.

Keeping this in one file avoids the YAML/policy-DSL temptation until the
set of task types actually stabilizes. The dispatch is a plain function.

Phase A.1 of the agent-harness roadmap. The enforcement middleware that
consumes this policy arrives in A.2 (``scope_enforcement.py``). This
module is intentionally standalone — importing it must not pull in agent
runtime or LangGraph.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --- Types ---------------------------------------------------------------


@dataclass(frozen=True)
class TaskPolicy:
    """Policy applied to a single agent run.

    Attributes:
        task_type: Short tag used by downstream consumers and logs
            (``docs``, ``bugfix``, ``feature``, etc.).
        allowed_paths: Glob patterns the agent may write to. An empty
            tuple means no file writes are allowed; a single ``"**"``
            pattern allows all paths (use sparingly — that's the default
            only for unknown task types where we can't infer scope).
        max_files_changed: Upper bound on distinct files an agent may
            touch during a single run. Prevents runaway refactors.
        require_tests_pass: When True, verification must confirm tests
            pass before the run is allowed to terminate.
        require_plan: When True, the agent must emit a plan before any
            file writes. (Enforced later by a separate middleware.)
        require_reviewer: When True, a reviewer sub-agent must issue a
            passing verdict before termination.
        approval_level: One of ``auto`` (no human approval), ``on-request``
            (agent asks for approval if it's unsure), or ``required``
            (human must approve before merge).
        required_checks: Named checks that must pass. Consumed by the
            verification pipeline; unknown names are ignored rather than
            errored so new checks can be added without breaking old
            policies.
    """

    task_type: str
    allowed_paths: tuple[str, ...] = ("**",)
    max_files_changed: int = 25
    require_tests_pass: bool = True
    require_plan: bool = False
    require_reviewer: bool = False
    approval_level: str = "on-request"
    required_checks: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.approval_level not in _APPROVAL_LEVELS:
            levels = ", ".join(sorted(_APPROVAL_LEVELS))
            msg = (
                f"approval_level must be one of [{levels}], "
                f"got {self.approval_level!r}"
            )
            raise ValueError(msg)
        if self.max_files_changed < 1:
            msg = "max_files_changed must be >= 1"
            raise ValueError(msg)


_APPROVAL_LEVELS = frozenset({"auto", "on-request", "required"})


# --- Predefined policies -------------------------------------------------

# Design note: these defaults are conservative. A real deployment tunes
# `allowed_paths` per repository via the policy layer's context resolver
# (Phase B). The globs below assume a generic repo layout and are a
# reasonable starting point; they can be overridden by `.harness/config.yaml`
# once that config format exists.


_DOCS_POLICY = TaskPolicy(
    task_type="docs",
    allowed_paths=("**/*.md", "**/*.rst", "**/*.txt", "docs/**"),
    max_files_changed=10,
    require_tests_pass=False,
    approval_level="auto",
    required_checks=("docs-lint",),
)

_TEST_GEN_POLICY = TaskPolicy(
    task_type="test-generation",
    allowed_paths=("**/tests/**", "**/test_*.py", "**/*_test.py", "**/*.test.*"),
    max_files_changed=15,
    require_tests_pass=True,
    require_plan=False,
    approval_level="on-request",
    required_checks=("typecheck", "test-suite"),
)

_BUGFIX_POLICY = TaskPolicy(
    task_type="bugfix",
    allowed_paths=("**",),  # bugs can be anywhere — max_files caps blast radius
    max_files_changed=10,
    require_tests_pass=True,
    require_plan=False,
    require_reviewer=True,
    approval_level="on-request",
    required_checks=("typecheck", "test-suite", "lint"),
)

_FEATURE_POLICY = TaskPolicy(
    task_type="feature",
    allowed_paths=("**",),
    max_files_changed=25,
    require_tests_pass=True,
    require_plan=True,
    require_reviewer=True,
    approval_level="on-request",
    required_checks=("typecheck", "test-suite", "lint", "arch-lint"),
)

_REFACTOR_POLICY = TaskPolicy(
    task_type="refactor",
    allowed_paths=("**",),
    max_files_changed=50,
    require_tests_pass=True,
    require_plan=True,
    require_reviewer=True,
    approval_level="on-request",
    required_checks=(
        "typecheck",
        "test-suite",
        "lint",
        "arch-lint",
        "behavior-preservation",
    ),
)

_MIGRATION_POLICY = TaskPolicy(
    task_type="migration",
    allowed_paths=("**",),
    max_files_changed=100,
    require_tests_pass=True,
    require_plan=True,
    require_reviewer=True,
    approval_level="required",
    required_checks=(
        "typecheck",
        "test-suite",
        "migration-plan",
        "rollback-plan",
    ),
)

_SECURITY_POLICY = TaskPolicy(
    task_type="security-fix",
    allowed_paths=("**",),
    max_files_changed=15,
    require_tests_pass=True,
    require_reviewer=True,
    approval_level="required",
    required_checks=(
        "typecheck",
        "test-suite",
        "security-scan",
        "regression-tests",
    ),
)

_FALLBACK_POLICY = TaskPolicy(
    task_type="unknown",
    allowed_paths=("**",),
    max_files_changed=10,
    require_tests_pass=True,
    require_reviewer=True,
    approval_level="on-request",
)


# Exported map so callers can look up a known policy by name without
# inspecting the dispatch logic.
POLICIES: dict[str, TaskPolicy] = {
    "docs": _DOCS_POLICY,
    "test-generation": _TEST_GEN_POLICY,
    "bugfix": _BUGFIX_POLICY,
    "feature": _FEATURE_POLICY,
    "refactor": _REFACTOR_POLICY,
    "migration": _MIGRATION_POLICY,
    "security-fix": _SECURITY_POLICY,
    "unknown": _FALLBACK_POLICY,
}


# --- Dispatch ------------------------------------------------------------


def policy_for(task_hints: dict[str, str] | None) -> TaskPolicy:
    """Map classifier hints to a ``TaskPolicy``.

    Resolution rules in order:

    1. Explicit ``task_type`` key in the hints — honored verbatim when it
       names a known policy.
    2. Inference from ``phase`` + ``domain`` via the heuristics below.
    3. Fallback to ``unknown`` policy (conservative: reviewer required,
       tight file cap).

    The hints are produced by ``deepagents.prompt.classify()``. Shape:
    ``{"phase": "fix", "domain": "python", "complexity": "exploratory",
    "guidance": "..."}``. All fields optional.

    Args:
        task_hints: Output of the task classifier, or None for an
            untagged run.

    Returns:
        The policy the agent should run under.
    """
    if not task_hints:
        return _FALLBACK_POLICY

    explicit = task_hints.get("task_type")
    if explicit and explicit in POLICIES:
        return POLICIES[explicit]

    phase = (task_hints.get("phase") or "").lower()
    domain = (task_hints.get("domain") or "").lower()

    # Domain-first overrides win: a crypto task is always security-fix
    # regardless of phase, because the stakes trump the phase gate.
    if domain == "crypto":
        return _SECURITY_POLICY

    # Phase-driven dispatch
    if phase == "fix":
        return _BUGFIX_POLICY
    if phase == "test":
        return _TEST_GEN_POLICY
    if phase == "examine":
        # Examine is read-heavy. Downgrade to docs policy so writes are
        # gated, but drop docs-specific checks.
        return TaskPolicy(
            task_type="examine",
            allowed_paths=("docs/**", "**/*.md"),
            max_files_changed=5,
            require_tests_pass=False,
            approval_level="auto",
        )
    if phase == "build":
        # Build = feature. We could split further on domain (e.g., migration
        # when domain=data + phase=build) but that's Phase B work.
        return _FEATURE_POLICY

    return _FALLBACK_POLICY


__all__ = [
    "POLICIES",
    "TaskPolicy",
    "policy_for",
]
