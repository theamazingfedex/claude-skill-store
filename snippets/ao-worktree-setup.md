# Linking skills in AO worktrees (and other fresh checkouts)

Links are never committed (`.claude/skills/.gitignore` ignores everything but itself), so every
new checkout or worktree starts **without** them. Something has to run `skill-store sync` there.

**Use the AO `--post-create` step as the primary path, and the `SessionStart` hook as a backstop.**
Verified with Claude Code 2.1.318: a session scans its skills *before* the `SessionStart` hook
runs. So links that only the hook creates are usable **from the second turn**, while an AO worker's
first prompt (or a `claude -p` one-shot) wouldn't see them. Links created by `--post-create` exist
before the session starts, so the first prompt can use them.

## 1. AO `--post-create` / `--pre-remove` steps (primary)

AO runs `--post-create` commands after it creates a session workspace and `--pre-remove` commands
before it deletes one:

- `skill-store sync` in post-create creates the links before the agent starts.
- `skill-store unlink --all` in pre-remove removes them, so that removing the worktree can't recurse
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
- `skill-store` must be on the PATH of the AO daemon's environment, and `SKILL_STORE_HOME` (if you
  use a non-default store) must be in it too. Otherwise use the full path to the console script
  plus `--store`, e.g.
  `C:/Users/YOU/AppData/Roaming/Python/Python312/Scripts/skill-store.exe --store D:/skill-store sync`.
- `unlink --all` removes only links that resolve into `<store>/skills`, non-recursively, and exits 0
  when there is nothing to unlink. Unlike `unlink <name>`, it **does not edit the manifest** (that
  file is committed, and other checkouts still want those skills).
- Call `skill-store` directly here, **not** through `hooks/skill-store-hook.*`. The hook wrapper
  always exits 0, but a pre-remove step should fail loudly if unlinking fails, before anything
  deletes the worktree.
- Unapproved manifest entries are reported by `sync` and not linked. Approval stays with the user.

## 2. SessionStart hook (backstop, any checkout, any tool)

Commit [`settings.project.json`](settings.project.json) into the project's `.claude/settings.json`
(merge it into the existing `hooks` object if there is one), or install the user-level hook from
[`settings.windows.json`](settings.windows.json) / [`settings.linux.json`](settings.linux.json).
Claude Code then runs `skill-store sync --quiet` at every session start, in whichever checkout the
session opens. This covers checkouts that AO didn't create, and repairs links after a `git pull`
changed the manifest. The links it creates are usable from the second turn.

The bare `skill-store …` command in `settings.project.json` needs `skill-store` on Claude Code's PATH
and `SKILL_STORE_HOME` (if set) in its environment. Otherwise it fails silently. If that isn't
guaranteed, use the wrapper snippets or the exec form with the full path plus `--store`:
`{"type": "command", "command": "<path>/skill-store.exe", "args": ["--store", "<store>", "sync", "--quiet"]}`.

In a project without `.claude/skills.manifest.json`, `sync` does nothing and creates nothing, so a
user-level hook is harmless in unrelated projects.
