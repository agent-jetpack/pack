#!/usr/bin/env python3
"""Save a Harbor job result as a benchmark run for tracking."""

import json
import sys
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path(__file__).parent.parent / "runs"


def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: save_run.py <harbor-job-dir> <run-id> <harness-version> [notes]")
        print("Example: save_run.py /tmp/pack-harbor/my-job run-002 'post-timeout-fix' 'Added 60s timeout'")
        sys.exit(1)

    job_dir = Path(sys.argv[1])
    run_id = sys.argv[2]
    harness_version = sys.argv[3]
    notes = sys.argv[4] if len(sys.argv) > 4 else ""

    result_path = job_dir / "result.json"
    if not result_path.exists():
        print(f"No result.json in {job_dir}")
        sys.exit(1)

    r = json.loads(result_path.read_text())
    st = list(r["stats"]["evals"].values())[0]

    results = {}
    for reward_val, task_list in st.get("reward_stats", {}).get("reward", {}).items():
        for t in task_list:
            tn = t.split("__")[0]
            steps = prompt = comp = 0
            for d in job_dir.iterdir():
                if d.name.startswith(tn + "__"):
                    tp = d / "agent" / "trajectory.json"
                    if tp.exists():
                        tj = json.loads(tp.read_text())
                        m = tj.get("final_metrics", {})
                        steps = len(tj.get("steps", []))
                        prompt = m.get("total_prompt_tokens", 0)
                        comp = m.get("total_completion_tokens", 0)
            results[tn] = {
                "result": "pass" if float(reward_val) == 1.0 else "fail",
                "steps": steps,
                "prompt_tokens": prompt,
                "completion_tokens": comp,
            }

    for exc_type, task_list in st.get("exception_stats", {}).items():
        for t in task_list:
            tn = t.split("__")[0]
            if tn not in results:
                results[tn] = {
                    "result": "error",
                    "error_type": exc_type,
                    "steps": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                }

    passes = sum(1 for v in results.values() if v["result"] == "pass")
    fails = sum(1 for v in results.values() if v["result"] == "fail")
    agent_attempted = passes + fails

    run = {
        "run_id": run_id,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "model": r.get("stats", {}).get("evals", {}) and list(r["stats"]["evals"].keys())[0].split("__")[0].replace("deepagent-harbor (", "").rstrip(")") or "unknown",
        "provider": "openrouter",
        "harness_version": harness_version,
        "notes": notes,
        "tasks_attempted": len(results),
        "tasks_passed": passes,
        "tasks_failed": fails,
        "tasks_errored": sum(1 for v in results.values() if v["result"] == "error"),
        "pass_rate": round(passes / max(agent_attempted, 1), 3),
        "total_prompt_tokens": sum(v["prompt_tokens"] for v in results.values()),
        "total_completion_tokens": sum(v["completion_tokens"] for v in results.values()),
        "total_cost_usd": round(
            sum(v["prompt_tokens"] * 0.95 / 1e6 + v["completion_tokens"] * 3.15 / 1e6 for v in results.values()),
            2,
        ),
        "results": dict(sorted(results.items())),
    }

    out_path = RUNS_DIR / f"{run_id}.json"
    out_path.write_text(json.dumps(run, indent=2))
    print(f"Saved {run_id}: {passes}/{agent_attempted} ({run['pass_rate']*100:.0f}%), ${run['total_cost_usd']}")


if __name__ == "__main__":
    main()
