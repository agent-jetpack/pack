"""Scenario 4: Pipe browser tools through Pack's middleware stack.

This is the "piping in Pack" the user asked about — the browser
tools running through ``ToolResultEnrichmentMiddleware`` so each
result gets a derived signal the agent can pattern-match on.

We don't spawn a real model; we simulate the tool-call shape that
LangGraph's tool node produces and walk it through the middleware
pipeline by hand. That's faithful to what the agent sees.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("OBSCURA_BIN", "/Users/c/.local/bin/obscura")

from langchain_core.messages import ToolMessage

from deepagents_cli.browser import make_obscura_tools
from deepagents_cli.tool_result_enrichment import ToolResultEnrichmentMiddleware


def _make_request(tool_name: str, args: dict) -> object:
    """Build the ToolCallRequest shape the middleware expects."""
    req = MagicMock()
    req.tool_call = {"name": tool_name, "args": args, "id": f"tc-{tool_name}"}
    return req


def _pipe_through_enrichment(tool, args: dict, middleware) -> str:
    """Invoke a browser tool and pipe its output through the middleware.

    Mirrors LangGraph's flow: the tool runs, returns a result, and
    middleware's ``wrap_tool_call`` annotates it. We synthesize the
    request manually so we don't need a real LangGraph runtime.
    """
    raw = tool.invoke(args) if args else tool.invoke({})

    def _handler(_req: object) -> ToolMessage:
        return ToolMessage(
            content=raw,
            name=tool.name,
            tool_call_id=f"tc-{tool.name}",
        )

    request = _make_request(tool.name, args)
    enriched = middleware.wrap_tool_call(request, _handler)
    return str(enriched.content)


def main() -> int:
    tools = make_obscura_tools()
    by = {t.name: t for t in tools}
    enrichment = ToolResultEnrichmentMiddleware()

    print("=== browser_open through enrichment ===")
    out = _pipe_through_enrichment(
        by["browser_open"],
        {"url": "https://example.com"},
        enrichment,
    )
    print(out)

    print("\n=== browser_text through enrichment (length signal) ===")
    out = _pipe_through_enrichment(by["browser_text"], {}, enrichment)
    # Print just the trailing marker since the body is long
    last_two = "\n".join(out.splitlines()[-3:])
    print(f"  ...\n{last_two}")

    print("\n=== browser_evaluate through enrichment (success) ===")
    out = _pipe_through_enrichment(
        by["browser_evaluate"],
        {"script": "({title: document.title, links: document.querySelectorAll('a').length})"},
        enrichment,
    )
    print(out)

    print("\n=== browser_evaluate through enrichment (failure path) ===")
    out = _pipe_through_enrichment(
        by["browser_evaluate"],
        {"script": "throw new Error('intentional');"},
        enrichment,
    )
    print(out)

    print("\n=== browser_screenshot through enrichment (Obscura gap) ===")
    out = _pipe_through_enrichment(
        by["browser_screenshot"],
        {"path": "/tmp/pack-browser-scenarios/test.png", "full_page": False},
        enrichment,
    )
    print(out[-180:])

    print("\n=== browser_open failure (bad URL) through enrichment ===")
    out = _pipe_through_enrichment(
        by["browser_open"],
        {"url": "not-a-url"},
        enrichment,
    )
    print(out)

    tools._session.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
