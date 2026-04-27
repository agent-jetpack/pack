"""Scenario 3: User-test / API debugging.

Use case: an agent verifying an HTTP API. httpbin.org provides
deterministic endpoints that echo what they receive — perfect for
testing the browser tool's request/response handling.

Tests:
  1. GET /headers — confirm the request headers Obscura sends.
  2. GET /user-agent — verify the UA string we configured.
  3. GET /redirect/3 — exercise multi-hop redirect handling.
  4. GET /status/404 — error-response handling.
  5. GET /delay/2 — wait_until + slow-server semantics.
"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("OBSCURA_BIN", "/Users/c/.local/bin/obscura")

from deepagents_cli.browser import make_obscura_tools


def _json_via_browser(by, url: str) -> dict | None:
    """Open URL, then JSON.parse the rendered body."""
    print(by["browser_open"].invoke({"url": url}))
    raw = by["browser_evaluate"].invoke({
        "script": "document.body.innerText.trim()",
    })
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print(f"  body did not parse as JSON: {raw[:120]!r}")
        return None


def main() -> int:
    tools = make_obscura_tools()
    by = {t.name: t for t in tools}

    print("=== /headers ===")
    headers = _json_via_browser(by, "https://httpbin.org/headers")
    if headers:
        # Show a few interesting ones — User-Agent, Accept, etc.
        h = headers.get("headers", {})
        for key in ("User-Agent", "Accept", "Accept-Language"):
            print(f"  {key}: {h.get(key, '<absent>')}")

    print("\n=== /user-agent ===")
    ua = _json_via_browser(by, "https://httpbin.org/user-agent")
    if ua:
        print(f"  user-agent: {ua.get('user-agent', '<absent>')}")

    print("\n=== /redirect/3 (3 hops then /get) ===")
    t0 = time.monotonic()
    final = _json_via_browser(by, "https://httpbin.org/redirect/3")
    elapsed = time.monotonic() - t0
    if final:
        print(f"  landed at:   {final.get('url', '<no url field>')}")
        print(f"  hops took:   {elapsed:.2f}s")

    print("\n=== /status/404 ===")
    print(by["browser_open"].invoke({"url": "https://httpbin.org/status/404"}))
    body = by["browser_evaluate"].invoke({
        "script": "({title: document.title, bodyLen: document.body.innerText.length})",
    })
    print(f"  {body}")

    print("\n=== /delay/2 with default wait_until=load ===")
    t0 = time.monotonic()
    print(by["browser_open"].invoke({"url": "https://httpbin.org/delay/2"}))
    elapsed = time.monotonic() - t0
    print(f"  total time: {elapsed:.2f}s (expect ~2-3s)")

    tools._session.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
