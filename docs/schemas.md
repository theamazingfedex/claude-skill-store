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
  "skills": ["godot-asset-pipeline", "gltf-2-subset"]
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `version` | integer, `1` | yes | Schema version |
| `cap` | integer ≥ 1 | no (default **8**) | Max linked domain skills for this project. Linking past it unlinks the least recently used |
| `skills` | array of skill names | yes | Skills to link. `sync` creates missing links (approved skills only) and removes store links not listed |

`link` and `unlink` edit this file for you. Editing it by hand and running `sync` is equivalent.

## `.claude/skills/.gitignore`

```gitignore
*
!.gitignore
```

Created by `init-project`, `link` and `sync` if missing. Commit it. It keeps links (and, because git
follows junctions, the store's third-party content) out of the repo, and makes sure the directory
exists at session start so Claude Code live-reloads link changes.

## Registry: `<store>/store.json`

```json
{
  "version": 1,
  "skills": {
    "godot-asset-pipeline": {
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
| `source` | string | Where the content came from (docs URL, repo, "hand-written" …) |
| `version` | string \| null | Upstream version the build reflects |
| `pinned_url` | string \| null | Exact URL scraped, if pinned |
| `built_at` | ISO 8601 \| null | Last successful build |
| `approved` | bool | The user's explicit approval for linking. New builds start `false` |
| `approved_at` | ISO 8601 \| null | When it was approved |
| `last_used` | ISO 8601 \| null | Last Skill tool call seen by the usage hook. Drives LRU pruning |

## Usage log: `<store>/usage.jsonl`

One JSON object per line, appended by `skill-store log-usage`:

```json
{"skill": "godot-asset-pipeline", "session": "abc123", "project": "E:\\Development\\my-game", "time": "2026-11-05T15:30:42Z"}
```

| Field | Type | Source |
|---|---|---|
| `skill` | string | Hook input `tool_input.skill` (or `tool_input.command`) |
| `session` | string \| null | Hook input `session_id` |
| `project` | string \| null | Resolved project root (`CLAUDE_PROJECT_DIR` / hook `cwd`) |
| `time` | ISO 8601 | When the hook ran |

## Hook input (what Claude Code sends on stdin)

`PostToolUse` with matcher `Skill`. Fields used by `log-usage`:

```json
{
  "session_id": "abc123",
  "cwd": "E:\\Development\\my-game",
  "hook_event_name": "PostToolUse",
  "tool_name": "Skill",
  "tool_input": { "skill": "godot-asset-pipeline" },
  "tool_use_id": "toolu_01…"
}
```

The Claude Code hooks reference documents the common fields (`session_id`, `cwd`, `tool_name`,
`tool_input`, …) but not the Skill tool's `tool_input` shape. That's why `log-usage` accepts either
`skill` or `command`. Typing `/skill-name` yourself bypasses the Skill tool, so those uses aren't logged.
