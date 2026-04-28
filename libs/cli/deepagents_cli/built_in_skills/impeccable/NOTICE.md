# Impeccable — attribution

This skill is the `impeccable` design skill by Paul Bakaus, vendored
into Pack's built-in-skills directory so agents driven by Pack can
invoke it without out-of-band install.

## Upstream

- **Repository:** <https://github.com/pbakaus/impeccable>
- **License:** Apache 2.0 (see ../../../../LICENSE-APACHE-IMPECCABLE
  for the upstream copy bundled here verbatim)
- **Copyright:** 2025-2026 Paul Bakaus
- **Built on:** Anthropic's `frontend-design` skill,
  <https://github.com/anthropics/skills/tree/main/skills/frontend-design>

Pack ships the skill verbatim. We do not modify the design guidance
or anti-pattern catalogue — those are domain knowledge owned by the
upstream maintainer.

## What's in this directory

- `SKILL.md` — entry point with frontmatter Pack's `SkillsMiddleware`
  parses to surface the skill to the agent.
- `reference/` — 35 markdown files covering typography, color &
  contrast, spatial design, motion, interaction, responsive design,
  UX writing, anti-patterns, audits, polish flows, and 25+ other
  topics.
- `scripts/` — Node.js scripts for the `npx impeccable` CLI surface
  (live browser iteration, command pinning, design parsing). These
  are **optional** — invoking them requires Node + `npx` on the
  agent's PATH and is gated by `allowed-tools: Bash(npx impeccable *)`
  in `SKILL.md`. Pack's permission middleware governs whether the
  agent can actually run them in any given session.

## How Pack agents activate the skill

Two surfaces:

1. **Implicit:** the `description` frontmatter is broad enough that
   Pack's `SkillsMiddleware` will surface this skill on most
   frontend / UI design tasks. The agent reads the SKILL.md content
   and reference files into its working context.
2. **Explicit:** in interactive Pack sessions the user can invoke
   the skill directly with one of Impeccable's 23 slash commands
   (`/audit`, `/craft`, `/polish`, etc.) listed under the skill's
   command-handler scripts.

## Updating

To pick up a new upstream release, replace the contents of this
directory with `git clone --depth 1
https://github.com/pbakaus/impeccable.git && cp -r
impeccable/source/skills/impeccable/* <this-dir>/`. Preserve this
NOTICE.md across updates.

Pack's components doc (`docs/harness/components.md`) lists this
skill under built-in skills; refresh that entry whenever the
upstream `description` changes materially.
