"""Hook execution helpers for subprocess-based hook commands.

Handles template variable substitution and async subprocess execution
with timeout enforcement. Template values are sanitized to prevent
shell injection.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Template variables supported in hook commands.
TEMPLATE_VARS = frozenset({"tool_name", "file_path", "command", "args", "result"})

#: Pattern matching template placeholders like ``{tool_name}``.
_TEMPLATE_PATTERN = re.compile(r"\{(" + "|".join(TEMPLATE_VARS) + r")\}")


@dataclass(frozen=True)
class HookResult:
    """Outcome of a single hook execution.

    Args:
        stdout: Standard output captured from the hook command.
        stderr: Standard error captured from the hook command.
        return_code: Process exit code, or -1 on timeout/failure.
        inject: Whether stdout should be injected into model context.
    """

    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    inject: bool = False


def _sanitize_value(value: str) -> str:
    """Shell-quote a template value to prevent injection.

    Args:
        value: Raw string to sanitize.

    Returns:
        A shell-escaped version safe for embedding in commands.
    """
    return shlex.quote(value)


def substitute_template(command: str, context: dict[str, str]) -> str:
    """Replace template placeholders in a command string.

    Only known template variables are substituted. Values are sanitized
    with shell quoting to prevent injection attacks.

    Args:
        command: Command string with ``{variable}`` placeholders.
        context: Mapping of variable names to their values.

    Returns:
        The command with placeholders replaced by sanitized values.
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = context.get(key, "")
        return _sanitize_value(value)

    return _TEMPLATE_PATTERN.sub(_replace, command)


async def run_hook(
    command: str,
    context: dict[str, str],
    timeout: int = 10,  # noqa: ASYNC109
    *,
    inject_output: bool = False,
) -> HookResult:
    """Execute a hook command as an async subprocess.

    Template variables in the command are substituted from ``context``
    before execution. The subprocess is killed if it exceeds the timeout.

    Args:
        command: Shell command string, possibly containing template
            placeholders like ``{tool_name}``.
        context: Template variable values for substitution.
        timeout: Maximum seconds before the process is killed.
        inject_output: Whether to mark stdout for injection into model
            context.

    Returns:
        A `HookResult` with captured output and exit status.
    """
    resolved = substitute_template(command, context)

    try:
        process = await asyncio.create_subprocess_shell(
            resolved,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
        return HookResult(
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            return_code=process.returncode or 0,
            inject=inject_output,
        )
    except TimeoutError:
        logger.warning("Hook command timed out after %ds: %s", timeout, resolved)
        if process.returncode is None:
            process.kill()
            await process.wait()
        return HookResult(
            stderr=f"Hook timed out after {timeout}s",
            return_code=-1,
            inject=False,
        )
    except OSError as exc:
        logger.warning("Hook command failed to start: %s — %s", resolved, exc)
        return HookResult(
            stderr=str(exc),
            return_code=-1,
            inject=False,
        )
