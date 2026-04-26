"""Tests for business-rule checker (M5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepagents_cli.business_rule_checker import (
    Invariant,
    InvariantViolation,
    MATCHER_TYPES,
    SEVERITIES,
    load_invariants,
    run_business_rules,
    run_invariants,
)


# ---------------------------------------------------------------------------
# Invariant validation
# ---------------------------------------------------------------------------


def test_invariant_rejects_unknown_severity() -> None:
    with pytest.raises(ValueError, match="severity"):
        Invariant(id="x", description="d", severity="maybe")


def test_invariant_rejects_unknown_matcher() -> None:
    with pytest.raises(ValueError, match="matcher"):
        Invariant(id="x", description="d", matcher="mystery")


def test_invariant_severities_cover_expected_set() -> None:
    assert SEVERITIES == frozenset({"block", "warn", "info"})


def test_matcher_types_cover_expected_set() -> None:
    assert MATCHER_TYPES == frozenset(
        {
            "regex",
            "absent_regex",
            "file_exists",
            "json_schema",
            "python_ast",
            "command_must_pass",
            "file_contains_all",
        }
    )


# ---------------------------------------------------------------------------
# load_invariants
# ---------------------------------------------------------------------------


def _write_pack(tmp_path: Path, yaml_body: str) -> Path:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "checks.yaml").write_text(yaml_body)
    return pack


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert load_invariants(tmp_path / "empty") == ()


def test_load_empty_yaml_returns_empty(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path, "")
    assert load_invariants(pack) == ()


def test_load_shape_without_invariants_key_returns_empty(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path, "name: pack\n")
    assert load_invariants(pack) == ()


def test_load_single_regex_invariant(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path,
        """\
invariants:
  - id: has_readme
    description: must have a README
    severity: block
    matcher: regex
    pattern: '^# '
    paths:
      - 'README.md'
""",
    )
    invariants = load_invariants(pack)
    assert len(invariants) == 1
    inv = invariants[0]
    assert inv.id == "has_readme"
    assert inv.matcher == "regex"
    assert inv.pattern == "^# "
    assert inv.paths == ("README.md",)


def test_load_skips_malformed_entries(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path,
        """\
invariants:
  - description: missing id
    severity: block
    matcher: regex
  - id: good_one
    description: real
    severity: warn
    matcher: absent_regex
    pattern: 'x'
""",
    )
    invariants = load_invariants(pack)
    ids = [i.id for i in invariants]
    assert ids == ["good_one"]


def test_load_rejects_invalid_severity_gracefully(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path,
        """\
invariants:
  - id: bad_severity
    description: x
    severity: whatever
    matcher: regex
    pattern: 'y'
""",
    )
    # Invalid severity raises at Invariant construction — caught and
    # logged as "skipping malformed."
    assert load_invariants(pack) == ()


# ---------------------------------------------------------------------------
# run_invariants: regex
# ---------------------------------------------------------------------------


def _setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    return repo


def test_regex_matcher_passes_when_pattern_found(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "README.md").write_text("# Project\n\nHello.")
    inv = Invariant(
        id="has_heading",
        description="README needs H1",
        matcher="regex",
        pattern="^# ",
        paths=("README.md",),
    )
    assert run_invariants((inv,), repo) == []


def test_regex_matcher_fails_when_pattern_missing(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "README.md").write_text("no heading here\n")
    inv = Invariant(
        id="has_heading",
        description="README needs H1",
        matcher="regex",
        pattern="^# ",
        paths=("README.md",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert violations[0].invariant_id == "has_heading"


def test_regex_matcher_with_no_paths_matches_returns_empty(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="x",
        description="x",
        matcher="regex",
        pattern="a",
        paths=("nonexistent/*.py",),
    )
    # No files match the glob → vacuously nothing to check.
    assert run_invariants((inv,), repo) == []


# ---------------------------------------------------------------------------
# run_invariants: absent_regex
# ---------------------------------------------------------------------------


def test_absent_regex_matcher_passes_when_pattern_absent(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "a.py").write_text("def f(): return 1\n")
    inv = Invariant(
        id="no_print",
        description="no prints",
        matcher="absent_regex",
        pattern=r"\bprint\(",
        paths=("*.py",),
    )
    assert run_invariants((inv,), repo) == []


def test_absent_regex_matcher_fails_on_hit(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "a.py").write_text("def f():\n    print('hi')\n")
    (repo / "b.py").write_text("def g(): return 1\n")
    inv = Invariant(
        id="no_print",
        description="no prints",
        matcher="absent_regex",
        pattern=r"\bprint\(",
        paths=("*.py",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert violations[0].file == "a.py"
    assert violations[0].line is not None


def test_absent_regex_reports_once_per_file(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "a.py").write_text("print(1)\nprint(2)\nprint(3)\n")
    inv = Invariant(
        id="no_print",
        description="x",
        matcher="absent_regex",
        pattern=r"\bprint\(",
        paths=("*.py",),
    )
    violations = run_invariants((inv,), repo)
    # One report for a.py even though it has 3 hits.
    assert len(violations) == 1


# ---------------------------------------------------------------------------
# run_invariants: file_exists
# ---------------------------------------------------------------------------


def test_file_exists_passes_when_companion_present(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "mod.py").write_text("x = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_mod.py").write_text("def test_x(): pass\n")
    inv = Invariant(
        id="has_test",
        description="companion test",
        matcher="file_exists",
        target="tests/test_{stem}.py",
        paths=("src/*.py",),
    )
    assert run_invariants((inv,), repo) == []


def test_file_exists_fails_when_companion_missing(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "mod.py").write_text("x = 1\n")
    inv = Invariant(
        id="has_test",
        description="companion test",
        severity="warn",
        matcher="file_exists",
        target="tests/test_{stem}.py",
        paths=("src/*.py",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert violations[0].severity == "warn"
    assert "test_mod.py" in violations[0].detail


# ---------------------------------------------------------------------------
# Edge cases in run_invariants
# ---------------------------------------------------------------------------


def test_missing_pattern_in_regex_matcher_reports_violation(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="bad",
        description="x",
        matcher="regex",
        # pattern intentionally empty — misconfigured rule
        paths=("*.py",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "pattern" in violations[0].detail


def test_missing_target_in_file_exists_reports_violation(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="bad",
        description="x",
        matcher="file_exists",
        paths=("*.py",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "target" in violations[0].detail


# ---------------------------------------------------------------------------
# run_business_rules — full pack scan
# ---------------------------------------------------------------------------


def test_run_business_rules_no_packs_dir(tmp_path: Path) -> None:
    status, summary, violations = run_business_rules(tmp_path)
    assert status == "not_configured"
    assert violations == []


def test_run_business_rules_pack_with_no_checks(tmp_path: Path) -> None:
    (tmp_path / ".context-packs" / "empty-pack").mkdir(parents=True)
    status, summary, violations = run_business_rules(tmp_path)
    assert status == "not_configured"
    assert violations == []


def test_run_business_rules_pass_when_all_invariants_satisfied(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    pack = repo / ".context-packs" / "p"
    pack.mkdir(parents=True)
    (pack / "checks.yaml").write_text(
        """\
invariants:
  - id: no_secrets
    description: no placeholder
    severity: block
    matcher: absent_regex
    pattern: 'SECRET_PLACEHOLDER'
    paths:
      - '*.py'
"""
    )
    (repo / "a.py").write_text("x = 1\n")
    status, summary, violations = run_business_rules(repo)
    assert status == "pass"
    assert violations == []


def test_run_business_rules_fail_on_block_severity(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    pack = repo / ".context-packs" / "p"
    pack.mkdir(parents=True)
    (pack / "checks.yaml").write_text(
        """\
invariants:
  - id: no_todos
    description: no TODO left
    severity: block
    matcher: absent_regex
    pattern: 'TODO'
    paths:
      - '*.py'
"""
    )
    (repo / "a.py").write_text("# TODO: fix this\n")
    status, summary, violations = run_business_rules(repo)
    assert status == "fail"
    assert any(v.invariant_id == "no_todos" for v in violations)


# ---------------------------------------------------------------------------
# json_schema matcher
# ---------------------------------------------------------------------------


def test_json_schema_passes_when_data_valid(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "user.json").write_text('{"name": "Ada", "age": 36}')
    inv = Invariant(
        id="user_shape",
        description="user objects must have name+age",
        matcher="json_schema",
        paths=("*.json",),
        schema={
            "type": "object",
            "required": ["name", "age"],
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        },
    )
    assert run_invariants((inv,), repo) == []


def test_json_schema_fails_on_missing_required(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "user.json").write_text('{"name": "Ada"}')
    inv = Invariant(
        id="user_shape",
        description="d",
        matcher="json_schema",
        paths=("*.json",),
        schema={"type": "object", "required": ["age"]},
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "age" in violations[0].detail


def test_json_schema_fails_on_wrong_type(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "user.json").write_text('{"name": "Ada", "age": "thirty-six"}')
    inv = Invariant(
        id="user_shape",
        description="d",
        matcher="json_schema",
        paths=("*.json",),
        schema={
            "type": "object",
            "properties": {"age": {"type": "integer"}},
        },
    )
    violations = run_invariants((inv,), repo)
    assert any("integer" in v.detail for v in violations)


def test_json_schema_handles_unparseable_file(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "broken.json").write_text("{not json")
    inv = Invariant(
        id="any",
        description="d",
        matcher="json_schema",
        paths=("*.json",),
        schema={"type": "object"},
    )
    violations = run_invariants((inv,), repo)
    assert any("could not parse as JSON" in v.detail for v in violations)


def test_json_schema_requires_non_empty_schema(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="bad",
        description="d",
        matcher="json_schema",
        paths=("*.json",),
        # No schema declared
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "schema" in violations[0].detail


# ---------------------------------------------------------------------------
# python_ast matcher
# ---------------------------------------------------------------------------


def test_python_ast_finds_print_calls(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "noisy.py").write_text("def f(x):\n    print(x)\n    return x\n")
    (repo / "quiet.py").write_text("def g(): return 1\n")
    inv = Invariant(
        id="no_print",
        description="agents shouldn't print",
        severity="warn",
        matcher="python_ast",
        ast_pattern="node:Call?func.id=print",
        paths=("*.py",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert violations[0].file == "noisy.py"
    assert violations[0].line is not None


def test_python_ast_detects_import_of_specific_module(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "uses_os.py").write_text("import os\nx = os.environ\n")
    (repo / "clean.py").write_text("import sys\n")
    inv = Invariant(
        id="no_os",
        description="d",
        severity="info",
        matcher="python_ast",
        ast_pattern="node:Import?names=os",
        paths=("*.py",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert violations[0].file == "uses_os.py"


def test_python_ast_unknown_node_type_reports(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="bad",
        description="d",
        matcher="python_ast",
        ast_pattern="node:NotARealNode",
        paths=("*.py",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "unknown ast node" in violations[0].detail


def test_python_ast_no_pattern_reports(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="bad",
        description="d",
        matcher="python_ast",
        paths=("*.py",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "ast_pattern" in violations[0].detail


def test_python_ast_skips_unparseable_file(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "broken.py").write_text("def f(:\n  bogus\n")
    inv = Invariant(
        id="any",
        description="d",
        matcher="python_ast",
        ast_pattern="node:Call",
        paths=("*.py",),
    )
    # Unparseable source is silently skipped — not an invariant violation.
    assert run_invariants((inv,), repo) == []


# ---------------------------------------------------------------------------
# command_must_pass matcher
# ---------------------------------------------------------------------------


def test_command_passes_returns_no_violations(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="true_passes",
        description="d",
        matcher="command_must_pass",
        command="true",
    )
    assert run_invariants((inv,), repo) == []


def test_command_failure_produces_violation(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="false_fails",
        description="d",
        matcher="command_must_pass",
        command="false",
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "exit=1" in violations[0].detail


def test_command_missing_executable(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="ghost",
        description="d",
        matcher="command_must_pass",
        command="this-binary-does-not-exist-xyz",
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "executable not found" in violations[0].detail


def test_command_unparseable_shell_quoting(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="bad_quoting",
        description="d",
        matcher="command_must_pass",
        command='echo "unterminated',
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "shlex" in violations[0].detail.lower() or "could not parse" in violations[0].detail


def test_command_required(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="bad",
        description="d",
        matcher="command_must_pass",
        # no command set
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "command" in violations[0].detail


# ---------------------------------------------------------------------------
# file_contains_all matcher
# ---------------------------------------------------------------------------


def test_file_contains_all_passes(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "config.toml").write_text("[project]\nname = 'x'\nversion = '1.0'\n")
    inv = Invariant(
        id="has_required_keys",
        description="d",
        matcher="file_contains_all",
        substrings=("name", "version"),
        paths=("*.toml",),
    )
    assert run_invariants((inv,), repo) == []


def test_file_contains_all_flags_missing(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / "config.toml").write_text("[project]\nname = 'x'\n")
    inv = Invariant(
        id="has_required_keys",
        description="d",
        matcher="file_contains_all",
        substrings=("name", "version", "license"),
        paths=("*.toml",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "version" in violations[0].detail
    assert "license" in violations[0].detail


def test_file_contains_all_requires_substrings(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    inv = Invariant(
        id="bad",
        description="d",
        matcher="file_contains_all",
        paths=("*.toml",),
    )
    violations = run_invariants((inv,), repo)
    assert len(violations) == 1
    assert "substrings" in violations[0].detail


def test_run_business_rules_warn_severity_does_not_fail(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    pack = repo / ".context-packs" / "p"
    pack.mkdir(parents=True)
    (pack / "checks.yaml").write_text(
        """\
invariants:
  - id: prefer_logger
    description: prefer logger over print
    severity: warn
    matcher: absent_regex
    pattern: 'print\\('
    paths:
      - '*.py'
"""
    )
    (repo / "a.py").write_text("print('hi')\n")
    status, summary, violations = run_business_rules(repo)
    assert status == "pass"  # warn doesn't flip status
    assert len(violations) == 1
    assert violations[0].severity == "warn"
