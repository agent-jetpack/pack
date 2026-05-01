"""Argparse + dispatch wiring for the ``deepagents harness`` subcommand.

Sharp-edge 3 from the second external review. The harness components
have always had clean Python APIs (``run_checks``, ``discover``,
``promote_from_trial``, ``Ratchet``) but no CLI surface. This module
wires four subcommands so operators can drive the harness from a
shell:

::

    deepagents harness check [--json] [REPO]
    deepagents harness discover [--no-write] [REPO]
    deepagents harness promote-lesson [--harness-dir DIR] TRIAL_DIR
    deepagents harness ratchet score [--json] [REPO]
    deepagents harness ratchet seed [REPO]

The functions live here rather than in ``main.py`` so the heavy
imports (yaml, subprocess-based check runners) stay lazy — running
``deepagents help`` shouldn't import the check pipeline.

Argparse setup is split into ``setup_harness_parser(subparsers)`` so
``main.py`` can call it the same way it calls ``setup_skills_parser``
and ``setup_deploy_parsers``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --- Argparse wiring -----------------------------------------------------


def setup_harness_parser(
    subparsers: argparse._SubParsersAction,
    *,
    add_output_args: Any = None,
) -> None:
    """Register the ``harness`` subcommand and its children.

    Args:
        subparsers: Root subparser action returned by
            ``parser.add_subparsers(...)``.
        add_output_args: Callable used elsewhere in ``main.py`` to
            attach the standard ``--json`` flag. Optional — when not
            supplied the subcommands still work but won't share the
            project's output convention.
    """
    harness_parser = subparsers.add_parser(
        "harness",
        help="Run agent-harness operations (check, discover, promote-lesson, ratchet)",
    )
    if add_output_args is not None:
        add_output_args(harness_parser)
    harness_sub = harness_parser.add_subparsers(dest="harness_command")

    check_p = harness_sub.add_parser(
        "check",
        help="Run all configured harness checks (arch-lint, business-rules, tests, lint, typecheck, docs-lint)",
    )
    check_p.add_argument("repo_root", nargs="?", default=".", help="Repo root (default: cwd)")
    check_p.add_argument(
        "--checks",
        nargs="*",
        help="Specific check names to run (default: every registered check)",
    )
    check_p.add_argument("--json", action="store_true", help="Emit JSON instead of human text")

    discover_p = harness_sub.add_parser(
        "discover",
        help="Scan a repo and emit codebase/package/domain/risk reports + skeleton context packs",
    )
    discover_p.add_argument("repo_root", nargs="?", default=".", help="Repo to scan (default: cwd)")
    discover_p.add_argument(
        "--no-write",
        action="store_true",
        help="Print findings but don't write reports or pack skeletons",
    )

    promote_p = harness_sub.add_parser(
        "promote-lesson",
        help="Stage a lesson-promotion proposal from a failed trial directory",
    )
    promote_p.add_argument("trial_dir", help="Trial directory to analyze")
    promote_p.add_argument(
        "--harness-dir",
        help="Where to stage the proposal (default: <trial_dir>/.harness)",
    )

    ratchet_p = harness_sub.add_parser(
        "ratchet",
        help="Inspect or seed the .harness/ ratchet state",
    )
    ratchet_sub = ratchet_p.add_subparsers(dest="ratchet_command")

    score_p = ratchet_sub.add_parser("score", help="Show the latest quality snapshot")
    score_p.add_argument("repo_root", nargs="?", default=".", help="Repo root (default: cwd)")
    score_p.add_argument("--json", action="store_true", help="Emit JSON instead of human text")

    seed_p = ratchet_sub.add_parser(
        "seed",
        help="Seed .harness/violations.json with the current arch-lint findings",
    )
    seed_p.add_argument("repo_root", nargs="?", default=".", help="Repo root (default: cwd)")


# --- Dispatch ------------------------------------------------------------


def execute_harness_command(args: argparse.Namespace) -> int:
    """Route ``deepagents harness ...`` to the right handler.

    Returns the exit code the CLI should pass to ``sys.exit``. Each
    sub-handler also prints its own user-facing output; this function
    only owns return-code semantics.
    """
    cmd = getattr(args, "harness_command", None)
    if cmd == "check":
        return _cmd_check(args)
    if cmd == "discover":
        return _cmd_discover(args)
    if cmd == "promote-lesson":
        return _cmd_promote_lesson(args)
    if cmd == "ratchet":
        return _cmd_ratchet(args)
    print(  # noqa: T201
        "Usage: deepagents harness {check,discover,promote-lesson,ratchet} ...",
        file=sys.stderr,
    )
    return 2


def _cmd_check(args: argparse.Namespace) -> int:
    from deepagents_cli.harness_check import run_checks

    report = run_checks(args.repo_root, checks=args.checks)
    if getattr(args, "json", False):
        print(report.to_json())  # noqa: T201
    else:
        print(report.to_human())  # noqa: T201
    return 0 if report.status != "fail" else 1


def _cmd_discover(args: argparse.Namespace) -> int:
    from deepagents_cli.harness_discover import discover

    write = not getattr(args, "no_write", False)
    result = discover(args.repo_root, write_outputs=write)

    print(  # noqa: T201
        f"Scanned {result.total_files:,} files ({result.total_loc:,} LOC) "
        f"in {len(result.packages)} package(s).",
    )
    if write:
        out = Path(args.repo_root) / "docs" / "generated"
        print(f"  Reports written to {out}")  # noqa: T201
        proposed = Path(args.repo_root) / ".context-packs" / "proposed"
        if proposed.is_dir():
            print(f"  Proposed packs at {proposed}")  # noqa: T201
    else:
        print("  --no-write: nothing written.")  # noqa: T201
    return 0


def _cmd_promote_lesson(args: argparse.Namespace) -> int:
    from deepagents_cli.promote_lesson import promote_from_trial

    result = promote_from_trial(
        args.trial_dir,
        harness_dir=args.harness_dir,
    )
    if result is None:
        print(  # noqa: T201
            "No promotable lesson — trial directory missing or insight "
            "below promotion threshold.",
        )
        return 0
    proposals, staged_paths = result
    if not proposals:
        print("No proposals produced (renderer returned empty).")  # noqa: T201
        return 0
    category = proposals[0].category
    confidence = proposals[0].confidence
    print(  # noqa: T201
        f"Staged {len(proposals)} {category} proposal(s) "
        f"(confidence={confidence}). Pick one or merge:",
    )
    for proposal, path in zip(proposals, staged_paths, strict=False):
        print(f"  [{proposal.strategy}] {path}")  # noqa: T201
    return 0


def _cmd_ratchet(args: argparse.Namespace) -> int:
    cmd = getattr(args, "ratchet_command", None)
    if cmd == "score":
        return _cmd_ratchet_score(args)
    if cmd == "seed":
        return _cmd_ratchet_seed(args)
    print(  # noqa: T201
        "Usage: deepagents harness ratchet {score,seed} [REPO]",
        file=sys.stderr,
    )
    return 2


def _cmd_ratchet_score(args: argparse.Namespace) -> int:
    from deepagents_cli.ratchet import Ratchet

    harness_dir = Path(args.repo_root) / ".harness"
    ratchet = Ratchet(harness_dir=harness_dir)
    history = ratchet.load_quality_history()
    violations = ratchet.load_violations()

    if not history:
        snapshot: dict[str, Any] = {"snapshots": "none recorded"}
    else:
        latest = history[-1]
        snapshot = {
            "taken_at": latest.taken_at,
            "total_loc": latest.total_loc,
            "large_files": latest.large_files,
            "forbidden_imports": latest.forbidden_imports,
            "test_coverage_pct": latest.test_coverage_pct,
            "notes": latest.notes,
            "snapshots_recorded": len(history),
        }

    payload = {
        "harness_dir": str(harness_dir),
        "violation_count": len(violations),
        "latest_snapshot": snapshot,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))  # noqa: T201
    else:
        print(f"Ratchet at: {payload['harness_dir']}")  # noqa: T201
        print(f"Tracked violations: {payload['violation_count']}")  # noqa: T201
        if isinstance(snapshot, dict) and "snapshots_recorded" in snapshot:
            print(  # noqa: T201
                f"Latest snapshot ({snapshot['taken_at']}): "
                f"{snapshot['total_loc']:,} LOC, "
                f"{snapshot['large_files']} large files, "
                f"{snapshot['forbidden_imports']} forbidden imports.",
            )
        else:
            print("No quality snapshots yet — run a check pipeline first.")  # noqa: T201
    return 0


def _cmd_ratchet_seed(args: argparse.Namespace) -> int:
    """Seed .harness/violations.json with the current arch-lint state.

    Establishes "old debt" baseline. After seeding, only NEW
    violations introduced by future agent runs will block.
    """
    from deepagents_cli.arch_lint import check_file
    from deepagents_cli.harness_check import _iter_python_files
    from deepagents_cli.ratchet import Ratchet

    repo_root = Path(args.repo_root).resolve()
    ratchet = Ratchet(harness_dir=repo_root / ".harness")

    seeded = 0
    for path in _iter_python_files(repo_root):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(repo_root))
        for v in check_file(rel, source):
            subject = f"{v.importer}:{v.imported}"
            _, is_new = ratchet.record(
                rule="arch.forbidden_import",
                subject=subject,
                reason=f"{rel}: {v.summary()}",
            )
            if is_new:
                seeded += 1

    print(  # noqa: T201
        f"Seeded {seeded} new arch violation(s) into "
        f"{ratchet.violations_path}. These are now treated as tolerated debt; "
        "future runs only block on fresh violations.",
    )
    return 0


__all__ = [
    "execute_harness_command",
    "setup_harness_parser",
]
