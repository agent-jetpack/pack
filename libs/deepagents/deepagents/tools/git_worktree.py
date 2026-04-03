"""Tools for git worktree management.

Provides LangChain tools for creating, listing, and removing git worktrees,
enabling isolated parallel development on multiple branches.
"""

import re
import subprocess

from langchain_core.tools import tool

# Pattern for valid git branch names (simplified).
# Disallows shell metacharacters and common injection vectors.
_BRANCH_RE = re.compile(r"^[a-zA-Z0-9._/\-]+$")

# Pattern for valid filesystem paths.
# Allows alphanumeric, dots, dashes, underscores, slashes, and tildes.
_PATH_RE = re.compile(r"^[a-zA-Z0-9._/\-~]+$")


def _validate_branch(branch: str) -> None:
    """Validate a branch name to prevent command injection.

    Args:
        branch: The branch name to validate.

    Raises:
        ValueError: If the branch name contains invalid characters.
    """
    if not branch or not _BRANCH_RE.match(branch):
        msg = (
            f"Invalid branch name: {branch!r}. "
            "Branch names must contain only alphanumeric characters, "
            "dots, dashes, underscores, and forward slashes."
        )
        raise ValueError(msg)


def _validate_path(path: str) -> None:
    """Validate a filesystem path to prevent command injection.

    Args:
        path: The filesystem path to validate.

    Raises:
        ValueError: If the path contains invalid characters.
    """
    if not path or not _PATH_RE.match(path):
        msg = (
            f"Invalid path: {path!r}. "
            "Paths must contain only alphanumeric characters, "
            "dots, dashes, underscores, tildes, and forward slashes."
        )
        raise ValueError(msg)


def _run_git(args: list[str]) -> str:
    """Run a git command and return its output.

    Args:
        args: Arguments to pass to git (not including 'git' itself).

    Returns:
        The stdout from the git command.

    Raises:
        RuntimeError: If the git command fails.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        msg = f"git {' '.join(args)} failed: {exc.stderr.strip()}"
        raise RuntimeError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"git {' '.join(args)} timed out after 30 seconds"
        raise RuntimeError(msg) from exc
    return result.stdout.strip()


@tool(description="Create a new git worktree for a branch. Returns the worktree path.")
def git_worktree_create(branch: str, path: str | None = None) -> str:
    """Create a new git worktree for isolated development on a branch.

    If no path is provided, the worktree is created at `../worktrees/<branch>`.

    Args:
        branch: The branch name to create the worktree for.
        path: The filesystem path where the worktree should be created.

    Returns:
        A message confirming the worktree was created, including its path.
    """
    _validate_branch(branch)

    if path is None:
        # Sanitize branch name for directory usage (replace slashes with dashes)
        safe_dir = branch.replace("/", "-")
        path = f"../worktrees/{safe_dir}"

    _validate_path(path)

    # Use -B to create or reset the branch, avoiding errors if it already exists
    output = _run_git(["worktree", "add", "-B", branch, path])
    return f"Worktree created at {path} for branch {branch}.\n{output}"


@tool(description="List all git worktrees in the repository.")
def git_worktree_list() -> str:
    """List all git worktrees in the current repository.

    Returns:
        A formatted list of all worktrees with their paths and branches.
    """
    return _run_git(["worktree", "list"])


@tool(description="Remove a git worktree by path.")
def git_worktree_remove(path: str) -> str:
    """Remove a git worktree.

    Args:
        path: The filesystem path of the worktree to remove.

    Returns:
        A confirmation message that the worktree was removed.
    """
    _validate_path(path)
    output = _run_git(["worktree", "remove", path])
    return f"Worktree at {path} removed.\n{output}"
