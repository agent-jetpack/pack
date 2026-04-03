"""9-segment extraction protocol for context summarization.

When full summarization (Tier 3) is triggered, the conversation is
parsed into 9 segments. Segments 1-5 and 7-9 are summarized, while
segment 6 (user messages) is ALWAYS preserved verbatim.

This is the core "secret sauce" from Claude Code's context management.
User messages contain implicit behavioral corrections that prevent
the model from repeating past mistakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage


@dataclass
class SegmentedConversation:
    """Conversation parsed into the 9-segment structure.

    Segments 1-5 and 7-9 are eligible for summarization.
    Segment 6 (user messages) is SACRED — never summarize.

    Args:
        original_request: The initial user request/intent.
        key_concepts: Technical concepts, patterns, and domain knowledge discussed.
        files_touched: Files read, written, or referenced.
        errors_and_fixes: Errors encountered and how they were resolved.
        resolution_process: The approach and reasoning chain.
        user_messages: ALL user messages — preserved verbatim.
        pending_tasks: Outstanding work from todo lists.
        current_work: What the agent is actively working on.
        next_steps: Planned next actions.
    """

    original_request: str
    key_concepts: list[str]
    files_touched: list[str]
    errors_and_fixes: list[str]
    resolution_process: list[str]
    user_messages: list[HumanMessage]  # SACRED — never summarize
    pending_tasks: list[str]
    current_work: str
    next_steps: list[str]


class SegmentProtocol:
    """Parse conversations into the 9-segment structure and reconstruct.

    The protocol extracts structured information from the conversation
    history, preserves user messages verbatim, and produces a compact
    summary that maintains behavioral context.
    """

    def parse(self, messages: list[AnyMessage]) -> SegmentedConversation:
        """Parse a message list into the 9-segment structure.

        Args:
            messages: Full conversation history to parse.

        Returns:
            Segmented conversation with extracted information.
        """
        user_messages: list[HumanMessage] = []
        files_touched: list[str] = []
        errors: list[str] = []
        concepts: list[str] = []
        resolution_steps: list[str] = []
        original_request = ""

        for i, msg in enumerate(messages):
            if isinstance(msg, HumanMessage):
                user_messages.append(msg)
                if i == 0 or (not original_request and isinstance(msg.content, str)):
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    original_request = content[:500]

            elif isinstance(msg, ToolMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                # Extract file paths from tool results
                self._extract_files(content, files_touched)
                # Extract errors
                if any(kw in content.lower() for kw in ("error", "traceback", "exception", "failed")):
                    errors.append(content[:200])

            elif isinstance(msg, AIMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if content.strip():
                    resolution_steps.append(content[:200])

        return SegmentedConversation(
            original_request=original_request,
            key_concepts=concepts,
            files_touched=list(dict.fromkeys(files_touched)),  # Deduplicate preserving order
            errors_and_fixes=errors[-10:],  # Keep last 10 errors
            resolution_process=resolution_steps[-5:],  # Keep last 5 steps
            user_messages=user_messages,
            pending_tasks=[],
            current_work=resolution_steps[-1] if resolution_steps else "",
            next_steps=[],
        )

    def build_summary_prompt(self, segments: SegmentedConversation) -> str:
        """Build a summarization prompt from parsed segments.

        This prompt is sent to the auxiliary model to produce a compact
        summary of segments 1-5 and 7-9. User messages (segment 6)
        are NOT included in the summary — they are preserved separately.

        Args:
            segments: Parsed conversation segments.

        Returns:
            Prompt string for the summarization model.
        """
        return f"""Summarize this conversation context concisely:

## Original Request
{segments.original_request}

## Files Touched
{chr(10).join(f'- {f}' for f in segments.files_touched[:20]) or 'None'}

## Errors Encountered
{chr(10).join(f'- {e}' for e in segments.errors_and_fixes[:5]) or 'None'}

## Resolution Process
{chr(10).join(f'- {s}' for s in segments.resolution_process) or 'None'}

## Current Work
{segments.current_work or 'None'}

Produce a concise summary (under 500 words) covering:
1. What was requested and why
2. Key technical decisions made
3. Files created or modified
4. Errors encountered and how they were resolved
5. Current state of the work
6. What needs to happen next"""

    def reconstruct(
        self,
        summary: str,
        segments: SegmentedConversation,
        recent_messages: list[AnyMessage] | None = None,
    ) -> list[AnyMessage]:
        """Reconstruct a compacted message list from summary and preserved messages.

        The reconstructed list contains:
        1. A system message with the summary
        2. All original user messages (verbatim)
        3. Recent messages (last N, preserved in full)

        Args:
            summary: LLM-generated summary of segments 1-5 and 7-9.
            segments: The parsed conversation with preserved user messages.
            recent_messages: Recent messages to keep in full (typically last 5-10).

        Returns:
            Compacted message list ready for the next model call.
        """
        result: list[AnyMessage] = []

        # Add compaction boundary marker with summary
        result.append(SystemMessage(
            content=(
                f"[Context compacted — previous conversation summarized]\n\n"
                f"{summary}\n\n"
                f"[End of summary — user messages below are preserved verbatim]"
            ),
        ))

        # Preserve ALL user messages verbatim (SACRED)
        for msg in segments.user_messages:
            result.append(msg)

        # Append recent messages if provided
        if recent_messages:
            for msg in recent_messages:
                # Avoid duplicating user messages already in segments
                if isinstance(msg, HumanMessage) and msg in segments.user_messages:
                    continue
                result.append(msg)

        return result

    def _extract_files(self, content: str, files: list[str]) -> None:
        """Extract file paths from tool result content.

        Args:
            content: Tool result text.
            files: Accumulator list for discovered paths.
        """
        import re
        # Match common file path patterns
        for match in re.finditer(r'(?:^|[\s"\'`])([a-zA-Z_./][\w./\-]*\.\w{1,10})\b', content):
            path = match.group(1)
            if "/" in path or path.endswith((".py", ".ts", ".js", ".tsx", ".jsx", ".md", ".toml", ".yaml", ".json")):
                files.append(path)
