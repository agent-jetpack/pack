---
title: "feat: Research-driven harness improvements (Goose/Droid/OpenSage insights)"
type: feat
status: completed
date: 2026-04-04
---

# Research-Driven Harness Improvements

## Overview

Comparative analysis of Goose (Block), Droid (Factory AI), and OpenSage (Berkeley) identified 5 architectural patterns that differentiate high-performing agent harnesses. Three are directly implementable in Pack's non-interactive mode without SDK changes.

## Implementation Units

- [x] **Unit 1: Environmental bootstrapping at session start**

  **Goal:** Pre-load system state (git status, directory tree, language versions, key file contents) into the system prompt so the agent starts with full context instead of wasting 3-5 tool calls on discovery.

  **Files:**
  - Modify: `libs/cli/deepagents_cli/agent.py` (add `_build_environment_bootstrap()`)
  - Modify: `libs/cli/deepagents_cli/non_interactive.py` (call bootstrap and inject into prompt)

  **Approach:**
  - Create `_build_environment_bootstrap(cwd: Path) -> str` that captures: `git status --short`, first 50 lines of directory tree, Python/Node/GCC versions, and first 20 lines of any README
  - Inject the result into the non-interactive system prompt as a "## Environment Context" section
  - Only for non-interactive mode — interactive users discover incrementally
  - Cap total bootstrap output at 1500 tokens to avoid prompt bloat

- [x] **Unit 2: File-anchored task state for long-running tasks**

  **Goal:** For non-interactive tasks, write a structured task plan to a temp file at the start. The agent reads it back on each iteration instead of relying on conversation history.

  **Files:**
  - Modify: `libs/cli/deepagents_cli/non_interactive.py` (write task file, add to prompt)

  **Approach:**
  - At the start of `_run_agent_loop`, write the task message to `{cwd}/.pack_task.md`
  - Add to the non-interactive prompt: "Your task plan is at .pack_task.md. Re-read it if you lose track of what to do."
  - On verification retry, append the error output to the task file so the agent has a persistent record
  - Clean up the file after task completion

- [x] **Unit 3: Compact non-interactive tool set**

  **Goal:** Reduce the tool surface for non-interactive mode to minimize tool-call failure rates. Droid's research shows tool reliability is the primary bottleneck — fewer, simpler tools improve task completion.

  **Files:**
  - Modify: `libs/cli/deepagents_cli/agent.py` (conditional tool filtering for non-interactive)

  **Approach:**
  - In non-interactive mode, exclude tools that are interactive-only: `ask_user`, `compact_conversation` (agent should just work, not manage context)
  - Exclude MCP documentation tools (langchain docs, API reference) unless explicitly requested — these add noise to the tool list
  - Keep core tools: `execute`, `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`, `web_search`, `fetch_url`, `task` (subagents)
  - This reduces tool count from ~20 to ~10, which per Droid's findings should reduce per-call error rate
