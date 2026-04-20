# TB2 Live Feedback Loop

Two-script system for watching Harbor benchmark runs as they execute and
producing per-task reflections plus a run-level summary.

## Quick Start

In one terminal, start a Harbor run:

```bash
cd libs/evals
OPENROUTER_API_KEY=... PACK_ENABLED=1 uv run harbor run \
  --agent-import-path deepagents_harbor:DeepAgentsWrapper \
  --dataset terminal-bench@2.0 \
  --jobs-dir /tmp/pack-harbor-run-008 \
  --model "openrouter:z-ai/glm-5.1" \
  --job-name "run-008" \
  -n 3
```

In a second terminal, watch it live:

```bash
python benchmarks/tb2/analysis/reflect_on_run.py \
  /tmp/pack-harbor-run-008/run-008
```

After the Harbor run finishes, aggregate:

```bash
python benchmarks/tb2/analysis/aggregate_reflections.py \
  /tmp/pack-harbor-run-008/run-008
```

## What Each Script Does

### `reflect_on_run.py` — live watcher

- Polls the job directory every 10s for new completed trials (detects
  `result.json` + `agent/trajectory.json`)
- For each newly-completed trial, spawns `claude -p "<prompt>"` with the
  `langsmith-trace-analyzer` skill context to produce a per-task reflection
- Writes `reflection.md` inside the trial directory and streams the
  reflection to stdout
- Pass `--heuristic-only` to skip Claude and use deterministic classification
  instead (no LLM cost, no external subprocess — just fast bucketing)

Flags:

- `--poll-interval` — seconds between scans (default 10)
- `--heuristic-only` — deterministic classification only; no Claude invocation

### `aggregate_reflections.py` — post-run summary

- Reads every `reflection.md` under the job directory
- Groups by outcome (PASS/FAIL/ERROR) and by failure mode
- Writes a single consolidated `summary.md` at the job root with counts,
  task lists, and links back to each individual reflection

## Failure Modes (Heuristic Classifier)

| Mode | Heuristic |
|------|-----------|
| `none` | Outcome is PASS |
| `api_hang` | 1 step, zero tokens in/out |
| `max_tokens_dump` | Completion tokens ≥ 60K |
| `agent_timeout` | Exception contains `Timeout` |
| `infra_error` | Any other exception |
| `wrong_solution` | FAIL with normal step/token counts |

Claude-powered reflections produce richer categorization plus a suggested
harness improvement per task.

## Design Notes

- The watcher is best-effort: it treats each trial as completed when both
  `result.json` and `agent/trajectory.json` exist. Harbor writes these
  atomically at the end of a trial, so missed detections are rare.
- Claude invocations are one-at-a-time (subprocess waits for each to finish
  before scanning for the next completion). This keeps output streams clean
  and caps concurrent cost.
- The watcher does not modify Harbor's output; it only reads trial files
  and writes a single `reflection.md` per trial.
- Reflections are written inside each trial directory (colocated with
  `trajectory.json`, `result.json`, `exception.txt`) so they travel with
  the run when archived.
