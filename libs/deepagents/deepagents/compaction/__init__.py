"""Multi-strategy context compaction inspired by Claude Code's harness.

Three tiers of compaction, applied in order of increasing aggressiveness:

1. **Trim** — remove old tool results, keep the 5 most recent
2. **Collapse** — replace verbose tool results with summaries, persist originals
3. **Summarize** — full 9-segment extraction protocol preserving user messages

The monitor checks token usage before each model call and triggers
the appropriate tier.
"""

from deepagents.compaction.context_collapse import ContextCollapseEntry, ContextCollapser
from deepagents.compaction.monitor import CompactionMonitor, CompactionTier
from deepagents.compaction.segment_protocol import SegmentProtocol

__all__ = [
    "CompactionMonitor",
    "CompactionTier",
    "ContextCollapseEntry",
    "ContextCollapser",
    "SegmentProtocol",
]
