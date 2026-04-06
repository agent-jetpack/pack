---
title: "feat: Close the iterative-task gap — doom loop, error reflection, retry, slim prompt"
type: feat
status: completed
date: 2026-04-06
origin: docs/plans/tb2-harbor-run-feedback.md
---

# Close the Iterative-Task Performance Gap

## Overview

LangSmith trace analysis reveals Pack's core weakness: one-shot tasks pass at 61-99% but iterative tasks (write → test → fix cycles) pass at 0-22%. Six targeted improvements — drawn from ForgeCode's 81.8% architecture and our Harbor run failure analysis — address the root causes.

## Problem Frame

Harbor formal run: 27% pass rate (3/11). Four categories of failure:
1. **Agent crashes (36%)** — model API disconnects with no retry
2. **No iteration on quality** — agent writes once, never benchmarks or optimizes
3. **Doom loops** — agent retries identical failing approach
4. **Prompt bloat** — 300-line CLI prompt wastes context in sandbox mode

ForgeCode solves categories 2-3 with doom loop detection, forced error reflection, and todo-driven execution. Category 1 is pure infrastructure. Category 4 is prompt engineering.

## Requirements Trace

- R1. Model API failures must be retried with exponential backoff (fixes 36% crash rate)
- R2. Harbor wrapper must use a slim, sandbox-focused prompt (not the full CLI prompt)
- R3. Repeated identical tool calls must be detected and interrupted with a redirect message
- R4. Tool failures must trigger a forced reflection prompt before the agent retries
- R5. The agent must plan tasks before executing and verify each step
- R6. The prompt must tell the agent to benchmark and optimize when tasks mention speed/performance

## Scope Boundaries

- **In scope:** Harbor wrapper (`libs/evals/deepagents_harbor/`), agent middleware (`libs/cli/deepagents_cli/agent.py`), system prompt
- **Out of scope:** SDK core changes, new tool implementations (undo, semantic search), agent-as-tool architecture (P1 item for next cycle)

## Key Technical Decisions

- **Retry at wrapper level, not middleware:** The `DeepAgentsWrapper.run()` method wraps `ainvoke` — retry belongs here because it catches the full agent invocation, not individual tool calls
- **Doom loop as middleware:** Tracks tool call history in the middleware's `wrap_tool_call`, counts consecutive identical calls, and injects a system message. Follows ForgeCode's pattern.
- **Error reflection as prompt injection, not middleware:** After a tool error, append a reflection prompt to the tool result message. This is simpler than a separate middleware and follows the `EditVerificationMiddleware` pattern we already have.
- **Slim prompt as a separate template:** Don't try to conditionally strip the CLI prompt — write a clean Harbor-specific prompt from scratch. Shorter = better.

## Implementation Units

- [ ] **Unit 1: Model API retry in Harbor wrapper**

  **Goal:** Wrap `deep_agent.ainvoke()` with retry on transient errors.

  **Requirements:** R1

  **Dependencies:** None

  **Files:**
  - Modify: `libs/evals/deepagents_harbor/deepagents_wrapper.py`

  **Approach:**
  - Wrap the `ainvoke` call in both the LangSmith-traced and non-traced paths
  - Catch connection errors, timeouts, and 5xx responses
  - Retry up to 3 times with exponential backoff (2s, 4s, 8s)
  - Log each retry attempt
  - If all retries exhausted, re-raise the original exception

  **Test scenarios:**
  - Happy path: Successful invocation on first try — no retry overhead
  - Error path: First call fails with connection error, second succeeds — retried once
  - Error path: All 3 retries fail — original exception propagated

  **Verification:** Transient API failures no longer produce 0-reward error trials

---

- [ ] **Unit 2: Slim Harbor system prompt**

  **Goal:** Replace the 300-line combined CLI+Harbor prompt with a focused ~80-line sandbox prompt.

  **Requirements:** R2, R5, R6

  **Dependencies:** None

  **Files:**
  - Modify: `libs/evals/deepagents_harbor/deepagents_wrapper.py`

  **Approach:**
  - Write a new `HARBOR_SYSTEM_PROMPT` constant directly in the wrapper (not importing from CLI)
  - Keep only rules relevant to sandbox execution:
    - Verify after writes (check file exists, run tests)
    - Compute with tools (never mental arithmetic)
    - Use exact names from instructions
    - Read task name carefully (e.g., "break-X" means bypass X)
    - Plan tasks before executing (todo-driven)
    - When task mentions speed/performance, benchmark and iterate
    - Never ask questions — complete autonomously
    - Code must go in files, not prose
    - If a file read is truncated, read more
    - Parallel tool calls when independent
  - Append Harbor's directory context dynamically (existing `_get_formatted_system_prompt`)
  - Remove the `get_system_prompt()` import from CLI entirely

  **Test scenarios:**
  - Happy path: Prompt is under 100 lines / 3000 tokens
  - Happy path: Contains all critical behavioral rules
  - Happy path: Does NOT contain git safety, HITL approval, or CLI-specific rules

  **Verification:** Token overhead reduced by ~60%

---

- [ ] **Unit 3: Doom loop detection middleware**

  **Goal:** Detect when the agent makes 3+ consecutive identical tool calls and inject a redirect message.

  **Requirements:** R3

  **Dependencies:** None

  **Files:**
  - Modify: `libs/cli/deepagents_cli/agent.py` (add `DoomLoopDetectionMiddleware`)

  **Approach:**
  - Track last N tool calls as `(tool_name, args_hash)` tuples
  - After each tool call, check if the last 3 entries are identical
  - Also detect repeating sequences: if `[A,B,C,A,B,C,A,B,C]` pattern detected
  - When detected: log warning, append message to tool result:
    "⚠️ STUCK: You have made {N} identical tool calls. You are not making progress. Stop and try a completely different approach. Do NOT retry the same command."
  - Reset detection counter after a non-repeated call
  - Wire into middleware stack (always active, not gated by mode)

  **Patterns to follow:**
  - ForgeCode's `doom_loop.rs` — consecutive + sequence detection
  - Existing `EditVerificationMiddleware` — tool result modification pattern

  **Test scenarios:**
  - Happy path: Varied tool calls — no intervention
  - Error path: 3 identical `execute("make build")` calls — warning injected on 3rd
  - Error path: Repeating sequence `[read,edit,execute]` × 3 — warning injected
  - Edge case: Similar but not identical calls (different args) — no false positive
  - Happy path: After warning, agent makes different call — counter resets

  **Verification:** Agent receives explicit "you are stuck" feedback when looping

---

- [ ] **Unit 4: Forced error reflection prompt**

  **Goal:** When a tool call fails, append a reflection prompt forcing the agent to analyze the failure before retrying.

  **Requirements:** R4

  **Dependencies:** None

  **Files:**
  - Modify: `libs/cli/deepagents_cli/agent.py` (enhance `EditVerificationMiddleware` or create new `ErrorReflectionMiddleware`)

  **Approach:**
  - Create `ErrorReflectionMiddleware(AgentMiddleware)` that wraps all tool calls
  - After execution, check if the result indicates an error (non-zero exit code in execute, error status in edit, etc.)
  - If error detected, append ForgeCode-style reflection prompt:
    ```
    ⚠️ TOOL FAILED. Before retrying, you MUST reflect:
    1. What exactly went wrong with this tool call?
    2. Why did it fail — wrong tool, wrong arguments, or wrong approach?
    3. What specific change will you make before retrying?
    Do NOT retry the same command without changes.
    ```
  - For shell `execute` errors: detect non-zero exit code from the output
  - For `edit_file` errors: already handled by `EditVerificationMiddleware` — extend it
  - For `write_file` with syntax errors: already handled by `PythonSyntaxCheckMiddleware` — extend it
  - Wire after existing verification middleware in the stack

  **Patterns to follow:**
  - ForgeCode's `forge-partial-tool-error-reflection.md` template
  - ForgeCode's `forge-tool-retry-message.md` — "Attempts remaining: N"

  **Test scenarios:**
  - Happy path: Successful tool call — no reflection prompt added
  - Error path: `execute("make build")` returns non-zero — reflection prompt appended
  - Error path: `edit_file` with no match — reflection prompt appended (extends existing)
  - Edge case: Tool returns error-like content but exit code 0 — no false positive

  **Verification:** Failed tool calls always include the reflection prompt in the result

---

- [ ] **Unit 5: Todo-driven execution prompt rule**

  **Goal:** The system prompt must instruct the agent to always plan tasks first using the todo/write_todos tool before starting work.

  **Requirements:** R5

  **Dependencies:** Unit 2 (integrated into the slim Harbor prompt)

  **Files:**
  - Modify: `libs/evals/deepagents_harbor/deepagents_wrapper.py` (the new HARBOR_SYSTEM_PROMPT)

  **Approach:**
  - Add to the Harbor prompt:
    ```
    ## Task Management
    ALWAYS create a task plan before starting work. Break the task into steps using the todo tool.
    Mark each step complete ONLY after executing AND verifying it works.
    Do not batch completions — mark each item done as you finish it.
    If a step fails, add a new step for the fix rather than silently retrying.
    ```
  - This is a prompt-level change integrated with Unit 2
  - The `write_todos` tool is already available via the SDK backend

  **Test scenarios:**
  - Happy path: Prompt contains task management instructions
  - Happy path: Agent creates todo items before starting work (observable in traces)

  **Verification:** Harbor prompt includes todo-driven execution rules

---

- [ ] **Unit 6: Performance optimization prompt rule**

  **Goal:** When a task mentions speed, performance, or "faster than", the agent should benchmark its implementation and iterate.

  **Requirements:** R6

  **Dependencies:** Unit 2 (integrated into the slim Harbor prompt)

  **Files:**
  - Modify: `libs/evals/deepagents_harbor/deepagents_wrapper.py` (the new HARBOR_SYSTEM_PROMPT)

  **Approach:**
  - Add to the Harbor prompt:
    ```
    ## Performance Tasks
    When the task mentions "faster", "speed", "performance", "optimize", or "benchmark":
    1. Write your initial implementation
    2. Run the benchmark or timing test
    3. If not fast enough, analyze bottlenecks and optimize
    4. Repeat until the performance target is met or you've tried 3 different approaches
    Never submit a performance-sensitive solution without benchmarking it first.
    ```

  **Test scenarios:**
  - Happy path: Prompt contains performance optimization rules

  **Verification:** Harbor prompt includes performance iteration rules

## System-Wide Impact

- **Interaction graph:** Doom loop detection + error reflection add 2 new middleware to the stack. Both are post-tool-call interceptors like existing middleware. Retry logic wraps the outer `ainvoke` call — doesn't touch the middleware stack.
- **Error propagation:** Retry catches transient errors only (connection, timeout). Application errors (wrong answer, test failure) are NOT retried — they're handled by Harbor's `-k 5` mechanism.
- **State lifecycle risks:** Retry could cause duplicate work if the first `ainvoke` partially executed. Mitigated: transient connection errors typically mean no work was done.
- **Unchanged invariants:** CLI interactive mode unaffected. Existing middleware stack preserved. SDK core unchanged.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Retry on partial execution could cause state corruption | Only retry on connection-level errors where no work was done |
| Doom loop detection false positives | Require 3 consecutive identical calls, not just similar ones |
| Error reflection adds tokens to every failure | Only ~100 tokens per reflection — negligible vs context window |
| Slim prompt misses important rules | Keep all rules from ForgeCode analysis; test on tasks that previously passed |

## Sources & References

- Feedback doc: `docs/plans/tb2-harbor-run-feedback.md`
- ForgeCode analysis: `docs/plans/forgecode-analysis.md`
- LangSmith trace clusters: LangSmith project `22784498-74c8-4642-93f7-2ddaba00c908`
- Harbor wrapper: `libs/evals/deepagents_harbor/deepagents_wrapper.py`
- Middleware patterns: `libs/cli/deepagents_cli/agent.py`
