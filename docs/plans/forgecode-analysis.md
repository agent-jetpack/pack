# ForgeCode Analysis: What It Does Right (81.8% on TB2)

## Architecture Overview

ForgeCode is a Rust-based multi-agent system with 3 specialized agents:
- **FORGE** — execution agent (writes code, runs commands, modifies files)
- **MUSE** — planning agent (creates implementation plans, no code modifications)
- **SAGE** — research agent (read-only codebase exploration, no modifications)

Each agent has its own system prompt, tool set, and reasoning config.

## Key Patterns That Differentiate ForgeCode

### 1. Agent Specialization with Minimal Tool Sets

Each agent gets ONLY the tools it needs:

| Agent | Tools | Notable |
|-------|-------|---------|
| FORGE | sem_search, sage, fs_search, read, write, undo, remove, patch, shell, fetch, skill, todo_write, todo_read | Has SAGE as a tool — can delegate research |
| MUSE | sem_search, sage, search, read, fetch, plan | No write/shell — physically can't modify code |
| SAGE | sem_search, search, read, fetch | Read-only — 4 tools only |

**Key insight:** FORGE can call SAGE as a tool (agent-as-tool pattern). This means research happens in a separate context with a specialized prompt, not polluting the execution agent's context.

### 2. Doom Loop Detection

ForgeCode detects when the agent is stuck:
- Tracks consecutive identical tool calls (e.g., [A,A,A,A])
- Tracks repeating sequences (e.g., [read,write,patch] × 3)
- When detected: injects a system reminder telling the agent to try a different approach
- Template: "You appear to be stuck in a repetitive loop, having made {{consecutive_calls}} similar calls."

**Pack gap:** We have no doom loop detection. Our agent can waste all its turns retrying the same failed approach.

### 3. Tool Error Reflection

When a tool call fails, ForgeCode injects a reflection prompt:
```
You must now deeply reflect on the error above:
1. Pinpoint exactly what was wrong
2. Explain why that mistake happened
3. Make the correct tool call
Do NOT skip this reflection.
```

Plus a retry message with attempts remaining:
```
Tool call failed
- Attempts remaining: {{attempts_left}}
- Next steps: Analyze the error, identify the root cause, and adjust
```

**Pack gap:** Our edit verification middleware shows the error but doesn't force reflection. ForgeCode makes the agent STOP and THINK before retrying.

### 4. Aggressive Task Tracking (Todo Write)

The FORGE prompt says:
> "Use this tool VERY frequently to ensure that you are tracking your tasks... If you do not use this tool when planning, you may forget to do important tasks - and that is unacceptable."

> "Mark todos complete ONLY after: 1. Actually executing the implementation 2. Verifying it works"

**Pack gap:** Our prompt says "verify" but doesn't enforce todo-driven execution. ForgeCode makes the agent plan before acting and verify before checking off.

### 5. Strict Non-Creation Rules

> "NEVER create files unless they're absolutely necessary"
> "NEVER create documentation files unless explicitly requested"
> "Do what has been asked; nothing more, nothing less"

This prevents the agent from wasting turns on unnecessary files.

### 6. Proactive Compaction

Compact settings: trigger at 100K tokens, retain 6 messages, 200 message threshold, 20% eviction window. This is more aggressive than the default 85% trigger.

### 7. Read-Before-Edit Enforcement

The tool executor validates that the agent has READ a file before trying to EDIT it. This is enforced at the tool level, not just the prompt level.

### 8. Parallel Tool Calls

> "For maximum efficiency, whenever you need to perform multiple independent operations, invoke all relevant tools simultaneously rather than sequentially."

### 9. Semantic Search

ForgeCode has a semantic search tool (not just grep) that understands code meaning. Parameters: `SEM_SEARCH_LIMIT=200`, `TOP_K=20`.

### 10. Undo Tool

FORGE has an `undo` tool — if a file edit goes wrong, it can revert. This is a safety net that encourages the agent to be more aggressive with changes.

---

## What Pack Should Adopt (Priority Order)

### P0 — Highest impact for TB2 performance

1. **Doom loop detection** — detect repeated tool calls and inject "try a different approach" message. ForgeCode's pattern: track last N tool calls, detect repeats, inject system reminder.

2. **Tool error reflection** — when a tool fails, force the agent to reflect before retrying. Don't just show the error — demand analysis of what went wrong and why.

3. **Todo-driven execution** — make the agent plan tasks first, then execute, then verify each one. The prompt should say "ALWAYS create a task plan before starting work."

### P1 — High impact

4. **Agent-as-tool** — let FORGE call SAGE for research in a separate context. This keeps execution context clean and gives the research agent a specialized prompt. Our subagent system exists but isn't used this way in the Harbor wrapper.

5. **Read-before-edit enforcement** — enforce at the tool level, not just the prompt. Reject edit/patch calls if the file hasn't been read in the current session.

### P2 — Medium impact

6. **Retry message with attempts remaining** — show "Attempts remaining: N" so the agent knows how many chances it has left.
7. **Undo tool** — let the agent revert file changes if something goes wrong.
8. **Parallel tool call encouragement** — explicitly tell the agent to batch independent operations.

---

## Architecture Comparison

| Feature | ForgeCode | Pack |
|---------|-----------|------|
| Multi-agent | FORGE/MUSE/SAGE with separate prompts | Single agent with subagent support |
| Agent-as-tool | SAGE callable from FORGE | Subagents exist but not used in Harbor |
| Doom loop detection | Yes (consecutive + sequence detection) | No |
| Tool error reflection | Forced reflection prompt | Edit verification middleware (shows error only) |
| Todo-driven execution | Heavily enforced in prompt | Mentioned but not enforced |
| Read-before-edit | Tool-level enforcement | Prompt-level only |
| Compaction | 100K trigger, 6 msg retain | 120K trigger, 10 msg retain |
| Retry tracking | Shows attempts remaining | No attempt tracking |
| Undo | Yes (fs_undo tool) | No |
| Semantic search | Yes (200 results, top-k 20) | No |
| Token limit | 20480 per response | No explicit limit |
| Max requests/turn | 100 | No limit |
