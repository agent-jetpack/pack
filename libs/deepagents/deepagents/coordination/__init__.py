"""Multi-agent coordination with async mailboxes and permission bubbling.

Three execution models for subagent delegation:

- **Fork**: Separate context, shared filesystem. Runs to completion.
- **Teammate**: Independent agent with async mailbox for mid-execution communication.
- **Worktree**: Full git isolation — agent works in a separate branch.
"""

from deepagents.coordination.mailbox import Mailbox, MailboxMessage, MessageType
from deepagents.coordination.teammate import ExecutionModel, TeammateConfig

__all__ = [
    "ExecutionModel",
    "Mailbox",
    "MailboxMessage",
    "MessageType",
    "TeammateConfig",
]
