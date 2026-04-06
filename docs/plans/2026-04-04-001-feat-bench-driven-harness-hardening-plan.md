---
title: "feat: Bench-driven harness hardening from TB2 failure analysis"
type: feat
status: active
date: 2026-04-04
---

# Bench-Driven Harness Hardening

## Overview

Terminal-Bench 2.0 testing (15 tasks across 2 rounds) revealed a 13% full-pass rate on the pack harness with `deepseek/deepseek-chat`. The failures cluster into 6 addressable harness-level issues — none of which require model changes. This plan implements the highest-ROI fixes inside the pack CLI codebase.

## Problem Frame

The pack CLI non-interactive mode (`-n`) drops tasks in predictable, repeatable ways:

1. **No self-verification** — across 15 tasks the agent never checked if its own output was correct. 5 tasks produced files that were empty, wrong, or malformed.
2. **System prompt gaps** — the prompt doesn't discourage mental arithmetic or encourage tool-based computation. The model hallucinated a byte-level XOR decryption instead of running a 3-line script.
3. **Silent edit failures** — `edit_file` match failures are swallowed; the agent proceeds as if the edit succeeded. Caused a partial failure on a Coq proof task.
4. **Shell allow-list too narrow** — the `recommended` list is read-only commands only. Any task requiring `openssl`, `gcc`, `python3`, or `pip` fails immediately in non-interactive mode with `-S recommended`.
5. **Non-interactive mode doesn't enforce completion** — the agent asks follow-up questions ("Would you like me to...") and gives up, despite `-n` mode having no human to answer.
6. **Tool call syntax leak** — DeepSeek models occasionally emit raw `<｜tool▁calls▁begin｜>` markup in text output instead of proper tool invocations. The harness treats this as text and the task does nothing.

## Requirements Trace

- R1. After `write_file` in non-interactive mode, the agent must verify the file exists and is non-empty
- R2. After `edit_file`, the agent must be informed when the edit match fails (not silently succeed)
- R3. System prompt must instruct: compute with tools, don't reason through arithmetic
- R4. System prompt must reinforce: match exact names/identifiers from instructions
- R5. Non-interactive prompt must prohibit asking questions or suggesting manual steps
- R6. Shell allow-list must have a `benchmark` tier that includes build/runtime tools
- R7. Tool call syntax leaks in text output should be detected and cause a retry
- R8. Non-interactive prompt must tell the agent to use tools for code, never write code only in prose
- R9. When a file is truncated during reading, the agent must be prompted to read more or grep
- R10. Non-interactive mode must have a per-action inactivity timeout that nudges or kills stalled runs
- R11. After writing a Python file, a syntax/import check should catch trivial errors before the agent moves on

## Scope Boundaries

- **In scope:** Pack CLI harness changes (system prompt, middleware, config, non-interactive mode)
- **Out of scope:** SDK-level changes to `deepagents` core, Docker/Harbor integration, bench runner scripts, model-specific fine-tuning
- **Deferred:** Task-aware shell allow-lists (would require NLP classification of task instructions)

## Context & Research

### Relevant Code and Patterns

- `libs/cli/deepagents_cli/system_prompt.md` — the full system prompt template (248 lines)
- `libs/cli/deepagents_cli/agent.py:get_system_prompt()` — dynamic prompt interpolation with `interactive` flag (lines 472-611)
- `libs/cli/deepagents_cli/agent.py:ShellAllowListMiddleware` — existing middleware pattern for tool-call interception (lines 78-175)
- `libs/cli/deepagents_cli/config.py:RECOMMENDED_SAFE_SHELL_COMMANDS` — the current read-only allow-list (lines 1468-1501)
- `libs/cli/deepagents_cli/config.py:parse_shell_allow_list()` — parses `-S` flag values including `recommended` and `all` (lines 709-766)
- `libs/cli/deepagents_cli/non_interactive.py:run_non_interactive()` — the non-interactive entry point (lines 742-948)
- `libs/cli/deepagents_cli/agent.py:_add_pack_middleware()` — where pack-specific middleware is added to the stack

### Institutional Learnings

- The middleware pattern (subclass `AgentMiddleware`, override `wrap_tool_call`) is well-established — `ShellAllowListMiddleware` is a clean 100-line example to follow
- System prompt uses `{placeholder}` interpolation, not Jinja — changes must use the same string replacement pattern
- Non-interactive mode spawns a LangGraph server subprocess; middleware runs server-side via `ServerConfig`

## Key Technical Decisions

- **Verification via system prompt, not middleware**: Post-action verification (R1) is best implemented as system prompt instructions rather than middleware-injected tool calls. The prompt already has a "Verify before declaring done" section — strengthening it is simpler and more model-agnostic than injecting synthetic tool calls into the graph.
- **Edit failure surfacing via middleware**: Edit match failures (R2) require a middleware that intercepts `edit_file` results and injects an explicit warning when the tool returns a "no match" status. This is the only approach that works regardless of what the model decides to do.
- **Benchmark shell tier as a named constant**: Rather than ad-hoc command lists, add a `BENCHMARK_SHELL_COMMANDS` tuple alongside the existing `RECOMMENDED_SAFE_SHELL_COMMANDS`, selectable via `-S benchmark`.
- **Tool call leak detection as middleware**: Detecting raw tool call syntax in AI message text (R7) fits the `wrap_model_call` middleware pattern — intercept the response, check for leak patterns, and trigger a retry if found.

## Open Questions

### Resolved During Planning

- **Where to add verification instructions?** In the non-interactive `ambiguity_guidance` block within `get_system_prompt()` (lines 534-544 of `agent.py`). This block is only injected for `-n` mode and already contains non-interactive-specific behavioral rules.
- **Should the benchmark shell tier include package managers?** Yes — `pip`, `pip3`, `npm` are needed for tasks that require dependency installation. The benchmark tier is explicitly opt-in and only used for eval runs.

### Deferred to Implementation

- Exact retry strategy for tool call leak detection (exponential backoff vs. immediate retry with modified temperature)
- Whether the edit-failure middleware should re-read the file and suggest a corrected `old_string`, or just report the failure

## Implementation Units

- [ ] **Unit 1: Strengthen non-interactive system prompt**

  **Goal:** Add verification instructions, compute-with-tools rule, exact-naming reinforcement, and no-questions rule to the non-interactive prompt path.

  **Requirements:** R1, R3, R4, R5

  **Dependencies:** None

  **Files:**
  - Modify: `libs/cli/deepagents_cli/agent.py` (the `get_system_prompt()` function, non-interactive branch)
  - Test: `libs/cli/tests/unit_tests/test_agent.py`

  **Approach:**
  - Extend the `interactive_preamble` string (lines 528-533) with completion-enforcement language
  - Extend `ambiguity_guidance` (lines 534-544) with the 3 new behavioral rules:
    1. "After writing a file, verify it exists and is non-empty using `ls` or `read_file`. After running a shell command, check the exit code and inspect output for errors."
    2. "When a task involves arithmetic, byte operations, data transformations, or processing structured data, write and execute a script — never attempt computation in your text response."
    3. "Use exact names, identifiers, class names, and file paths specified in the task. Do not rename, abbreviate, or 'improve' them."
  - Add to the preamble: "Complete the task fully. Do not ask follow-up questions, suggest manual steps, or say 'Would you like me to...'. If a step fails, try an alternative approach."

  **Patterns to follow:**
  - The existing `ambiguity_guidance` block is the canonical place for mode-specific behavioral rules
  - Keep each rule to 1-2 sentences — the prompt is already 248 lines

  **Test scenarios:**
  - Happy path: `get_system_prompt(interactive=False)` contains "verify it exists" and "write and execute a script" and "Do not ask follow-up questions"
  - Happy path: `get_system_prompt(interactive=True)` does NOT contain these non-interactive-only rules
  - Edge case: Prompt string has no unreplaced `{placeholders}` after interpolation

  **Verification:**
  - Unit tests pass
  - Non-interactive prompt contains all 4 new behavioral rules
  - Interactive prompt is unchanged

---

- [ ] **Unit 2: Add benchmark shell allow-list tier**

  **Goal:** Add a `benchmark` tier to the shell allow-list that includes build tools, interpreters, and package managers needed for eval tasks.

  **Requirements:** R6

  **Dependencies:** None

  **Files:**
  - Modify: `libs/cli/deepagents_cli/config.py` (add `BENCHMARK_SHELL_COMMANDS` tuple, update `parse_shell_allow_list`)
  - Test: `libs/cli/tests/unit_tests/test_config.py`

  **Approach:**
  - Define `BENCHMARK_SHELL_COMMANDS` as a superset of `RECOMMENDED_SAFE_SHELL_COMMANDS` that adds: `python3`, `python`, `pip`, `pip3`, `uv`, `npm`, `node`, `gcc`, `g++`, `make`, `cmake`, `rustc`, `cargo`, `openssl`, `git`, `curl`, `wget`, `tar`, `unzip`, `gzip`, `gunzip`, `chmod`, `mkdir`, `cp`, `mv`, `rm`, `touch`, `tee`, `sort`, `uniq`, `awk`, `sed`, `find`, `xargs`, `env`, `export`, `echo`, `printf`, `test`, `true`, `false`, `date`, `sleep`, `timeout`, `coqc` (Coq compiler for proof tasks)
  - In `parse_shell_allow_list()`, handle `'benchmark'` input alongside `'recommended'` and `'all'`
  - Update the CLI help text for `-S` to document the new tier

  **Patterns to follow:**
  - `RECOMMENDED_SAFE_SHELL_COMMANDS` tuple with docstring explaining the category
  - `parse_shell_allow_list()` switch logic for named tiers

  **Test scenarios:**
  - Happy path: `parse_shell_allow_list("benchmark")` returns list containing `python3`, `gcc`, `openssl`, `git`
  - Happy path: `parse_shell_allow_list("benchmark")` is a superset of `parse_shell_allow_list("recommended")`
  - Happy path: `is_shell_command_allowed("python3 script.py", benchmark_list)` returns True
  - Edge case: `is_shell_command_allowed("python3 -c '$(evil)'", benchmark_list)` returns False (dangerous pattern still blocked)
  - Happy path: `-S benchmark` is accepted as a valid CLI argument

  **Verification:**
  - `parse_shell_allow_list("benchmark")` returns a list that includes all recommended commands plus build tools
  - Dangerous patterns are still rejected even with the benchmark tier

---

- [ ] **Unit 3: Edit-file failure surfacing middleware**

  **Goal:** When `edit_file` returns a "no match" or failure status, inject an explicit warning message so the agent knows the edit didn't apply.

  **Requirements:** R2

  **Dependencies:** Unit 1 (the prompt must tell the agent to act on verification signals)

  **Files:**
  - Modify: `libs/cli/deepagents_cli/agent.py` (add `EditVerificationMiddleware`, wire it into the middleware stack)
  - Test: `libs/cli/tests/unit_tests/test_agent.py`

  **Approach:**
  - Create `EditVerificationMiddleware(AgentMiddleware)` following the `ShellAllowListMiddleware` pattern
  - Override `wrap_tool_call` to intercept `edit_file` tool results
  - After the tool executes, inspect the result content for failure indicators (the SDK's `edit_file` tool returns error text when `old_string` isn't found)
  - If a failure is detected, append a clear warning: `"⚠️ EDIT FAILED: The old_string was not found in the file. The file was NOT modified. Re-read the file to see its actual content before retrying."`
  - Wire into the middleware stack in `create_cli_agent()` — always active, not gated by mode

  **Patterns to follow:**
  - `ShellAllowListMiddleware._validate_tool_call()` — intercept-and-modify pattern
  - `wrap_tool_call(request, handler)` — call handler first, then inspect result

  **Test scenarios:**
  - Happy path: Successful edit passes through unchanged
  - Error path: Failed edit (no match) gets warning appended to tool message content
  - Edge case: Non-edit tools pass through unmodified
  - Edge case: Edit with empty old_string handled gracefully

  **Verification:**
  - When `edit_file` fails to match, the agent receives an explicit "EDIT FAILED" message in the tool result
  - Successful edits are not affected

---

- [ ] **Unit 4: Tool call syntax leak detection middleware**

  **Goal:** Detect when the model emits raw tool call markup in its text response instead of proper tool invocations, and trigger a retry.

  **Requirements:** R7

  **Dependencies:** None

  **Files:**
  - Modify: `libs/cli/deepagents_cli/agent.py` (add `ToolCallLeakDetectionMiddleware`, wire into stack)
  - Test: `libs/cli/tests/unit_tests/test_agent.py`

  **Approach:**
  - Create `ToolCallLeakDetectionMiddleware(AgentMiddleware)` that overrides `wrap_model_call`
  - After the model responds, scan the AI message text content for known leak patterns:
    - DeepSeek: `<｜tool▁calls▁begin｜>`, `<｜tool▁call▁begin｜>`, `<｜tool▁sep｜>`
    - Generic: `{"function":`, `"tool_calls": [`, other raw JSON tool patterns
  - If a leak is detected:
    - Log a warning with the leaked content
    - Strip the leaked content from the message
    - Set a flag to trigger a retry of the model call (up to 2 retries)
  - If retries are exhausted, pass through the original response with leaks stripped

  **Patterns to follow:**
  - `wrap_model_call(messages, handler)` override pattern from SDK middleware base class
  - `PatchToolCallsMiddleware` in the SDK for a similar post-model-response transformation

  **Test scenarios:**
  - Happy path: Normal AI message with no leaks passes through unchanged
  - Error path: Message containing `<｜tool▁calls▁begin｜>` triggers retry and logs warning
  - Error path: After 2 retries, message passes through with leaked content stripped
  - Edge case: Message containing `{"function":` in legitimate code context (inside a code block) is NOT treated as a leak — only match patterns outside code fences
  - Happy path: Non-DeepSeek model responses are not affected (patterns are model-specific)

  **Verification:**
  - DeepSeek tool call syntax in AI text triggers retry
  - Normal responses and code blocks containing JSON are not affected
  - Maximum 2 retries before pass-through

---

- [ ] **Unit 5: Non-interactive CWD documentation and prompt fix**

  **Goal:** Ensure the non-interactive mode system prompt shows the correct working directory (the user's CWD, not the CLI package directory).

  **Requirements:** R1 (prerequisite — the agent needs correct CWD in prompt to verify files)

  **Dependencies:** None

  **Files:**
  - Modify: `libs/cli/deepagents_cli/non_interactive.py` (pass user's CWD to agent creation)
  - Modify: `libs/cli/deepagents_cli/agent.py` (ensure `cwd` parameter flows to `get_system_prompt`)
  - Test: `libs/cli/tests/unit_tests/test_non_interactive.py`

  **Approach:**
  - In `run_non_interactive()`, capture `Path.cwd()` before any `uv run --directory` changes the process CWD
  - Pass it through to `get_system_prompt(cwd=user_cwd)` via the server config
  - The prompt already handles `cwd` parameter (line 573-594 of `agent.py`) — the issue is that `Path.cwd()` returns the CLI package dir when invoked via `uv run --directory`
  - Capture `os.environ.get("ORIGINAL_CWD")` or `Path.cwd()` at CLI entry point (`main.py`) before any directory changes

  **Patterns to follow:**
  - `ServerConfig` env-var bridge pattern for passing config to the server subprocess
  - Existing `cwd` parameter on `get_system_prompt()`

  **Test scenarios:**
  - Happy path: When run via `uv run --directory /pack/libs/cli`, the system prompt shows the user's original CWD, not `/pack/libs/cli`
  - Edge case: When CWD cannot be determined, falls back gracefully to `Path.cwd()`
  - Happy path: Sandbox mode is not affected (uses sandbox working directory)

  **Verification:**
  - System prompt's "Current Working Directory" section shows the user's invocation directory
  - Agent's `execute` tool runs commands from the correct directory

- [ ] **Unit 6: "Code in tools, not prose" prompt rule**

  **Goal:** Add explicit system prompt instruction that the agent must never write code only in its text response — it must always use `write_file`, `edit_file`, or `execute` to deliver code.

  **Requirements:** R8

  **Dependencies:** Unit 1 (add alongside the other non-interactive prompt rules)

  **Files:**
  - Modify: `libs/cli/deepagents_cli/agent.py` (`get_system_prompt()`, non-interactive branch)
  - Test: `libs/cli/tests/unit_tests/test_agent.py`

  **Approach:**
  - Add to the non-interactive `ambiguity_guidance` block: "Never write code only in your text response. If you produce code, it must go into a file via `write_file` or be executed via the shell tool. Describing code without saving or running it does not complete the task."
  - This is a small addition to Unit 1's prompt changes, but tracked separately for traceability to FM6-R1.

  **Patterns to follow:**
  - Same interpolation block as Unit 1

  **Test scenarios:**
  - Happy path: Non-interactive prompt contains "Never write code only in your text response"
  - Happy path: Interactive prompt does NOT contain this rule (in interactive mode, explaining code is fine)

  **Verification:**
  - Rule present in non-interactive prompt, absent from interactive prompt

---

- [ ] **Unit 7: Incomplete file reading — non-interactive override**

  **Goal:** When in non-interactive mode, override the "start with limit=100" file reading guidance to encourage reading more or using grep on large files.

  **Requirements:** R9

  **Dependencies:** Unit 1

  **Files:**
  - Modify: `libs/cli/deepagents_cli/system_prompt.md` (the "File Reading Best Practices" section)
  - Modify: `libs/cli/deepagents_cli/agent.py` (`get_system_prompt()`, add a non-interactive file reading override)
  - Test: `libs/cli/tests/unit_tests/test_agent.py`

  **Approach:**
  - The current prompt (lines 107-126 of `system_prompt.md`) tells agents to "First scan: `read_file(path, limit=100)`". This is fine for interactive mode but causes non-interactive agents to read only headers and give up.
  - Add a new placeholder `{file_reading_override}` after the File Reading Best Practices section
  - In the non-interactive branch of `get_system_prompt()`, inject: "In non-interactive mode: if a file read appears truncated or incomplete, do not stop — read the next section with `offset`, or use `grep` to find relevant patterns. Never conclude a file lacks content based on only the first 100 lines."
  - In interactive mode, inject empty string (no override)

  **Patterns to follow:**
  - Existing `{ambiguity_guidance}` placeholder pattern

  **Test scenarios:**
  - Happy path: Non-interactive prompt contains "do not stop" file reading override
  - Happy path: Interactive prompt does not contain this override
  - Edge case: Template has no unreplaced placeholders

  **Verification:**
  - Non-interactive prompt includes the file reading override
  - Interactive prompt is unchanged in the file reading section

---

- [ ] **Unit 8: Non-interactive inactivity timeout**

  **Goal:** Detect when the agent stalls (no tool calls or text output for a configurable duration) during non-interactive execution, and either nudge or terminate.

  **Requirements:** R10

  **Dependencies:** None

  **Files:**
  - Modify: `libs/cli/deepagents_cli/non_interactive.py` (`_run_agent_loop` or its caller)
  - Test: `libs/cli/tests/unit_tests/test_non_interactive.py`

  **Approach:**
  - In `_run_agent_loop()`, wrap the `agent.astream()` iteration with an inactivity timer
  - Track the timestamp of the last received chunk (text or tool call)
  - If no chunk arrives within `INACTIVITY_TIMEOUT_SEC` (default: 120s for non-interactive), terminate the agent loop and return exit code 1 with a clear error message: "Agent stalled — no output for {N}s. Task incomplete."
  - Use `asyncio.wait_for()` or a watchdog task pattern around the stream iteration
  - Make timeout configurable via environment variable `PACK_NI_TIMEOUT` for benchmark tuning
  - Do NOT apply this timeout in interactive mode (user pauses are normal)

  **Patterns to follow:**
  - Existing `KeyboardInterrupt` handling in `run_non_interactive()` (lines 925-927)
  - `asyncio.wait_for()` for async timeout patterns

  **Test scenarios:**
  - Happy path: Normal agent run completes within timeout — no effect
  - Error path: Agent produces no output for 120s — loop terminates with exit code 1 and error message
  - Edge case: Agent produces a text chunk then stalls — timer resets on each chunk, then fires after 120s of silence
  - Happy path: `PACK_NI_TIMEOUT=60` overrides default to 60s
  - Happy path: Interactive mode is not affected by timeout

  **Verification:**
  - Stalled non-interactive runs terminate with a clear error instead of hanging forever
  - Active runs are not affected by the timeout

---

- [ ] **Unit 9: Post-write Python syntax check middleware**

  **Goal:** After `write_file` creates or overwrites a `.py` file, automatically run a syntax check and report errors to the agent so it can fix them before moving on.

  **Requirements:** R11

  **Dependencies:** Unit 3 (uses the same middleware intercept pattern)

  **Files:**
  - Modify: `libs/cli/deepagents_cli/agent.py` (add `PythonSyntaxCheckMiddleware`, wire into stack)
  - Test: `libs/cli/tests/unit_tests/test_agent.py`

  **Approach:**
  - Create `PythonSyntaxCheckMiddleware(AgentMiddleware)` that overrides `wrap_tool_call`
  - After `write_file` executes successfully, check if the target path ends in `.py`
  - If so, run `ast.parse()` on the file contents (read the file, parse it — no subprocess needed)
  - If `SyntaxError` is raised, append a warning to the tool result: "⚠️ SYNTAX ERROR in {path}: {error}. The file was written but contains invalid Python. Fix the syntax before proceeding."
  - If parse succeeds, pass through silently (no noise on success)
  - This catches missing imports at the `import` statement level (SyntaxError), but NOT runtime ImportError (e.g., `from base_terminal import BaseTerminal` where the module exists but wasn't imported). For runtime import checking, a more invasive approach would be needed — defer that.
  - Only active in non-interactive mode to avoid slowing interactive workflows

  **Patterns to follow:**
  - `EditVerificationMiddleware` from Unit 3 — same intercept-and-inspect pattern
  - `ShellAllowListMiddleware` — gated activation based on mode

  **Test scenarios:**
  - Happy path: Writing valid Python — no extra output, passes through cleanly
  - Error path: Writing Python with syntax error — warning appended with line number and error description
  - Edge case: Writing non-Python files (`.js`, `.txt`) — no syntax check, passes through
  - Edge case: Writing a `.py` file that is empty — `ast.parse("")` succeeds, no warning
  - Happy path: Interactive mode — middleware is not active

  **Verification:**
  - Writing a `.py` file with `def foo(` (missing colon) produces a warning in the tool result
  - Writing valid `.py` files produces no extra output

## System-Wide Impact

- **Interaction graph:** System prompt changes affect all non-interactive runs. 4 new middleware classes run on tool calls (edit verification, tool call leak detection, Python syntax check) or model calls (leak detection). All additive — no existing behavior is removed.
- **Error propagation:** Edit verification and syntax check middleware append warnings to tool results — the agent sees them but is not forced to act. Tool call leak middleware triggers retries — capped at 2 to prevent loops. Inactivity timeout terminates the run with exit code 1.
- **State lifecycle risks:** Retry on tool call leak could cause duplicate tool calls if the first attempt partially executed. Mitigated by the fact that the leak pattern means NO tool calls executed (the model emitted text instead of tool calls). Inactivity timeout could kill a legitimately slow operation — 120s default is generous and configurable.
- **API surface parity:** The `-S benchmark` flag is new CLI surface. `PACK_NI_TIMEOUT` is a new env var. No existing flags change behavior.
- **Unchanged invariants:** Interactive mode prompt and behavior are unchanged (Units 6-9 are gated to non-interactive). `RECOMMENDED_SAFE_SHELL_COMMANDS` is unchanged. Existing middleware stack ordering is preserved. SDK core is not modified.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| System prompt changes could degrade interactive mode | Non-interactive rules are gated by `interactive=False` — interactive prompt is untouched |
| Tool call leak detection could have false positives on code blocks | Pattern matching excludes content inside code fences (triple backticks) |
| Benchmark shell tier could be used outside of eval contexts | Tier name "benchmark" makes intended use clear; security-conscious users will use "recommended" or explicit lists |
| Edit verification middleware could be noisy for intentional no-ops | Only triggers on actual "not found" errors, not on successful edits |
| CWD fix may not fully resolve the `--directory` issue | This fixes the prompt; the `execute` tool CWD is set separately by the backend's `root_dir`. Both need to align. |
| Inactivity timeout could kill legitimately slow model responses | 120s default is generous; configurable via `PACK_NI_TIMEOUT` env var |
| Python syntax check adds latency to every `.py` write | `ast.parse()` is <1ms for typical files; only runs in non-interactive mode |
| File reading override could make agents read entire large files | Override says "read more or grep" not "read everything" — agent still has judgment |

## Sources & References

- Benchmark report R1: `pack-bench-tb2/results/REPORT.md`
- Benchmark report R2: `pack-bench-tb2-r2/results/REPORT.md`
- Pack CLI architecture: `libs/cli/deepagents_cli/agent.py`, `config.py`, `non_interactive.py`
- Middleware pattern: `libs/cli/deepagents_cli/agent.py:ShellAllowListMiddleware`
- SDK middleware base: `libs/deepagents/deepagents/middleware/__init__.py`
