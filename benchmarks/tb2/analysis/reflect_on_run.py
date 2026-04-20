#!/usr/bin/env python3
"""Live reflection loop for Harbor benchmark runs.

Watches a running Harbor job directory, detects when each task completes,
and spawns a Claude Code subprocess to reflect on the trajectory. Produces
both a live console stream and a persisted per-task reflection file.

Usage:
    python reflect_on_run.py <harbor-job-dir>
    python reflect_on_run.py /tmp/pack-harbor-run-008/pack-run-008

The watcher:
    1. Polls the job dir every 10s for new trial directories
    2. When a trial's trajectory.json appears AND result.json exists,
       treats it as completed
    3. Spawns `claude -p "<reflection prompt>"` with the trajectory as
       context, using the langsmith-trace-analyzer skill
    4. Streams the reflection to stdout AND writes it to
       <trial-dir>/reflection.md

Cost: ~$0.01-0.05 per task via Claude Code subprocess (Sonnet default).
Turn off with --heuristic-only for deterministic classification instead.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REFLECTION_PROMPT_TEMPLATE = """Analyze this completed Harbor benchmark trial and write a concise reflection.

**Trial directory:** {trial_dir}
**Task:** {task_name}
**Result:** {result_summary}
**Steps:** {steps}
**Tokens:** {tokens_in:,} input / {tokens_out:,} output

Read the trajectory.json and result.json in the trial directory.

Write a markdown reflection with these sections:

## Summary
One-sentence outcome (pass/fail + why).

## Failure mode (if failed)
Categorize: wrong_solution | agent_timeout | api_hang | max_tokens_dump | infra_error | other.
Point to specific tool calls or moments in the trajectory that reveal the failure.

## Agent behavior pattern
2-3 sentences on what the agent did: did it plan? did it run tests? did it iterate or one-shot?
Did it respect the PreCompletionChecklist? Did it react to budget markers?

## Suggested harness improvement
If the failure suggests a harness gap (not a model capability gap), name it specifically.
Otherwise write "none — appears to be a model capability limit."

Keep total output under 25 lines. Be direct; this is for a per-task operator dashboard,
not a full report.

Save your reflection to {reflection_path} — write the file before finishing your response.
"""


def _parse_trial_status(trial_dir: Path) -> dict | None:
    """Return dict with task/result/steps/tokens if trial completed, else None."""
    result_path = trial_dir / "result.json"
    traj_path = trial_dir / "agent" / "trajectory.json"
    if not result_path.exists() or not traj_path.exists():
        return None
    try:
        result = json.loads(result_path.read_text())
        traj = json.loads(traj_path.read_text())
    except json.JSONDecodeError:
        return None

    metrics = traj.get("final_metrics", {})
    # Harbor result.json shape: verifier_result.rewards.reward is the canonical score
    verifier = result.get("verifier_result") or {}
    rewards = verifier.get("rewards") or {}
    reward = rewards.get("reward")
    exception_info = result.get("exception_info") or {}
    exc_type = exception_info.get("type", "") if isinstance(exception_info, dict) else ""

    if reward == 1.0:
        outcome = "PASS"
    elif reward == 0.0:
        outcome = "FAIL"
    elif exc_type:
        outcome = f"ERROR ({exc_type})"
    else:
        outcome = "UNKNOWN"

    return {
        "task_name": trial_dir.name.rsplit("__", 1)[0],
        "result_summary": outcome,
        "steps": len(traj.get("steps", [])),
        "tokens_in": metrics.get("total_prompt_tokens", 0),
        "tokens_out": metrics.get("total_completion_tokens", 0),
    }


def _spawn_claude_reflection(trial_dir: Path, status: dict) -> bool:
    """Invoke `claude -p` on the trial. Returns True if successful."""
    reflection_path = trial_dir / "reflection.md"
    prompt = REFLECTION_PROMPT_TEMPLATE.format(
        trial_dir=trial_dir,
        task_name=status["task_name"],
        result_summary=status["result_summary"],
        steps=status["steps"],
        tokens_in=status["tokens_in"],
        tokens_out=status["tokens_out"],
        reflection_path=reflection_path,
    )

    print(
        f"\n{'='*70}\n"
        f"[{datetime.now().strftime('%H:%M:%S')}] {status['task_name']} "
        f"→ {status['result_summary']} "
        f"({status['steps']} steps, {status['tokens_in']:,}/{status['tokens_out']:,} tok)\n"
        f"{'='*70}",
        flush=True,
    )

    try:
        proc = subprocess.Popen(
            [
                "claude",
                "-p",
                prompt,
                "--allowedTools",
                "Read,Grep,Glob,Bash,Write",
                "--output-format",
                "text",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except FileNotFoundError:
        print("  [ERROR: `claude` CLI not found on PATH]", flush=True)
        return False

    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"  {line}", end="", flush=True)
    proc.wait()
    return proc.returncode == 0


def _heuristic_reflection(trial_dir: Path, status: dict) -> None:
    """Fast deterministic classification without LLM call."""
    reflection_path = trial_dir / "reflection.md"
    task = status["task_name"]
    outcome = status["result_summary"]
    steps = status["steps"]
    tokens_in = status["tokens_in"]
    tokens_out = status["tokens_out"]

    if outcome == "PASS":
        mode = "none"
    elif steps <= 1 and tokens_in == 0:
        mode = "api_hang"
    elif tokens_out >= 60000:
        mode = "max_tokens_dump"
    elif "Timeout" in outcome:
        mode = "agent_timeout"
    elif "Error" in outcome or "error" in outcome.lower():
        mode = "infra_error"
    else:
        mode = "wrong_solution"

    reflection = f"""# Reflection: {task}

**Outcome:** {outcome}
**Steps:** {steps}
**Tokens:** {tokens_in:,} in / {tokens_out:,} out
**Failure mode (heuristic):** {mode}

*Deterministic classification only. Run with LLM reflection for richer analysis.*
"""
    reflection_path.write_text(reflection)
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] {task} → {outcome} "
        f"| mode={mode} | {steps} steps",
        flush=True,
    )


def watch(job_dir: Path, *, poll_interval: int, heuristic_only: bool) -> None:
    """Watch the job directory and reflect on each completed trial."""
    print(f"Watching {job_dir} (poll={poll_interval}s, heuristic_only={heuristic_only})")
    print("Press Ctrl+C to stop.\n", flush=True)

    seen: set[str] = set()
    while True:
        try:
            if not job_dir.exists():
                time.sleep(poll_interval)
                continue

            for entry in sorted(job_dir.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.name in seen:
                    continue
                status = _parse_trial_status(entry)
                if status is None:
                    continue
                seen.add(entry.name)
                if heuristic_only:
                    _heuristic_reflection(entry, status)
                else:
                    _spawn_claude_reflection(entry, status)

            time.sleep(poll_interval)
        except KeyboardInterrupt:
            print(f"\n\nWatched {len(seen)} completed trials. Exiting.")
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("job_dir", type=Path, help="Harbor job directory to watch")
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=10,
        help="Seconds between directory scans (default: 10)",
    )
    parser.add_argument(
        "--heuristic-only",
        action="store_true",
        help="Use deterministic classification only, no Claude subprocess",
    )
    args = parser.parse_args()

    if not args.job_dir.exists():
        print(f"Job directory does not exist yet: {args.job_dir}", file=sys.stderr)
        print("Will wait for it to appear...", file=sys.stderr)

    watch(
        args.job_dir,
        poll_interval=args.poll_interval,
        heuristic_only=args.heuristic_only,
    )


if __name__ == "__main__":
    main()
