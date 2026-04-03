"""Filesystem-based async mailbox for inter-agent communication.

Each agent gets an inbox as a JSONL file. Messages are appended
atomically and read in order. This enables async communication
without shared memory or complex IPC.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class MessageType(str, Enum):
    """Types of inter-agent messages."""

    QUESTION = "question"      # Agent needs a decision
    UPDATE = "update"          # Progress update
    RESULT = "result"          # Final result from agent
    REQUEST = "request"        # Request for action
    PERMISSION = "permission"  # Permission escalation
    ERROR = "error"            # Error report


@dataclass
class MailboxMessage:
    """A message in an agent's mailbox.

    Args:
        msg_type: The type of message.
        sender: ID of the sending agent.
        content: Message content (free-form text).
        metadata: Optional structured data.
        timestamp: When the message was created.
    """

    msg_type: MessageType
    sender: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def to_json(self) -> str:
        """Serialize to JSON string for JSONL storage."""
        data = asdict(self)
        data["msg_type"] = self.msg_type.value
        data["timestamp"] = self.timestamp.isoformat()
        return json.dumps(data)

    @classmethod
    def from_json(cls, line: str) -> MailboxMessage:
        """Deserialize from a JSON string.

        Args:
            line: JSON string from JSONL file.

        Returns:
            Reconstructed message.
        """
        data = json.loads(line)
        return cls(
            msg_type=MessageType(data["msg_type"]),
            sender=data["sender"],
            content=data["content"],
            metadata=data.get("metadata", {}),
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )


class Mailbox:
    """Filesystem-based message queue for an agent.

    Messages are stored as JSONL (one JSON object per line) for
    atomic appends and sequential reads.

    Args:
        directory: Base directory for all mailboxes.
        agent_id: This agent's identifier.
    """

    def __init__(self, directory: Path | str, agent_id: str) -> None:
        self._directory = Path(directory)
        self._agent_id = agent_id
        self._read_offset: int = 0

    @property
    def inbox_path(self) -> Path:
        """Path to this agent's inbox file."""
        return self._directory / self._agent_id / "inbox.jsonl"

    def send(self, recipient_id: str, message: MailboxMessage) -> None:
        """Send a message to another agent's inbox.

        Args:
            recipient_id: ID of the receiving agent.
            message: The message to send.
        """
        inbox = self._directory / recipient_id / "inbox.jsonl"
        inbox.parent.mkdir(parents=True, exist_ok=True)
        with inbox.open("a") as f:
            f.write(message.to_json() + "\n")

    def receive(self) -> list[MailboxMessage]:
        """Read new messages from this agent's inbox.

        Returns only messages added since the last call to receive().

        Returns:
            List of new messages in chronological order.
        """
        if not self.inbox_path.exists():
            return []

        messages: list[MailboxMessage] = []
        with self.inbox_path.open() as f:
            for i, line in enumerate(f):
                if i < self._read_offset:
                    continue
                line = line.strip()
                if line:
                    messages.append(MailboxMessage.from_json(line))
                    self._read_offset = i + 1

        return messages

    def peek(self) -> list[MailboxMessage]:
        """Read all messages without advancing the read offset.

        Returns:
            All messages in the inbox.
        """
        if not self.inbox_path.exists():
            return []

        messages: list[MailboxMessage] = []
        with self.inbox_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    messages.append(MailboxMessage.from_json(line))
        return messages

    def clear(self) -> None:
        """Clear all messages from the inbox."""
        if self.inbox_path.exists():
            self.inbox_path.unlink()
        self._read_offset = 0
