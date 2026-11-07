# claude-skill-store

A user-level **skills store** for [Claude Code](https://code.claude.com/docs). You acquire skills
once into a store outside every project. Each project then links in only the small,
approved, capped set it needs, and a few skills you want everywhere can be linked into
`~/.claude/skills` with the [global scope](#global-scope). Pruning **unlinks and never deletes**,
so a skill can be re-linked later without being rebuilt or re-downloaded.

Works on **Windows** (NTFS junctions, no admin or Developer Mode needed) and **Linux/macOS**
(symlinks).

- [Why: the context budget](#why-the-context-budget)
- [How it works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Install](#install): [Windows](#windows-powershell) · [Linux](#linux-bash)
- [Initialize the store](#initialize-the-store)
- [Set up a project](#set-up-a-project-manifest-and-gitignore)
- [Build a skill](#build-a-skill) · [Approve it](#approve-a-skill) · [Link, unlink, sync](#link-unlink-sync-status)
- [Global scope](#global-scope): [Windows](#global-scope-on-windows-powershell) · [Linux](#global-scope-on-linux-bash) · [Moving from `--project ~`](#moving-from---project-)
- [Cap and pruning](#cap-and-pruning)
- [Enable the hooks](#enable-the-hooks-usage-tracking-and-session-start-sync)
- [Unlink safety (hard rule)](#unlink-safety-hard-rule)
- [Verify in a fresh Claude Code session](#verify-in-a-fresh-claude-code-session)
- [Troubleshooting](#troubleshooting)
- [Command reference](#command-reference) · [Repo layout](#repo-layout) · [License](#license)

## Why: the context budget

Every installed skill puts a **listing entry** (name + description) into Claude's context on
**every turn**, whether or not it is ever used. All entries share one budget:
`skillListingBudgetFraction`, **1 % of the context window** by default, with each description
capped at 1,536 chars. When the listing overflows, Claude Code drops the descriptions of the
least-used skills, and a skill without a description no longer auto-triggers. Synced claude.ai
skills alone can fill most of that 1 %.

So installing every useful skill into `~/.claude/skills` makes all of them worse. The store keeps
skills **out of every place Claude Code scans**. Each project links in only what it needs, by
default **at most 8 domain skills**, and every store skill must pass admission rules that keep it
cheap:

| Layer | Costs context | Admission rule |
|---|---|---|
| Listing entry | every turn | description ≤ **500 chars**, use case first, **quoted** YAML |
| `SKILL.md` body | once invoked, for the rest of the session | ≤ **4k tokens** (chars / 4) |
| each reference file | only when read (one Read returns ≤ 25k tokens) | ≤ **25k tokens**, with a table of contents |

Background and measurements: [docs/design.md](docs/design.md).

## How it works

```
~/.skill-store/                                   (Windows: %USERPROFILE%\.skill-store\)
├── skills/<name>/      SKILL.md + references/    ◄─┐ linkable skills
├── build/<name>/       skill-seekers cwd + raw pages (rebuilds need no re-download)
├── store.json          registry: source, version, built_at, approved, last_used
└── usage.jsonl         one line per Skill tool call (written by the usage hook)
                                                    │
<project>/.claude/                                  │
├── skills.manifest.json   committed: {"version":1,"cap":8,"skills":[...]}
└── skills/                                         │
    ├── .gitignore         committed: "*" + "!.gitignore"
    └── <name>  ── junction (Windows) / symlink ────┘   never committed
```

- **build** a skill into the store (free: anyone or any automation may do it);
- **approve** it (the user's explicit decision, recorded in `store.json`, needed once per skill);
- **link** it into a project (adds it to the manifest and creates the link; re-linking an approved
  skill needs no new approval);
- **sync** at session start makes the links in a checkout match its manifest (new worktrees
  start with no links);
- past the cap, the **least recently used** link is unlinked automatically and reported;
- the **usage hook** logs each Skill tool call, which is what "least recently used" is based on;
- optionally, `--global` links a skill into `~/.claude/skills` for **every** project, with its own
  manifest (in the store) and its own cap: see [Global scope](#global-scope).

The store lives outside `~/.claude/skills` (Claude Code manages that directory itself), outside
`~/.agents` (owned by the vercel `skills` CLI), and outside every repo. Scraped docs are
third-party text and must never end up in a project's git history.

## Prerequisites

| | Version | Needed for |
|---|---|---|
| Python | **3.10+** | the `skill-store` CLI (stdlib + PyYAML) |
| git | any recent | cloning; project root detection |
| [Claude Code](https://code.claude.com/docs) | recent (`/skill-doctor` needs v2.1.252+) | using the skills, hooks |
| skill-seekers | **exactly 3.1.3** (installed by the `[build]` extra) | only `skill-store build` |
| `gh` (GitHub CLI) | optional | cloning this private repo, reporting verification results |
| [Agent Orchestrator (AO)](https://github.com/OrchestratorInc/agent-orchestrator) | optional, additional | running coding agents in parallel worktrees; see [AO worktrees](#ao-worktrees) |

On Windows, nothing needs admin rights or Developer Mode: the tool uses directory **junctions**,
not symlinks.

## Install

The examples clone into `~/src/claude-skill-store`. Any path works, but the hook snippets in
[`snippets/`](snippets/) use that one, so adjust them if yours differs.

### Windows (PowerShell)

```powershell
# 1. Clone (private repo: gh handles auth; plain git works too if you have access)
New-Item -ItemType Directory -Force "$env:USERPROFILE\src" | Out-Null
gh repo clone theamazingfedex/claude-skill-store "$env:USERPROFILE\src\claude-skill-store"
Set-Location "$env:USERPROFILE\src\claude-skill-store"

# 2. Install the CLI (editable, so `git pull` updates it). The [build] extra adds skill-seekers==3.1.3,
#    which only `skill-store build` needs; drop it for link/sync-only machines.
py -3 -m pip install --user -e ".[build]"

# 3. Make sure the console script is on PATH (pip prints a warning if it isn't)
skill-store --help
# If not found: add the user Scripts dir, e.g.
#   $s = py -3 -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"
#   [Environment]::SetEnvironmentVariable('Path', "$env:Path;$s", 'User')   # then open a new terminal
# Without PATH changes you can always use:  py -3 -m skillstore --help
```

Prefer isolation? `pipx install -e ".[build]"` (the package is `claude-skill-store`). To run from the
checkout without installing at all, use the thin wrappers in [`bin/`](bin/):
`bin\skill-store.cmd` / `bin\skill-store.ps1`. They still need Python and PyYAML.

### Linux (bash)

```bash
# 1. Clone
mkdir -p ~/src
gh repo clone theamazingfedex/claude-skill-store ~/src/claude-skill-store   # or: git clone git@github.com:theamazingfedex/claude-skill-store.git
cd ~/src/claude-skill-store

# 2. Install the CLI (editable). The [build] extra adds skill-seekers==3.1.3 (only `build` needs it).
python3 -m pip install --user -e ".[build]"
# On distros with an "externally managed" system Python (PEP 668), use pipx or a venv instead:
#   pipx install -e ".[build]"
# Or run from the checkout without installing: bin/skill-store.sh (needs Python + PyYAML)

# 3. Check it is on PATH (~/.local/bin for --user installs)
skill-store --help || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
chmod +x hooks/*.sh
```

macOS works like Linux.

## Initialize the store

```sh
skill-store init          # creates ~/.skill-store/{skills,build}, store.json and usage.jsonl. Idempotent.
skill-store status
```

To put the store somewhere else, set `SKILL_STORE_HOME`, or pass `--store PATH` to any command.
Set it **persistently** so Claude Code's hooks see the same value:

```powershell
[Environment]::SetEnvironmentVariable('SKILL_STORE_HOME', 'D:\skill-store', 'User')   # Windows, new terminals
```
```bash
echo 'export SKILL_STORE_HOME="$HOME/.skill-store"' >> ~/.bashrc                    # Linux
```

The store is **not** a git repo and should not be put in one.

## Set up a project: manifest and `.gitignore`

From the project root:

```sh
skill-store init-project
```

This creates two files. **Commit both**:

- `.claude/skills.manifest.json`, the skills this project links:

  ```json
  { "version": 1, "cap": 8, "skills": [] }
  ```

  `cap` is optional (default 8). `link`, `unlink` and `prune` edit the list for you; `sync` only
  reads it (it never writes the manifest, so session starts don't create git diffs).

- `.claude/skills/.gitignore`, which keeps links out of git:

  ```gitignore
  # Managed by skill-store: links into the skill store are never committed.
  *
  !.gitignore
  ```

  An existing `.gitignore` there is never modified. `sync` warns if it doesn't contain `*`.

  Both reasons matter. **Git follows junctions**: without this file, `git add -A` stages the
  store's files as regular files (verified on Windows), and on Linux a committed symlink is
  meaningless on other machines. The committed file also guarantees `.claude/skills/` **exists at
  session start**, which Claude Code needs in order to pick up links added later in the session.

Then add the unlink-safety section to the project's `CLAUDE.md`. Copy
[`snippets/CLAUDE.skill-store.md`](snippets/CLAUDE.skill-store.md) (see
[Unlink safety](#unlink-safety-hard-rule)).

Full field reference: [docs/schemas.md](docs/schemas.md).

## Build a skill

```sh
skill-store build godot47-asset-pipeline --recipe examples/recipes/godot47-asset-pipeline.recipe.json
skill-store build <name> --recipe <recipe.json> --skip-scrape    # rebuild from cached raw pages
skill-store postprocess <name> [--recipe FILE] [--from DIR]      # re-run only the post-processor
skill-store check <name|path>                                    # admission rules; exit 1 on failure
```

A **recipe** (JSON, [`schemas/recipe.schema.json`](schemas/recipe.schema.json)) carries the skill's
`name`, the hand-written `description`, provenance (`source`, `version`, `pinned_url`) and the
skill-seekers config under `skill_seekers.config`. Examples are in [`examples/recipes/`](examples/recipes/).

`build` runs skill-seekers **3.1.3** (`scrape --config … --enhance-level 0`) with
`~/.skill-store/build/<name>/` as the cwd. The post-processor then:
- writes the recipe's **quoted** description;
- splits references over `max_reference_tokens` (default 20k) at headings and adds TOCs;
- drops junk (e.g. `quality_report.json`);
- validates the result: a strict YAML parse and `check`, plus `claude plugin validate` on a
  throwaway plugin wrapper when `claude` is on PATH (`--no-claude-validate` skips that);
- registers it in `store.json` with `approved: false`.

A rebuild swaps `skills/<name>` in place, so existing links keep working, and the previous version
is kept in `build/<name>/previous/`. `build` refuses other skill-seekers versions unless you pass
`--any-skill-seekers-version`. It fails if skill-seekers produced no reference files.

Read [docs/build-recipe.md](docs/build-recipe.md) before writing a recipe. skill-seekers 3.1.3 has
about a dozen pitfalls: unquoted descriptions, `--output` ignored, the unified docs route that
silently produces no references, a crashing GitHub route, and more. **Always hand-write the
description.** The generated ones don't trigger.

Never use `skill-seekers install-agent` for the store: it copies into a hard-coded `~/.claude/skills/`.

## Approve a skill

```sh
skill-store approve <name>
```

Approval is **your** explicit decision and is recorded in `store.json`. It is needed once per skill.
`approve` also registers a hand-written skill you placed in `~/.skill-store/skills/<name>/` yourself
(`source: "manual"`). It warns, but still approves, if the skill fails `check`; `link` will refuse it
until it passes.


- linking a never-approved skill fails, telling you to run `approve`;
- `sync` reports unapproved manifest entries and doesn't link them;
- re-linking a skill that was approved before (e.g. after it was pruned) is automatic.

Agents and automation may build skills freely, but should ask you before anything new is approved.

## Link, unlink, sync, status

```sh
skill-store link <name>      # must exist, pass `check`, and be approved; adds to manifest, creates link, enforces cap
skill-store unlink <name>    # removes ONLY the link (never the store copy), removes from manifest
skill-store unlink --all     # removes every store link in this checkout; manifest unchanged (before deleting a worktree)
skill-store sync             # make links match the manifest; idempotent, safe to run any time
skill-store status           # store skills, approval, linked here?, last used (store-wide), cap usage
```

All commands accept `--store PATH` and `--project PATH`, and `--json` where it makes sense. The
project is resolved from `--project`, then `CLAUDE_PROJECT_DIR`, then the git toplevel of the cwd,
then the cwd.

What `sync` does (it only reads the manifest, never writes it):

- creates missing links for manifest entries that are **approved** and pass **`check`**;
- reports, without linking, entries that are not approved, not in the store, or fail `check`
  (a skill that fails `check` after a rebuild also has its existing link removed);
- removes links that point **into the store** but are no longer in the manifest;
- leaves real directories and links pointing elsewhere alone (a name taken by one is reported as a
  conflict);
- if the manifest lists more linkable skills than the cap, it links the `cap` most recently used
  ones and reports the rest as over the cap;
- does nothing at all (not even creating `.claude/skills/`) in a project with no manifest.

Run it after `git pull` changes a manifest, and in every new checkout or worktree. The
[hooks](#enable-the-hooks-usage-tracking-and-session-start-sync) do this for you at session start.

Under the hood: Windows creates an NTFS **junction** (like `New-Item -ItemType Junction` /
`mklink /J`), and Linux/macOS an ordinary **symlink** (`ln -s`).

## Global scope

Some skills are useful in every project. The global scope links a store skill into the
user-level Claude Code skills directory instead of a project:

```
~/.claude/skills/<name>  ──link──►  <store>/skills/<name>  [──link──►  a checkout, if the store entry is itself a link]
```

- **Target directory:** `$CLAUDE_CONFIG_DIR/skills` if `CLAUDE_CONFIG_DIR` is set, otherwise
  `~/.claude/skills` (Windows: `%USERPROFILE%\.claude\skills`). It is created if missing.
  skill-store **never** writes a `.gitignore` or a manifest there.
- **Manifest:** `<store>/global.manifest.json` (same format as a project manifest; see
  [docs/schemas.md](docs/schemas.md#global-manifest-storeglobalmanifestjson)). `--global link`
  creates it.
- **Cap:** its own, default **8**, separate from every project's. To change it, set `"cap": N` in
  `<store>/global.manifest.json`. Linking past it unlinks the least recently used **global** link.
- **Approval:** the same gate as projects. `skill-store approve <name>` is needed once per skill,
  whichever scope it is linked into.
- **Safety:** the same rules as projects. Only links that resolve into the store are ever removed,
  one at a time and non-recursively. Everything else in `~/.claude/skills` is left alone: links
  made by other tools (e.g. the vercel `skills` CLI, which points into `~/.agents`), `synced/`,
  `.trash/` and real skill directories. If one of those already uses a skill's name, `link`
  refuses and `sync` reports it as a conflict. If `~/.claude/skills` is itself a link, the global
  commands refuse to manage it.
- **A store entry that is a link works too.** If `<store>/skills/<name>` is a junction or symlink
  to a git checkout, the global link points at the store entry, and Claude Code follows both.
  Unlinking removes only the global link; the store entry and the checkout are untouched.
- **Status:** `skill-store --global status` lists global links and cap usage. Plain
  `skill-store status` in a project adds a `global` column and a `global: N/cap` line, so you can
  see which skills every session already gets. A skill linked globally does not need to be linked
  into a project too.
- `--global` cannot be combined with `--project`. It works with `link`, `unlink`,
  `unlink --all`, `sync`, `status` and `prune`. The flag can go before or after the command.

Every global link costs listing budget in **every** session on the machine, so keep the global
set small and put project-specific skills in project manifests.

Claude Code's docs allow a skill entry in `~/.claude/skills` to be a link to a directory elsewhere,
as in a project. The recorded live-session results in
[docs/verification.md](docs/verification.md#last-verified) cover project-scope links; run the same
checklist with `--global` to confirm global links on your setup.

### Global scope on Windows (PowerShell)

```powershell
skill-store approve blender-asset             # once per skill; your decision
skill-store --global link blender-asset       # creates $HOME\.claude\skills\blender-asset (a junction)
skill-store --global status                   # global links, cap usage, entries left alone
skill-store --global sync                     # make global links match <store>\global.manifest.json
skill-store --global unlink blender-asset     # removes only the link and drops it from the global manifest
skill-store --global unlink --all             # removes every global store link; manifest unchanged
skill-store --global prune --cap 4            # unlink least recently used global links down to 4

Get-Item "$HOME\.claude\skills\blender-asset" | Select-Object LinkType, Target   # inspect a link
```

### Global scope on Linux (bash)

```bash
skill-store approve blender-asset             # once per skill; your decision
skill-store --global link blender-asset       # creates ~/.claude/skills/blender-asset (a symlink)
skill-store --global status                   # global links, cap usage, entries left alone
skill-store --global sync                     # make global links match <store>/global.manifest.json
skill-store --global unlink blender-asset     # removes only the link and drops it from the global manifest
skill-store --global unlink --all             # removes every global store link; manifest unchanged
skill-store --global prune --cap 4            # unlink least recently used global links down to 4

readlink ~/.claude/skills/blender-asset       # inspect a link
```

Global links live outside every checkout, so new worktrees need nothing for them: there is no
global step at session start. Run `skill-store --global sync` after you edit
`<store>/global.manifest.json` by hand, or to repair global links.

### Moving from `--project ~`

Before the global scope existed, the only way to link a skill everywhere was to treat the home
directory as a project (`skill-store --project ~ link <name>`). That wrote
`~/.claude/skills.manifest.json` and `~/.claude/skills/.gitignore`. skill-store detects that
file: `--global status`, `--global sync` and plain `status` print an `old setup found` notice
with the exact commands for your machine. To move:

```powershell
skill-store --global status            # shows the "old setup found" notice and the commands
skill-store migrate-global             # adds every skill the old manifest lists to the global scope;
                                       # existing links are kept as they are; no files are deleted
[System.IO.File]::Delete("$HOME\.claude\skills.manifest.json")
[System.IO.File]::Delete("$HOME\.claude\skills\.gitignore")
skill-store --global status            # the notice is gone
```

```bash
skill-store --global status            # shows the "old setup found" notice and the commands
skill-store migrate-global             # adds every skill the old manifest lists to the global scope;
                                       # existing links are kept as they are; no files are deleted
rm -- ~/.claude/skills.manifest.json ~/.claude/skills/.gitignore
skill-store --global status            # the notice is gone
```

- `migrate-global` is the same as running `skill-store --global link <name>` for each skill the
  old manifest lists. It copies the old manifest's `cap`, if it had one, into a new global
  manifest. Skills that are no longer approved or no longer pass `check` are reported and their
  links are left in place.
- The notice only lists `~/.claude/skills/.gitignore` when it is still the unchanged file
  skill-store wrote. Delete files with the commands above only (they remove single files, never
  directories).
- If `CLAUDE_CONFIG_DIR` points somewhere else, the old links in `~/.claude/skills` are no longer
  read by Claude Code. The notice then also lists `skill-store --project <home> unlink --all`, which
  removes those old store links only.
- Until you migrate, `--global sync` and `--global prune` leave the old setup's links alone and
  don't count them toward the global cap. `--global unlink <name>` and `--global unlink --all` are
  different: they remove store links whatever listed them, including old-setup links you haven't
  migrated yet. Run `migrate-global` first if you want to keep those.
- If `~/.claude/skills.manifest.json` can't be read, the notice says so and prints no delete
  command. `migrate-global` refuses, and global `sync` and `prune` leave every store link that the
  global manifest doesn't list alone. Fix the file (or check what it listed and delete it
  yourself), then migrate.
- Once `<store>/global.manifest.json` exists, project commands refuse a project whose
  `.claude/skills` is the global skills directory (`--project ~`), so the two can't fight over it.
  Use `--global` instead.

## Cap and pruning

- Each project links at most **8** domain skills by default. Override it with `"cap": N` in the manifest.
- `link` past the cap **unlinks the least recently used** other skill(s), **removes them from the
  manifest** (so the next `sync` doesn't re-link them), and tells you which:
  `over cap (8): unlinked least recently used X (last used …); its store copy is untouched`.
  The skill stays in the store and stays approved, so `skill-store link X` brings it back at any
  time without new approval.
- `skill-store prune [--cap N]` does the same on demand, down to the cap (default: the manifest's).
  There is **no** scheduled pruning.
- `sync` never prunes the manifest. If the manifest lists more skills than the cap (e.g. after a
  merge), it links the most recently used ones and reports the rest.
- "Recently used" means the latest `usage.jsonl` entry for the skill **across all projects and
  worktrees**, falling back to `store.json` `last_used`. **Never-used skills count as least
  recent**, and ties break by manifest order. So a skill you just linked but haven't used yet is
  the first to go when you link another one past the cap.

LRU only works if the [usage hook](#enable-the-hooks-usage-tracking-and-session-start-sync) is
installed. Typing `/skill-name` yourself bypasses the Skill tool, so those uses aren't counted.

## Enable the hooks (usage tracking and session-start sync)

Two Claude Code hooks connect the store to sessions:

| Hook | Matcher | Runs | Purpose |
|---|---|---|---|
| `PostToolUse` | `Skill` | `skill-store log-usage` | append `{skill, session, project, time}` to `usage.jsonl` (append only; it never writes `store.json`) |
| `SessionStart` | (all) | `skill-store sync --quiet --hook-output` | create or repair this checkout's links, and tell Claude Code and the user when it created any (see below) |

Both must never get in the way: `log-usage` and `sync --quiet --hook-output` always exit 0.

**What `--hook-output` does.** Claude Code scans skills before SessionStart hooks finish, so links
the hook creates during startup would otherwise be missed by that session. When `sync` creates or
re-points at least one link, `--hook-output` prints one line of
[SessionStart hook JSON](https://code.claude.com/docs/en/hooks#sessionstart) and nothing else:

```json
{"systemMessage": "skill-store linked 2 skills into this checkout at startup: verify-alpha, verify-beta. If /skills doesn't list them, run /reload-skills once.",
 "hookSpecificOutput": {"hookEventName": "SessionStart", "reloadSkills": true,
  "additionalContext": "skill-store linked these skills into this checkout after the session's first skill scan: verify-alpha, verify-beta. If one of them is needed but isn't available, ask the user to run /reload-skills once."}}
```

(Printed on one line; wrapped here for reading.)

- `reloadSkills: true` asks Claude Code to re-scan the skill directories once the SessionStart
  hooks finish. With Claude Code 2.1.319 the new links were then available on the first prompt
  (see [docs/verification.md](docs/verification.md#last-verified)).
- `systemMessage` is shown to you, e.g. `SessionStart:startup says: skill-store linked …` in the
  interactive TUI. If the skills still aren't listed (an older Claude Code without the re-scan),
  run `/reload-skills` once.
- `additionalContext` gives the model the same note.

When nothing was created (links already in place, only removals, no manifest, no store), it prints
nothing. Problems go to stderr as with `--quiet`. `--hook-output` implies `--quiet` and ignores
`--json`, so stdout carries only the hook JSON. It works with `--global sync` too. Without
`--hook-output`, `sync --quiet` is unchanged and prints nothing on success: plain-text
SessionStart output would be added to Claude's context.

The snippets follow the [Claude Code hooks reference](https://code.claude.com/docs/en/hooks).
Merge them into your existing `hooks` object instead of replacing it. Check the result with
`/hooks` inside Claude Code.

**Recommended setup: user-level, via the wrapper scripts.** These live in `~/.claude/settings.json`,
so they cover every project and every session, including automated ones in [AO](#ao-worktrees) worktrees. The wrappers in [`hooks/`](hooks/) find
`skill-store` even if it isn't on the hook's PATH (`SKILL_STORE_CMD`, then PATH, then
`python -m skillstore`) and always exit 0. In a project without a manifest, `sync` is a no-op that
creates nothing, so a user-level SessionStart hook is harmless in unrelated projects.

### Windows

Merge [`snippets/settings.windows.json`](snippets/settings.windows.json) into
`%USERPROFILE%\.claude\settings.json` and replace `C:/Users/YOU/src/claude-skill-store` with your
clone path. It uses the exec form (`"command": "powershell.exe"` + `"args"`), so it works whether or
not Git Bash is installed, and no quoting is needed:

```json
{
  "type": "command",
  "command": "powershell.exe",
  "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
           "C:/Users/YOU/src/claude-skill-store/hooks/skill-store-hook.ps1", "log-usage"],
  "timeout": 10
}
```

### Linux

Merge [`snippets/settings.linux.json`](snippets/settings.linux.json) into `~/.claude/settings.json`
(adjust the path if you didn't clone to `~/src`) and make the scripts executable
(`chmod +x hooks/*.sh`):

```json
{
  "type": "command",
  "command": "\"$HOME/src/claude-skill-store/hooks/skill-store-hook.sh\" log-usage",
  "timeout": 10
}
```

### Alternatives

- **Direct commands** (no wrapper), if `skill-store` is reliably on PATH for Claude Code. These work
  on both OSes:
  - [`snippets/settings.user.json`](snippets/settings.user.json): user-level usage hook;
  - [`snippets/settings.project.json`](snippets/settings.project.json): a `SessionStart` sync to
    **commit** into a project's `.claude/settings.json`, so everyone who clones it gets the sync.
- The bare `skill-store …` commands need `skill-store` on Claude Code's PATH and, if you set one, a
  custom `SKILL_STORE_HOME` in its environment (user environment variable / shell profile). Hooks
  inherit Claude Code's environment. Otherwise they fail silently. For a venv install, use the exec
  form with the full path plus `--store`:
  `{"type": "command", "command": "C:/…/venv/Scripts/skill-store.exe", "args": ["--store", "C:/…/store", "log-usage"]}`.

**Workspace trust.** Project-level hooks (in a project's `.claude/settings.json`) run only in a folder you
have trusted. In the interactive TUI, a new folder (including a new worktree path) shows "Is this a
project you created or one you trust?" with **"No, exit" selected by default**. Until you choose to
trust it, neither the SessionStart sync nor the usage hook runs. Claude Code records the choice in
`~/.claude.json`. In `claude -p` mode, project hooks ran without a prompt. User-level hooks in
`~/.claude/settings.json` don't depend on this.

### AO worktrees

**AO** ([Agent Orchestrator](https://github.com/OrchestratorInc/agent-orchestrator)) is a separate
tool for planning, running and supervising many coding agents (Claude Code, Codex and others) at
once. It gives each agent session its own git worktree. **You don't need AO to use the skill
store.** This section matters only if you use AO, or anything else that creates checkouts or
worktrees for you. For AO it shows which project settings run `skill-store` at the right moments.
Without AO, the launch wrapper below does the same job.

New worktrees start without links. **Always run `skill-store sync` before launching `claude` in a
fresh checkout or worktree.** Use an AO `--post-create "skill-store sync"` step, or a small launch
wrapper. Links created that way are seen on the first prompt:

```powershell
# Windows: e.g. a function in your PowerShell profile
function claude-ss { skill-store sync; claude @args }
```
```bash
# Linux: e.g. in ~/.bashrc
claude-ss() { skill-store sync; claude "$@"; }
```

`sync` exits 0 when there's nothing to do, and the wrapper starts `claude` even if `sync` reports a
problem.

The SessionStart hook creates the links too, but only after the session has scanned its skills.
With a plain `sync --quiet` hook, that first session missed them. Verified on Windows and Linux: in the
interactive TUI (Claude Code 2.1.319 on Windows, 2.1.318 on Linux) the session **didn't pick them up
at all** until `/reload-skills`. With `claude -p` they appeared only from the first turn that
started after Claude Code's file watcher saw them (a few seconds after launch; turn 2 or later).
The snippets therefore use `sync --quiet --hook-output`, which asks Claude Code to re-scan skills
and tells you to run `/reload-skills` once if they're still missing (see
[What `--hook-output` does](#enable-the-hooks-usage-tracking-and-session-start-sync)). With Claude
Code 2.1.319 that made fresh-worktree links available on the first prompt (`claude -p` on Windows
and Linux, interactive TUI on Linux). Syncing before launch still works on any Claude Code version
and doesn't depend on folder trust, so keep it as the primary path.

As a `--pre-remove` step, an AO project can run `skill-store unlink --all`, which removes the
worktree's store links non-recursively, without editing the manifest, before AO deletes the
directory. See
[snippets/ao-worktree-setup.md](snippets/ao-worktree-setup.md).

## Unlink safety (hard rule)

> **Unlinking removes only the link. Never run a recursive delete on anything under
> `.claude/skills/`.** The entries there are junctions/symlinks into the shared store, and a
> recursive delete through one destroys the store copy for every project on the machine.

- Use `skill-store unlink` / `sync` / `prune`. They check that the entry is a link, refuse real
  directories, and remove the link non-recursively (`os.rmdir`/`os.unlink`).
- By hand, one link at a time:
  - Windows: `cmd /c rmdir .claude\skills\<name>` or
    `[System.IO.Directory]::Delete("$PWD\.claude\skills\<name>", $false)`. **Never**
    `Remove-Item -Recurse` and never `rmdir /s`.
  - Linux/macOS: `rm .claude/skills/<name>` or `unlink .claude/skills/<name>`. **Never** `rm -r`.
- **Git Bash trap:** `rm -rf .claude/skills/<name>/` **with a trailing slash wipes the store's
  files** and leaves the link behind (verified). Without the slash it removes only the link, but
  don't rely on remembering that.
- **Never use the vercel `skills` CLI `remove`** (`npx skills remove …`). It has no unlink, and
  `remove` deletes the canonical copy too. Leave links it created (`~/.claude/skills/<name>` →
  `~/.agents/skills/<name>`) alone; this tool never touches them.
- Before deleting a checkout or worktree that has links, run `skill-store unlink --all` there
  first. It removes every store link non-recursively and leaves the manifest unchanged.

Put this rule in every project that uses the store. Copy
[`snippets/CLAUDE.skill-store.md`](snippets/CLAUDE.skill-store.md) into its `CLAUDE.md`, so agents
working there follow it too.

## Verify in a fresh Claude Code session

Verified on Windows 10 and on Linux (WSL2 Kali) with Claude Code 2.1.318/2.1.319 (details in
[docs/verification.md](docs/verification.md#last-verified)):

- ✅ a project-scope **linked** skill (a junction on Windows, a symlink on Linux) is discovered and
  invoked. On Windows that includes a **cross-volume** link (store on `C:`, project on `E:`); on
  WSL it includes a project on `/mnt/c` with the store on the Linux filesystem;
- ✅ a link **added** mid-session is picked up on the next turn, without a restart or `/reload-skills`;
- ✅ an **edit** to the store copy is picked up when the skill is invoked again;
- ✅ a link **removed** mid-session can no longer be invoked (`Unknown skill`). ⚠️ The model isn't
  told about removals, though, so it may still name the skill until a new session starts;
- ❌/⚠️ links created by a plain `sync --quiet` `SessionStart` hook **during startup** are missed by
  that session. In the interactive TUI they don't appear until `/reload-skills`. In `claude -p` they
  appear from the first turn that starts after the file watcher sees them (turn 2 or later). Links
  that exist **before** launch are found on turn 1. So sync before launching (AO `--post-create`):
  see [AO worktrees](#ao-worktrees);
- ✅ with `sync --quiet --hook-output` (Claude Code 2.1.319), the hook's `reloadSkills` re-scan made
  those links available on turn 1 (`claude -p` on Windows and Linux, interactive TUI on Linux), and
  the TUI showed the `/reload-skills` notice;
- ✅ the `PostToolUse` usage hook logs one line per Skill call; `/skill-doctor` shows per-skill cost.

These results hold on both Windows 10 (junctions) and Linux (WSL2 Kali, symlinks).

Run the ~15-minute checklist in [docs/verification.md](docs/verification.md) on each new OS or
Claude Code version. It uses a scratch store, a scratch project with two tiny test skills, and
project-level hooks only. In short:

```sh
skill-store link verify-alpha    # then start `claude` in the project: /skills lists it, asking triggers it
skill-store link verify-beta     # with the session still open: does it appear without a restart?
skill-store unlink verify-alpha  # can it still be invoked? is the store copy still there?
# edit the store copy of verify-alpha's SKILL.md: is the change picked up?
```

Record ✅/❌ per step, along with your Claude Code version and OS.

## Troubleshooting

**`skill-store` not found (terminal or hooks).** The user Scripts dir isn't on PATH (Windows
`py -3 -c "import sysconfig; print(sysconfig.get_path('scripts','nt_user'))"`, Linux `~/.local/bin`).
Fix PATH, or use the wrapper hooks and set `SKILL_STORE_CMD` to the full path of the console
script (or of `bin/skill-store.sh` / `bin\skill-store.cmd` in your clone). `python -m skillstore`
works wherever the package is installed. Claude Code inherits its environment from where it
was launched, so restart it after changing PATH.

**Windows: "A required privilege is not held by the client" / "You do not have sufficient
privilege".** That is a *symlink* being created. Symlinks need Developer Mode or an elevated shell;
junctions don't. `skill-store` uses junctions on Windows. If you made links by hand, use
`New-Item -ItemType Junction` or `mklink /J`, not `mklink /D`. Turning on Developer Mode isn't
required for anything here.

**Windows: store and project on different drives.** Junctions support any **local** volume
(e.g. store on `C:`, repo on `E:`). `link`, reading and writing through the junction, `sync` and
`unlink` were tested in both directions and work. Claude Code discovers and invokes skills through
such a cross-drive junction too: this was verified with Claude Code 2.1.318 on Windows 10 (store on
`C:`, project on `E:`; see [docs/verification.md](docs/verification.md#last-verified)). Junctions
**cannot** point at network shares
or mapped network drives, so keep the store on a local disk. A junction stores an absolute path: if
you move the store, unlink everywhere first, then `sync` again with the new `SKILL_STORE_HOME`.

**Git wants to commit files under `.claude/skills/`.** The `.claude/skills/.gitignore` is missing
or was edited (`sync` warns when it lacks `*`). If it is missing, `skill-store init-project`
recreates it. An existing file is never modified, so if it was edited, put the `*` and `!.gitignore`
lines back by hand. If store files are already
staged, unstage them with `git rm -r --cached .claude/skills/<name>`. `--cached` touches only the
index and leaves the working tree and the store alone. If they were committed, remove them from
history before pushing: they are third-party content.

**A committed symlink checked out as a small text file** (Windows without symlink privilege). Links
must never be committed. Delete that *file*, make sure the `.gitignore` is in place, and run `sync`.

**A linked skill doesn't show up, or doesn't trigger.**
- `skill-store status`: is it linked in *this* checkout? Approved? Run `skill-store sync`.
- Did `.claude/skills/` exist when the session started? If not, Claude Code isn't watching it:
  run `/reload-skills` (the committed `.gitignore` prevents this).
- Fresh worktree or checkout, with links created by the SessionStart hook? With a plain
  `sync --quiet` hook that session misses them. With `sync --quiet --hook-output` the hook asks
  Claude Code to re-scan and shows a `/reload-skills` notice. Either way, `/reload-skills` once
  fixes it; sync before launching next time (AO `--post-create`).
- **Listing budget:** if the listing overflows, descriptions of rarely used skills are dropped.
  Run `/skill-doctor` (per-skill cost vs use, v2.1.252+; in newer interactive versions it opens the
  Plugins panel's Stats tab). `/doctor` is a general setup check. To free
  budget, link fewer skills, keep descriptions short and use-case-first, or set skills you rarely
  need via `skillOverrides` in `settings.json`:

  ```json
  { "skillOverrides": { "docx": "name-only", "pptx": "name-only", "some-skill": "off" } }
  ```

  Values: `on` (default), `name-only` (listed without description), `user-invocable-only` (hidden
  from Claude, still in the `/` menu), `off`. The `/skills` menu can set them for you (Space to
  cycle, Esc saves to `.claude/settings.local.json`). Plugin skills aren't affected. As a last
  resort, raise `skillListingBudgetFraction` (default `0.01`).
- **YAML errors / empty metadata:** the description is probably unquoted with a `: ` inside. Run
  `skill-store check <name>` and `skill-store postprocess <name>`.

**`link` refuses.** It requires the skill to exist in the store, pass `check`, and be approved.
The error says which. Run `skill-store check <name>`, then `skill-store approve <name>` (your decision).

**`sync` didn't link something in the manifest, or removed a link.** Run `skill-store sync`
without `--quiet` (or `skill-store status`).

Entries it didn't link are reported as:
- `needs approval`;
- `not in store`;
- `fails admission, not linked` (it fails `check`, e.g. after a rebuild; the reasons follow in brackets);
- `over cap, not linked` (more listed than the cap; the most recently used win);
- `name taken by a non-store entry` (a real directory or a foreign link there, which `sync` never
  touches).

Every link it removed is reported under exactly one reason:
- `unlinked (no longer listed)`;
- `over cap, unlinked`;
- `unlinked (now fails admission)`;
- `unlinked (not in store or not approved)`;
- `relinked` (it pointed at the wrong store skill).

A link that can't be removed (e.g. a junction held open by antivirus or the indexer) is reported as
an error for that skill, and the rest of the sync continues. With `--quiet`, these notes go to
stderr and the exit code is always 0. You can see them with `claude --debug`.

**Usage isn't being logged / LRU looks wrong.** Check `/hooks` in Claude Code, and run with
`claude --debug` to see hook output. Only Skill **tool** calls are logged: typing `/skill-name`
bypasses the tool. Test the hook by hand:
`echo '{"tool_name":"Skill","tool_input":{"skill":"x"},"session_id":"t"}' | skill-store log-usage`
(use a scratch `SKILL_STORE_HOME`).

**SessionStart sync prints into the conversation.** Plain-text SessionStart output is added to
Claude's context. Use `sync --quiet --hook-output` (the snippets do): it prints nothing, or one
line of hook JSON that Claude Code parses instead of adding it as text. Make sure nothing else in
the hook command (e.g. a shell profile) writes to stdout, or Claude Code can't read the JSON.

**PowerShell refuses to run the hook script** (execution policy). The snippets pass
`-ExecutionPolicy Bypass` for that one process. Running `Unblock-File hooks\*.ps1` also helps if
the files came from a download.

**Something deleted store files.** Almost always a recursive delete through a link (see
[Unlink safety](#unlink-safety-hard-rule)). Rebuild with `skill-store build <name> --recipe
~/.skill-store/build/<name>/recipe.json --skip-scrape`: the raw pages in `build/<name>/output/` make
this cheap, unless they were deleted too. After a successful rebuild, the version before it is in
`build/<name>/previous/`.

If a rebuild can't move the new version into place (e.g. Windows Defender holds a file open),
`build` puts the previous version back as the live `skills/<name>/`, so links never dangle. The new
build is left in `build/<name>/staging/` for inspection. In that case `build/<name>/previous/` no
longer exists: the old backup was deleted, and the live version was moved into it and back out.

## Command reference

| Command | Does |
|---|---|
| `init` | create the store layout, `store.json` and `usage.jsonl` (idempotent) |
| `init-project` | create `.claude/skills/.gitignore` and an empty manifest if missing |
| `build <name> --recipe FILE [--skip-scrape] [--no-claude-validate] [--any-skill-seekers-version]` | skill-seekers 3.1.3 build + post-process + register (unapproved) |
| `postprocess <name> [--recipe FILE] [--from DIR] [--no-claude-validate]` | re-run only the post-processor (default input `build/<name>/output/<name>`) |
| `check <name\|path>` | admission rules; exit 1 on failure |
| `approve <name>` | record your approval in `store.json`; registers hand-written skills (`source: "manual"`) |
| `link <name>` | approved + checked only; add to manifest, link, enforce cap (LRU unlinked **and removed from manifest**, reported) |
| `unlink <name>` | remove the link only; remove from manifest |
| `unlink --all` | remove every store link in this project, non-recursively; manifest unchanged; exits 0 if none |
| `sync [--quiet]` | make links match the manifest (never writes it); idempotent; no-op (creates nothing) without a manifest |
| `sync --quiet --hook-output` | SessionStart hook entry point: as `sync --quiet`, plus one line of hook JSON (`reloadSkills` and a `/reload-skills` notice) when it created links, otherwise no output; always exits 0 |
| `status` | skills, approval, manifest, linked here, linked globally, check result, last used, cap usage |
| `prune [--cap N]` | unlink LRU skills down to the cap and remove them from the manifest (on demand only) |
| `--global link <name>` | the same as `link`, into `$CLAUDE_CONFIG_DIR/skills` or `~/.claude/skills`; manifest `<store>/global.manifest.json` |
| `--global unlink <name>` / `--global unlink --all` | remove global store links only, non-recursively; foreign entries are never touched |
| `--global sync [--quiet] [--hook-output]` | make global links match the global manifest; no-op without one; prints the migration notice if `--project ~` was used |
| `--global status` | global links, cap usage, entries left alone, migration notice |
| `--global prune [--cap N]` | unlink LRU global links down to the global cap |
| `migrate-global` | move the old `--project ~` setup to the global scope: links only, deletes no files, prints the cleanup commands |
| `log-usage` | hook entry point: read hook JSON on stdin, append to `usage.jsonl`; always exits 0 |

Global flags (before or after the command): `--store PATH`, `--project PATH` or `--global`
(not both), `--json`, plus `--version`. `skill-store <command> --help` is authoritative.

Exit codes: `0` = ok, `1` = refused or failed, `2` = usage error (e.g. `--global` with `--project`). Exceptions: `log-usage`,
`sync --quiet` and `sync --hook-output` always exit 0 (`log-usage` and `--hook-output` even on bad arguments), and plain `sync` exits 1 only if an operation errored.

## Running tests

Tests run locally; hosted CI is optional and manual-only. Each script creates (or reuses) a
venv in the system temp dir (not the repo), runs `pip install -e ".[test]"` and then pytest, and
exits non-zero if anything fails. The tests redirect `HOME`/`USERPROFILE`/`SKILL_STORE_HOME`/
`CLAUDE_CONFIG_DIR` to temp dirs and never touch the real `~/.claude`, `~/.agents` or `~/.skill-store`.

Windows (PowerShell), optionally also inside WSL:

```powershell
.\scripts\test-local.ps1                     # Windows only
.\scripts\test-local.ps1 -Wsl                # Windows, then WSL (default distro kali-linux)
.\scripts\test-local.ps1 -Wsl -WslDistro Ubuntu -OtherVolume E:\tmp
```

Linux, or from a WSL shell:

```bash
sh scripts/test-local.sh
sh scripts/test-local.sh --other-volume /mnt/c/Users/<you>/AppData/Local/Temp -- -k sync
```

- `-OtherVolume` / `--other-volume` enables the opt-in cross-volume link tests. Give a writable
  directory on a different drive or filesystem than the temp dir.
- In WSL, a checkout under `/mnt/<drive>/` is copied to a Linux temp dir first.
- If the distro's Python has no `ensurepip`, the venv is created `--without-pip` and pip is
  bootstrapped from `get-pip.py` (no sudo).
- Extra arguments go to pytest (after `--` for the `.sh` script).

## Repo layout

| Path | Contents |
|---|---|
| `skillstore/` | the Python CLI (`skill-store`, `python -m skillstore`) |
| `bin/` | thin `skill-store.ps1` / `.sh` / `.cmd` wrappers |
| `schemas/`, `examples/` | JSON Schemas (project and global manifest, store, usage record, recipe); example manifests, `store.json`, recipes |
| `tests/` | tests for link/unlink/sync/prune safety and the build pipeline: `pip install -e ".[test]"` then `pytest` |
| `.github/workflows/` | CI (ubuntu + windows × Python 3.10 / 3.12), **manual-only** (`workflow_dispatch`): never runs on push or PR and is never required; running it uses the account's free included Actions minutes |
| `scripts/` | local test runners: `test-local.ps1` (Windows, `-Wsl` for WSL too) and `test-local.sh` (Linux/WSL); see [Running tests](#running-tests) |
| `hooks/` | Claude Code hook wrappers (`log-usage`, `sync --quiet --hook-output`) |
| `snippets/` | `settings.json` snippets (Windows, Linux, direct), CLAUDE.md safety section, AO worktree setup |
| `docs/` | [design rationale](docs/design.md), [build recipe](docs/build-recipe.md), [schemas](docs/schemas.md), [verification checklist](docs/verification.md) |

Contributors: tests and development must **never** touch the real `~/.claude`, `~/.agents` or
`~/.skill-store`. Use temp dirs and `SKILL_STORE_HOME` / `--store`.

## License

This project is dedicated to the public domain under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/). See [LICENSE](LICENSE)
for the full legal code. CC0 covers this repository's own code and docs, not the third-party
documentation that `skill-store` scrapes into your store, which keeps its original license.
