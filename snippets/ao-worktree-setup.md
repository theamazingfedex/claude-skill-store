# Linking skills in AO worktrees (and other fresh checkouts)

Links are never committed (`.claude/skills/.gitignore` ignores everything but itself), so every
new checkout or worktree starts **without** them. Something has to run `skill-store sync` there.
Use one or both of the following.

## 1. SessionStart hook (any checkout, any tool)

Commit [`settings.project.json`](settings.project.json) into the project's `.claude/settings.json`
(merge it into the existing `hooks` object if there is one), or install the user-level hook from
[`settings.windows.json`](settings.windows.json) / [`settings.linux.json`](settings.linux.json).
Claude Code then runs `skill-store sync --quiet` at every session start, in whichever checkout
the session opens. The user-level wrapper skips projects that have no `.claude/skills.manifest.json`.

## 2. AO `--post-create` / `--pre-remove` steps

AO runs `--post-create` commands after it creates a session workspace and `--pre-remove` commands
before it deletes one. Syncing in post-create means the links already exist when the agent starts.
Unlinking in pre-remove means that removing the worktree can't recurse through a junction into the store.

Check what is configured first: **a repeatable flag replaces the whole list**, so repeat any
existing `postCreate`/`preRemove` entries in the same command.

```sh
ao project get <project-id> --json      # look at config.postCreate / config.preRemove
```

Windows (PowerShell; adjust the clone path):

```powershell
ao project set-config <project-id> `
  --post-create "skill-store sync" `
  --pre-remove  "powershell -NoProfile -ExecutionPolicy Bypass -File C:/Users/YOU/src/claude-skill-store/hooks/unlink-all.ps1"
```

Linux (bash):

```bash
ao project set-config <project-id> \
  --post-create "skill-store sync" \
  --pre-remove  "sh $HOME/src/claude-skill-store/hooks/unlink-all.sh"
```

Notes:

- Both steps assume AO runs them with the new workspace as the working directory. `skill-store`
  resolves the project from `--project`, then `CLAUDE_PROJECT_DIR`, then the git toplevel of the cwd.
  `unlink-all` takes the project as its first argument (`-Project` on Windows), and otherwise
  `CLAUDE_PROJECT_DIR`, then the cwd.
- `skill-store` must be on the PATH of the AO daemon's environment. If it isn't, use the full path to
  the console script, e.g. `C:/Users/YOU/AppData/Roaming/Python/Python312/Scripts/skill-store.exe`
  or `~/.local/bin/skill-store`.
- `unlink-all` removes only links that resolve into `<store>/skills`, non-recursively. It never edits
  the manifest (that file is committed; the worktree is going away anyway).
- Unapproved manifest entries are reported by `sync` and not linked. Approval stays with the user.
