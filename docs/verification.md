# Verifying the store in a fresh Claude Code session

This checklist confirms that Claude Code really picks up skills linked from the store:

1. a **project-scope** junction/symlink skill is discovered and listed;
2. a link **added mid-session** is picked up without a restart;
3. a link **removed mid-session** stops working without a restart;
4. **edits inside the link target** (the store copy) are picked up;
5. on Windows, a **cross-volume** junction (store on `C:`, project on `E:`) behaves the same;
6. a fresh worktree gets its links from the `SessionStart` hook;
7. listing cost is visible in `/skill-doctor`.

## Results so far

Windows 10 Pro 19045, Claude Code **2.1.318**, run by an independent agent on 2026-11-06. It used
one live `claude -p --input-format stream-json` process for the mid-session steps (no restart, no `--resume`):

| # | Step | Result |
|---|---|---|
| 1 | Project-scope junction skill discovered and invoked | ✅ |
| 2 | Link added mid-session | ✅ picked up on the next turn; the model is told (a `skill_listing` delta) |
| 3 | Link removed mid-session | ✅ for Claude Code (the skill is gone from the registry; invoking it gives `Unknown skill`). ⚠️ **the model is not told**, so if asked it still claims the skill is available |
| 4 | Edit inside the store copy | ✅ re-invoking it loads the new body |
| 5 | Cross-volume (store `C:`, project `E:`) | ✅ |
| 6 | Fresh worktree via `SessionStart` sync | ⚠️ links are created, but the skills only become available **from the second turn**: the session's first skill scan runs before the hook |
| 7 | `/skill-doctor` per-skill cost | ✅ (~40 tokens of listing per test skill). `/doctor` in `-p` mode showed no listing estimate |
| — | `PostToolUse` `Skill` usage hook | ✅ exactly one `usage.jsonl` line per successful Skill call; none for a failed call |

`/reload-skills` and restarts were never needed. If you run this on another OS or Claude Code
version, add a row set here.

What this means in practice:

- Mid-session `link`, `unlink` and edits just work. After an `unlink`, start a new session if you
  want the model's own picture of its skills to be accurate. The skill can no longer be invoked, but
  the model isn't told it's gone.
- **When the first prompt matters** (AO workers, `claude -p` one-shots), create the links **before**
  the session starts: an AO `--post-create "skill-store sync"` step, or `skill-store sync` in the
  script that launches Claude Code. The `SessionStart` hook alone makes them available from the
  second turn onwards.

## How to run it

It takes about 15 minutes and uses a throwaway store and project. Your real store and your
`~/.claude/settings.json` stay untouched: **both test hooks go in the scratch project's own
`.claude/settings.json`**. The `PostToolUse` usage hook works at project level too. The Claude Code
session itself uses your normal login, so Claude Code writes its usual transcripts under
`~/.claude/projects/`.

The hooks below use the **exec form** with the full path of `skill-store` and an explicit
`--store`. So they work even when `skill-store` isn't on Claude Code's PATH (e.g. a venv install) and
`SKILL_STORE_HOME` isn't in its environment. A bare `skill-store sync --quiet` hook fails silently
in that case.

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
  SessionStart = @(@{ hooks = @(@{ type='command'; command=$x; args=@('--store',$s,'sync','--quiet'); timeout=60 }) })
} }
[IO.File]::WriteAllText("$proj\.claude\settings.json", ($settings | ConvertTo-Json -Depth 10), $utf8)
```

Linux (bash):

```bash
V=/tmp/ss-verify; store=$V/store; proj=$V/proj
python3 -m venv $V/venv && $V/venv/bin/pip install -q -e ~/src/claude-skill-store
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
  "SessionStart": [{"hooks": [{"type": "command", "command": "$exe", "args": ["--store", "$store", "sync", "--quiet"], "timeout": 60}]}]
}}
EOF
```

Open a second terminal for the mid-session commands and define the same `$store` / `$exe` / `ss`
there (Git Bash only sees variables you export in it).

### 1. Project-scope link is discovered

Start `claude` in `$proj` and accept the workspace-trust prompt if it appears. (In `-p` mode,
project hooks run without one.)

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

### 6. Fresh worktree / checkout

```sh
git -C "$proj" add .claude
git -C "$proj" -c user.name=verify -c user.email=verify@example.invalid commit -qm "manifest + hooks"
git -C "$proj" worktree add ../proj-wt
```

`.claude/skills/` in `../proj-wt` now contains only `.gitignore`. Start `claude` there.

- [ ] After the session starts, the links exist (the `SessionStart` hook created them).
- [ ] The skills are available **from the second turn**. On the very first turn, Claude doesn't know them yet.
- [ ] `git -C ../proj-wt status` shows nothing under `.claude/skills/`.

### 7. Listing cost

- [ ] `/skill-doctor` lists verify-alpha and verify-beta with their listing cost and use.
- [ ] Optionally check whether an interactive `/doctor` shows a skill-listing estimate. (2.1.318
      in `-p` mode showed none.)

From Git Bash, run `claude -p /skill-doctor` as `MSYS_NO_PATHCONV=1 claude -p /skill-doctor`.
Otherwise MSYS rewrites `/skill-doctor` into a Windows path.

## Clean up

Unlink first, check that no links remain, then delete the scratch dirs. **Never** delete a project
with a recursive delete while links exist: it would recurse into the store through them.

```powershell
ss unlink --all --project "$V\proj-wt"; ss unlink --all --project $proj; ss unlink --all --project E:\ss-verify\proj
git -C $proj worktree remove --force ..\proj-wt
Get-ChildItem -Recurse -Force $V, E:\ss-verify -Attributes ReparsePoint -ErrorAction SilentlyContinue | Select-Object FullName   # must print nothing
Remove-Item -Recurse -Force $V, E:\ss-verify       # only if the line above printed nothing
```

On Linux: `ss unlink --all --project` each project, `git worktree remove`, check with
`find "$V" -type l`, then `rm -r "$V"`.

## Report the results

Record ✅/❌ per step, with your Claude Code version, your OS, and whether the project was on the
same volume as the store or a different one. Add them to the results table above (by PR), or post
them on issue #15/#16 in `claude-blender-generations`.
