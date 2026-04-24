# `.harness/` — Pack's self-hosting harness state

This directory is the per-repo state Pack's harness reads and writes when
operating against *this* repository. Pack is dogfooding its own harness
here; external users of Pack would have the same directory in their repo.

Files:

- `config.yaml` — task-policy definitions, approval rules, quality gates
  (Phase A. Empty until Phase A.1 lands.)
- `violations.json` — ratchet state: known violations tolerated, new ones
  blocked. Seeded on first discovery run. (Phase A.3)
- `quality-score.json` — rolling metrics (LOC, file-size distribution,
  test coverage delta, forbidden-import count). Moves monotonically up
  over time under the ratchet. (Phase A.3)

See `docs/roadmap/agent-harness-roadmap.md` for the full plan.

**Nothing here is load-bearing yet.** The roadmap phases populate this
directory incrementally. Reading the files before the corresponding
phase ships will return empty or default values.
