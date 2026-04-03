"""Structured memory with 4-category taxonomy and autoDream consolidation.

This module provides a memory system that stores preferences, behavioral
corrections, project conventions, and resource pointers -- never code facts.
Code is read in real-time; memory prevents stale hallucinations.

Three-layer storage:
    1. Index (MEMORY.md) -- always loaded, ~150-char pointers per entry.
    2. Topic files -- loaded on demand when an index entry matches a query.
    3. Transcripts -- grep-only, never fully loaded.
"""

from deepagents.memory.dream import DreamConsolidator
from deepagents.memory.extractor import MemoryExtractor
from deepagents.memory.index import MemoryIndex
from deepagents.memory.taxonomy import MemoryCategory, MemoryEntry, validate_not_code_fact

__all__ = [
    "DreamConsolidator",
    "MemoryCategory",
    "MemoryEntry",
    "MemoryExtractor",
    "MemoryIndex",
    "validate_not_code_fact",
]
