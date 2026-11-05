# Building a skill into the store

`skill-store build` wraps [skill-seekers](https://github.com/yusufkaraaslan/Skill_Seekers),
**pinned to 3.1.3**, and post-processes its output into something that passes the admission rules.
This page explains the recipe and the skill-seekers pitfalls it works around, so you can debug a
build or write a recipe by hand.

> The recipe file format and the exact build flags are defined by the CLI (`skillstore/`) and the
> example recipe in [`examples/`](../examples/). If this page disagrees with `skill-store build --help`,
> the CLI wins.

## Pipeline

```
recipe ──► skill-seekers 3.1.3 scrape            (cwd = <store>/build/<name>/, --enhance-level 0)
             └─► <store>/build/<name>/output/<name>/   raw SKILL.md + references + scraped data
        ──► post-processor
             ├─ description: write/validate a real, quoted, use-case-first description (≤ 500 chars)
             ├─ references: split files over 25k tokens at headings, add a TOC to each
             ├─ drop junk (quality_report.json, *_data dirs, .skill_version.json …)
             └─► <store>/skills/<name>/
        ──► validation
             ├─ strict YAML parse of the frontmatter (PyYAML)
             ├─ `claude plugin validate <dir>` if the claude CLI is available
             └─ skill-store check <name>   (admission rules)
        ──► store.json: register/update (new skills: approved = false)
```

```sh
pip install skill-seekers==3.1.3                    # optional dependency, only needed for build
skill-store build <name> --recipe path/to/recipe.json
skill-store build <name> --recipe path/to/recipe.json --skip-scrape   # rebuild from cached raw pages
skill-store postprocess <name>                      # re-run only the post-processor
skill-store check <name>                            # admission rules; non-zero exit on failure
```

A rebuild with `--skip-scrape` reuses the pages already in `build/<name>/`, so no re-download is needed.

## Admission rules (what `check` enforces)

| Rule | Limit | Why |
|---|---|---|
| `description` in frontmatter | ≤ 500 chars, use case first, **quoted** YAML string | It's in the listing every turn. Unquoted `: ` breaks strict YAML parsers |
| `SKILL.md` | ≤ 4,000 tokens (chars / 4) | It stays in context for the whole session once invoked |
| each `references/*` file | ≤ 25,000 tokens, with a table of contents | One Read returns at most 25k tokens. A TOC lets Claude jump to sections |

Write the description by hand. Lead with *when to use it*, then what it covers, for example:

```yaml
---
name: godot-asset-pipeline
description: "Use when importing glTF/GLB into Godot 4.7: import hints (-col, -noimp), retargeting with SkeletonProfileHumanoid, MeshLibrary export, import settings."
---
```

## skill-seekers 3.1.3 pitfalls and their workarounds

These come from hands-on builds (research #15). Pin the version: an upgrade has already changed
config formats once.

| # | Pitfall | Workaround used by the recipe |
|---|---|---|
| 1 | Generated descriptions are generic ("Use when working with …") and won't trigger | Post-processor writes or validates a real description. Every store skill needs a hand-written one |
| 2 | Custom `-d` descriptions are emitted **unquoted**, so any `: ` produces invalid YAML. Claude Code tolerates it; PyYAML and the vercel `skills` CLI don't | Post-processor re-emits the description as a quoted string; strict YAML parse in validation |
| 3 | `-p quick` and `-p standard` produce **byte-identical** output for web docs (presets only reach the local-codebase route) | Don't bother with presets for docs; set limits in the config |
| 4 | `--output` is **ignored** on the config route: output always goes to `./output/<name>` | Run skill-seekers with cwd = `<store>/build/<name>/` |
| 5 | The unified-config docs route can **silently produce no references** (it writes a legacy temp config, rejects it, and still prints "Scraped … successfully") | Use `scrape --config` with a **hybrid** config (top-level keys *and* `sources`). Fail the build if `references/` is empty |
| 6 | `create <config.json>` with a legacy single-source config errors (legacy format removed in 2.11) | Hybrid config, as above |
| 7 | `create <url> --max-pages N` is rejected by the top-level parser | Use `scrape --max-pages` or `max_pages` in the config |
| 8 | Re-running where `output/<name>_data` exists blocks on `Use existing data? (y/n)` and dies with EOF when non-interactive | Use `--skip-scrape` (or `--fresh`) explicitly, never rely on the prompt |
| 9 | The GitHub route crashes in language detection (`int + str`), e.g. on `KhronosGroup/glTF-Validator` | Build such skills from docs or hand-write the reference |
| 10 | `skill-seekers estimate <config>` fails ("unrecognized arguments: --config") | `python -I -m skill_seekers.cli.estimate_pages <config>` |
| 11 | Versioned doc URLs get a default category named after the path segment (e.g. `4.7`), giving one ~99 KB reference | Set explicit `categories` in the config |
| 12 | Asciidoctor single-page specs (glTF 2.0) lose their section headings, giving one unnavigable 31–37k-token file | Split by section in the post-processor or hand-curate a subset |
| 13 | `quality` writes `quality_report.json` into the skill dir, and its score is a **filename heuristic** (every build scored 72.75 B-) | Don't run or use `quality`; the post-processor deletes the file if present |
| 14 | `update --check-changes` only hashes local files against `.skill_version.json` and never contacts the source | Re-scrape first, then compare |
| 15 | `install-agent --agent claude` **copies** into a hard-coded `~/.claude/skills/` (and `--force` does `rmtree` first) | **Never use it** for the store. The build copies `output/<name>/` into `<store>/skills/<name>/` itself |
| 16 | `split_config` only splits unified configs by source. `generate_router` writes a prose router that can't activate unlinked sub-skills | Not used. Split references instead of skills |

Not measured: whether `--enhance-level 1`/`2` (LOCAL mode, which spends AI tokens) produces usable
descriptions and bodies. The recipe uses level 0 and writes the description itself.

## Validating by hand

```sh
python -c "import sys,yaml; t=open(sys.argv[1],encoding='utf-8').read().split('---')[1]; print(yaml.safe_load(t))" ~/.skill-store/skills/<name>/SKILL.md
claude plugin validate ~/.skill-store/skills/<name>      # if your claude CLI has it
skill-store check <name>
```

Then `skill-store approve <name>` (your explicit decision) and `skill-store link <name>` in a project.
