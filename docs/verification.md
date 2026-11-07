# Verifying the store in a fresh Claude Code session

This checklist confirms that Claude Code really picks up skills linked from the store:

1. a **project-scope** junction/symlink skill is discovered and listed;
2. a link **added mid-session** is picked up without a restart;
3. a link **removed mid-session** stops working without a restart;
4. **edits inside the link target** (the store copy) are picked up;
5. on Windows, a **cross-volume** junction (store on `C:`, project on `E:`) behaves the same;
6. a fresh worktree gets its links from the `SessionStart` hook, and `--hook-output` makes them
   usable in that same session (or tells the user to run `/reload-skills`);
7. listing cost is visible in `/skill-doctor`.

## Last verified

| Date | OS | Claude Code | Mode |
|---|---|---|---|
| 2026-11-06 | Windows 10 Pro 19045 | 2.1.318 | `claude -p`, one continuous session for the mid-session steps |
| 2026-11-06 | Windows 10 Pro 19045 | 2.1.319 | interactive TUI (step 6) |
| 2026-11-06 | Kali Linux 2026.2 on WSL2 (kernel 6.18.40.1) | 2.1.318 | `claude -p`, one continuous session for the mid-session steps; `-p` step 6 |
| 2026-11-06 | Kali Linux 2026.2 on WSL2 (kernel 6.18.40.1) | 2.1.318 / 2.1.319 | interactive TUI (step 6: SessionStart case on 2.1.318, sync-before-launch control on 2.1.319); step 7 on 2.1.319 |
| 2026-11-06 | Windows 10 Pro 19045 | 2.1.319 | `claude -p`, step 6 with `sync --quiet --hook-output`, plus a plain `sync --quiet` control |
| 2026-11-06 | Kali Linux 2026.2 on WSL2 (kernel 6.18.40.1) | 2.1.319 | `claude -p` and interactive TUI (in tmux), step 6 with `sync --quiet --hook-output` via `hooks/skill-store-hook.sh`, plus a plain `sync --quiet` `-p` control |

| # | Step | Windows 10 (junctions) | Linux, WSL2 Kali (symlinks) |
|---|---|---|---|
| 1 | Project-scope linked skill discovered and invoked | ✅ | ✅ |
| 2 | Link added mid-session | ✅ next turn; the model is told (a `skill_listing` delta) | ✅ next turn, same delta; the watcher fires immediately |
| 3 | Link removed mid-session | ✅ gone from the registry; invoking it gives `Unknown skill`. ⚠️ **the model is not told**, so if asked it still claims the skill is available | ✅ same: `Unknown skill`, store copy kept. ⚠️ no removal delta for the model |
| 4 | Edit inside the store copy | ✅ re-invoking it loads the new body | ✅ |
| 5 | Cross-volume | ✅ store `C:`, project `E:` | n/a. Analogue: project on drvfs (`/mnt/c`), store on ext4 ✅ (Windows sees that symlink as a junction) |
| 6 | Fresh worktree via a plain `SessionStart` `sync --quiet` | ❌ interactive (2.1.319): not picked up at all (still missing on turn 2, ~26 s in) until `/reload-skills` or a new session. ⚠️ `-p` (2.1.318): from turn 2 | ❌ interactive (2.1.318): still missing on turn 2 (~1 min in); ✅ after `/reload-skills`. ⚠️ `-p` (2.1.318): from the first turn that *starts* after Claude Code's file watcher sees the new links (a few seconds after launch; ~4.4 s on Linux), so turn 2 or later (turn 3 in a fast run) |
| 6 | Fresh worktree via `SessionStart` `sync --quiet --hook-output` | ✅ `-p` (2.1.319): turn 1; the hook's `reloadSkills` put both skills in the session's skill list (the plain-hook control on the same version: missing on turn 1). The transcript records the notice as a hook system message. Interactive: not run | ✅ `-p` (2.1.319): turn 1 (control: missing). ✅ interactive (2.1.319): the TUI shows `SessionStart:startup says: skill-store linked 2 skills …`, and the skill works on turn 1 without `/reload-skills` |
| 6 | Control: `skill-store sync` before launch | ✅ turn 1 | ✅ turn 1, interactive and `-p` |
| 7 | `/skill-doctor` per-skill cost | ✅ ~40 tokens of listing per test skill. `/doctor`: no listing estimate in `-p` | ✅ ~40 tokens per test skill (2.1.319). In the interactive TUI it now opens Plugins → **Stats**. `/doctor`: an agentic health check, no estimate, in both modes |
| — | `PostToolUse` `Skill` usage hook | ✅ one `usage.jsonl` line per successful Skill call; none for a failed call | ✅ same, in both `-p` and the TUI |

On both systems, `/reload-skills` and restarts were never needed for steps 1–5. Links created *after* startup (step 2)
are picked up in the interactive TUI too. Only links created by a plain `sync --quiet`
`SessionStart` hook *during* startup are missed; with `--hook-output` (2.1.319) they are not.

What this means in practice:

- Mid-session `link`, `unlink` and edits just work. After an `unlink`, start a new session if you
  want the model's own picture of its skills to be accurate. The skill can no longer be invoked, but
  the model isn't told it's gone.
- For fresh checkouts and worktrees, **create the links before Claude Code starts**: run
  `skill-store sync` from a worktree-creation hook (e.g. an AO `--post-create` step) or from whatever
  launches Claude Code.
  This is the only path that works on the first prompt.
- A plain `sync --quiet` `SessionStart` hook is **not enough** for a fresh checkout. It creates the
  links, but the running session doesn't pick them up until `/reload-skills` or a new session.
- `sync --quiet --hook-output` fixes that on Claude Code 2.1.319: its `reloadSkills` output makes
  Claude Code re-scan after the SessionStart hooks, so the links work on the first prompt, and the
  user sees a notice to run `/reload-skills` once if they're still missing (e.g. on a version
  without the re-scan). Syncing before launch remains the most robust path: it doesn't depend on
  the Claude Code version or on folder trust.

## How to run it

It takes about 15 minutes and uses a throwaway store and project. Your real store and your
`~/.claude/settings.json` stay untouched: **both test hooks go in the scratch project's own
`.claude/settings.json`**. The `PostToolUse` usage hook works at project level too. The Claude Code
session itself uses your normal login, so Claude Code writes its usual transcripts under
`~/.claude/projects/`.

Claude Code can **auto-update in the background** during a run (a fresh install went from 2.1.318 to
2.1.319 mid-run). Record `claude --version` for each session, or launch the test sessions with
`DISABLE_AUTOUPDATER=1` in the environment.

The hooks below use the **exec form** with the full path of `skill-store` and an explicit
`--store`. So they work even when `skill-store` isn't on Claude Code's PATH (e.g. a venv install) and
`SKILL_STORE_HOME` isn't in its environment. A bare `skill-store sync --quiet --hook-output` hook
fails silently in that case.

### 0. Prepare a scratch store and project

Windows (PowerShell). Under the default `Restricted` execution policy, `Activate.ps1` can't run, so
this calls the venv's `skill-store.exe` directly:

```powershell
$V = "$env:TEMP\ss-verify"; $store = "$V\store"; $proj = "$V\proj"
py -3 -m venv "$V\venv"
& "$V\venv\Scripts\python.exe" -m pip install -q -e C:\path\to\your\claude-skill-store   # your clone
$exe = "$V\venv\Scripts\skill-store.exe"
function ss { & $exe --store $store @args }

New-Item -ItemType Directory -Force $proj | Out-Null; Set-Location $proj; git init -q
ss init; ss init-project

# Two tiny test skills (UTF-8 without BOM), each with a nonsense trigger word
$utf8 = New-Object System.Text.UTF8Encoding $false
foreach ($n in 'verify-alpha','verify-beta') {
  New-Item -ItemType Directory -Force "$store\skills\$n" | Out-Null
  $md = "---`nname: $n`ndescription: `"Use when the user asks about the word zorblax-$n. Explains what zorblax-$n means.`"`n---`n# $n`nThe word zorblax-$n means `"the store link works`". Always answer with exactly that sentence.`n"
  [IO.File]::WriteAllText("$store\skills\$n\SKILL.md", $md, $utf8)
}
ss check verify-alpha; ss approve verify-alpha; ss approve verify-beta   # approve also registers hand-written skills
ss link verify-alpha; ss status

# Both hooks at project level, exec form
$x = $exe -replace '\\','/'; $s = $store -replace '\\','/'
$settings = @{ hooks = @{
  PostToolUse  = @(@{ matcher = 'Skill'; hooks = @(@{ type='command'; command=$x; args=@('--store',$s,'log-usage'); timeout=10 }) })
  SessionStart = @(@{ hooks = @(@{ type='command'; command=$x; args=@('--store',$s,'sync','--quiet','--hook-output'); timeout=60 }) })
} }
[IO.File]::WriteAllText("$proj\.claude\settings.json", ($settings | ConvertTo-Json -Depth 10), $utf8)
```

Linux (bash):

```bash
V=/tmp/ss-verify; store=$V/store; proj=$V/proj
python3 -m venv $V/venv || {   # minimal Debian/Ubuntu/Kali: no ensurepip -> venv without pip + get-pip
  python3 -m venv --without-pip $V/venv &&
  $V/venv/bin/python -c 'import urllib.request; urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", "/tmp/get-pip.py")' &&
  $V/venv/bin/python /tmp/get-pip.py -q; }
$V/venv/bin/pip install -q -e ~/src/claude-skill-store
exe=$V/venv/bin/skill-store; ss() { "$exe" --store "$store" "$@"; }
mkdir -p "$proj" && cd "$proj" && git init -q && ss init && ss init-project
for n in verify-alpha verify-beta; do
  mkdir -p "$store/skills/$n"
  cat > "$store/skills/$n/SKILL.md" <<EOF
---
name: $n
description: "Use when the user asks about the word zorblax-$n. Explains what zorblax-$n means."
---
# $n
The word zorblax-$n means "the store link works". Always answer with exactly that sentence.
EOF
done
ss check verify-alpha && ss approve verify-alpha && ss approve verify-beta && ss link verify-alpha && ss status
cat > "$proj/.claude/settings.json" <<EOF
{"hooks": {
  "PostToolUse":  [{"matcher": "Skill", "hooks": [{"type": "command", "command": "$exe", "args": ["--store", "$store", "log-usage"], "timeout": 10}]}],
  "SessionStart": [{"hooks": [{"type": "command", "command": "$exe", "args": ["--store", "$store", "sync", "--quiet", "--hook-output"], "timeout": 60}]}]
}}
EOF
```

Open a second terminal for the mid-session commands and define the same `$store` / `$exe` / `ss`
there. A Git Bash window only inherits variables such as `SKILL_STORE_HOME` from the shell that
started it. A new window started from the Start menu or Explorer doesn't see a `$env:` variable set
in PowerShell, so `export` it again there, or pass `--store` explicitly (the `ss` helper does).

### 1. Project-scope link is discovered

Start `claude` in `$proj`. The interactive TUI asks whether you trust the folder, with **"No, exit"
selected by default**: choose to trust it. Project hooks (the usage hook and the SessionStart sync)
run only after that. Expect the prompt again for a new folder, such as the `E:` project in step 5.
Once one worktree of a repository is trusted, other worktrees of the same repository may **not** be
prompted (seen on Linux). In `-p` mode, project hooks ran without a prompt.

- [ ] `/skills` lists **verify-alpha** (and not verify-beta).
- [ ] Ask *"What does zorblax-verify-alpha mean?"*. Claude invokes the skill and answers "the store link works".
- [ ] `usage.jsonl` in the scratch store has a line for verify-alpha, and `ss status` shows its
      last-used time. (That column is **store-wide**: the latest use in any project.)

### 2. Link added mid-session

Leave the session open. In the second terminal, `ss link verify-beta`.

- [ ] Without a restart, on the next turn: `/skills` lists **verify-beta**, and asking about
      zorblax-verify-beta works.

### 3. Link removed mid-session

`ss unlink verify-alpha`

- [ ] `/skills` no longer lists verify-alpha (after the next turn).
- [ ] Asking Claude to invoke the verify-alpha skill fails with `Unknown skill`.
- [ ] `$store/skills/verify-alpha/SKILL.md` **still exists** (unlink never deletes).

Don't test removal by asking the model which skills it has. It isn't told about removals and will
still name verify-alpha.

### 4. Edits inside the target

Re-link alpha (`ss link verify-alpha`), then edit the **store copy**
`$store/skills/verify-alpha/SKILL.md` and change the answer sentence to "edit detected".

- [ ] In the same session, asking Claude to invoke verify-alpha again yields "edit detected".

### 5. Cross-volume (Windows only)

```powershell
$p2 = "E:\ss-verify\proj"; New-Item -ItemType Directory -Force $p2 | Out-Null; git -C $p2 init -q
ss --project $p2 init-project; ss --project $p2 link verify-alpha
Copy-Item "$proj\.claude\settings.json" "$p2\.claude\"
```

Start `claude` in `$p2`.

- [ ] verify-alpha is listed and works.

Optional Linux/WSL analogue: put a project on the Windows drive (`/mnt/c/...`, drvfs) and keep the
store on ext4. `ss --project <that dir> init-project`, `ss --project <that dir> link verify-alpha`,
copy the settings file, then start `claude` there. Windows sees that symlink as a **junction**: run
`ss unlink --all --project <that dir>` from WSL before deleting the folder from either side. Also
include that dir in the clean-up checks below.

### 6. Fresh worktree / checkout

```sh
git -C "$proj" add .claude
git -C "$proj" -c user.name=verify -c user.email=verify@example.invalid commit -qm "manifest + hooks"
git -C "$proj" worktree add ../proj-wt
```

`.claude/skills/` in `../proj-wt` now contains only `.gitignore`. Start `claude` there (the
committed `.claude/settings.json` carries the `sync --quiet --hook-output` hook).

- [ ] After the session starts, the links exist (the `SessionStart` hook created them).
- [ ] Interactive: the startup screen shows `SessionStart:startup says: skill-store linked 2 skills
      into this checkout at startup: verify-alpha, verify-beta. If /skills doesn't list them, run
      /reload-skills once.` With `claude -p --output-format stream-json --verbose`, the
      `hook_response` event's `output` is that hook JSON, on one line, with `"reloadSkills": true`.
- [ ] Ask about zorblax-verify-beta on the **first** turn: expected to work on 2.1.319 (the
      re-scan). If it doesn't, run `/reload-skills` (the notice says so), ask again: now it works.
- [ ] Start a second session in `../proj-wt`: no notice this time (nothing was created).
- [ ] `git -C ../proj-wt status` shows nothing under `.claude/skills/`.

For comparison, with a plain `sync --quiet` hook (no `--hook-output`, so no re-scan) the skills
were **not** known on turn 1 on 2.1.318/2.1.319. Interactive: still not on turn 2. `-p`: known from the first turn that *starts* after Claude
Code's file watcher sees the new links (a few seconds after launch; ~4.4 s on Linux), so turn 2 or
later. `/reload-skills` fixes it.

Control: sync **before** launch, in a second, genuinely fresh worktree. `proj-wt` can't be used for
this, because it already has its links by now.

```sh
git -C "$proj" worktree add ../proj-wt2
ss sync --project "$V/proj-wt2"        # creates the links before Claude Code starts
```

Start `claude` in `../proj-wt2` and trust the folder.

- [ ] Asking about zorblax-verify-beta works on the **first** turn, with no `/reload-skills`.

### 7. Listing cost

- [ ] `/skill-doctor` lists verify-alpha and verify-beta with their listing cost and use. In newer
      interactive versions (2.1.319) it opens the Plugins panel's **Stats** tab instead, with the
      same rows. `claude -p /skill-doctor` still prints the table.
- [ ] Don't rely on `/doctor` for this: on 2.1.318/2.1.319 it is an agentic health check and showed
      no skill-listing estimate, interactive or `-p`.

From Git Bash, prefix slash commands passed to `claude -p` with `MSYS_NO_PATHCONV=1`, e.g.
`MSYS_NO_PATHCONV=1 claude -p "/doctor"` or `MSYS_NO_PATHCONV=1 claude -p /skill-doctor`. Otherwise
MSYS rewrites `/doctor` into a Windows path such as `C:/Program Files/Git/doctor`. PowerShell doesn't
have this problem.

## Clean up

Unlink first, check that no links remain, then delete the scratch dirs. **Never** delete a project
with a recursive delete while links exist: it would recurse into the store through them.

```powershell
ss unlink --all --project "$V\proj-wt"; ss unlink --all --project "$V\proj-wt2"
ss unlink --all --project $proj; ss unlink --all --project E:\ss-verify\proj
git -C $proj worktree remove --force ..\proj-wt; git -C $proj worktree remove --force ..\proj-wt2
Get-ChildItem -Recurse -Force $V, E:\ss-verify -Attributes ReparsePoint -ErrorAction SilentlyContinue | Select-Object FullName   # must print nothing
Remove-Item -Recurse -Force $V, E:\ss-verify       # only if the line above printed nothing
```

Linux (bash):

```bash
ss unlink --all --project "$V/proj-wt"; ss unlink --all --project "$V/proj-wt2"; ss unlink --all --project "$proj"
git -C "$proj" worktree remove --force ../proj-wt; git -C "$proj" worktree remove --force ../proj-wt2
find "$V" -path "$V/venv" -prune -o -type l -print   # must print nothing (the venv's own symlinks are skipped)
rm -r "$V"                 # only if the line above printed nothing
```

