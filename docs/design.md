# Design and rationale

Why the skill store exists, and why it is built the way it is. Measurements were taken on Windows 10
with Claude Code 2.1.3xx and skill-seekers 3.1.3. Public sources are listed at the end.

## The problem: the skill listing is the binding cost

A skill costs context in three layers:

| Layer | When it costs | Limit |
|---|---|---|
| **Listing entry** (name + description) | **Every turn**, whether or not the skill is used | All entries share `skillListingBudgetFraction` (default **1 %** of the window). Each description is capped at `skillListingMaxDescChars` (default 1,536 chars) |
| **`SKILL.md` body** | Once invoked, for the rest of the session | After compaction only the first 5k tokens per skill survive, 25k combined |
| **Reference files** | Only when Claude reads them | One Read returns ≤ 25k tokens; larger files come back partial |

When the listing overflows, Claude Code **drops descriptions of the least-used skills**, and a skill
without a description does not auto-trigger. The synced claude.ai skills of a typical account can
already take ~11k chars (~2.8k tokens), about 1 % of a 200k window. So every always-installed skill
competes for a budget that is often already full.

Hence the store. Skills you have acquired sit **outside** every place Claude Code scans. Each
project **links in only the few it needs**, up to a cap. Pruning is **unlinking**, so a skill can
come back later without being rebuilt or re-downloaded.

Measured skill-seekers builds at `--enhance-level 0` give an idea of sizes. A focused 15-page docs
scope gave a `SKILL.md` of ~0.6–0.8k tokens and ~25k tokens of references. A large single-page
specification gave one unstructured 31–37k-token reference, larger than one Read returns. A useful,
hand-written description is 220–320 chars (55–80 tokens) of listing.

## Decisions

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

The store stays **out of** `~/.claude/skills`, `~/.agents` and every project repo:
- Claude Code actively manages `~/.claude/skills`, and claude.ai sync moves folders to `.trash`.
- `~/.agents` is owned by the vercel `skills` CLI.

The store is not a git repo of content either: scraped docs are third-party text under their own
licences (e.g. CC BY, or a vendor's copyright).

### Linking at project scope

`<project>/.claude/skills/<name>` → `<store>/skills/<name>`.

- **Windows: NTFS directory junctions.** They work without admin rights or Developer Mode. Directory
  symlinks (`mklink /D`) fail with "You do not have sufficient privilege" unless Developer Mode is on
  or the shell is elevated.
- **Linux/macOS: symlinks.**
- Claude Code follows both. Its docs say a skill entry "can be a symlink to a directory elsewhere on
  disk". Project-scope junctions were verified on Windows 10 with Claude Code 2.1.318, including
  cross-volume links and mid-session link, unlink and edit pickup: see the
  [recorded results](verification.md#last-verified).
- **Links are never committed.** Each project commits `.claude/skills/.gitignore` containing `*`
  and `!.gitignore`, for two reasons:
  1. Git follows junctions and would stage the target's files as regular files (verified). And on
     Windows without symlink privilege, a committed symlink checks out as a small text file holding
     the target path.
  2. The directory must exist at session start, because Claude Code only live-reloads changes
     under a skills directory that already existed.
- Linking is driven by the manifest through an **idempotent** `sync`. Run it in every checkout or
  worktree **before** Claude Code starts, for example from a worktree-creation hook such as an AO
  `--post-create` step, or from a launch wrapper. A `SessionStart` hook can run it too, but only as a
  backstop: a session scans its skills before that hook runs. Links the hook creates during startup
  appeared from turn 2 in `claude -p` (2.1.318), and **not at all until `/reload-skills`** in the
  interactive TUI (2.1.319). Links created later in a session are picked up normally.

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

- **Building into the store is free.** People, scripts and agents may build anything into it.
- **Linking a never-approved skill needs the user's explicit approval**, recorded in `store.json`
  (`skill-store approve <name>`).
- **Re-linking a previously approved skill is automatic.**

### Admission rules

`skill-store check` enforces these before linking:

- description **≤ 500 chars**, use case first, **quoted** YAML;
- `SKILL.md` **≤ 4k tokens** (measured as chars / 4);
- each reference file **≤ 25k tokens** (what one Read returns), with a table of contents
  (required for files over 100 lines).

The limits are deliberately a little generous, not minimal. Tighter targets (≤ 300-char
descriptions, ≤ 2k-token bodies, ≤ 20k-token references) are good practice. The build's
post-processor splits references at 20k tokens by default.

### Cap and pruning

- Default cap: **8 linked domain skills per project**. The manifest can override it.
- Linking beyond the cap **automatically unlinks the least recently used** skill (from the usage
  log) and reports which. That is the only pruning signal. The evicted skill is also dropped from
  the manifest, or the next `sync` would re-link it.
- Otherwise pruning runs **on demand** (`skill-store prune`). There is no schedule.
- `sync` runs in every checkout, so it **never writes the manifest** (no surprise git diffs). If the
  manifest lists more than the cap, `sync` links the most recently used and reports the rest.
- Recency is global: the latest use of a skill in any project or worktree. Never-used skills count
  as least recent.

### Usage tracking

A `PostToolUse` hook with matcher `Skill` appends `{skill, session, project, time}` to
`usage.jsonl` in the store:

- The hook only appends. It never rewrites `store.json`, so concurrent sessions can't lose each
  other's updates (or an `approve`).
- `usage.jsonl` is the source of truth for recency. `store.json` `last_used` is refreshed from it
  whenever a command saves `store.json`.
- The log lives in the store, outside every repo, and covers every session that has the hook,
  including ones in automated worktrees.
- Typing `/skill-name` directly bypasses the Skill tool and therefore the hook. Those invocations
  are not counted, which can make a skill you only invoke by hand look unused.

### Build recipe

skill-seekers is pinned to **3.1.3** and run with `--enhance-level 0`. A **post-processor** then
writes or validates a real quoted description, splits references to the limits with TOCs, and
drops junk. skill-seekers' `quality` score is **not** used, because it is a filename heuristic
(renaming reference files changes it). See [build-recipe.md](build-recipe.md).

### Out of scope

- Global `skillOverrides` for synced claude.ai skills (e.g. setting rarely used ones to
  `name-only`) are a user-settings change, not part of this tool.
- If a tool's documentation is already served on demand by an MCP doc server, prefer that to a
  scraped skill. It costs no listing budget beyond the tool definitions.

## Sources

- Claude Code docs: [Skills](https://code.claude.com/docs/en/skills) (listing budget, per-entry cap,
  content lifecycle and compaction, locations, symlinks, live change detection and `/reload-skills`,
  `skillOverrides`, `/skill-doctor`); [Settings](https://code.claude.com/docs/en/settings)
  (`skillListingBudgetFraction`, `skillListingMaxDescChars`); [Hooks](https://code.claude.com/docs/en/hooks);
  [Tools reference](https://code.claude.com/docs/en/tools-reference) (Read limits).
- Anthropic, [Agent Skills best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices).
- Microsoft, [Hard links and junctions](https://learn.microsoft.com/en-us/windows/win32/fileio/hard-links-and-junctions);
  [Create symbolic links privilege](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/create-symbolic-links).
- Git for Windows, [Symbolic links](https://github.com/git-for-windows/git/wiki/Symbolic-Links).
- [skill-seekers](https://github.com/yusufkaraaslan/Skill_Seekers) (3.1.3 source: `install_agent.py`,
  `create_command.py`, `incremental_updater.py`, `quality_metrics.py`).
- vercel-labs [`skills` CLI](https://github.com/vercel-labs/skills).
