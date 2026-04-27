"""Scenario 1: Documentation lookup.

Use case: an agent needs to look up Python's pathlib API for the
`Path.glob` method. Three browse stages:

  1. Open the Python docs page for pathlib.
  2. Extract just the section under `Path.glob`.
  3. Pull a structured signature via JS to make the agent's job easier.

Real success criterion: the agent receives a usable, scoped chunk of
documentation, not the entire 200 KB page rendered as text.
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("OBSCURA_BIN", "/Users/c/.local/bin/obscura")

from deepagents_cli.browser import make_obscura_tools


def main() -> int:
    tools = make_obscura_tools()
    by = {t.name: t for t in tools}

    t0 = time.monotonic()
    print("=== open Python docs (pathlib) ===")
    print(by["browser_open"].invoke({"url": "https://docs.python.org/3/library/pathlib.html"}))
    print(f"  ({time.monotonic() - t0:.1f}s)")

    print("\n=== extract Path.glob heading + first 600 chars of its body ===")
    js = """
    () => {
      // Find the dt/dd pair that defines Path.glob — Python docs use
      // <dt id="pathlib.Path.glob"> as the anchor.
      const dt = document.getElementById('pathlib.Path.glob');
      if (!dt) return null;
      const dd = dt.parentElement.querySelector('dd');
      const sig = dt.innerText.replace(/\\s+/g, ' ').trim();
      const body = dd ? dd.innerText.trim().slice(0, 600) : '';
      return { signature: sig, body };
    }
    """
    result = by["browser_evaluate"].invoke({"script": js})
    print(result)

    print("\n=== count code examples on the page ===")
    print(by["browser_evaluate"].invoke({
        "script": "document.querySelectorAll('div.highlight-default, div.highlight-pycon3').length",
    }))

    print("\n=== verify sidebar 'Quick search' input is present ===")
    print(by["browser_evaluate"].invoke({
        "script": "(() => !!document.querySelector('input[name=q]'))()",
    }))

    tools._session.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
