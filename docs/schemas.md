# Schema reference

Machine-readable JSON Schemas live in [`schemas/`](../schemas/) and examples in
[`examples/`](../examples/). This page is the human-readable summary. If they disagree, the
files in `schemas/` win.

## Paths and resolution

| What | Resolution order |
|---|---|
| Store root | `--store PATH` > `SKILL_STORE_HOME` env var > `~/.skill-store` (`%USERPROFILE%\.skill-store`) |
| Project root | `--project PATH` > `CLAUDE_PROJECT_DIR` env var > git toplevel of the cwd > cwd |

```
<store>/
├── skills/<name>/          linkable skill: SKILL.md + references/
├── build/<name>/           skill-seekers working dir + raw scraped pages
├── store.json              registry
└── usage.jsonl             usage log

<project>/.claude/
├── skills.manifest.json    committed: which skills this project links
└── skills/
    ├── .gitignore          committed: "*" and "!.gitignore"
    └── <name>  ──link──►   <store>/skills/<name>   (junction on Windows, symlink elsewhere)
```

## Project manifest: `<project>/.claude/skills.manifest.json`

Committed in the project.

```json
{
  "version": 1,
  "cap": 8,
  "skills": ["godot47-asset-pipeline"]
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `$schema` | string | no | Optional pointer to `schemas/manifest.schema.json` for editors |
| `version` | `1` | yes | Schema version |
| `cap` | integer | no (default **8**) | Max linked store skills in this project. `link` past it unlinks the least recently used and drops them from `skills` |
| `skills` | array of skill names | yes | Skills to link. `sync` links those that are approved and pass `check` (up to `cap`, most recently used first) and removes store links not listed |

Skill names: lowercase letters, digits and single hyphens, starting and ending with a letter or
digit, at most 64 chars.

Who writes it: `init-project` creates it. `link`, `unlink <name>` and `prune` edit it. **`sync`
and `unlink --all` never write it**, so session starts and worktree removal don't create git diffs.
Editing it by hand and running `sync` is fine.

## `.claude/skills/.gitignore`

```gitignore
# Managed by skill-store: links into the skill store are never committed.
*
!.gitignore
```

Created by `init-project` and `link` if missing (`sync` creates nothing in a project without a
manifest). An existing file is never modified; `sync` warns if it lacks `*`. Commit it. It keeps links (and, because git
follows junctions, the store's third-party content) out of the repo, and makes sure the directory
exists at session start so Claude Code live-reloads link changes.

## Registry: `<store>/store.json`

```json
{
  "version": 1,
  "skills": {
    "godot47-asset-pipeline": {
      "source": "https://docs.godotengine.org/en/4.7/tutorials/assets_pipeline/",
      "version": "4.7",
      "pinned_url": null,
      "built_at": "2026-11-05T14:02:11Z",
      "approved": true,
      "approved_at": "2026-11-05T14:10:00Z",
      "last_used": "2026-11-05T15:30:42Z"
    }
  }
}
```

| Field | Type | Meaning |
|---|---|---|
| `source` | string | Where the content came from (docs URL, repo …). `"manual"` for hand-written skills registered by `approve` |
| `version` | string \| null | Upstream version the build reflects |
| `pinned_url` | string \| null | Exact URL scraped, if pinned |
| `built_at` | ISO 8601 \| null | Last successful build |
| `approved` | bool | The user's explicit approval for linking. New builds start `false` |
| `approved_at` | ISO 8601 \| null | When it was approved |
| `last_used` | ISO 8601 \| null | Latest Skill tool call, **copied from `usage.jsonl`** whenever a command saves `store.json` (the usage hook itself never writes `store.json`). Can lag behind the log; LRU reads the log first and uses this only as a fallback |

## Usage log: `<store>/usage.jsonl`

One JSON object per line, appended by `skill-store log-usage`:

```json
{"skill": "godot47-asset-pipeline", "session": "abc123", "project": "E:/Development/my-game", "time": "2026-11-05T15:30:42Z"}
```

Schema: [`schemas/usage-record.schema.json`](../schemas/usage-record.schema.json). `log-usage`
never creates a store, logs unknown or plugin skills without registering them, and always exits 0.
LRU uses the latest entry per skill **across all projects** (AO worktree paths differ per worktree),
falling back to `store.json` `last_used`.

| Field | Type | Source |
|---|---|---|
| `skill` | string | Hook input `tool_input.skill` (or `tool_input.command`) |
| `session` | string \| null | Hook input `session_id` |
| `project` | string \| null | `$CLAUDE_PROJECT_DIR`, falling back to the hook payload's `cwd` (forward slashes) |
| `time` | ISO 8601 | When the hook ran |

## Build recipe

See [build-recipe.md](build-recipe.md#recipe) and
[`schemas/recipe.schema.json`](../schemas/recipe.schema.json). Required: `recipe_version` (`1`),
`name`, `description`, `skill_seekers.config`. Optional: `frontmatter`, `source`, `version`,
`pinned_url`, `max_reference_tokens` (default 20000, max 25000).

## Hook input (what Claude Code sends on stdin)

`PostToolUse` with matcher `Skill`. Fields used by `log-usage`:

```json
{
  "session_id": "abc123",
  "cwd": "E:\\Development\\my-game",
  "hook_event_name": "PostToolUse",
  "tool_name": "Skill",
  "tool_input": { "skill": "godot47-asset-pipeline" },
  "tool_use_id": "toolu_01…"
}
```

The Claude Code hooks reference documents the common fields (`session_id`, `cwd`, `tool_name`,
`tool_input`, …) but not the Skill tool's `tool_input` shape. That's why `log-usage` accepts either
`skill` or `command`. Typing `/skill-name` yourself bypasses the Skill tool, so those uses aren't logged.
