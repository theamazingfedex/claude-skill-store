# Design rationale

This summarises the research and the binding decision behind the skill store. They are recorded in
two issues in the private repo `theamazingfedex/claude-blender-generations`:

- **#15** *What do skill-seekers skills cost in context, and how can Claude link them from a skill store?*
  This is the research. Full findings are in `research/skill-context-and-linking.md` on branch
  `research/skill-context-and-linking`.
- **#16** *Design skill acquisition, linking and pruning for pipeline sessions*. Its resolution
  comment is the binding design. If this file and #16 disagree, #16 wins.

## The problem: the skill listing is the binding cost

A skill costs context in three layers:

| Layer | When it costs | Limit |
|---|---|---|
| **Listing entry** (name + description) | **Every turn**, whether or not the skill is used | All entries share `skillListingBudgetFraction` (default **1 %** of the window). Each description is capped at `skillListingMaxDescChars` (default 1,536 chars) |
| **`SKILL.md` body** | Once invoked, for the rest of the session | After compaction only the first 5k tokens per skill survive, 25k combined |
| **Reference files** | Only when Claude reads them | One Read returns ≤ 25k tokens; larger files come back partial |

When the listing overflows, Claude Code **drops descriptions of the least-used skills**. A skill
without a description does not auto-trigger. On the reference machine, synced claude.ai skills
alone used ~11.3k chars (~2.8k tokens), about 1 % of a 200k window. So every always-installed skill
competes for a budget that is already full.

Hence the store. Skills you have acquired sit **outside** every place Claude Code scans. Each
project **links in only the few it needs**, up to a cap. Pruning is **unlinking**, so a skill can
come back later without being rebuilt or re-downloaded.

## Decisions (from #16)

### A general, user-level tool

The store, the link tooling, the usage hook and the default cap live at user level. The tooling
is versioned in its own repo (this one). Each project commits only its **manifest**
(`.claude/skills.manifest.json`) and may override the cap.

### Store layout

`~/.skill-store/` (Windows: `%USERPROFILE%\.skill-store\`):

- `skills/<name>/`: linkable skills (`SKILL.md` + `references/`);
- `build/<name>/`: skill-seekers working dir plus raw scraped pages, so rebuilds need no re-download;
- `store.json`: the registry (source, version or pinned URL, build date, approval, last use);
- `usage.jsonl`: one line per Skill tool call.

The store stays **out of** `~/.claude/skills` (Claude Code actively manages that dir, and claude.ai
sync moves folders to `.trash`), out of `~/.agents` (owned by the vercel `skills` CLI), and out of
every project repo. The store is not a git repo of content either: scraped docs are third-party
text (e.g. Godot docs CC BY 3.0, Khronos copyright).

### Linking at project scope

`<project>/.claude/skills/<name>` → `<store>/skills/<name>`.

- **Windows: NTFS directory junctions.** They work without admin rights or Developer Mode. Directory
  symlinks fail with "You do not have sufficient privilege" unless Developer Mode is on or the
  session is elevated.
- **Linux/macOS: symlinks.**
- Claude Code follows both. Its docs say a skill entry "can be a symlink to a directory elsewhere on
  disk". `claude plugin validate` confirms a session "does follow" junctions, and a user-scope
  junction (`find-skills`) was seen loading. Project-scope junctions were then verified on Windows 10
  with Claude Code 2.1.318, including cross-volume links and mid-session link, unlink and edit
  pickup: see the [recorded results](verification.md#last-verified).
- **Links are never committed.** Each project commits `.claude/skills/.gitignore` containing `*`
  and `!.gitignore`, for two reasons:
  1. git follows junctions and would stage the target's files as regular files (verified);
  2. the directory must exist at session start, because Claude Code only live-reloads changes
     under a skills directory that already existed.
- Linking is driven by the manifest through an **idempotent** `sync`. It runs in every checkout or
  worktree, via an AO `--post-create` step and/or a `SessionStart` hook. Only `--post-create` (or
  any sync before launch) makes the links available on the **first** prompt. A session scans its
  skills before the `SessionStart` hook runs, so hook-created links appear from the second turn
  (verified with 2.1.318). `--post-create` is therefore the primary path and the hook a backstop.

### Unlink safety (standing rule)

Unlinking removes only the link (a non-recursive reparse-point or symlink removal) and never store
content. Measured on Windows:

| Operation on a junction | Result |
|---|---|
| `cmd /c rmdir <link>`, `os.rmdir`, `os.unlink`, `[IO.Directory]::Delete(p, $false)`, `(Get-Item p).Delete()` | link only |
| Git Bash `rm -rf <link>` | link only |
| Git Bash **`rm -rf <link>/`** (trailing slash) | **deletes the target's contents**, leaves the link |
| `shutil.rmtree(<link>)` (Python 3.15) | refuses ("Cannot call rmtree on a symbolic link") |
| PowerShell 5.1 `Remove-Item -Recurse -Force <link>` | link only on the tested build, but **not relied upon** |
| vercel `skills remove` | **deletes the canonical copy** too; it has no unlink |

So the tool checks that an entry is a link before removing it and refuses real directories. The
rule ships as a CLAUDE.md snippet ([`snippets/CLAUDE.skill-store.md`](../snippets/CLAUDE.skill-store.md)).

### Acquisition and approval

- **Building into the store is free.** Automation and workers may build anything.
- **Linking a never-approved skill needs the user's explicit approval**, recorded in `store.json`
  (`skill-store approve <name>`).
- **Re-linking a previously approved skill is automatic.**

### Admission rules

These are enforced by `skill-store check` before linking. They are looser than the research first suggested:

- description **≤ 500 chars**, use case first, **quoted** YAML;
- `SKILL.md` **≤ 4k tokens** (measured as chars / 4);
- each reference file **≤ 25k tokens** (what one Read returns), with a table of contents
  (required for files over 100 lines).

### Cap and pruning

- Default cap: **8 linked domain skills per project**. The manifest can override it.
- Linking beyond the cap **automatically unlinks the least recently used** skill (from the usage
  log) and reports which. That is the only pruning signal. The evicted skill is also dropped from
  the manifest, or the next `sync` would re-link it.
- Otherwise pruning runs **on demand** (`skill-store prune`). There is no schedule.
- `sync` runs at every session start in every checkout, so it **never writes the manifest** (no
  surprise git diffs). If the manifest lists more than the cap, `sync` links the most recently used
  and reports the rest.
- Recency is global: the latest use of a skill in any project or worktree. Never-used skills count
  as least recent.

### Usage tracking

A `PostToolUse` hook with matcher `Skill` appends `{skill, session, project, time}` to
`usage.jsonl` in the store. The hook only appends: it never rewrites `store.json`, so
concurrent sessions can't lose each other's updates (or an `approve`). `usage.jsonl` is the source
of truth for recency; `store.json` `last_used` is refreshed from it whenever a command saves
`store.json`. This covers AO workers and stays outside every
repo. Note that typing `/skill-name` directly bypasses the Skill tool and therefore the hook. Those
invocations are not counted, which can make a skill you only invoke by hand look unused.

### Build recipe

skill-seekers is pinned to **3.1.3** and run with `--enhance-level 0`. A **post-processor** then
writes or validates a real quoted description, splits references to the limits with TOCs, and
drops junk. skill-seekers' `quality` score is **not** used, because it is a filename heuristic.
See [build-recipe.md](build-recipe.md).

### Out of scope / related decisions

- Global `skillOverrides` for synced claude.ai skills (e.g. the Office group set to `name-only`)
  are a user-settings change, not part of this tool.
- No Blender skills: the Blender Lab MCP already serves the 5.x API and manual on demand, at zero
  listing cost.
