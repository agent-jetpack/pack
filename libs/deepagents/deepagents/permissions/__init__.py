"""Permission pipeline for tool call authorization.

Implements a multi-layer permission system inspired by Claude Code's harness:

1. Rule matching — cross-session whitelist/blacklist from persisted rules
2. Risk assessment — static classification by tool risk level
3. Read-only whitelist — auto-approve known safe tools
4. Classifier — deterministic regex patterns first, optional LLM fallback

Includes a circuit breaker that degrades to manual mode after repeated denials.
"""

from deepagents.permissions.circuit_breaker import CircuitBreaker
from deepagents.permissions.classifier import PermissionClassifier
from deepagents.permissions.pipeline import Decision, PermissionPipeline
from deepagents.permissions.rules import PermissionRule, RuleStore

__all__ = [
    "CircuitBreaker",
    "Decision",
    "PermissionClassifier",
    "PermissionPipeline",
    "PermissionRule",
    "RuleStore",
]
