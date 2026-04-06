---
title: "feat: Add verify-retry loop to non-interactive mode"
type: feat
status: completed
date: 2026-04-04
origin: pack-bench-glm5/results/REPORT.md
---

# Add Verify-Retry Loop to Non-Interactive Mode

## Overview

The pack harness hits 82% pass rate with GLM-5 but the agent never self-verifies output. Tasks that produce almost-correct results (openssl fingerprint format, cobol logic) could pass if the agent ran a check and fixed errors. This plan adds a `--verify-cmd` flag that runs a verification command after the agent completes, and re-invokes the agent with the error output if verification fails.

## Problem Frame

Across 17 GLM-5 benchmark tasks, the agent used an average of 6.2 tool calls — but zero of those were verification calls. The system prompt says "verify your output" but the model skips this step. The three non-passing tasks all share the same pattern: the agent produced output, declared done, and walked away from fixable errors.

- **write-compressor (FAIL):** Agent iterated 14+ times on compressor scripts but never ran `cat data.comp | ./decomp` to test the output.
- **openssl (MOSTLY PASS):** Fingerprint format wrong in verification.txt — a single `openssl x509 -fingerprint -sha256` would have caught it.
- **cobol (PARTIAL):** Agent wrote program.py but never ran it against the test data to check output.

The harness can close this gap mechanically — after the agent declares done, run the user-provided verification command and feed failures back.

## Requirements Trace

- R1. New `--verify-cmd` CLI flag that accepts a shell command to run after task completion
- R2. If verification fails (non-zero exit), re-invoke the agent with the error output as a follow-up message
- R3. Retry up to `--verify-retries` times (default 3) before giving up
- R4. Report verification status (pass/fail/retries) in the task completion output
- R5. Verification must work within the existing LangGraph thread (same conversation context)
- R6. No effect when `--verify-cmd` is not provided — existing behavior unchanged

## Scope Boundaries

- **In scope:** CLI flag, verification loop in non_interactive.py, retry with error feedback
- **Out of scope:** Auto-detecting what to verify, Docker-based evaluation, changes to the SDK or middleware layer
- **Not changing:** Interactive mode, system prompt, middleware stack

## Context & Research

### Relevant Code and Patterns

- `libs/cli/deepagents_cli/non_interactive.py:_run_agent_loop()` — the main loop that drives agent execution. Currently: run agent → handle HITL → print results → exit. The verify-retry loop wraps this at the same level.
- `libs/cli/deepagents_cli/non_interactive.py:_stream_agent()` — streams chunks from `agent.astream()`. Accepts `Command(resume=...)` for HITL continuation — the same mechanism can inject verification feedback as a new user message.
- `libs/cli/deepagents_cli/non_interactive.py:run_non_interactive()` — entry point that accepts CLI args. New `verify_cmd` and `verify_retries` params go here.
- `libs/cli/deepagents_cli/main.py` — CLI arg parsing. New `--verify-cmd` and `--verify-retries` flags.
- Existing pattern: the HITL interrupt loop (lines 675-690) already re-invokes the agent with new input after an interrupt. The verify-retry loop follows the exact same pattern but with verification output instead of HITL decisions.

## Key Technical Decisions

- **Verify at the agent-loop level, not middleware:** Middleware operates per-tool-call. Verification needs to run after the agent has finished all tool calls and declared the task complete. This is a loop-level concern in `_run_agent_loop`.
- **Feed errors as user messages, not tool results:** After verification fails, inject a new `{"role": "user", "content": "Verification failed: {stderr}. Fix the issues and try again."}` message into the thread. This keeps the agent's full conversation context and lets it reason about what went wrong.
- **Run verification via subprocess, not the agent's shell tool:** The verification command runs directly via `asyncio.create_subprocess_exec`, not through the agent's `execute` tool. This prevents the agent from interfering with the verification process.
- **Use the CWD from DEEPAGENTS_USER_CWD:** Verification commands run in the user's workspace, not the CLI package directory.

## Open Questions

### Resolved During Planning

- **Should verification run in the agent's sandbox?** No — verification commands are provided by the benchmark runner (external to the agent). They run in the host process using the user's CWD.
- **Should the agent see the full verification output?** Yes — both stdout and stderr, truncated to 2000 chars to avoid blowing context. The agent needs the error details to fix the issue.

### Deferred to Implementation

- Exact truncation strategy for very large verification output
- Whether to include verification timing in the usage stats table

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

```mermaid
sequenceDiagram
    participant CLI as main.py
    participant NI as run_non_interactive
    participant Loop as _run_agent_loop
    participant Agent as LangGraph Agent
    participant Verify as subprocess

    CLI->>NI: message, verify_cmd, verify_retries=3
    NI->>Loop: message, verify_cmd, verify_retries
    Loop->>Agent: initial message
    Agent-->>Loop: tool calls + completion
    Loop->>Verify: run verify_cmd
    alt verify passes (exit 0)
        Loop-->>NI: done (pass)
    else verify fails (exit != 0)
        Loop->>Agent: "Verification failed: {output}. Fix and retry."
        Agent-->>Loop: tool calls + completion
        Loop->>Verify: run verify_cmd (retry 2)
        alt verify passes
            Loop-->>NI: done (pass after retry)
        else still fails
            Loop->>Agent: retry 3...
            Loop-->>NI: done (fail after max retries)
        end
    end
```

## Implementation Units

- [x] **Unit 1: Add --verify-cmd and --verify-retries CLI flags**

  **Goal:** Parse new CLI flags and pass them through to `run_non_interactive()`.

  **Requirements:** R1, R6

  **Dependencies:** None

  **Files:**
  - Modify: `libs/cli/deepagents_cli/main.py` (add argparse arguments)
  - Modify: `libs/cli/deepagents_cli/non_interactive.py` (add params to `run_non_interactive` signature)
  - Test: `libs/cli/tests/unit_tests/test_non_interactive.py`

  **Approach:**
  - Add `--verify-cmd` as an optional string argument in the non-interactive argument group
  - Add `--verify-retries` as an optional int argument, default 3
  - Pass both through in the `run_non_interactive()` call in `main.py`
  - Add `verify_cmd: str | None = None` and `verify_retries: int = 3` to `run_non_interactive()` signature
  - When `verify_cmd` is None, behavior is unchanged

  **Patterns to follow:**
  - Existing `--shell-allow-list` flag parsing pattern in `main.py`
  - Existing parameter passthrough to `run_non_interactive()`

  **Test scenarios:**
  - Happy path: `--verify-cmd "python3 test.py"` is parsed and passed to run_non_interactive
  - Happy path: `--verify-retries 5` overrides default
  - Edge case: No `--verify-cmd` provided → verify_cmd is None, no verification runs
  - Edge case: `--verify-retries` without `--verify-cmd` → no effect (no error)

  **Verification:**
  - CLI `--help` shows the new flags
  - `run_non_interactive` accepts the new parameters

---

- [x] **Unit 2: Implement verification runner**

  **Goal:** Create an async function that runs the verification command as a subprocess and returns structured results.

  **Requirements:** R1, R4

  **Dependencies:** None

  **Files:**
  - Modify: `libs/cli/deepagents_cli/non_interactive.py` (add `_run_verification()` function)
  - Test: `libs/cli/tests/unit_tests/test_non_interactive.py`

  **Approach:**
  - Create `async def _run_verification(cmd: str, cwd: Path, timeout: int = 60) -> VerifyResult`
  - `VerifyResult` is a simple dataclass: `passed: bool, exit_code: int, output: str`
  - Run via `asyncio.create_subprocess_shell(cmd, cwd=cwd, stdout=PIPE, stderr=STDOUT)`
  - Capture combined stdout+stderr, truncate to 2000 chars
  - CWD comes from `DEEPAGENTS_USER_CWD` env var or `Path.cwd()`
  - Timeout defaults to 60s (verification should be fast)

  **Patterns to follow:**
  - Existing subprocess usage in `server_manager.py` for the LangGraph server process

  **Test scenarios:**
  - Happy path: Command `"echo ok"` → VerifyResult(passed=True, exit_code=0, output="ok\n")
  - Error path: Command `"exit 1"` → VerifyResult(passed=False, exit_code=1, output="")
  - Error path: Command `"python3 -c 'assert False'"` → VerifyResult(passed=False, output contains "AssertionError")
  - Edge case: Command produces >2000 chars output → truncated with "[truncated]" suffix
  - Edge case: Command times out → VerifyResult(passed=False, output="Verification timed out")

  **Verification:**
  - `_run_verification("true", cwd)` returns passed=True
  - `_run_verification("false", cwd)` returns passed=False

---

- [x] **Unit 3: Implement verify-retry loop in _run_agent_loop**

  **Goal:** After the agent completes, run verification and re-invoke with error feedback if it fails.

  **Requirements:** R2, R3, R4, R5

  **Dependencies:** Unit 1, Unit 2

  **Files:**
  - Modify: `libs/cli/deepagents_cli/non_interactive.py` (`_run_agent_loop` function)
  - Test: `libs/cli/tests/unit_tests/test_non_interactive.py`

  **Approach:**
  - Add `verify_cmd: str | None = None` and `verify_retries: int = 3` params to `_run_agent_loop`
  - After the existing agent loop completes (after HITL handling, before stats printing):
    1. If `verify_cmd` is None, skip — existing behavior
    2. Run `_run_verification(verify_cmd, cwd)`
    3. If passed, print verification success and continue to stats
    4. If failed and retries remain:
       - Print verification failure with output excerpt
       - Construct a new user message: `"Verification failed (exit code {N}):\n\n{output}\n\nFix the issues and try again. Do not ask questions — just fix it."`
       - Re-invoke `_stream_agent` with this message as `stream_input`
       - Handle any HITL interrupts from the retry
       - Decrement retry counter and go back to step 2
    5. If failed and no retries remain, print final failure
  - Track verification attempts in stats output: "Verification: PASS (attempt 2/3)" or "Verification: FAIL (3/3 attempts)"

  **Patterns to follow:**
  - The existing HITL interrupt loop (lines 675-690) — same re-invocation pattern
  - `stream_input` as `{"messages": [{"role": "user", "content": ...}]}` for injecting new messages

  **Test scenarios:**
  - Happy path: verify_cmd=None → no verification, existing behavior unchanged
  - Happy path: verify passes on first attempt → "Verification: PASS" printed, exit 0
  - Error path: verify fails once, agent fixes, verify passes on retry → "Verification: PASS (attempt 2/3)"
  - Error path: verify fails all 3 retries → "Verification: FAIL (3/3 attempts)", exit 1
  - Edge case: verify_retries=0 → run verification once, no retries on failure
  - Integration: agent receives verification error output and makes tool calls to fix the issue

  **Verification:**
  - With `--verify-cmd "test -f output.txt"`, if the agent doesn't create output.txt, it gets re-invoked with the error and creates it on retry
  - Without `--verify-cmd`, behavior is identical to before

---

- [x] **Unit 4: Wire verify params through run_non_interactive to _run_agent_loop**

  **Goal:** Connect the CLI flags through the full call chain.

  **Requirements:** R1, R6

  **Dependencies:** Unit 1, Unit 3

  **Files:**
  - Modify: `libs/cli/deepagents_cli/non_interactive.py` (pass params from `run_non_interactive` to `_run_agent_loop`)
  - Modify: `libs/cli/deepagents_cli/main.py` (pass parsed args to `run_non_interactive`)
  - Test: `libs/cli/tests/unit_tests/test_non_interactive.py`

  **Approach:**
  - In `run_non_interactive()`: pass `verify_cmd` and `verify_retries` to `_run_agent_loop()`
  - In `main.py`: extract `args.verify_cmd` and `args.verify_retries` and pass to `run_non_interactive()`
  - Update the exit code: if verification failed after all retries, return exit code 1

  **Patterns to follow:**
  - Existing param passthrough for `quiet`, `stream`, `mcp_config_path`

  **Test scenarios:**
  - Happy path: `deepagents -n "task" --verify-cmd "echo ok"` → exits 0
  - Error path: `deepagents -n "task" --verify-cmd "false"` → exits 1 after retries

  **Verification:**
  - End-to-end: the full flag → parse → passthrough → execution chain works

## System-Wide Impact

- **Interaction graph:** The verify-retry loop adds a new re-invocation path in `_run_agent_loop`. It uses the same `_stream_agent` function and thread context as the HITL loop. No new middleware or graph nodes.
- **Error propagation:** Verification failures are injected as user messages, not errors. The agent sees them as task feedback and responds normally. If the subprocess itself crashes, the error is captured in VerifyResult.
- **State lifecycle risks:** Each retry re-invokes the agent in the same LangGraph thread, preserving full conversation history. This means context grows with each retry — bounded by `verify_retries` (default 3).
- **API surface parity:** Two new CLI flags (`--verify-cmd`, `--verify-retries`). No changes to interactive mode, SDK, or middleware.
- **Unchanged invariants:** When `--verify-cmd` is not provided, the entire verify-retry path is skipped. Zero behavioral change for existing users.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Retry loop could blow context window | Max 3 retries default; verification output truncated to 2000 chars |
| Verification command could hang | 60s timeout on subprocess |
| Agent could enter a fix-break cycle | Retry limit prevents infinite loops; each retry carries full error context |
| Verification output could contain secrets | Output is only shown to the agent (same thread), not logged externally |

## Success Metrics

- Tasks that currently MOSTLY PASS or PARTIAL should reach full PASS with appropriate verify commands
- write-compressor should show improvement (agent iterates with real decompressor feedback)
- No regression on tasks that already PASS

## Sources & References

- Benchmark report: `pack-bench-glm5/results/REPORT.md`
- Prior harness plan: `docs/plans/2026-04-04-001-feat-bench-driven-harness-hardening-plan.md`
- Non-interactive loop: `libs/cli/deepagents_cli/non_interactive.py:_run_agent_loop`
- CLI arg parsing: `libs/cli/deepagents_cli/main.py`
