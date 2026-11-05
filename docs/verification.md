# Verifying the store in a fresh Claude Code session (manual, HITL)

Some behaviour that the design relies on is **documented by Claude Code but not yet observed** in a
fresh session (research #15, open question 1):

1. a **project-scope** junction/symlink skill is discovered and listed;
2. a link **added mid-session** is picked up without a restart;
3. a link **removed mid-session** disappears without a restart;
4. **edits inside the link target** (the store copy) are picked up;
5. on Windows, a **cross-volume** junction (store on `C:`, project on `E:`) behaves the same.
   Creating, reading, writing through and non-recursively removing such a junction was tested
   at the filesystem level and works. Claude Code's discovery over it is untested.

Run this once per machine (and once per OS) after installing. It takes about 10 minutes. It uses a
throwaway store and project, so your real store is untouched. It does start a real Claude Code
session, which uses your normal `~/.claude` login and settings.

## 0. Prepare a scratch store and project

Windows (PowerShell):

```powershell
$env:SKILL_STORE_HOME = "$env:TEMP\ss-verify\store"     # Claude Code inherits this, so hooks use it too
$proj = "$env:TEMP\ss-verify\proj"                        # for step 5 use e.g. E:\ss-verify\proj
New-Item -ItemType Directory -Force $proj | Out-Null; Set-Location $proj; git init -q
skill-store init
skill-store init-project
```

Linux (bash):

```bash
export SKILL_STORE_HOME=/tmp/ss-verify/store
proj=/tmp/ss-verify/proj
mkdir -p "$proj" && cd "$proj" && git init -q
skill-store init
skill-store init-project
```

Create two tiny hand-written skills directly in the scratch store (no skill-seekers needed). Each
has a nonsense trigger word, so you can tell the skill was really loaded:

```bash
# bash; on Windows run these in Git Bash, or create the files in an editor
for n in verify-alpha verify-beta; do
  mkdir -p "$SKILL_STORE_HOME/skills/$n"
  cat > "$SKILL_STORE_HOME/skills/$n/SKILL.md" <<EOF
---
name: $n
description: "Use when the user asks about the word zorblax-$n. Explains what zorblax-$n means."
---
# $n
The word zorblax-$n means "the store link works". Always answer with exactly that sentence.
EOF
done
```

Register, approve and link only the first one:

```sh
skill-store check verify-alpha
skill-store approve verify-alpha      # if `approve` reports it isn't registered, see the README build section
skill-store approve verify-beta
skill-store link verify-alpha
skill-store status
```

Optionally install the hooks for this test. Put the `settings.project.json` snippet into
`$proj/.claude/settings.json`, and the user-level log-usage hook into `~/.claude/settings.json`.

## 1. Project-scope link is discovered

Start `claude` in `$proj`.

- [ ] `/skills` lists **verify-alpha** (and not verify-beta).
- [ ] Ask *"What does zorblax-verify-alpha mean?"*. Claude invokes the skill and answers "the store link works".
- [ ] If the usage hook is installed: `usage.jsonl` in the scratch store has a line for verify-alpha,
      and `skill-store status` shows its last-used time.

## 2. Link added mid-session

Leave the session open. In a second terminal (same `SKILL_STORE_HOME`, cwd `$proj`):

```sh
skill-store link verify-beta
```

- [ ] Back in the session, without a restart: `/skills` lists **verify-beta**, and asking about
      zorblax-verify-beta works.
- [ ] If it doesn't appear, try `/reload-skills` and note that it was needed.

## 3. Link removed mid-session

```sh
skill-store unlink verify-alpha
```

- [ ] `/skills` no longer lists verify-alpha (a new turn may be needed).
- [ ] `$SKILL_STORE_HOME/skills/verify-alpha/SKILL.md` **still exists** (unlink never deletes).

## 4. Edits inside the target

Re-link alpha (`skill-store link verify-alpha`), then edit the **store copy**
`$SKILL_STORE_HOME/skills/verify-alpha/SKILL.md`: change the answer sentence to "edit detected".

- [ ] In the same session, asking again yields "edit detected" (the watcher sees changes inside a link
      target) — or note that a restart / `/reload-skills` was needed.

## 5. Cross-volume (Windows only)

Repeat steps 0–1 with `$proj` on a different drive from `%USERPROFILE%` (e.g. `E:\ss-verify\proj`).

- [ ] verify-alpha is listed and works.

## 6. Fresh worktree / checkout

```sh
git -C "$proj" add .claude && git -C "$proj" commit -qm "manifest"
git -C "$proj" worktree add ../proj-wt
```

Start `claude` in `../proj-wt` (with the `SessionStart` hook installed).

- [ ] The links exist when the session starts and `/skills` lists them.
- [ ] `git -C ../proj-wt status` shows nothing under `.claude/skills/` (links are ignored).

## 7. Listing cost

- [ ] `/doctor` shows the skill-listing estimate, and `/skill-doctor` shows per-skill cost and use for
      the linked skills.

## Clean up

Unlink first, then delete the scratch dirs. **Never** delete the project with a recursive delete
while links exist (it would recurse into the store through them):

```sh
skill-store unlink --all --project ../proj-wt     # removes the worktree's store links; manifest unchanged
skill-store unlink --all --project "$proj"
git -C "$proj" worktree remove ../proj-wt
```

Then delete `ss-verify` normally. The store there is scratch, so losing it is harmless. That is
exactly why this test uses one.

## Report the results

Record ✅/❌ per step (with Claude Code version, OS, and same-volume vs cross-volume) and post them on
issue #15/#16 in `claude-blender-generations`, or in this repo. Update the README's "Known
unverified behaviour" section once confirmed.
