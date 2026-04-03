"""Tests for multi-agent coordination."""

from __future__ import annotations

from pathlib import Path

from deepagents.coordination.mailbox import Mailbox, MailboxMessage, MessageType
from deepagents.coordination.teammate import ExecutionModel, TeammateConfig


class TestMailboxMessage:
    def test_roundtrip_serialization(self) -> None:
        msg = MailboxMessage(
            msg_type=MessageType.UPDATE,
            sender="agent-1",
            content="Task 50% complete",
            metadata={"progress": 0.5},
        )
        json_str = msg.to_json()
        restored = MailboxMessage.from_json(json_str)
        assert restored.msg_type == MessageType.UPDATE
        assert restored.sender == "agent-1"
        assert restored.content == "Task 50% complete"
        assert restored.metadata["progress"] == 0.5

    def test_all_message_types(self) -> None:
        for msg_type in MessageType:
            msg = MailboxMessage(msg_type=msg_type, sender="test", content="test")
            restored = MailboxMessage.from_json(msg.to_json())
            assert restored.msg_type == msg_type


class TestMailbox:
    def test_send_and_receive(self, tmp_path: Path) -> None:
        leader = Mailbox(tmp_path, "leader")
        worker = Mailbox(tmp_path, "worker")

        msg = MailboxMessage(
            msg_type=MessageType.UPDATE,
            sender="worker",
            content="Done with task 1",
        )
        worker.send("leader", msg)

        received = leader.receive()
        assert len(received) == 1
        assert received[0].content == "Done with task 1"
        assert received[0].sender == "worker"

    def test_receive_only_new_messages(self, tmp_path: Path) -> None:
        mailbox = Mailbox(tmp_path, "agent")
        other = Mailbox(tmp_path, "other")

        other.send("agent", MailboxMessage(
            msg_type=MessageType.UPDATE, sender="other", content="msg1",
        ))

        # First receive gets msg1
        msgs = mailbox.receive()
        assert len(msgs) == 1

        # Send another
        other.send("agent", MailboxMessage(
            msg_type=MessageType.UPDATE, sender="other", content="msg2",
        ))

        # Second receive only gets msg2
        msgs = mailbox.receive()
        assert len(msgs) == 1
        assert msgs[0].content == "msg2"

    def test_empty_inbox(self, tmp_path: Path) -> None:
        mailbox = Mailbox(tmp_path, "agent")
        assert mailbox.receive() == []

    def test_peek_does_not_advance(self, tmp_path: Path) -> None:
        mailbox = Mailbox(tmp_path, "agent")
        other = Mailbox(tmp_path, "other")

        other.send("agent", MailboxMessage(
            msg_type=MessageType.RESULT, sender="other", content="result",
        ))

        # Peek should return messages
        peeked = mailbox.peek()
        assert len(peeked) == 1

        # Receive should still return the same messages
        received = mailbox.receive()
        assert len(received) == 1

    def test_clear_inbox(self, tmp_path: Path) -> None:
        mailbox = Mailbox(tmp_path, "agent")
        other = Mailbox(tmp_path, "other")

        other.send("agent", MailboxMessage(
            msg_type=MessageType.UPDATE, sender="other", content="msg",
        ))
        mailbox.clear()
        assert mailbox.receive() == []

    def test_permission_escalation(self, tmp_path: Path) -> None:
        leader = Mailbox(tmp_path, "leader")
        worker = Mailbox(tmp_path, "worker")

        # Worker escalates a permission question
        worker.send("leader", MailboxMessage(
            msg_type=MessageType.PERMISSION,
            sender="worker",
            content="May I delete old log files?",
            metadata={"tool": "execute", "command": "rm logs/*.log"},
        ))

        msgs = leader.receive()
        assert len(msgs) == 1
        assert msgs[0].msg_type == MessageType.PERMISSION
        assert msgs[0].metadata["tool"] == "execute"

    def test_multiple_agents_communicate(self, tmp_path: Path) -> None:
        leader = Mailbox(tmp_path, "leader")
        worker1 = Mailbox(tmp_path, "worker1")
        worker2 = Mailbox(tmp_path, "worker2")

        worker1.send("leader", MailboxMessage(
            msg_type=MessageType.UPDATE, sender="worker1", content="task A done",
        ))
        worker2.send("leader", MailboxMessage(
            msg_type=MessageType.UPDATE, sender="worker2", content="task B done",
        ))

        msgs = leader.receive()
        assert len(msgs) == 2
        senders = {m.sender for m in msgs}
        assert senders == {"worker1", "worker2"}


class TestTeammateConfig:
    def test_default_execution_model(self) -> None:
        config = TeammateConfig(agent_id="test")
        assert config.execution_model == ExecutionModel.FORK

    def test_worktree_config(self) -> None:
        config = TeammateConfig(
            agent_id="feat-worker",
            execution_model=ExecutionModel.WORKTREE,
            worktree_branch="feat/new-feature",
        )
        assert config.execution_model == ExecutionModel.WORKTREE
        assert config.worktree_branch == "feat/new-feature"

    def test_serialization(self) -> None:
        config = TeammateConfig(
            agent_id="worker-1",
            agent_type="explore",
            execution_model=ExecutionModel.TEAMMATE,
            model_override="deepseek/deepseek-chat",
        )
        data = config.to_dict()
        assert data["execution_model"] == "teammate"
        assert data["model_override"] == "deepseek/deepseek-chat"
