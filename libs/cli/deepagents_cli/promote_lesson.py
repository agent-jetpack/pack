"""promote-lesson — turn trace insights into durable harness artifacts.

M6 of the agent-harness roadmap, plus Integration 1 from the
Meta-Harness review (multi-candidate fan-out). The trace analyzer
produces a ``TraceInsight`` per failed trial; this module renders
**up to three** qualitatively different artifact proposals per
insight and stages them under ``.harness/pending-promotions/`` for
human review. No files outside the staging directory are modified.

Flow:

::

    failed trial
      ↓
    trace_analyzer.analyze_trial → TraceInsight
      ↓
    promote_lesson.propose → tuple[PromotionProposal, ...]
      ↓
    stage every variant to .harness/pending-promotions/
      ↓
    human picks one (or none, or merges several)

The fan-out is **strategy-distinct** rather than intensity-graded:
variants differ in *what kind of artifact* they propose, not in how
hard they enforce. Reviewers picking between "tighten a rule",
"add a worked example", and "open a tooling task" make a more
informative call than picking between block / warn / info on the
same rule. Severity and intensity stay reviewer-tunable in the
chosen artifact.

Per-category renderers each return a sequence of proposals. Adding
a new category means: add a rendering function, register it in
``_RENDERERS``. New strategies for an existing category just append
``PromotionProposal`` instances to that renderer's tuple.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from deepagents_cli.trace_analyzer import TraceInsight

logger = logging.getLogger(__name__)


# --- Types --------------------------------------------------------------


@dataclass(frozen=True)
class PromotionProposal:
    """A staged artifact proposal ready for human review.

    Attributes:
        category: Matches ``TraceInsight.category``.
        confidence: Carried over from the insight.
        strategy: Short tag distinguishing variants of the same
            category (e.g. ``rule_edit``, ``example_file``,
            ``companion_test``). Stable identifier so a future
            reviewer can grep for "all rule_edit proposals from the
            past month."
        title: One-line description of the proposed change.
        target_path: Repo-relative path the artifact would eventually
            land at. ``None`` when the strategy doesn't map to a file
            edit (e.g. opening a backlog issue).
        body: Markdown describing the proposed change in full.
        evidence: Raw evidence from the insight, reproduced so the
            proposal file is self-contained.
        rationale: Short explanation tying the insight to this
            artifact choice.
    """

    category: str
    confidence: str
    strategy: str
    title: str
    target_path: str | None
    body: str
    evidence: tuple[str, ...] = ()
    rationale: str = ""


# --- Body assembly -----------------------------------------------------


def _wrap_proposal(
    *,
    intent: str,
    suggested_edit: str,
    insight: TraceInsight,
) -> str:
    """Render a standardized markdown body for any promotion proposal."""
    evidence_lines = "\n".join(f"- {e}" for e in insight.evidence) or "_(no structured evidence)_"
    return f"""\
## Insight

{insight.summary.strip() or "_(no summary provided)_"}

## Proposed action

{intent.strip()}

## Suggested edit

{suggested_edit.strip()}

## Confidence

**{insight.confidence}** — {{{_confidence_note(insight.confidence)}}}

## Evidence

{evidence_lines}
"""


def _confidence_note(confidence: str) -> str:
    return {
        "high": "auto-apply after governance review — the signal is structural",
        "medium": "human read recommended; proposed edit is likely but not certain",
        "low": "treat as a triage hint, not a near-committable change",
    }.get(confidence, "unknown confidence level — review carefully")


def _proposal(
    *,
    insight: TraceInsight,
    strategy: str,
    title: str,
    target_path: str | None,
    intent: str,
    suggested_edit: str,
) -> PromotionProposal:
    """Compose a ``PromotionProposal`` while preserving common fields.

    Ensures every variant carries the insight's evidence + rationale
    without each renderer having to thread them manually.
    """
    return PromotionProposal(
        category=insight.category,
        confidence=insight.confidence,
        strategy=strategy,
        title=title,
        target_path=target_path,
        body=_wrap_proposal(intent=intent, suggested_edit=suggested_edit, insight=insight),
        evidence=insight.evidence,
        rationale=insight.proposed_promotion,
    )


# --- Renderers (per category) -----------------------------------------
#
# Each renderer returns a tuple of strategy-distinct proposals. The
# first entry is the most likely fit for typical cases; humans can
# scan the rest if the first doesn't match the team's preferred
# response shape.


def _render_missing_context(insight: TraceInsight) -> tuple[PromotionProposal, ...]:
    return (
        _proposal(
            insight=insight,
            strategy="rule_edit",
            title="Add rule to `coding-task` pack: context the agent was missing",
            target_path=".context-packs/coding-task/rules.md",
            intent=(
                "Append a short rule to the coding-task context pack so "
                "the next agent encountering this kind of task has the "
                "context this run was missing."
            ),
            suggested_edit=(
                "Append a bullet under the appropriate section of "
                "`rules.md`. Phrase it as an instruction, not a story. "
                "Keep it one sentence if possible."
            ),
        ),
        _proposal(
            insight=insight,
            strategy="example_file",
            title="Add a worked example showing the missing context in action",
            target_path=".context-packs/coding-task/examples/missing-context.md",
            intent=(
                "Some context is easier to convey by demonstration than "
                "instruction. Drop a short example file showing a task "
                "of this shape going right — agents pattern-match on "
                "examples even when they skim rules."
            ),
            suggested_edit=(
                "Write a 30–80 line markdown file with a representative "
                "task prompt, the correct opening reads, the missing "
                "context surfaced explicitly, and the resulting commit. "
                "Use a descriptive filename (e.g. `auth-token-rotation."
                "md`)."
            ),
        ),
    )


def _render_missing_rule(insight: TraceInsight) -> tuple[PromotionProposal, ...]:
    return (
        _proposal(
            insight=insight,
            strategy="rule_edit",
            title="Echo encoded rule in the context pack so the agent learns it earlier",
            target_path=".context-packs/coding-task/rules.md",
            intent=(
                "Arch-lint already rejected the agent's change — the "
                "lesson is that the context pack should have warned "
                "the agent first so we don't burn a tool call teaching "
                "it the rule."
            ),
            suggested_edit=(
                "Add a rule bullet to `rules.md` calling out the "
                "package dependency direction (see "
                "`docs/harness/components.md` for the diagram). Consider "
                "whether `PACKAGE_EDGES` in arch_lint.py also needs a "
                "companion comment."
            ),
        ),
        _proposal(
            insight=insight,
            strategy="companion_test",
            title="Lock the rule with a regression test against the violation pattern",
            target_path="libs/cli/tests/unit_tests/test_arch_lint.py",
            intent=(
                "Belt-and-suspenders: the rule fires now, but a future "
                "refactor could quietly drop it. A targeted regression "
                "test that asserts the specific (importer, imported) "
                "pair is rejected makes the rule durable."
            ),
            suggested_edit=(
                "Add a test in `test_arch_lint.py` that constructs the "
                "violating import shape this run produced and asserts "
                "`check_file()` returns a non-empty violation list. "
                "Use the existing test_arch_lint patterns as templates."
            ),
        ),
    )


def _render_missing_tool(insight: TraceInsight) -> tuple[PromotionProposal, ...]:
    return (
        _proposal(
            insight=insight,
            strategy="policy_relaxation",
            title="Widen this task type's allowed_paths to cover the legitimate operation",
            target_path="libs/cli/deepagents_cli/policy.py",
            intent=(
                "If the agent kept trying to write to a path the policy "
                "doesn't allow, and the writes were *legitimate* for "
                "this task type, the policy is wrong — relax it."
            ),
            suggested_edit=(
                "Identify the right preset (bugfix, feature, refactor, "
                "etc.) in `policy.py` and add the missing glob to its "
                "`allowed_paths` tuple. Keep the cap on "
                "`max_files_changed`; loosening one shouldn't loosen "
                "the other."
            ),
        ),
        _proposal(
            insight=insight,
            strategy="tool_proposal",
            title="Open a backlog item for a dedicated tool",
            target_path=None,
            intent=(
                "If the writes were illegitimate-but-attempted-anyway, "
                "the agent is reaching for the wrong primitive. Adding "
                "a tool that does the right thing more cleanly (e.g. a "
                "dedicated migration runner instead of raw SQL writes) "
                "removes the friction at the source."
            ),
            suggested_edit=(
                "File an issue describing the operation the agent kept "
                "attempting, the friction observed, and a sketch of the "
                "tool surface that would make it cleanly. No file edit "
                "is staged here — this is a backlog item, not a quick "
                "fix."
            ),
        ),
    )


def _render_missing_example(insight: TraceInsight) -> tuple[PromotionProposal, ...]:
    return (
        _proposal(
            insight=insight,
            strategy="narrative_example",
            title="Add a worked example: single-shot dump pattern needs the canonical decomposition",
            target_path=".context-packs/coding-task/examples/decomposition.md",
            intent=(
                "The agent produced tens of thousands of reasoning "
                "tokens in a handful of steps without committing to a "
                "solution. A worked example demonstrating the EXPECTED "
                "decomposition — read-then-plan-then-write — gives "
                "future runs something to pattern-match against."
            ),
            suggested_edit=(
                "Write a short markdown file showing the canonical "
                "shape: task prompt, opening reads, plan, first write, "
                "verification. Keep it under 100 lines. Reference the "
                "task kind (e.g. `data-reshape`) in the filename."
            ),
        ),
        _proposal(
            insight=insight,
            strategy="golden_test",
            title="Add a golden-test fixture that locks the expected decomposition",
            target_path="tests/golden/decomposition_fixture.json",
            intent=(
                "Examples teach by demonstration; golden tests teach by "
                "regression. If the agent's behavior should match a "
                "specific shape on a known task, capture that shape as "
                "a fixture so subsequent runs visibly drift from it."
            ),
            suggested_edit=(
                "Save the trial's expected step shape (or a sanitized "
                "version) as a JSON fixture under `tests/golden/`. Add "
                "a test that loads the fixture and asserts the agent's "
                "trajectory keys (step count, tool sequence) match. "
                "Heavier setup than the example file; locks behavior "
                "harder."
            ),
        ),
    )


def _render_model_capability_limit(insight: TraceInsight) -> tuple[PromotionProposal, ...]:
    # Single-variant intentionally — model_capability_limit insights
    # are usually low-confidence (provider blip / silent giveup) and
    # multiple proposals just add operator noise.
    return (
        _proposal(
            insight=insight,
            strategy="known_limits_note",
            title="Known limit: minimal agent engagement before failure",
            target_path="docs/harness/known-limits.md",
            intent=(
                "No productive activity before failure usually means an "
                "API hang or the model giving up — not a harness gap. "
                "Record as a known limit so it doesn't keep showing up "
                "in lesson-promotion triage."
            ),
            suggested_edit=(
                "Append to `docs/harness/known-limits.md` under the "
                "relevant section (create the file if it doesn't "
                "exist). Don't attempt to 'fix' with a harness change."
            ),
        ),
    )


_RENDERERS: dict[str, Callable[[TraceInsight], tuple[PromotionProposal, ...]]] = {
    "missing_context": _render_missing_context,
    "missing_rule": _render_missing_rule,
    "missing_tool": _render_missing_tool,
    "missing_example": _render_missing_example,
    "model_capability_limit": _render_model_capability_limit,
}


# --- Public API --------------------------------------------------------


def propose(insight: TraceInsight) -> tuple[PromotionProposal, ...]:
    """Render strategy-distinct ``PromotionProposal``s for ``insight``.

    Falls back to a single generic "needs human review" proposal for
    unknown categories so new categories don't silently drop.
    """
    renderer = _RENDERERS.get(insight.category)
    if renderer is None:
        return (
            _proposal(
                insight=insight,
                strategy="manual_review",
                title=f"Uncategorized insight: {insight.category}",
                target_path=None,
                intent=(
                    "The trace analyzer produced an insight category "
                    "this renderer doesn't know about yet. Review the "
                    "evidence and decide what durable artifact (if any) "
                    "should capture the lesson."
                ),
                suggested_edit=(
                    "Add a renderer in "
                    "`libs/cli/deepagents_cli/promote_lesson.py` for "
                    f"category {insight.category!r} once the pattern "
                    "is understood."
                ),
            ),
        )
    return renderer(insight)


def stage_proposal(
    proposal: PromotionProposal,
    *,
    harness_dir: str | Path,
    trial_id: str | None = None,
) -> Path:
    """Write a single proposal to ``.harness/pending-promotions/``.

    Filename includes the strategy tag so multi-variant runs from the
    same trial don't collide:
    ``<timestamp>-<category>-<strategy>-<trial>.md``.
    """
    base = Path(harness_dir) / "pending-promotions"
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tag = (trial_id or "run").replace("/", "_")
    strategy_seg = proposal.strategy.replace("/", "_") or "default"
    path = base / f"{stamp}-{proposal.category}-{strategy_seg}-{tag}.md"

    target_text = (
        "`" + proposal.target_path + "`"
        if proposal.target_path
        else "_(no single target; architectural decision)_"
    )
    header = f"""\
# Promotion proposal

**Category:** `{proposal.category}`  |  **Strategy:** `{proposal.strategy}`  |  **Confidence:** `{proposal.confidence}`  |  **Trial:** `{tag}`  |  **Staged:** `{stamp}`

**Target path:** {target_text}

**Rationale:** {proposal.rationale or '_(none)_'}

---

"""
    path.write_text(header + proposal.body + "\n", encoding="utf-8")
    return path


def propose_and_stage(
    insight: TraceInsight,
    *,
    harness_dir: str | Path,
    trial_id: str | None = None,
) -> tuple[tuple[PromotionProposal, ...], tuple[Path, ...]]:
    """Render every variant for ``insight`` and stage each.

    Returns parallel tuples (proposals, staged_paths) so callers can
    correlate proposals to file locations without re-deriving them.
    """
    proposals = propose(insight)
    staged: list[Path] = []
    for proposal in proposals:
        try:
            staged.append(
                stage_proposal(proposal, harness_dir=harness_dir, trial_id=trial_id),
            )
        except OSError as exc:
            logger.warning(
                "stage_proposal failed for strategy=%s: %s",
                proposal.strategy,
                exc,
            )
    return proposals, tuple(staged)


# --- CLI-ish entry point -----------------------------------------------


def promote_from_trial(
    trial_dir: str | Path,
    *,
    harness_dir: str | Path | None = None,
) -> tuple[tuple[PromotionProposal, ...], tuple[Path, ...]] | None:
    """End-to-end: read a trial, analyze, render every variant, stage all.

    Returns ``(proposals, staged_paths)`` or ``None`` when there's
    nothing promotable (missing trial dir, low-confidence model_limit).

    The two tuples are parallel and have the same length: one staged
    path per rendered proposal. A renderer that emits two variants
    yields two staged files; the human picks among them.
    """
    from deepagents_cli.trace_analyzer import analyze_trial

    trial = Path(trial_dir)
    if not trial.is_dir():
        logger.warning("promote_from_trial: %s is not a directory", trial)
        return None

    insight = analyze_trial(trial)
    if insight.category == "model_capability_limit" and insight.confidence == "low":
        logger.debug("Skipping low-confidence model_capability_limit insight")
        return None

    hd = Path(harness_dir) if harness_dir else trial / ".harness"
    return propose_and_stage(insight, harness_dir=hd, trial_id=trial.name)


__all__ = [
    "PromotionProposal",
    "promote_from_trial",
    "propose",
    "propose_and_stage",
    "stage_proposal",
]
