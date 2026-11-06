# Linking skills in AO worktrees (and other fresh checkouts)

Links are never committed (`.claude/skills/.gitignore` ignores everything but itself), so every
new checkout or worktree starts **without** them. Something has to run `skill-store sync` there.
Use one or both of the following.

## 1. SessionStart hook (any checkout, any tool)

Commit [`settings.project.json`](settings.project.json) into the project's `.claude/settings.json`
(merge it into the existing `hooks` object if there is one), or install the user-level hook from
[`settings.windows.json`](settings.windows.json) / [`settings.linux.json`](settings.linux.json).
Claude Code then runs `skill-store sync --quiet` at every session start, in whichever checkout
the session opens. In a project without `.claude/skills.manifest.json`, `sync` does nothing and
creates nothing, so a user-level hook is harmless in unrelated projects.

## 2. AO `--post-create` / `--pre-remove` steps

AO runs `--post-create` commands after it creates a session workspace and `--pre-remove` commands
before it deletes one. Syncing in post-create means the links already exist when the agent starts.
Running `skill-store unlink --all` in pre-remove means that removing the worktree can't recurse
through a junction into the store.

Check what is configured first: **a repeatable flag replaces the whole list**, so repeat any
existing `postCreate`/`preRemove` entries in the same command.

```sh
ao project get <project-id> --json      # look at config.postCreate / config.preRemove
```

Windows (PowerShell):

```powershell
ao project set-config <project-id> `
  --post-create "skill-store sync" `
  --pre-remove  "skill-store unlink --all"
```

Linux (bash):

```bash
ao project set-config <project-id> \
  --post-create "skill-store sync" \
  --pre-remove  "skill-store unlink --all"
```

Notes:

- Both steps assume AO runs them with the new workspace as the working directory. `skill-store`
  resolves the project from `--project`, then `CLAUDE_PROJECT_DIR`, then the git toplevel of the cwd.
- `skill-store` must be on the PATH of the AO daemon's environment. If it isn't, use the full path to
  the console script, e.g. `C:/Users/YOU/AppData/Roaming/Python/Python312/Scripts/skill-store.exe`
  or `~/.local/bin/skill-store`.
- `unlink --all` removes only links that resolve into `<store>/skills`, non-recursively, and exits 0
  when there is nothing to unlink. Unlike `unlink <name>`, it **does not edit the manifest** (that
  file is committed, and other checkouts still want those skills).
- Call `skill-store` directly here, **not** through `hooks/skill-store-hook.*`. The hook wrapper
  always exits 0, but a pre-remove step should fail loudly if unlinking fails, before anything
  deletes the worktree.
- Unapproved manifest entries are reported by `sync` and not linked. Approval stays with the user.
