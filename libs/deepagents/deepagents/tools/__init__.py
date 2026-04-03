"""Extended tools for deep agents.

Provides git worktree management and document reading tools.
"""

from deepagents.tools.document_reader import read_image, read_pdf
from deepagents.tools.git_worktree import (
    git_worktree_create,
    git_worktree_list,
    git_worktree_remove,
)

__all__ = [
    "git_worktree_create",
    "git_worktree_list",
    "git_worktree_remove",
    "read_image",
    "read_pdf",
]
