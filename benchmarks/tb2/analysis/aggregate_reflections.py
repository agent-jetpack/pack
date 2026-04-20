#!/usr/bin/env python3
"""Aggregate per-task reflection files into a single run-level summary.

Reads every ``reflection.md`` file under a Harbor job directory and produces
one consolidated markdown report grouped by failure mode, with counts,
task lists, and sections for each category. Intended to run after
``reflect_on_run.py`` has processed all trials.

Usage:
    python aggregate_reflections.py <harbor-job-dir>
    python aggregate_reflections.py /tmp/pack-harbor-run-008/pack-run-008
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path


MODE_PATTERN = re.compile(
    r"\*\*Failure mode \(heuristic\):\*\*\s*(\S+)", re.IGNORECASE
)
OUTCOME_PATTERN = re.compile(r"\*\*Outcome:\*\*\s*(\S+)", re.IGNORECASE)
STEPS_PATTERN = re.compile(r"\*\*Steps:\*\*\s*(\d+)")
TOKENS_PATTERN = re.compile(r"\*\*Tokens:\*\*\s*([\d,]+)\s*in\s*/\s*([\d,]+)\s*out")


def _parse_reflection(path: Path) -> dict:
    text = path.read_text()
    task = path.parent.name.rsplit("__", 1)[0]
    outcome = (m.group(1) if (m := OUTCOME_PATTERN.search(text)) else "UNKNOWN")
    mode = (m.group(1) if (m := MODE_PATTERN.search(text)) else "unknown")
    steps = int(m.group(1)) if (m := STEPS_PATTERN.search(text)) else 0
    tokens_in = tokens_out = 0
    if m := TOKENS_PATTERN.search(text):
        tokens_in = int(m.group(1).replace(",", ""))
        tokens_out = int(m.group(2).replace(",", ""))
    return {
        "task": task,
        "outcome": outcome,
        "mode": mode,
        "steps": steps,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "path": str(path),
    }


def aggregate(job_dir: Path) -> str:
    reflections = sorted(job_dir.glob("*/reflection.md"))
    if not reflections:
        return f"No reflections found under {job_dir}\n"

    parsed = [_parse_reflection(p) for p in reflections]
    by_outcome: dict[str, list[dict]] = defaultdict(list)
    by_mode: dict[str, list[dict]] = defaultdict(list)
    for entry in parsed:
        by_outcome[entry["outcome"]].append(entry)
        by_mode[entry["mode"]].append(entry)

    total = len(parsed)
    passes = len(by_outcome.get("PASS", []))
    fails = len(by_outcome.get("FAIL", []))
    errors = total - passes - fails
    pass_rate = (passes / max(passes + fails, 1)) * 100

    lines = [
        f"# Run Summary: {job_dir.name}",
        "",
        f"**Trials:** {total}  |  **PASS:** {passes}  |  **FAIL:** {fails}"
        f"  |  **ERROR/UNKNOWN:** {errors}",
        f"**Pass rate (agent-attempted):** {passes}/{passes + fails} ({pass_rate:.0f}%)",
        "",
        "## By Outcome",
        "",
    ]
    for outcome in ("PASS", "FAIL"):
        tasks = by_outcome.get(outcome, [])
        if not tasks:
            continue
        lines.append(f"### {outcome} ({len(tasks)})")
        for entry in sorted(tasks, key=lambda e: e["task"]):
            lines.append(
                f"- `{entry['task']}` — {entry['steps']} steps, "
                f"{entry['tokens_in']:,} in / {entry['tokens_out']:,} out"
            )
        lines.append("")

    lines.append("## Failure Modes")
    lines.append("")
    for mode, entries in sorted(
        by_mode.items(), key=lambda item: -len(item[1])
    ):
        if mode == "none":
            continue
        lines.append(f"### {mode} ({len(entries)})")
        for entry in sorted(entries, key=lambda e: e["task"]):
            lines.append(f"- `{entry['task']}` ({entry['outcome']})")
        lines.append("")

    # Reflection snippet index for quick jumps
    lines.append("## Reflection Files")
    lines.append("")
    for entry in sorted(parsed, key=lambda e: e["task"]):
        lines.append(f"- `{entry['task']}` → `{entry['path']}`")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("job_dir", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write summary to this file (default: <job_dir>/summary.md)",
    )
    args = parser.parse_args()

    if not args.job_dir.exists():
        print(f"Job directory does not exist: {args.job_dir}", file=sys.stderr)
        sys.exit(1)

    output = args.output or args.job_dir / "summary.md"
    summary = aggregate(args.job_dir)
    output.write_text(summary)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
