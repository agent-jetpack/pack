"""Scenario 2: Structured data sourcing.

Use case: an agent gathering reference data — pull the top 10
Hacker News story titles + URLs as structured JSON. Tests:

  1. Browse to HN front page.
  2. Extract list of {rank, title, url, score} via JS.
  3. Pivot: open the top story's URL and read its title.

Demonstrates the pattern of "navigate, structurally extract, pivot,
extract again" — common for data pipelines.

Note: we make the JS side return a JSON string and parse with
``json.loads`` here. This avoids relying on Python ``repr`` round-
tripping through any kind of exec-shaped parser.
"""
from __future__ import annotations

import os
import sys
import json

os.environ.setdefault("OBSCURA_BIN", "/Users/c/.local/bin/obscura")

from deepagents_cli.browser import make_obscura_tools


def main() -> int:
    tools = make_obscura_tools()
    by = {t.name: t for t in tools}

    print("=== open Hacker News ===")
    print(by["browser_open"].invoke({"url": "https://news.ycombinator.com/"}))

    print("\n=== extract top 10 stories (JSON-stringified by JS) ===")
    js = """
    () => {
      const rows = document.querySelectorAll('tr.athing.submission');
      const stories = Array.from(rows).slice(0, 10).map((row) => {
        const titleEl = row.querySelector('.titleline > a');
        const subtext = row.nextElementSibling;
        const score = subtext ? subtext.querySelector('.score') : null;
        return {
          rank: row.querySelector('.rank')
            ? row.querySelector('.rank').innerText.replace('.','')
            : '',
          title: titleEl ? titleEl.innerText : '',
          url: titleEl ? titleEl.href : '',
          score: score ? score.innerText : null,
        };
      });
      return JSON.stringify(stories);
    }
    """
    raw = by["browser_evaluate"].invoke({"script": js})
    print(f"  raw payload: {raw[:200]}...")
    try:
        stories = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"\n[parse failed: {exc}]")
        tools._session.shutdown()
        return 1

    print(f"\nParsed {len(stories)} stories. Top 3:")
    for s in stories[:3]:
        print(f"  #{s.get('rank', '?'):>2}  {s.get('score') or '(no score)':<10}  {s.get('title','')[:60]}")

    if stories and stories[0].get("url", "").startswith(("http://", "https://")):
        print("\n=== pivot: open the top story's link ===")
        print(by["browser_open"].invoke({"url": stories[0]["url"]}))
        title = by["browser_evaluate"].invoke({"script": "document.title"})
        print(f"  title: {title!r}")
        h1 = by["browser_evaluate"].invoke({
            "script": "(document.querySelector('h1') || {}).innerText || ''",
        })
        print(f"  H1:    {h1[:120]!r}")

    tools._session.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
