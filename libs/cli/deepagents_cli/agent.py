"""Agent management and creation for the CLI."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, LocalShellBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware import MemoryMiddleware, SkillsMiddleware

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from deepagents.backends.sandbox import SandboxBackendProtocol
    from deepagents.middleware.async_subagents import AsyncSubAgent
    from deepagents.middleware.subagents import CompiledSubAgent, SubAgent
    from langchain.agents.middleware import InterruptOnConfig
    from langchain.agents.middleware.types import AgentState
    from langchain.messages import ToolCall
    from langchain.tools import BaseTool
    from langchain_core.language_models import BaseChatModel
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.pregel import Pregel
    from langgraph.runtime import Runtime

    from deepagents_cli.mcp_tools import MCPServerInfo
    from deepagents_cli.output import OutputFormat

from deepagents_cli import theme
from deepagents_cli.config import (
    _ShellAllowAll,
    config,
    console,
    get_default_coding_instructions,
    get_glyphs,
    settings,
)
from deepagents_cli.configurable_model import ConfigurableModelMiddleware
from deepagents_cli.integrations.sandbox_factory import get_default_working_dir
from deepagents_cli.arch_lint import ArchLintMiddleware, ArchViolation, edges_from_config
from deepagents_cli.budget_observable import BudgetObservableMiddleware
from deepagents_cli.output_ceiling import OutputCeilingMiddleware
from deepagents_cli.policy import TaskPolicy
from deepagents_cli.progressive_disclosure import ProgressiveDisclosureMiddleware
from deepagents_cli.ratchet import Ratchet
from deepagents_cli.reviewer import ReviewerSubAgent
from deepagents_cli.reviewer_middleware import ReviewerMiddleware
from deepagents_cli.scope_enforcement import ScopeEnforcementMiddleware
from deepagents_cli.tool_result_enrichment import ToolResultEnrichmentMiddleware
from deepagents_cli.loop_detection import LoopDetectionMiddleware
from deepagents_cli.local_context import (
    LocalContextMiddleware,
    _AsyncExecutableBackend,
    _ExecutableBackend,
)
from deepagents_cli.precompletion_checklist import PreCompletionChecklistMiddleware
from deepagents_cli.project_utils import ProjectContext, get_server_project_context
from deepagents_cli.subagents import list_subagents
from deepagents_cli.unicode_security import (
    check_url_safety,
    detect_dangerous_unicode,
    format_warning_detail,
    render_with_unicode_markers,
    strip_dangerous_unicode,
    summarize_issues,
)

logger = logging.getLogger(__name__)

DEFAULT_AGENT_NAME = "agent"
"""The default agent name used when no `-a` flag is provided."""

REQUIRE_COMPACT_TOOL_APPROVAL: bool = True
"""When `True`, `compact_conversation` requires HITL approval like other gated tools."""


# Middleware classes extracted to deepagents_cli/middleware/. Re-exported here
# for backwards compatibility — existing imports of the form
# ``from deepagents_cli.agent import ShellAllowListMiddleware`` keep working.
from deepagents_cli.middleware import (  # noqa: E402  (re-export must follow module setup)
    DoomLoopDetectionMiddleware,
    EditVerificationMiddleware,
    ErrorReflectionMiddleware,
    PythonSyntaxCheckMiddleware,
    ReadBeforeWriteMiddleware,
    RequestBudgetMiddleware,
    ShellAllowListMiddleware,
    ToolCallLeakDetectionMiddleware,
)


def load_async_subagents(config_path: Path | None = None) -> list[AsyncSubAgent]:
    """Load async subagent definitions from `config.toml`.

    Reads the `[async_subagents]` section where each sub-table defines a remote
    LangGraph deployment:

    ```toml
    [async_subagents.researcher]
    description = "Research agent"
    url = "https://my-deployment.langsmith.dev"
    graph_id = "agent"
    ```

    Args:
        config_path: Path to config file.

            Defaults to `~/.deepagents/config.toml`.

    Returns:
        List of `AsyncSubAgent` specs (empty if section is absent or invalid).
    """
    if config_path is None:
        config_path = Path.home() / ".deepagents" / "config.toml"

    if not config_path.exists():
        return []

    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, PermissionError, OSError) as e:
        logger.warning("Could not read async subagents from %s: %s", config_path, e)
        console.print(
            f"[bold yellow]Warning:[/bold yellow] Could not read async subagents "
            f"from {config_path}: {e}",
        )
        return []

    section = data.get("async_subagents")
    if not isinstance(section, dict):
        return []

    required = {"description", "graph_id"}
    agents: list[AsyncSubAgent] = []
    for name, spec in section.items():
        if not isinstance(spec, dict):
            logger.warning("Skipping async subagent '%s': expected a table", name)
            continue
        missing = required - spec.keys()
        if missing:
            logger.warning(
                "Skipping async subagent '%s': missing fields %s", name, missing
            )
            continue
        agent: AsyncSubAgent = {
            "name": name,
            "description": spec["description"],
            "graph_id": spec["graph_id"],
        }
        if "url" in spec and isinstance(spec["url"], str):
            agent["url"] = spec["url"]
        if "headers" in spec and isinstance(spec["headers"], dict):
            agent["headers"] = spec["headers"]
        agents.append(agent)

    return agents


def list_agents(*, output_format: OutputFormat = "text") -> None:
    """List all available agents.

    Args:
        output_format: Output format — `'text'` (Rich) or `'json'`.
    """
    agents_dir = settings.user_deepagents_dir

    if not agents_dir.exists() or not any(agents_dir.iterdir()):
        if output_format == "json":
            from deepagents_cli.output import write_json

            write_json("list", [])
            return
        console.print("[yellow]No agents found.[/yellow]")
        console.print(
            "[dim]Agents will be created in ~/.deepagents/ "
            "when you first use them.[/dim]",
            style=theme.MUTED,
        )
        return

    if output_format == "json":
        from deepagents_cli.output import write_json

        agents = []
        for agent_path in sorted(agents_dir.iterdir()):
            if agent_path.is_dir():
                agent_name = agent_path.name
                agents.append(
                    {
                        "name": agent_name,
                        "path": str(agent_path),
                        "has_agents_md": (agent_path / "AGENTS.md").exists(),
                        "is_default": agent_name == DEFAULT_AGENT_NAME,
                    }
                )
        write_json("list", agents)
        return

    from rich.markup import escape as escape_markup

    console.print("\n[bold]Available Agents:[/bold]\n", style=theme.PRIMARY)

    for agent_path in sorted(agents_dir.iterdir()):
        if agent_path.is_dir():
            agent_name = escape_markup(agent_path.name)
            agent_md = agent_path / "AGENTS.md"
            is_default = agent_path.name == DEFAULT_AGENT_NAME
            default_label = " [dim](default)[/dim]" if is_default else ""

            bullet = get_glyphs().bullet
            if agent_md.exists():
                console.print(
                    f"  {bullet} [bold]{agent_name}[/bold]{default_label}",
                    style=theme.PRIMARY,
                )
                console.print(
                    f"    {escape_markup(str(agent_path))}",
                    style=theme.MUTED,
                )
            else:
                console.print(
                    f"  {bullet} [bold]{agent_name}[/bold]{default_label}"
                    " [dim](incomplete)[/dim]",
                    style=theme.WARNING,
                )
                console.print(
                    f"    {escape_markup(str(agent_path))}",
                    style=theme.MUTED,
                )

    console.print()


def reset_agent(
    agent_name: str,
    source_agent: str | None = None,
    *,
    dry_run: bool = False,
    output_format: OutputFormat = "text",
) -> None:
    """Reset an agent to default or copy from another agent.

    Args:
        agent_name: Name of the agent to reset.
        source_agent: Copy AGENTS.md from this agent instead of default.
        dry_run: If `True`, print what would happen without making changes.
        output_format: Output format — `'text'` (Rich) or `'json'`.

    Raises:
        SystemExit: If the source agent is not found.
    """
    agents_dir = settings.user_deepagents_dir
    agent_dir = agents_dir / agent_name

    if source_agent:
        source_dir = agents_dir / source_agent
        source_md = source_dir / "AGENTS.md"

        if not source_md.exists():
            console.print(
                f"[bold red]Error:[/bold red] Source agent '{source_agent}' not found "
                "or has no AGENTS.md\n"
                "  Available agents: deepagents agents list"
            )
            raise SystemExit(1)

        source_content = source_md.read_text()
        action_desc = f"contents of agent '{source_agent}'"
    else:
        source_content = get_default_coding_instructions()
        action_desc = "default"

    if dry_run:
        if output_format == "json":
            from deepagents_cli.output import write_json

            write_json(
                "reset",
                {
                    "agent": agent_name,
                    "reset_to": source_agent or "default",
                    "path": str(agent_dir),
                    "dry_run": True,
                },
            )
            return
        exists = "remove and recreate" if agent_dir.exists() else "create"
        console.print(f"Would {exists} {agent_dir} with {action_desc} prompt.")
        console.print("No changes made.", style=theme.MUTED)
        return

    if agent_dir.exists():
        shutil.rmtree(agent_dir)
        if output_format != "json":
            console.print(
                f"Removed existing agent directory: {agent_dir}", style=theme.WARNING
            )

    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_md = agent_dir / "AGENTS.md"
    agent_md.write_text(source_content)

    if output_format == "json":
        from deepagents_cli.output import write_json

        write_json(
            "reset",
            {
                "agent": agent_name,
                "reset_to": source_agent or "default",
                "path": str(agent_dir),
            },
        )
        return

    console.print(
        f"{get_glyphs().checkmark} Agent '{agent_name}' reset to {action_desc}",
        style=theme.PRIMARY,
    )
    console.print(f"Location: {agent_dir}\n", style=theme.MUTED)


MODEL_IDENTITY_RE = re.compile(r"### Model Identity\n\n.*?(?=###|\Z)", re.DOTALL)
"""Matches the `### Model Identity` section in the system prompt, up to the
next heading or end of string."""


def build_model_identity_section(
    name: str | None,
    provider: str | None = None,
    context_limit: int | None = None,
    unsupported_modalities: frozenset[str] = frozenset(),
) -> str:
    """Build the `### Model Identity` section for the system prompt.

    Args:
        name: Model identifier (e.g. `claude-opus-4-6`).
        provider: Provider identifier (e.g. `anthropic`).
        context_limit: Max input tokens from the model profile.
        unsupported_modalities: Input modalities not indicated as supported by
            the model profile (e.g. `{"audio", "video"}`).

    Returns:
        The section text including the heading and trailing newline,
        or an empty string if `name` is falsy.
    """
    if not name:
        return ""
    section = f"### Model Identity\n\nYou are running as model `{name}`"
    if provider:
        section += f" (provider: {provider})"
    section += ".\n"
    if context_limit:
        section += f"Your context window is {context_limit:,} tokens.\n"
    if unsupported_modalities:
        items = sorted(unsupported_modalities)
        if len(items) == 1:
            joined = items[0]
        elif len(items) == 2:  # noqa: PLR2004
            joined = f"{items[0]} and {items[1]}"
        else:
            joined = ", ".join(items[:-1]) + f", and {items[-1]}"
        section += (
            f"{joined.capitalize()} input may not be available for this model. "
            "Do not attempt to read or process these content types.\n"
        )
    section += "\n"
    return section


def _build_environment_bootstrap(cwd: Path) -> str:
    """Capture environment context for non-interactive mode.

    Pre-loads system state so the agent starts with full context
    instead of wasting tool calls on discovery.

    Args:
        cwd: The user's working directory.

    Returns:
        A formatted string with environment details, capped at ~1500 tokens.
    """
    import subprocess

    sections: list[str] = []

    # Git status
    try:
        git_out = subprocess.run(
            ["git", "status", "--short"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if git_out.returncode == 0 and git_out.stdout.strip():
            status = git_out.stdout.strip()[:500]
            sections.append(f"**Git status:**\n```\n{status}\n```")

        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if branch.returncode == 0 and branch.stdout.strip():
            sections.append(f"**Branch:** `{branch.stdout.strip()}`")
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Directory tree (top-level only)
    try:
        items = sorted(p.name for p in cwd.iterdir() if not p.name.startswith("."))
        if items:
            listing = "  ".join(items[:30])
            sections.append(f"**Files in workspace:** {listing}")
    except OSError:
        pass

    # Language versions
    for cmd, label in [
        (["python3", "--version"], "Python"),
        (["node", "--version"], "Node"),
        (["gcc", "--version"], "GCC"),
    ]:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                ver = result.stdout.strip().split("\n")[0]
                sections.append(f"**{label}:** {ver}")
        except (OSError, subprocess.TimeoutExpired):
            pass

    if not sections:
        return ""

    return (
        "\n## Environment Context\n\n"
        + "\n".join(sections)
        + "\n\n"
    )


def _resolve_reviewer_model(model: Any) -> Any:
    """Produce a LangChain chat model suitable for the reviewer pass.

    Accepts whatever ``create_cli_agent`` received as ``model``. Strings
    are resolved via ``init_chat_model``; concrete ``BaseChatModel``
    instances are reused. Returns ``None`` on any failure so the caller
    can skip the reviewer rather than crashing the whole run.

    A subtle decision: the reviewer currently reuses the main agent's
    model. That keeps configuration simple and avoids a separate key
    requirement; callers who want a cheaper reviewer can set an explicit
    ``reviewer_model`` kwarg once we add one (not yet needed).
    """
    if model is None:
        return None
    if not isinstance(model, str):
        # Already a BaseChatModel (or similar) — reuse directly.
        return model
    try:
        from langchain.chat_models import init_chat_model

        return init_chat_model(model)
    except Exception:  # noqa: BLE001  # reviewer failure must never kill the run
        logger.warning("Could not resolve reviewer model from %r", model, exc_info=True)
        return None


def get_system_prompt(
    assistant_id: str,
    sandbox_type: str | None = None,
    *,
    interactive: bool = True,
    cwd: str | Path | None = None,
) -> str:
    """Get the base system prompt for the agent.

    Loads the base system prompt template from `system_prompt.md` and
    interpolates dynamic sections (model identity, working directory,
    skills path, execution mode, and todo-list guidance for
    interactive vs headless).

    Args:
        assistant_id: The agent identifier for path references
        sandbox_type: Type of sandbox provider
            (`'agentcore'`, `'daytona'`, `'langsmith'`, `'modal'`, `'runloop'`).

            If `None`, agent is operating in local mode.
        interactive: When `False`, the prompt is tailored for headless
            non-interactive execution (no human in the loop).
        cwd: Override the working directory shown in the prompt.

    Returns:
        The system prompt string

    Example:
        ```txt
        You are running as model {MODEL} (provider: {PROVIDER}).

        Your context window is {CONTEXT_WINDOW} tokens.

        ... {CONDITIONAL SECTIONS} ...
        ```
    """
    template = (Path(__file__).parent / "system_prompt.md").read_text()

    skills_path = f"~/.deepagents/{assistant_id}/skills"

    if interactive:
        mode_description = "an interactive CLI on the user's computer"
        interactive_preamble = (
            "The user sends you messages and you respond with text and tool "
            "calls. Your tools run on the user's machine. The user can see "
            "your responses and tool outputs in real time, so keep them "
            "informed — but don't over-explain."
        )
        ambiguity_guidance = (
            "- If the request is ambiguous, ask questions before acting.\n"
            "- If asked how to approach something, explain first, then act."
        )
        todo_guidance = (
            "6. When first creating a todo list for a task, ALWAYS ask the user if "
            "the plan looks good before starting work\n"
            '   - Create the todos, then ask: "Does this plan '
            'look good?" or similar\n'
            "   - Wait for the user's response before marking the first todo as "
            "in_progress\n"
            "7. Update todo status promptly as you complete each item"
        )
    else:
        mode_description = (
            "non-interactive (headless) mode — there is no human operator "
            "monitoring your output in real time"
        )
        interactive_preamble = (
            "You received a single task and must complete it fully and "
            "autonomously. There is no human available to answer follow-up "
            "questions, so do NOT ask for clarification — make reasonable "
            "assumptions and proceed.\n\n"
            "Complete the task fully. Do not ask follow-up questions, suggest "
            "manual steps, or say 'Would you like me to...'. If a step fails, "
            "try an alternative approach automatically."
        )
        ambiguity_guidance = (
            "- Do NOT ask clarifying questions — there is no human to answer "
            "them. Make reasonable assumptions and proceed.\n"
            "- If you encounter ambiguity, choose the most reasonable "
            "interpretation and note your assumption briefly.\n"
            "- Always use non-interactive command variants — no human is "
            "available to respond to prompts. Examples: `npm init -y` not "
            "`npm init`, `apt-get install -y` not `apt-get install`, "
            "`yes |` or `--no-input`/`--non-interactive` flags where "
            "available. Never run commands that block waiting for stdin.\n"
            "- After writing a file, verify it exists and is non-empty using "
            "`ls` or `read_file`. After running a shell command, check the "
            "exit code and inspect output for errors.\n"
            "- When a task involves arithmetic, byte operations, data "
            "transformations, or processing structured data, write and "
            "execute a script — never attempt computation in your text "
            "response.\n"
            "- Use exact names, identifiers, class names, and file paths "
            "specified in the task. Do not rename, abbreviate, or 'improve' "
            "them.\n"
            "- Never write code only in your text response. If you produce "
            "code, it must go into a file via `write_file` or be executed "
            "via the shell tool. Describing code without saving or running "
            "it does not complete the task.\n"
            "- If a file read appears truncated or incomplete, do not stop — "
            "read the next section with `offset`, or use `grep` to find "
            "relevant patterns. Never conclude a file lacks content based on "
            "only the first 100 lines."
        )
        todo_guidance = (
            "6. There is no human operator in this mode — do NOT ask the user to "
            "approve your plan or wait for a reply.\n"
            "   After you create todos for a multi-step task, mark the first item "
            "`in_progress` immediately and start work.\n"
            "   If the plan needs adjustment, revise the todo list yourself; do "
            "not block on human confirmation.\n"
            "7. Update todo status promptly as you complete each item"
        )

    model_identity_section = build_model_identity_section(
        settings.model_name,
        provider=settings.model_provider,
        context_limit=settings.model_context_limit,
        unsupported_modalities=settings.model_unsupported_modalities,
    )

    # Build working directory section (local vs sandbox)
    if sandbox_type:
        working_dir = get_default_working_dir(sandbox_type)
        working_dir_section = (
            f"### Current Working Directory\n\n"
            f"You are operating in a **remote Linux sandbox** at `{working_dir}`.\n\n"
            f"All code execution and file operations happen in this sandbox "
            f"environment.\n\n"
            f"**Important:**\n"
            f"- The CLI is running locally on the user's machine, but you execute "
            f"code remotely\n"
            f"- Use `{working_dir}` as your working directory for all operations\n"
            f"- **You do NOT have access to the user's local filesystem.** Paths "
            f"like `/Users/...`, `/home/<local-user>/...`, `C:\\...`, etc. do not "
            f"exist in this sandbox. Never reference or attempt to read/write local "
            f"paths — all files must be within the sandbox at `{working_dir}`\n"
            f"- When delegating to subagents, ensure they also use sandbox paths "
            f"(`{working_dir}/...`), not local paths\n\n"
        )
    else:
        if cwd is not None:
            resolved_cwd = Path(cwd)
        else:
            try:
                resolved_cwd = Path.cwd()
            except OSError:
                logger.warning(
                    "Could not determine working directory for system prompt",
                    exc_info=True,
                )
                resolved_cwd = Path()
        cwd = resolved_cwd
        working_dir_section = (
            f"### Current Working Directory\n\n"
            f"The filesystem backend is currently operating in: `{cwd}`\n\n"
            f"### File System and Paths\n\n"
            f"**IMPORTANT - Path Handling:**\n"
            f"- All file paths must be absolute paths (e.g., `{cwd}/file.txt`)\n"
            f"- Use the working directory to construct absolute paths\n"
            f"- Example: To create a file in your working directory, "
            f"use `{cwd}/research_project/file.md`\n"
            f"- Never use relative paths - always construct full absolute paths\n\n"
        )

    # Build environment bootstrap for non-interactive mode
    env_bootstrap = ""
    if not interactive and cwd is not None:
        env_bootstrap = _build_environment_bootstrap(Path(cwd) if not isinstance(cwd, Path) else cwd)

    result = (
        template.replace("{mode_description}", mode_description)
        .replace("{interactive_preamble}", interactive_preamble)
        .replace("{ambiguity_guidance}", ambiguity_guidance)
        .replace("{todo_guidance}", todo_guidance)
        .replace("{model_identity_section}", model_identity_section)
        .replace("{working_dir_section}", working_dir_section + env_bootstrap)
        .replace("{skills_path}", skills_path)
    )

    # Detect unreplaced placeholders (defense-in-depth for template typos)
    unreplaced = re.findall(r"\{[a-z_]+\}", result)
    if unreplaced:
        logger.warning("System prompt contains unreplaced placeholders: %s", unreplaced)

    return result


def _format_write_file_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format write_file tool call for approval prompt.

    Returns:
        Formatted description string for the write_file tool call.
    """
    args = tool_call["args"]
    file_path = args.get("file_path", "unknown")

    action = "Overwrite" if Path(file_path).exists() else "Create"

    return f"Action: {action} file"


def _format_edit_file_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format edit_file tool call for approval prompt.

    Returns:
        Formatted description string for the edit_file tool call.
    """
    args = tool_call["args"]
    replace_all = bool(args.get("replace_all", False))

    scope = "all occurrences" if replace_all else "single occurrence"
    return f"Action: Replace text ({scope})"


def _format_web_search_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format web_search tool call for approval prompt.

    Returns:
        Formatted description string for the web_search tool call.
    """
    args = tool_call["args"]
    query = args.get("query", "unknown")
    max_results = args.get("max_results", 5)

    return (
        f"Query: {query}\nMax results: {max_results}\n\n"
        f"{get_glyphs().warning}  This will use Tavily API credits"
    )


def _format_fetch_url_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format fetch_url tool call for approval prompt.

    Returns:
        Formatted description string for the fetch_url tool call.
    """
    args = tool_call["args"]
    url = str(args.get("url", "unknown"))
    display_url = strip_dangerous_unicode(url)
    timeout = args.get("timeout", 30)
    safety = check_url_safety(url)

    warning_lines: list[str] = []
    if not safety.safe:
        detail = format_warning_detail(safety.warnings)
        warning_lines.append(f"{get_glyphs().warning}  URL warning: {detail}")
    if safety.decoded_domain:
        warning_lines.append(
            f"{get_glyphs().warning}  Decoded domain: {safety.decoded_domain}"
        )

    warning_block = "\n".join(warning_lines)
    if warning_block:
        warning_block = f"\n{warning_block}"

    return (
        f"URL: {display_url}\nTimeout: {timeout}s\n\n"
        f"{get_glyphs().warning}  Will fetch and convert web content to markdown"
        f"{warning_block}"
    )


def _format_task_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format task (subagent) tool call for approval prompt.

    The task tool signature is: task(description: str, subagent_type: str)
    The description contains all instructions that will be sent to the subagent.

    Returns:
        Formatted description string for the task tool call.
    """
    args = tool_call["args"]
    description = args.get("description", "unknown")
    subagent_type = args.get("subagent_type", "unknown")

    # Truncate description if too long for display
    description_preview = description
    if len(description) > 500:  # noqa: PLR2004  # Subagent description length threshold
        description_preview = description[:500] + "..."

    glyphs = get_glyphs()
    separator = glyphs.box_horizontal * 40
    warning_msg = "Subagent will have access to file operations and shell commands"
    return (
        f"Subagent Type: {subagent_type}\n\n"
        f"{glyphs.warning} {warning_msg} {glyphs.warning}\n\n"
        f"Task Instructions:\n"
        f"{separator}\n"
        f"{description_preview}"
    )


def _format_execute_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format execute tool call for approval prompt.

    Returns:
        Formatted description string for the execute tool call.
    """
    args = tool_call["args"]
    command_raw = str(args.get("command", "N/A"))
    command = strip_dangerous_unicode(command_raw)
    project_context = get_server_project_context()
    effective_cwd = (
        str(project_context.user_cwd)
        if project_context is not None
        else str(Path.cwd())
    )
    lines = [f"Execute Command: {command}", f"Working Directory: {effective_cwd}"]

    issues = detect_dangerous_unicode(command_raw)
    if issues:
        summary = summarize_issues(issues)
        lines.append(f"{get_glyphs().warning}  Hidden Unicode detected: {summary}")
        raw_marked = render_with_unicode_markers(command_raw)
        if len(raw_marked) > 220:  # noqa: PLR2004  # UI display truncation threshold
            raw_marked = raw_marked[:220] + "..."
        lines.append(f"Raw: {raw_marked}")

    return "\n".join(lines)


def _add_interrupt_on() -> dict[str, InterruptOnConfig]:
    """Configure human-in-the-loop interrupt settings for all gated tools.

    Every tool that can have side effects or access external resources
    (shell execution, file writes/edits, web search, URL fetch, task
    delegation) is gated behind an approval prompt unless auto-approve
    is enabled.

    Returns:
        Dictionary mapping tool names to their interrupt configuration.
    """
    execute_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_execute_description,  # type: ignore[typeddict-item]  # Callable description narrower than TypedDict expects
    }

    write_file_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_write_file_description,  # type: ignore[typeddict-item]  # Callable description narrower than TypedDict expects
    }

    edit_file_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_edit_file_description,  # type: ignore[typeddict-item]  # Callable description narrower than TypedDict expects
    }

    web_search_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_web_search_description,  # type: ignore[typeddict-item]  # Callable description narrower than TypedDict expects
    }

    fetch_url_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_fetch_url_description,  # type: ignore[typeddict-item]  # Callable description narrower than TypedDict expects
    }

    task_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_task_description,  # type: ignore[typeddict-item]  # Callable description narrower than TypedDict expects
    }

    async_subagent_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": "Launch, update, or cancel a remote async subagent.",
    }

    interrupt_map: dict[str, InterruptOnConfig] = {
        "execute": execute_interrupt_config,
        "write_file": write_file_interrupt_config,
        "edit_file": edit_file_interrupt_config,
        "web_search": web_search_interrupt_config,
        "fetch_url": fetch_url_interrupt_config,
        "task": task_interrupt_config,
        "launch_async_subagent": async_subagent_interrupt_config,
        "update_async_subagent": async_subagent_interrupt_config,
        "cancel_async_subagent": async_subagent_interrupt_config,
    }

    if REQUIRE_COMPACT_TOOL_APPROVAL:
        interrupt_map["compact_conversation"] = {
            "allowed_decisions": ["approve", "reject"],
            "description": (
                "Offloads older messages to backend storage and "
                "replaces them with a summary, freeing context "
                "window space. Recent messages are kept as-is. "
                "Full history remains available for retrieval."
            ),
        }

    return interrupt_map


def create_cli_agent(
    model: str | BaseChatModel,
    assistant_id: str,
    *,
    tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None,
    sandbox: SandboxBackendProtocol | None = None,
    sandbox_type: str | None = None,
    system_prompt: str | None = None,
    interactive: bool = True,
    auto_approve: bool = False,
    interrupt_shell_only: bool = False,
    shell_allow_list: list[str] | None = None,
    enable_ask_user: bool = True,
    enable_memory: bool = True,
    enable_skills: bool = True,
    enable_shell: bool = True,
    checkpointer: BaseCheckpointSaver | None = None,
    mcp_server_info: list[MCPServerInfo] | None = None,
    cwd: str | Path | None = None,
    project_context: ProjectContext | None = None,
    async_subagents: list[AsyncSubAgent] | None = None,
    task_hints: dict[str, str] | None = None,
    prompt_env_override: dict[str, str | None] | None = None,
    budget_total_sec: int | None = None,
    task_policy: TaskPolicy | None = None,
    context_pack: Any = None,
    ratchet_dir: str | Path | None = None,
) -> tuple[Pregel, CompositeBackend]:
    """Create a CLI-configured agent with flexible options.

    This is the main entry point for creating a deepagents CLI agent, usable
    both internally and from external code (e.g., benchmarking frameworks).

    Args:
        model: LLM model to use (e.g., `'anthropic:claude-sonnet-4-6'`)
        assistant_id: Agent identifier for memory/state storage
        tools: Additional tools to provide to agent
        sandbox: Optional sandbox backend for remote execution
            (e.g., `ModalSandbox`).

            If `None`, uses local filesystem + shell.
        sandbox_type: Type of sandbox provider
            (`'agentcore'`, `'daytona'`, `'langsmith'`, `'modal'`, `'runloop'`).
            Used for system prompt generation.
        system_prompt: Override the default system prompt.

            If `None`, generates one based on `sandbox_type`, `assistant_id`,
            and `interactive`.
        interactive: When `False`, the auto-generated system prompt is
            tailored for headless non-interactive execution. Ignored when
            `system_prompt` is provided explicitly.
        auto_approve: If `True`, no tools trigger human-in-the-loop
            interrupts — all calls (shell execution, file writes/edits,
            web search, URL fetch) run automatically.

            If `False`, tools pause for user confirmation via the approval menu.
            See `_add_interrupt_on` for the full list of gated tools.
        interrupt_shell_only: If `True`, all HITL interrupts are disabled;
            shell commands are validated inline by `ShellAllowListMiddleware`
            against the configured allow-list instead.

            Used in non-interactive mode with a restrictive shell allow-list
            to avoid splitting traces into multiple LangSmith runs.

            Has no effect when `auto_approve` is `True` (interrupts are already
            disabled) or when `shell_allow_list` is `SHELL_ALLOW_ALL`.
        shell_allow_list: Explicit restrictive shell allow-list forwarded from
            the CLI process. When provided (and `interrupt_shell_only` is
            `True`), used directly instead of reading `settings.shell_allow_list`
            (which may not be set in the server subprocess environment).
        enable_ask_user: Enable `AskUserMiddleware` so the agent can ask
            clarifying questions.

            Disabled in non-interactive mode.
        enable_memory: Enable `MemoryMiddleware` for persistent memory
        enable_skills: Enable `SkillsMiddleware` for custom agent skills
        enable_shell: Enable shell execution via `LocalShellBackend`
            (only in local mode). When enabled, the `execute` tool is available.
        checkpointer: Optional checkpointer for session persistence.
            When `None`, the graph is compiled without a checkpointer.
        mcp_server_info: MCP server metadata to surface in the system prompt.
        cwd: Override the working directory for the agent's filesystem backend
            and system prompt.
        project_context: Explicit project path context for project-sensitive
            behavior such as project `AGENTS.md` files, skills, subagents, and
            MCP trust.
        async_subagents: Remote LangGraph deployments to expose as async subagent tools.

            Loaded from `[async_subagents]` in `config.toml` or passed directly.

    Returns:
        2-tuple of `(agent_graph, backend)`

            - `agent_graph`: Configured LangGraph Pregel instance ready
                for execution
            - `composite_backend`: `CompositeBackend` for file operations
    """
    tools = tools or []

    # Non-interactive mode: filter out noisy MCP tools that add to the tool
    # surface without helping task completion. Droid's research shows tool
    # reliability is the primary bottleneck — fewer tools = lower compound
    # error rate.
    if not interactive and tools:
        _NI_TOOL_EXCLUDE_PREFIXES = (
            "docs-",       # Documentation search tools (langchain docs, etc.)
            "reference-",  # API reference lookup tools
        )
        tools = [
            t for t in tools
            if not any(
                getattr(t, "name", "").startswith(prefix)
                for prefix in _NI_TOOL_EXCLUDE_PREFIXES
            )
        ]

    effective_cwd = (
        Path(cwd)
        if cwd is not None
        else (project_context.user_cwd if project_context is not None else None)
    )

    # Setup agent directory for persistent memory (if enabled)
    if enable_memory or enable_skills:
        agent_dir = settings.ensure_agent_dir(assistant_id)
        agent_md = agent_dir / "AGENTS.md"
        if not agent_md.exists():
            # Create empty file for user customizations
            # Base instructions are loaded fresh from get_system_prompt()
            agent_md.touch()

    # Skills directories (if enabled)
    skills_dir = None
    user_agent_skills_dir = None
    project_skills_dir = None
    project_agent_skills_dir = None
    if enable_skills:
        skills_dir = settings.ensure_user_skills_dir(assistant_id)
        user_agent_skills_dir = settings.get_user_agent_skills_dir()
        project_skills_dir = (
            project_context.project_skills_dir()
            if project_context is not None
            else settings.get_project_skills_dir()
        )
        project_agent_skills_dir = (
            project_context.project_agent_skills_dir()
            if project_context is not None
            else settings.get_project_agent_skills_dir()
        )

    # Load custom subagents from filesystem
    custom_subagents: list[SubAgent | CompiledSubAgent] = []
    restrictive_shell_allow_list: list[str] | None = None
    if interrupt_shell_only and not auto_approve:
        # Prefer the explicitly forwarded allow-list (set by the CLI process
        # and passed through ServerConfig).  Fall back to settings only for
        # direct callers (e.g. benchmarking frameworks) that don't go through
        # the server subprocess path.
        if shell_allow_list:
            restrictive_shell_allow_list = list(shell_allow_list)
        elif settings.shell_allow_list and not isinstance(
            settings.shell_allow_list, _ShellAllowAll
        ):
            restrictive_shell_allow_list = list(settings.shell_allow_list)
        else:
            logger.warning(
                "interrupt_shell_only=True but no restrictive shell allow-list "
                "available; falling back to standard HITL interrupts"
            )

    user_agents_dir = settings.get_user_agents_dir(assistant_id)
    project_agents_dir = (
        project_context.project_agents_dir()
        if project_context is not None
        else settings.get_project_agents_dir()
    )

    for subagent_meta in list_subagents(
        user_agents_dir=user_agents_dir,
        project_agents_dir=project_agents_dir,
    ):
        subagent: SubAgent = {
            "name": subagent_meta["name"],
            "description": subagent_meta["description"],
            "system_prompt": subagent_meta["system_prompt"],
        }
        if subagent_meta["model"]:
            subagent["model"] = subagent_meta["model"]
        if restrictive_shell_allow_list is not None:
            subagent["middleware"] = [
                ShellAllowListMiddleware(restrictive_shell_allow_list)
            ]
        custom_subagents.append(subagent)

    if restrictive_shell_allow_list is not None:
        from deepagents.middleware.subagents import (
            GENERAL_PURPOSE_SUBAGENT,
            SubAgent as RuntimeSubAgent,
        )

        if not any(
            subagent["name"] == GENERAL_PURPOSE_SUBAGENT["name"]
            for subagent in custom_subagents
        ):
            general_purpose_subagent: RuntimeSubAgent = {
                "name": GENERAL_PURPOSE_SUBAGENT["name"],
                "description": GENERAL_PURPOSE_SUBAGENT["description"],
                "system_prompt": GENERAL_PURPOSE_SUBAGENT["system_prompt"],
                "middleware": [ShellAllowListMiddleware(restrictive_shell_allow_list)],
            }
            custom_subagents.append(general_purpose_subagent)

    # Build middleware stack based on enabled features
    agent_middleware = []
    agent_middleware.append(ConfigurableModelMiddleware())

    # Token state: adds _context_tokens to graph state (checkpointed, not
    # passed to model).  Must be registered before any middleware that might
    # read the channel.
    from deepagents_cli.token_state import TokenStateMiddleware

    agent_middleware.append(TokenStateMiddleware())

    # Add ask_user middleware (must be early so its tool is available)
    if enable_ask_user:
        from deepagents_cli.ask_user import AskUserMiddleware

        agent_middleware.append(AskUserMiddleware())

    # Add memory middleware
    if enable_memory:
        memory_sources = [str(settings.get_user_agent_md_path(assistant_id))]
        project_agent_md_paths = (
            project_context.project_agent_md_paths()
            if project_context is not None
            else settings.get_project_agent_md_path()
        )
        memory_sources.extend(str(p) for p in project_agent_md_paths)

        agent_middleware.append(
            MemoryMiddleware(
                backend=FilesystemBackend(),
                sources=memory_sources,
            )
        )

    # Add skills middleware
    if enable_skills:
        # Lowest to highest precedence:
        # built-in -> user .deepagents -> user .agents
        # -> project .deepagents -> project .agents
        # -> user .claude (experimental) -> project .claude (experimental)
        sources = [str(settings.get_built_in_skills_dir())]
        sources.extend([str(skills_dir), str(user_agent_skills_dir)])
        if project_skills_dir:
            sources.append(str(project_skills_dir))
        if project_agent_skills_dir:
            sources.append(str(project_agent_skills_dir))

        # Experimental: Claude Code skill directories
        user_claude_skills_dir = settings.get_user_claude_skills_dir()
        if user_claude_skills_dir.exists():
            sources.append(str(user_claude_skills_dir))
        project_claude_skills_dir = settings.get_project_claude_skills_dir()
        if project_claude_skills_dir:
            sources.append(str(project_claude_skills_dir))

        agent_middleware.append(
            SkillsMiddleware(
                backend=FilesystemBackend(),
                sources=sources,
            )
        )

    # CONDITIONAL SETUP: Local vs Remote Sandbox
    if sandbox is None:
        # ========== LOCAL MODE ==========
        root_dir = effective_cwd if effective_cwd is not None else Path.cwd()
        if enable_shell:
            # Create environment for shell commands
            # Restore user's original LANGSMITH_PROJECT so their code traces separately
            shell_env = os.environ.copy()
            if settings.user_langchain_project:
                shell_env["LANGSMITH_PROJECT"] = settings.user_langchain_project

            # Use LocalShellBackend for filesystem + shell execution.
            # The SDK's FilesystemMiddleware exposes per-command timeout
            # on the execute tool natively.
            backend = LocalShellBackend(
                root_dir=root_dir,
                inherit_env=True,
                env=shell_env,
            )
        else:
            # No shell access - use plain FilesystemBackend
            backend = FilesystemBackend(root_dir=root_dir)
    else:
        # ========== REMOTE SANDBOX MODE ==========
        backend = sandbox  # Remote sandbox (ModalSandbox, etc.)
        # Note: Shell middleware not used in sandbox mode
        # File operations and execute tool are provided by the sandbox backend

    # Local context middleware (git info, directory tree, etc.).
    if isinstance(backend, (_ExecutableBackend, _AsyncExecutableBackend)):
        agent_middleware.append(
            LocalContextMiddleware(backend=backend, mcp_server_info=mcp_server_info)
        )

    # Add shell allow-list middleware when interrupt_shell_only is active.
    shell_middleware_added = False
    if restrictive_shell_allow_list is not None:
        agent_middleware.append(ShellAllowListMiddleware(restrictive_shell_allow_list))
        shell_middleware_added = True

    # Ratchet (Phase A.3 + M2): when ratchet_dir is supplied, load the
    # existing violation state and route all enforcement middleware's
    # rejections to it. Existing violations at run start are tolerated;
    # new ones are blocked and persisted to .harness/violations.json.
    _ratchet = Ratchet(harness_dir=ratchet_dir) if ratchet_dir is not None else None
    _existing_arch_violations: set[tuple[str, str]] = set()
    if _ratchet is not None:
        try:
            for violation in _ratchet.load_violations():
                # Arch-lint violation keys are (importer_pkg, imported_pkg).
                if violation.rule.startswith("arch.forbidden_import"):
                    # `subject` is stored as "<importer>:<imported>"; split.
                    parts = violation.subject.split(":", 1)
                    if len(parts) == 2:
                        _existing_arch_violations.add((parts[0], parts[1]))
        except Exception:  # noqa: BLE001  # ratchet failure must not abort agent construction
            logger.warning("Ratchet load failed; enforcement runs without seeded state", exc_info=True)

    def _record_scope_violation(tool: str, path: str, reason: str) -> None:
        if _ratchet is None:
            return
        rule_key = f"scope.{reason.split()[0].lower()}"
        try:
            _ratchet.record(rule=rule_key, subject=path, reason=f"{tool}: {reason}")
        except Exception:  # noqa: BLE001  # persistence failure is non-fatal
            logger.debug("Ratchet scope record failed", exc_info=True)

    def _record_arch_violation(path: str, violation: ArchViolation) -> None:
        if _ratchet is None:
            return
        subject = f"{violation.importer}:{violation.imported}"
        reason = f"{path}: {violation.summary()}"
        try:
            _ratchet.record(
                rule="arch.forbidden_import",
                subject=subject,
                reason=reason,
            )
        except Exception:  # noqa: BLE001
            logger.debug("Ratchet arch record failed", exc_info=True)

    # Scope enforcement (Phase A harness layer): when a TaskPolicy is
    # supplied, gate file writes against its allowed_paths globs + the
    # max_files_changed cap. No-ops when task_policy is None, preserving
    # existing behaviour for callers that haven't opted into the harness.
    if task_policy is not None:
        agent_middleware.append(
            ScopeEnforcementMiddleware(
                policy=task_policy,
                violation_recorder=_record_scope_violation if _ratchet else None,
            ),
        )

    # Arch lint (Phase D.1 + M2 + sharp-edge 2): enforce the package
    # dependency direction. Edge map is sourced from
    # ``.harness/config.yaml`` when present (via
    # ``edges_from_config``); falls back to the hardcoded
    # ``PACKAGE_EDGES`` table otherwise.
    _arch_edges = None
    _arch_repo_root: str | None = None
    try:
        from deepagents_cli.harness_config import find_harness_dir, load_config

        _harness_dir = find_harness_dir()
        if _harness_dir is not None:
            _harness_config = load_config(_harness_dir)
            _arch_edges = edges_from_config(_harness_config)
            _arch_repo_root = str(_harness_dir.parent)
    except Exception:  # noqa: BLE001  # config load must never abort agent construction
        logger.debug("Arch-lint config load failed; using defaults", exc_info=True)

    agent_middleware.append(
        ArchLintMiddleware(
            existing_violations=_existing_arch_violations,
            violation_recorder=_record_arch_violation if _ratchet else None,
            edges=_arch_edges,
            repo_root=_arch_repo_root,
        ),
    )

    # Always-on tool result middleware
    agent_middleware.append(EditVerificationMiddleware())
    agent_middleware.append(ReadBeforeWriteMiddleware())
    # Two complementary loop detectors: DoomLoop catches tight same-tool-same-args
    # repeats (any tool, stateful); LoopDetection catches slow file-edit thrashing
    # (8/12 edits on the same path, stateless — survives checkpoint/resume).
    agent_middleware.append(DoomLoopDetectionMiddleware())
    agent_middleware.append(LoopDetectionMiddleware())
    agent_middleware.append(ErrorReflectionMiddleware())
    agent_middleware.append(RequestBudgetMiddleware(max_requests=100))
    if not interactive:
        agent_middleware.append(PythonSyntaxCheckMiddleware())
        agent_middleware.append(ToolCallLeakDetectionMiddleware())
        # Pre-completion verification gate: force a verification checklist
        # before the agent can declare done. Targets the "wrong_solution"
        # failure mode where agents submit without running tests.
        agent_middleware.append(PreCompletionChecklistMiddleware())
        # Reviewer pass (Phase C + sharp-edge 6): when the policy
        # demands it, invoke a separate reviewer sub-agent before
        # allowing termination. The reviewer critiques the main
        # agent's work and either approves or injects specific
        # fixes back into the loop. With ``repo_root`` set the
        # reviewer also receives arch-lint and business-rule output
        # for the touched files. No-ops when policy.require_reviewer
        # is False or no model is resolvable.
        if task_policy is not None and task_policy.require_reviewer:
            _reviewer_model = _resolve_reviewer_model(model)
            if _reviewer_model is not None:
                agent_middleware.append(
                    ReviewerMiddleware(
                        reviewer=ReviewerSubAgent(model=_reviewer_model),
                        policy=task_policy,
                        repo_root=_arch_repo_root,
                        arch_edges=_arch_edges,
                    ),
                )
        # Output ceiling: nudge the agent to commit to a solution when
        # cumulative completion tokens spike. Targets the "single-shot
        # dump" failure mode (30-60K tokens of analysis in 2-4 steps
        # without ever writing code).
        agent_middleware.append(OutputCeilingMiddleware())
        # Progressive tool disclosure: drop distractor tools from the
        # model's schema for classified coding tasks. No-ops when
        # task_hints is empty so non-benchmark sessions keep the full
        # surface.
        agent_middleware.append(
            ProgressiveDisclosureMiddleware(task_hints=task_hints),
        )
        # Tool result enrichment: append derived-signal markers (line
        # counts, exit codes, match counts) to tool outputs so the
        # agent can pattern-match instead of re-deriving state on
        # every return.
        agent_middleware.append(ToolResultEnrichmentMiddleware())
        # Budget observability: append remaining time to every tool result
        # so the agent can reason about its runway. Converts silent timeouts
        # into informed best-so-far submissions. Callers (e.g. Harbor) that
        # know the real per-task agent_timeout should pass it via
        # ``budget_total_sec``; otherwise the middleware's conservative
        # default applies.
        budget_kwargs = (
            {"total_budget_sec": budget_total_sec} if budget_total_sec else {}
        )
        agent_middleware.append(BudgetObservableMiddleware(**budget_kwargs))

    # Get or use custom system prompt
    if system_prompt is None:
        system_prompt = get_system_prompt(
            assistant_id=assistant_id,
            sandbox_type=sandbox_type,
            interactive=interactive,
            cwd=effective_cwd,
        )

    # Configure interrupt_on based on auto_approve / shell_middleware_added
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None
    if auto_approve or shell_middleware_added:  # noqa: SIM108  # if-else clearer than ternary for dual-path config
        # No HITL interrupts — tools run automatically.
        # When shell_middleware_added is True, shell validation is handled by
        # ShellAllowListMiddleware (added above) which rejects disallowed
        # commands inline as error ToolMessages, keeping the entire run in
        # a single LangSmith trace.
        interrupt_on = {}
    else:
        # Full HITL for destructive operations
        interrupt_on = _add_interrupt_on()  # type: ignore[assignment]  # InterruptOnConfig is compatible at runtime

    # Set up composite backend with routing
    # For local FilesystemBackend, route large tool results to /tmp to avoid polluting
    # the working directory. For sandbox backends, no special routing is needed.
    if sandbox is None:
        # Local mode: Route large results to a unique temp directory
        large_results_backend = FilesystemBackend(
            root_dir=tempfile.mkdtemp(prefix="deepagents_large_results_"),
            virtual_mode=True,
        )
        conversation_history_backend = FilesystemBackend(
            root_dir=tempfile.mkdtemp(prefix="deepagents_conversation_history_"),
            virtual_mode=True,
        )
        composite_backend = CompositeBackend(
            default=backend,
            routes={
                "/large_tool_results/": large_results_backend,
                "/conversation_history/": conversation_history_backend,
            },
        )
    else:
        # Sandbox mode: No special routing needed
        composite_backend = CompositeBackend(
            default=backend,
            routes={},
        )

    from deepagents.middleware.summarization import (
        SummarizationMiddleware,
        SummarizationToolMiddleware,
        compute_summarization_defaults,
        create_summarization_tool_middleware,
    )

    if interactive:
        agent_middleware.append(
            create_summarization_tool_middleware(model, composite_backend)
        )
    else:
        # Non-interactive mode: use more aggressive compaction thresholds to
        # proactively manage context — following the Goose/Droid pattern of
        # treating context management as a first-class loop concern rather
        # than an emergency measure. Still wrap in SummarizationToolMiddleware
        # to keep compact_conversation available.
        #
        # Uses fractional thresholds when the model has profile info, falling
        # back to absolute token/message counts for models without profiles
        # (60% of typical 85% default => ~120K tokens).
        from langchain.chat_models import BaseChatModel as RuntimeBaseChatModel

        if isinstance(model, RuntimeBaseChatModel):
            defaults = compute_summarization_defaults(model)
            # Detect whether the default uses fractions (model has profile)
            # or absolute counts (model without profile) and set aggressive
            # thresholds in the same unit type.
            if defaults["trigger"][0] == "fraction":
                trigger = ("fraction", 0.60)
                keep = ("fraction", 0.15)
            else:
                # Absolute token trigger — more aggressive than 170K default
                trigger = ("tokens", 120000)
                keep = ("messages", 10)
            summarization = SummarizationMiddleware(
                model=model,
                backend=composite_backend,
                trigger=trigger,
                keep=keep,
                trim_tokens_to_summarize=None,
                truncate_args_settings=defaults["truncate_args_settings"],
            )
            agent_middleware.append(SummarizationToolMiddleware(summarization))
        else:
            # Fallback to default if model isn't resolved yet
            agent_middleware.append(
                create_summarization_tool_middleware(model, composite_backend)
            )

    # Create the agent
    all_subagents: list[SubAgent | CompiledSubAgent | AsyncSubAgent] = [
        *custom_subagents,
        *(async_subagents or []),
    ]
    agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        backend=composite_backend,
        middleware=agent_middleware,
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
        subagents=all_subagents or None,
        task_hints=task_hints,
        prompt_env_override=prompt_env_override,
        context_pack=context_pack,
    ).with_config(config)
    return agent, composite_backend
