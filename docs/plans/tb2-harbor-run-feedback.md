# TB2 Harbor Run — Feedback & Development Items

**Date:** 2026-04-06
**Run:** 14 trials dispatched, 11 completed (3 PASS, 4 FAIL, 4 ERROR)
**Pass rate:** 27% — below 75% threshold, run stopped early
**Model:** z-ai/glm-5 via OpenRouter on Daytona

---

## Root Cause Analysis

### Category 1: Agent Crashes (4 errors — 36% of completed)

**gpt2-codegolf, llm-inference-batching-scheduler, merge-diff-arc-agi-task, write-compressor**

The agent crashed without producing a trajectory. Causes:
- `llm-inference-batching-scheduler`: "Server disconnected without sending a response" — OpenRouter dropped the connection
- Others: silent crash, empty error messages

**Root cause:** No error recovery in the wrapper. When the model API disconnects or returns an unparseable response, the agent crashes instead of retrying.

**Dev items:**
- [ ] **D1: Add retry/reconnect logic in DeepAgentsWrapper.run()** — wrap the `ainvoke` call with retry on transient errors (connection reset, timeout, 5xx)
- [ ] **D2: Add structured error handling for model API failures** — catch OpenRouter disconnects and retry with exponential backoff before failing the trial

### Category 2: Not Fast Enough (1 failure)

**largest-eigenval:** Passed 18/27 tests but failed 9 speedup tests. The agent used `scipy.sparse.linalg.eigs` which is correct but not fast enough for larger matrices in the Docker environment.

**Root cause:** The agent doesn't iterate on performance. It writes one implementation and moves on. The system prompt says "verify" but doesn't say "benchmark and optimize."

**Dev items:**
- [ ] **D3: Add "optimize for performance" prompt rule** — when the task mentions speed, performance, or "faster than", the agent should benchmark its implementation and iterate if too slow
- [ ] **D4: Consider scipy.linalg.eig instead of sparse.eigs** — for small matrices (≤10x10), dense `eig` with `eigvals_only` or a direct Schur decomposition may be faster than sparse methods

### Category 3: Network Issues in Containers (2 failures)

**pytorch-model-cli:** PyTorch download from `download.pytorch.org` connection reset
**reshard-c4-data:** HuggingFace Hub connection reset

**Root cause:** Daytona containers have network connectivity but some external endpoints are flaky. The agent doesn't handle download failures.

**Dev items:**
- [ ] **D5: Add retry logic for pip/uv install commands** — when the agent runs install commands and they fail with network errors, it should retry (the system prompt says to retry on failure, but the agent doesn't always do this)
- [ ] **D6: Consider pre-checking network availability** — before attempting large downloads, check connectivity

### Category 4: Misunderstood Task Requirements (1 failure)

**break-filter-js-from-html:** The test checks that the agent can BYPASS an XSS filter, not just write one. Our agent wrote a filter (the simpler task) but the test expects a bypass payload.

**Root cause:** The agent doesn't carefully parse task names/instructions. "break-filter" means "break the filter", not "build a filter."

**Dev items:**
- [ ] **D7: Improve instruction parsing in system prompt** — add: "Read the task name AND instruction carefully. The task name often contains the key action (e.g., 'break-X' means bypass/defeat X, not build X)."

### Category 5: Hard Tasks (gpt2-codegolf, write-compressor)

These are genuinely hard tasks at the frontier of model capability. gpt2-codegolf requires implementing GPT-2 inference in <5000 bytes of C. write-compressor requires reverse-engineering a custom compression format.

**Dev items:**
- [ ] **D8: Accept these as stretch goals** — focus optimization on medium-difficulty tasks first

---

## Gap: Ad-Hoc vs Harbor Performance

Our ad-hoc runs showed 100% pass rate on 19 tasks, but Harbor showed 27% on a different task set. Why:

1. **Ad-hoc used cherry-picked tasks** — we tested tasks we could set up locally, which skewed toward simpler ones
2. **Ad-hoc verification was lenient** — our `--verify-cmd` checks were simpler than Harbor's full test suites
3. **Harbor's test suites are stricter** — e.g., `largest-eigenval` needs both correctness AND speed; our ad-hoc test only checked correctness
4. **No retry on model API failures in Harbor** — our ad-hoc runs could be re-run manually; Harbor counts crashes as failures

**Dev items:**
- [ ] **D9: Run ad-hoc tests with Harbor's actual test_outputs.py** — mirror the real test suites in our verification commands
- [ ] **D10: Test on the full task distribution** — don't cherry-pick easy tasks for development

---

## System Prompt Issues

The combined prompt (CLI hardened + Harbor context) may be too long. At ~300 lines, it consumes significant context before the agent starts working. Some of our rules may conflict with Harbor's execution model.

**Dev items:**
- [ ] **D11: Create a Harbor-specific system prompt** — strip CLI-specific rules (git safety, tool approval, debugging tips) that don't apply in sandbox mode. Keep only: verify-after-write, compute-with-tools, exact-naming, no-questions, code-in-tools
- [ ] **D12: Measure prompt token overhead** — compare prompt size vs task instruction size

---

## Infrastructure Observations

1. Daytona works well — zero infra errors with -n 3
2. OpenRouter GLM-5 has occasional disconnects — needs retry logic
3. `-k 5` means 5 attempts per task, 445 total trials for 89 tasks
4. Wall time at -n 3: ~24 hours (too slow for iteration)

**Dev items:**
- [ ] **D13: Add `-n` concurrency back to 5-10** once we confirm resource limits
- [ ] **D14: Consider a faster model for iteration** — GLM-5 is slow; a cheaper/faster model for development runs, GLM-5 for submission

---

## Priority Development Items (ordered by impact)

### P0 — Must fix before next run
1. **D1/D2:** Retry logic for model API failures (fixes 36% of errors)
2. **D11:** Harbor-specific slim system prompt (reduces context waste)
3. **D7:** Better instruction parsing prompt rule

### P1 — High impact
4. **D3:** Performance optimization prompt rule
5. **D5:** Network retry for install commands
6. **D9:** Use Harbor's real test suites for local testing

### P2 — Nice to have
7. **D12:** Prompt token measurement
8. **D13/D14:** Concurrency and model selection for dev runs
9. **D10:** Full task distribution testing

---

## LangSmith Trace Analysis (added post-run)

### Cluster Performance

| Cluster | Pass Rate | Insight |
|---------|-----------|---------|
| Digital Forensics / Password Recovery | **99%** | One-shot analysis — agent's strength |
| PyTorch Model Recovery | **96.6%** | Reverse engineering works well |
| Artifact Reconstruction (overall) | **61.6%** | Strongest category |
| Performance Tuning | **22.7%** | Agent doesn't iterate on speed |
| Iterative Debugging | **7%** | Worst category — agent can't refine |
| Coroutines/Scheduling | **1.7%** | Constraint solving weak |
| LaTeX Overfull HBox | **0%** | Iterative constraint satisfaction fails |
| Arithmetic Coding Compressor | **0%** | Reverse-engineering bitstream format fails |

### Core Pattern

**One-shot tasks succeed. Iterative tasks fail.**

Tasks requiring a single analysis + generation pass (forensics, recovery, translation) hit 60-99%. Tasks requiring write → test → fix → repeat cycles (performance tuning, compression, constraint optimization) hit 0-22%.

This directly maps to the missing harness features:
- **No doom loop detection** — agent retries same approach
- **No forced error reflection** — agent sees error but doesn't analyze root cause
- **No verification loop in Harbor** — our `--verify-cmd` isn't used in Harbor mode
- **No performance benchmarking prompt** — agent writes code once, doesn't optimize

### ForgeCode Comparison (81.8% leader)

ForgeCode's key advantages over Pack on iterative tasks:
1. Doom loop detection (breaks repeat patterns after 3 cycles)
2. Forced error reflection prompt ("Why did this fail? What specifically was wrong?")
3. Todo-driven execution (plan → execute → verify each step)
4. Agent-as-tool (SAGE research in separate context)
5. Retry message with "attempts remaining: N"

## Updated Priority Development Items

### P0 — Must fix (target: +20-30% pass rate)
1. **D1/D2:** Retry logic for model API failures (fixes 36% of errors)
2. **D11:** Harbor-specific slim system prompt
3. **D15:** Doom loop detection middleware (ForgeCode pattern)
4. **D16:** Forced error reflection — inject "Why did this fail?" after tool errors
5. **D17:** Todo-driven execution enforcement in prompt

### P1 — High impact (target: +10-15%)
6. **D3:** Performance optimization prompt rule
7. **D7:** Better instruction parsing
8. **D18:** Agent-as-tool pattern (research subagent in Harbor wrapper)
9. **D19:** Retry message with attempts-remaining counter

### P2 — Medium impact
10. **D5:** Network retry for install commands
11. **D9:** Use Harbor's real test suites locally
12. **D20:** Read-before-edit enforcement at tool level

## Next Steps

1. Implement P0 items (D1, D2, D11, D15, D16, D17)
2. Re-run on 15 tasks covering all clusters
3. If >50%, expand to 30 tasks
4. If >60%, run full 89 tasks with -k 5 for submission
5. Target: 65%+ for competitive leaderboard position
