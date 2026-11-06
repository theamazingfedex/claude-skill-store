# Building a skill into the store

`skill-store build` wraps [skill-seekers](https://github.com/yusufkaraaslan/Skill_Seekers),
**pinned to 3.1.3**, and post-processes its output into something that passes the admission rules.
This page explains the recipe and the skill-seekers pitfalls it works around, so you can debug a
build or write a recipe.

> The CLI (`skillstore/build.py`, `skillstore/postprocess.py`) and
> [`schemas/recipe.schema.json`](../schemas/recipe.schema.json) are authoritative. If this page
> disagrees with them or with `skill-store build --help`, they win.

## Install

```sh
pip install -e ".[build]"      # from your clone: adds skill-seekers==3.1.3
```

`build` checks `skill-seekers --version` and refuses anything but 3.1.3 unless you pass
`--any-skill-seekers-version`. An upgrade has already changed config formats once.

## Recipe

A recipe is a JSON file ([schema](../schemas/recipe.schema.json); examples in
[`examples/recipes/`](../examples/recipes/)):

```json
{
  "recipe_version": 1,
  "name": "godot47-asset-pipeline",
  "description": "Use when exporting Blender assets for Godot 4.7 or debugging Godot glTF/.glb import: import hints and name suffixes (-col, -noimp), ...",
  "source": "https://docs.godotengine.org/en/4.7/tutorials/assets_pipeline/",
  "version": "4.7",
  "pinned_url": "https://docs.godotengine.org/en/4.7/tutorials/assets_pipeline/index.html",
  "max_reference_tokens": 20000,
  "skill_seekers": {
    "version": "3.1.3",
    "config": { "base_url": "...", "start_urls": ["..."], "selectors": {}, "url_patterns": {},
                "categories": { "importing_3d": ["importing_3d_scenes", "..."] },
                "rate_limit": 0.5, "max_pages": 40, "skip_llms_txt": true }
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| `recipe_version` | yes | `1` |
| `name` | yes | Skill name: lowercase letters, digits and single hyphens, ≤ 64 chars |
| `description` | yes | The **hand-written** description: use case first, third person, ≤ 500 chars. Written to `SKILL.md` as a quoted YAML string |
| `skill_seekers.config` | yes | A skill-seekers scrape config. skill-store turns it into a *hybrid* config (top-level keys plus `sources`) and forces `name` |
| `frontmatter` | no | Extra `SKILL.md` frontmatter keys, e.g. `when_to_use` |
| `source`, `version`, `pinned_url` | no | Provenance, copied into `store.json` |
| `max_reference_tokens` | no | Split target for reference files (chars / 4), default **20000**, max 25000 (the admission limit) |

Always set explicit `categories` in the config, or skill-seekers writes one giant reference file;
`build` warns when they're missing.

## Pipeline

```
recipe ──► skill-seekers 3.1.3: scrape --config config.json --enhance-level 0
             cwd = <store>/build/<name>/   (stdin closed, ANTHROPIC_API_KEY unset: no AI spend)
             └─► build/<name>/output/<name>/          raw SKILL.md + references
                 build/<name>/output/<name>_data/     raw scraped pages (reused by --skip-scrape)
        ──► post-processor
             ├─ copy, dropping junk (quality_report.json …)
             ├─ SKILL.md frontmatter: name + the recipe's quoted description (+ extra frontmatter)
             ├─ references: split files over max_reference_tokens at headings; add a
             │   "## Table of Contents" to each; an oversized file becomes parts + an index
             └─► validate, then swap into <store>/skills/<name>/ (previous version → build/<name>/previous/;
                 if the swap fails, the previous version is put back live and the new build is kept
                 in build/<name>/staging/; previous/ is then gone)
        ──► validation
             ├─ strict YAML parse of the frontmatter (PyYAML)              ┐ the real gate
             ├─ admission rules (= skill-store check)                      ┘
             └─ claude plugin validate on a throwaway plugin wrapper, only if `claude` is on PATH
                 (or $SKILL_STORE_CLAUDE is set); --no-claude-validate skips it
        ──► store.json: register/update (new skills: approved = false)
```

`build/<name>/` also keeps `recipe.json` (a copy of the recipe used), `config.json` (the hybrid
config passed to skill-seekers) and `build.log`.

```sh
skill-store build <name> --recipe path/to/recipe.json
skill-store build <name> --recipe path/to/recipe.json --skip-scrape   # rebuild from cached raw pages
skill-store postprocess <name>                       # re-run only the post-processor, from
                                                     # build/<name>/output/<name> + build/<name>/recipe.json
skill-store postprocess <name> --from DIR --recipe FILE   # post-process any skill-seekers output dir
skill-store check <name>                             # admission rules; exit 1 on failure
```

The swap into `skills/<name>` happens in place, so links in projects keep working after a rebuild.

## Admission rules (what `check` enforces)

| Rule | Limit | Why |
|---|---|---|
| `description` in frontmatter | ≤ 500 chars, use case first, **quoted** YAML string; the generic skill-seekers "Use when working with X" is rejected | It's in the listing every turn. Unquoted `: ` breaks strict YAML parsers |
| `SKILL.md` | ≤ 4,000 tokens (chars / 4) | It stays in context for the whole session once invoked |
| each `references/*` file | ≤ 25,000 tokens | One Read returns at most 25k tokens |
| table of contents | required for reference files **over 100 lines**: a Markdown heading (any level) reading exactly `Contents` or `Table of Contents` in the first 60 lines | Lets Claude jump to sections. skill-seekers' per-page `**Contents:**` lists don't count |

Write the description by hand. Lead with *when to use it*, then what it covers.

## Why `claude plugin validate` is not the gate

`claude plugin validate` rejects a bare skill directory ("No manifest found"), and it passes
genuinely broken frontmatter both in a project dir and inside a plugin. So the post-processor runs
it on a throwaway wrapper (`<tmp>/.claude-plugin/plugin.json` + `skills/<name>/`) as an extra
signal only. The strict PyYAML parse plus the admission rules decide.

## skill-seekers 3.1.3 pitfalls and their workarounds

These come from hands-on builds (research #15).

| # | Pitfall | What `build` does about it |
|---|---|---|
| 1 | Generated descriptions are generic ("Use when working with …") and won't trigger | The description comes from the recipe and is required |
| 2 | Custom `-d` descriptions are emitted **unquoted**, so any `: ` produces invalid YAML. Claude Code tolerates it; PyYAML and the vercel `skills` CLI don't | The post-processor rewrites the frontmatter with a quoted description; strict YAML parse in validation |
| 3 | `-p quick` and `-p standard` produce **byte-identical** output for web docs (presets only reach the local-codebase route) | Presets aren't used; limits go in the config (`max_pages`, `rate_limit`) |
| 4 | `--output` is **ignored** on the config route: output always goes to `./output/<name>` | Runs skill-seekers with cwd = `<store>/build/<name>/` |
| 5 | The unified-config docs route can **silently produce no references** (it writes a legacy temp config, rejects it, and still prints "Scraped … successfully") | Always `scrape --config` with a **hybrid** config; **fails the build** if no reference files came out |
| 6 | `create <config.json>` with a legacy single-source config errors (legacy format removed in 2.11) | Hybrid config, as above |
| 7 | `create <url> --max-pages N` is rejected by the top-level parser | Uses `scrape` with `max_pages` in the config |
| 8 | Re-running where `output/<name>_data` exists blocks on `Use existing data? (y/n)` and dies with EOF when non-interactive | Passes `--fresh` when cached pages exist (or `--skip-scrape` on request), closes stdin, clears stale `output/<name>/` first |
| 9 | The GitHub route crashes in language detection (`int + str`), e.g. on `KhronosGroup/glTF-Validator` | Warns when a GitHub source is used. Build such skills from docs or hand-write the reference |
| 10 | `skill-seekers estimate <config>` fails ("unrecognized arguments: --config") | Not used. By hand: `python -I -m skill_seekers.cli.estimate_pages <config>` |
| 11 | Versioned doc URLs get a default category named after the path segment (e.g. `4.7`), giving one ~99 KB reference | Warns when `categories` is missing; the post-processor splits oversized files |
| 12 | Asciidoctor single-page specs (glTF 2.0) lose their section headings, giving one unnavigable 31–37k-token file | The post-processor splits it into parts plus an index (glTF: 18.0k / 18.0k / 1.0k tokens) |
| 13 | `quality` writes `quality_report.json` into the skill dir, and its score is a **filename heuristic** (every build scored 72.75 B-) | Never runs `quality`; the post-processor drops the file if present |
| 14 | `update --check-changes` only hashes local files against `.skill_version.json` and never contacts the source | Not used. A rebuild re-scrapes (or reuses cached pages with `--skip-scrape`) |
| 15 | `install-agent --agent claude` **copies** into a hard-coded `~/.claude/skills/` (and `--force` does `rmtree` first) | **Never used.** The post-processor writes into `<store>/skills/<name>/` itself |
| 16 | `split_config` only splits unified configs by source. `generate_router` writes a prose router that can't activate unlinked sub-skills | Not used. References are split instead of skills |

Not measured: whether `--enhance-level 1`/`2` (LOCAL mode, which spends AI tokens) produces usable
descriptions and bodies. The recipe uses level 0 and takes the description from the recipe.

## Hand-written skills

You don't need skill-seekers for a small skill. Create `<store>/skills/<name>/SKILL.md` (plus
optional `references/`), run `skill-store check <name>`, then `skill-store approve <name>`. That
registers it in `store.json` with `source: "manual"` and approves it. Then `skill-store link <name>`
in a project.
