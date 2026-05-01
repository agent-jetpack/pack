"""Tests for the ``deepagents harness`` subcommand wiring (sharp-edge 3)."""

from __future__ import annotations

import argparse
import json
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from deepagents_cli.harness_cli import (
    execute_harness_command,
    setup_harness_parser,
)


def _build_namespace(**kwargs: Any) -> argparse.Namespace:
    """Compose an argparse.Namespace the dispatch helper can consume."""
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _root_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    setup_harness_parser(sub, add_output_args=lambda _p: None)
    return parser


def test_harness_parser_registers_check_subcommand() -> None:
    parser = _root_parser()
    args = parser.parse_args(["harness", "check", "."])
    assert args.command == "harness"
    assert args.harness_command == "check"
    assert args.repo_root == "."


def test_harness_parser_registers_discover_subcommand() -> None:
    parser = _root_parser()
    args = parser.parse_args(["harness", "discover", "/some/path", "--no-write"])
    assert args.harness_command == "discover"
    assert args.repo_root == "/some/path"
    assert args.no_write is True


def test_harness_parser_registers_promote_lesson() -> None:
    parser = _root_parser()
    args = parser.parse_args(
        ["harness", "promote-lesson", "/tmp/trial", "--harness-dir", "/tmp/h"]
    )
    assert args.harness_command == "promote-lesson"
    assert args.trial_dir == "/tmp/trial"
    assert args.harness_dir == "/tmp/h"


def test_harness_parser_ratchet_score_and_seed() -> None:
    parser = _root_parser()
    args = parser.parse_args(["harness", "ratchet", "score", "/repo"])
    assert args.harness_command == "ratchet"
    assert args.ratchet_command == "score"
    args = parser.parse_args(["harness", "ratchet", "seed", "/repo"])
    assert args.ratchet_command == "seed"


def test_harness_parser_check_json_flag() -> None:
    parser = _root_parser()
    args = parser.parse_args(["harness", "check", ".", "--json"])
    assert args.json is True


# ---------------------------------------------------------------------------
# execute_harness_command — dispatch + return codes
# ---------------------------------------------------------------------------


def test_dispatch_unknown_subcommand_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    args = _build_namespace(harness_command=None)
    code = execute_harness_command(args)
    assert code == 2
    err = capsys.readouterr().err
    assert "Usage: deepagents harness" in err


def test_check_returns_zero_on_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    args = _build_namespace(
        harness_command="check",
        repo_root=str(repo),
        checks=["arch-lint"],  # in-process, deterministic on empty repo
        json=False,
    )
    code = execute_harness_command(args)
    assert code == 0
    out = capsys.readouterr().out
    assert "status:" in out


def test_check_json_emits_parseable_payload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    args = _build_namespace(
        harness_command="check",
        repo_root=str(repo),
        checks=["arch-lint"],
        json=True,
    )
    execute_harness_command(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "status" in payload
    assert "checks" in payload


def test_check_returns_one_on_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Plant an arch violation and confirm the exit code flips."""
    repo = tmp_path / "r"
    target = repo / "libs" / "deepagents" / "deepagents" / "bad.py"
    target.parent.mkdir(parents=True)
    target.write_text("from deepagents_cli.policy import TaskPolicy\n")
    args = _build_namespace(
        harness_command="check",
        repo_root=str(repo),
        checks=["arch-lint"],
        json=False,
    )
    code = execute_harness_command(args)
    assert code == 1


def test_discover_writes_when_default(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "README.md").write_text("# r\n")
    args = _build_namespace(
        harness_command="discover",
        repo_root=str(repo),
        no_write=False,
    )
    code = execute_harness_command(args)
    assert code == 0
    assert (repo / "docs" / "generated").is_dir()


def test_discover_skips_writes_with_no_write_flag(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "README.md").write_text("# r\n")
    args = _build_namespace(
        harness_command="discover",
        repo_root=str(repo),
        no_write=True,
    )
    execute_harness_command(args)
    assert not (repo / "docs" / "generated").exists()


def test_promote_lesson_with_missing_trial(capsys: pytest.CaptureFixture[str]) -> None:
    args = _build_namespace(
        harness_command="promote-lesson",
        trial_dir="/does/not/exist",
        harness_dir=None,
    )
    code = execute_harness_command(args)
    assert code == 0
    out = capsys.readouterr().out
    assert "No promotable lesson" in out


def test_promote_lesson_stages_proposal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Build a fake arch-rejection trial so promote_from_trial returns
    # a real proposal.
    trial = tmp_path / "trial"
    (trial / "agent").mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps({"verifier_result": {"rewards": {"reward": 0.0}}})
    )
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps(
            {
                "steps": [{"step_id": 1}],
                "final_metrics": {"total_prompt_tokens": 1000, "total_completion_tokens": 200},
            }
        )
        + "\nArch-lint violation\n"
    )
    args = _build_namespace(
        harness_command="promote-lesson",
        trial_dir=str(trial),
        harness_dir=str(tmp_path / ".harness"),
    )
    code = execute_harness_command(args)
    assert code == 0
    out = capsys.readouterr().out
    # Multi-candidate output: "Staged 2 missing_rule proposal(s) ..."
    # plus one bullet per staged variant.
    assert "Staged" in out
    assert "missing_rule" in out
    assert "[rule_edit]" in out
    assert "[companion_test]" in out


def test_ratchet_score_no_state(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    args = _build_namespace(
        harness_command="ratchet",
        ratchet_command="score",
        repo_root=str(repo),
        json=False,
    )
    code = execute_harness_command(args)
    assert code == 0
    out = capsys.readouterr().out
    assert "Ratchet at:" in out
    assert "No quality snapshots" in out


def test_ratchet_score_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    args = _build_namespace(
        harness_command="ratchet",
        ratchet_command="score",
        repo_root=str(repo),
        json=True,
    )
    execute_harness_command(args)
    payload = json.loads(capsys.readouterr().out)
    assert "harness_dir" in payload
    assert "violation_count" in payload


def test_ratchet_seed_records_violations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Plant an arch violation, run seed, confirm it's persisted."""
    repo = tmp_path / "r"
    target = repo / "libs" / "deepagents" / "deepagents" / "bad.py"
    target.parent.mkdir(parents=True)
    target.write_text("from deepagents_cli.policy import TaskPolicy\n")
    args = _build_namespace(
        harness_command="ratchet",
        ratchet_command="seed",
        repo_root=str(repo),
    )
    code = execute_harness_command(args)
    assert code == 0
    out = capsys.readouterr().out
    assert "Seeded" in out
    # Check the file actually exists with at least one entry
    violations_path = repo / ".harness" / "violations.json"
    assert violations_path.is_file()
    payload = json.loads(violations_path.read_text())
    assert len(payload) >= 1


def test_ratchet_unknown_subcommand_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    args = _build_namespace(
        harness_command="ratchet",
        ratchet_command=None,
    )
    code = execute_harness_command(args)
    assert code == 2
    assert "Usage:" in capsys.readouterr().err
