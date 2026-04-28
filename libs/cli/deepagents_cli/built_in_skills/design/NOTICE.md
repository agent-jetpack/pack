# Design skill — attribution + rename rationale

This skill is a Pack-renamed vendor of the `impeccable` design skill
by Paul Bakaus, exposing the same content under the generic key
`design` so agents and humans can invoke it without recalling a
brand name.

## Upstream

- **Repository:** <https://github.com/pbakaus/impeccable>
- **License:** Apache 2.0 (see `LICENSE` in this directory)
- **Copyright:** 2025-2026 Paul Bakaus
- **Built on:** Anthropic's `frontend-design` skill,
  <https://github.com/anthropics/skills/tree/main/skills/frontend-design>

## What was renamed

Pack agents discover skills via the `name:` frontmatter field, which
must equal the directory name. To make the skill invokable as
`design` rather than `impeccable`, both were renamed in this vendor
copy:

| Surface | Upstream | Pack vendor |
|---------|----------|-------------|
| Directory | `impeccable/` | `design/` |
| `name:` frontmatter | `impeccable` | `design` |
| Slash command prefix | `/impeccable <cmd>` | `/design <cmd>` |
| Templated invocation | `{{command_prefix}}impeccable <cmd>` | `{{command_prefix}}design <cmd>` |

## What was NOT renamed

The following stayed verbatim because they're not invocation surfaces
— they're identifiers internal to the upstream tool. Renaming them
would break the `npx impeccable` CLI when an operator installs it:

- `Bash(npx impeccable *)` in the SKILL.md `allowed-tools` field —
  the actual npm package name is `impeccable`.
- "Impeccable live helper" — the proper noun for the local HTTP
  helper server the live-browser-iteration mode runs.
- `data-impeccable-variant`, `data-impeccable-css` — CSS attributes
  used by `scripts/live-browser.js` to scope per-variant styles.
- `event.action === 'impeccable'` — the freeform-mode action name
  the live helper emits over SSE.
- Legacy filename `.impeccable.md` — referenced by the migration
  path that auto-renames pre-existing project files.

If the upstream package is ever renamed (or forked under a new
package name), update those references to match.

## Updating

To pick up upstream changes:

```bash
git clone --depth 1 https://github.com/pbakaus/impeccable.git /tmp/impeccable
rm -r libs/cli/deepagents_cli/built_in_skills/design/{SKILL.md,reference,scripts}
cp -r /tmp/impeccable/source/skills/impeccable/{SKILL.md,reference,scripts} \
      libs/cli/deepagents_cli/built_in_skills/design/

# Reapply the rename so the skill stays discoverable as `design`:
sed -i '' 's/^name: impeccable$/name: design/' \
  libs/cli/deepagents_cli/built_in_skills/design/SKILL.md
find libs/cli/deepagents_cli/built_in_skills/design \
  -type f \( -name '*.md' -o -name '*.json' -o -name '*.js' -o -name '*.mjs' \) \
  -exec sed -i '' \
    -e 's|/impeccable |/design |g' \
    -e 's|`impeccable |`design |g' \
    -e 's|{{command_prefix}}impeccable|{{command_prefix}}design|g' \
    {} +
```

`NOTICE.md` and `LICENSE` are preserved across updates.
