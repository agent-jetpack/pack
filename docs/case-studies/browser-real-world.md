# Browser tools — real-world test scenarios

Live tests of Pack's Obscura-backed browser tools against real
public sites. Run from the repo root with:

```bash
OBSCURA_BIN=/path/to/obscura uv run python docs/case-studies/<file>.py
```

All four scenarios were validated on macOS aarch64 with Obscura
v0.1.1 against unmodified production sites on 2026-04-27.

## Scenario 1 — Documentation lookup

**File:** `01_docs_lookup.py`
**Use case:** agent needs the API signature for `pathlib.Path.glob`.
**Outcome:**

| Step | Result |
|---|---|
| Open `https://docs.python.org/3/library/pathlib.html` | ✅ 1.0s, title `'pathlib — Object-oriented filesystem paths — Python 3.14.4 documentation'` |
| Extract `Path.glob(pattern, *, case_sensitive=None, recurse_symlinks=False)` signature via JS | ✅ |
| Extract body of the `Path.glob` section | ⚠️ DOM-shape gotcha: `dt.parentElement.querySelector('dd')` returns the *first* `<dd>` in the parent `<dl>`, not the sibling. Agents writing extractors need to use the immediate-sibling traversal pattern. |
| Count code-example divs | ✅ returned 2 |
| Verify search box presence | ✅ `True` |

**Finding:** the wrapper's tool surface works fine; correctness of
extraction depends on the agent's selector design. Low-effort
selectors hit DOM-shape edge cases; reasoning about siblings vs
descendants matters.

## Scenario 2 — Structured data sourcing

**File:** `02_data_sourcing.py`
**Use case:** pull HN top-10 stories as JSON, pivot to top story.
**Outcome:**

| Step | Result |
|---|---|
| Open `https://news.ycombinator.com/` | ✅ |
| Extract 10 stories via JS, JSON-stringified | ✅ parsed cleanly |
| Display top 3 | ⚠️ Display-loop bug surfaced: my `.rank` selector matched the same element across rows on render — every row showed `rank=1`. Real-world DOM: HN puts the rank in `<span class="rank">` per row but my JS used `row.querySelector('.rank')` which returned the same element due to a row-detection issue. Fixable by tightening the selector. |
| Pivot: open top story (Bloomberg article) | ✅ tool reported success |
| Read title of pivoted page | ⚠️ Got `'Bloomberg - Are you a robot?'` — Bloomberg's anti-bot detection caught the headless browser even with stealth. |

**Findings:**
1. Cross-site JSON extraction works end-to-end.
2. **Production news sites detect Obscura's stealth mode.** Agents
   working in scraping mode should expect bot-detection from
   Bloomberg/NYT/etc. and either bring proxies or pivot to RSS /
   API endpoints. Documenting this in the agent's context pack as
   a `known-failure-modes.md` entry would be appropriate.

## Scenario 3 — User test / API debugging

**File:** `03_user_tests.py`
**Use case:** verify HTTP behavior via httpbin endpoints.
**Outcome:**

| Endpoint | Result |
|---|---|
| `/headers` | ✅ Headers parsed; UA + Accept + Accept-Language captured |
| `/user-agent` | ✅ Confirmed `Mozilla/5.0 (X11; Linux x86_64)` — Obscura's stealth UA spoofs Linux x86_64 / Chrome 145 even on macOS arm64 |
| `/redirect/3` | ✅ Three hops resolved cleanly; 0.43s end-to-end; landed on `/get` |
| `/status/404` | ✅ Page loaded with empty body (httpbin convention) — agent can detect via `bodyLen == 0` |
| `/delay/2` | ✅ 2.43s total with default `wait_until='load'` |

**Finding:** all 5 scenarios passed. Useful UA datapoint: Obscura
masquerades as Linux x86_64 Chrome regardless of host OS, which
matters for sites that fingerprint by OS. Configurable via
`ObscuraConfig(extra_args=("--user-agent=...",))`.

## Scenario 4 — Pack pipeline composition

**File:** `04_pack_pipeline.py`
**Use case:** confirm browser-tool outputs flow through Pack's
`ToolResultEnrichmentMiddleware` with derived signals.

For each browser tool, the middleware appends a structured marker:

| Tool result | Marker appended |
|---|---|
| Successful `browser_open` | `[browser: ok, url='...']` |
| Failed `browser_open` | `[browser: failed, url='...']` |
| `browser_text` content | `[browser: N chars rendered]` |
| `browser_text` unavailable | `[browser: text unavailable]` |
| `browser_html` content | `[browser: N chars rendered]` (shares text derivation) |
| `browser_evaluate` success | `[browser: returned N chars]` |
| `browser_evaluate` failure | `[browser: evaluate failed]` |
| `browser_screenshot` success | `[browser: screenshot saved, path='...']` |
| `browser_screenshot` failure | `[browser: screenshot failed]` |

**Findings:**
1. The browser tools compose cleanly with Pack's existing
   middleware — no special integration needed beyond registering
   derivations.
2. **Obscura's `evaluate` swallows JS-side throws.** When you write
   `throw new Error('...')` the V8 sandbox catches it silently and
   returns `null`. The Python tool wrapper reports `'None'`. Agents
   should pattern-match for `None` (or empty string) when they
   expect a non-empty result, not just `[browser: evaluate failed]`.
   A future enhancement would have `_evaluate` install a JS-level
   try/catch wrapper that converts caught exceptions to a sentinel
   string the wrapper can detect.

## Aggregate verdict

Across the four scenarios — 5/6 native tools work against real
sites under real conditions. The one gap (`browser_screenshot`) is
an Obscura CDP-coverage limitation, documented and graceful. The
biggest *agent-facing* findings:

- **Anti-bot detection** is real. Stealth helps but doesn't cloak
  enterprise news sites.
- **DOM-shape correctness** is on the agent. The tool surface is
  honest; selector design matters.
- **JS-side exceptions silently null out.** Agents should validate
  evaluate results as a pattern, not assume success on non-error
  marker.

The pipeline composition through `ToolResultEnrichmentMiddleware`
gives the agent stable, scannable signals across every browser
tool — the same pattern Pack uses for `read_file`, `execute`, etc.
