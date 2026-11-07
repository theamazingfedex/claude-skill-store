<!-- Paste this section into a project's CLAUDE.md (or any agent-instructions file). -->
## Skills store: unlink safety (hard rule)

Entries under `.claude/skills/` are **links** (NTFS junctions on Windows, symlinks on
Linux/macOS) into the shared user-level skills store (`~/.skill-store/skills/`). Deleting
*through* a link destroys the store copy for every project on this machine.

- Manage links only with the store tool: `skill-store link <name>`, `skill-store unlink <name>`,
  `skill-store unlink --all`, `skill-store sync`, `skill-store prune`. Unlinking removes the link and never the store copy.
- **Never run a recursive delete on anything under `.claude/skills/`**: no `rm -r`/`rm -rf`,
  no `Remove-Item -Recurse`, no `rmdir /s`, no `shutil.rmtree`, no file-by-file deletes inside a link.
- In Git Bash, **`rm -rf .claude/skills/<name>/` (trailing slash) deletes the store's files**
  and leaves the link behind. Without the slash it removes only the link, but don't rely on that.
- If you must remove one link by hand: Windows `cmd /c rmdir .claude\skills\<name>` or
  `[System.IO.Directory]::Delete('.claude\skills\<name>', $false)`; Linux/macOS `rm .claude/skills/<name>`
  or `unlink .claude/skills/<name>` (no `-r`, no trailing slash).
- The same rule holds for store links in the user-level `~/.claude/skills/` (the global scope):
  manage them only with `skill-store --global link|unlink|sync|prune`, and never touch the other
  entries there (other tools' links, `synced/`, `.trash/`, real directories).
- **Never use the vercel `skills` CLI `remove`** (`npx skills remove`): it deletes the canonical copy,
  not just the link. Never use `skill-seekers install-agent` either; it copies into `~/.claude/skills`.
- Never commit anything under `.claude/skills/` except its `.gitignore`: git follows junctions and
  would commit third-party store content.
- Linking a skill that has never been approved needs the user's explicit approval
  (`skill-store approve <name>` is run by the user, not by you). Building into the store is fine.
- Before deleting a checkout or worktree, remove its links first with `skill-store unlink --all`
  (removes every store link there, non-recursively, and leaves the manifest unchanged).
- If a skill listed in `.claude/skills.manifest.json` is missing in this session (e.g. a fresh
  worktree where the SessionStart hook created the links during startup), run `skill-store sync`,
  then ask the user to run `/reload-skills` once. Claude Code may not pick up links created while
  the session was starting (a `skill-store linked … after the session's first skill scan` note at
  session start means the hook created some).
