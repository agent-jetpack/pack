#!/usr/bin/env python3
"""Compare two Terminal Bench 2.0 benchmark runs and show regressions/improvements."""

import json
import sys
from pathlib import Path

RUNS_DIR = Path(__file__).parent.parent / "runs"
REGISTRY = Path(__file__).parent.parent / "task_registry.json"


def load_run(run_id: str) -> dict:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        print(f"Run not found: {path}")
        sys.exit(1)
    return json.loads(path.read_text())


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: compare_runs.py <run-id-a> <run-id-b>")
        print("Example: compare_runs.py run-001 run-002")
        sys.exit(1)

    a = load_run(sys.argv[1])
    b = load_run(sys.argv[2])

    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {"tasks": []}
    task_meta = {t["id"]: t for t in registry["tasks"]}

    print(f"Comparing {a['run_id']} vs {b['run_id']}")
    print(f"  {a['run_id']}: {a['date']} | {a['model']} | {a['tasks_passed']}/{a['tasks_attempted']} ({a['pass_rate']*100:.0f}%) | ${a['total_cost_usd']}")
    print(f"  {b['run_id']}: {b['date']} | {b['model']} | {b['tasks_passed']}/{b['tasks_attempted']} ({b['pass_rate']*100:.0f}%) | ${b['total_cost_usd']}")
    print()

    all_tasks = sorted(set(list(a.get("results", {}).keys()) + list(b.get("results", {}).keys())))

    regressions = []
    improvements = []
    new_passes = []
    new_fails = []

    for task in all_tasks:
        ra = a.get("results", {}).get(task, {}).get("result", "not_run")
        rb = b.get("results", {}).get(task, {}).get("result", "not_run")

        if ra == "pass" and rb == "fail":
            regressions.append(task)
        elif ra == "fail" and rb == "pass":
            improvements.append(task)
        elif ra == "not_run" and rb == "pass":
            new_passes.append(task)
        elif ra == "not_run" and rb == "fail":
            new_fails.append(task)

    if regressions:
        print(f"REGRESSIONS ({len(regressions)}):")
        for t in regressions:
            meta = task_meta.get(t, {})
            print(f"  ✗ {t} ({meta.get('difficulty', '?')}, {meta.get('category', '?')})")

    if improvements:
        print(f"\nIMPROVEMENTS ({len(improvements)}):")
        for t in improvements:
            meta = task_meta.get(t, {})
            print(f"  ✓ {t} ({meta.get('difficulty', '?')}, {meta.get('category', '?')})")

    if new_passes:
        print(f"\nNEW PASSES ({len(new_passes)}):")
        for t in new_passes:
            meta = task_meta.get(t, {})
            print(f"  + {t} ({meta.get('difficulty', '?')}, {meta.get('category', '?')})")

    if new_fails:
        print(f"\nNEW FAILS ({len(new_fails)}):")
        for t in new_fails:
            meta = task_meta.get(t, {})
            print(f"  - {t} ({meta.get('difficulty', '?')}, {meta.get('category', '?')})")

    if not any([regressions, improvements, new_passes, new_fails]):
        print("No changes between runs.")

    # Cost comparison
    print(f"\nCost delta: ${b['total_cost_usd'] - a['total_cost_usd']:+.2f}")
    print(f"Pass rate delta: {(b['pass_rate'] - a['pass_rate'])*100:+.1f}pp")


if __name__ == "__main__":
    main()
