"""Specialized agent types with scoped tools and prompts.

Provides pre-configured agent profiles for common coding tasks:

- **Explore**: Read-only tools, cheap model. Fast codebase exploration.
- **Plan**: Read + planning tools. Task decomposition and architecture.
- **Review**: Read + test execution. Code review and quality checks.
- **General**: All tools. Default for unclassified tasks.
"""

from deepagents.agents.profiles import AgentProfile, AgentType, get_profile

__all__ = [
    "AgentProfile",
    "AgentType",
    "get_profile",
]
