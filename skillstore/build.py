"""Build a skill into the store with skill-seekers 3.1.3, then post-process it.

Workarounds for skill-seekers 3.1.3 (see the research for issue #15):

- the config route ignores ``--output``: we control the cwd (``build/<name>/``) and read
  ``output/<name>/`` from there;
- ``create <config>`` with a unified config can silently produce no references: we always
  run ``scrape --config`` with a *hybrid* config (top-level doc keys and a ``sources`` array),
  and fail the build if no reference files came out;
- re-running with existing ``output/<name>_data`` blocks on a y/n prompt: we pass ``--fresh``
  (or ``--skip-scrape`` to rebuild from the cached pages) and stdin is /dev/null;
- generic/unquoted descriptions: the post-processor writes the recipe's quoted description;
- ``quality_report.json`` is dropped; the ``quality`` score is never used;
- ``install-agent`` copies into a hard-coded ``~/.claude/skills`` and is never called;
- versioned URLs become a single huge category unless ``categories`` is explicit: warned.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from skillstore import SKILL_SEEKERS_VERSION
from skillstore.links import is_link
from skillstore.paths import SkillStoreError, validate_name
from skillstore.postprocess import DEFAULT_TARGET_TOKENS, postprocess, promote
from skillstore.store import Store, now_iso, write_json

RECIPE_VERSION = 1
_LIFT_KEYS = ("base_url", "start_urls", "selectors", "url_patterns", "categories",
              "rate_limit", "max_pages", "skip_llms_txt")


def load_recipe(path: Path) -> dict:
    try:
        recipe = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SkillStoreError(f"cannot read recipe {path}: {exc}") from exc
    if not isinstance(recipe, dict) or recipe.get("recipe_version") != RECIPE_VERSION:
        raise SkillStoreError(f"{path}: not a skill-store recipe (want recipe_version 1)")
    return recipe


def hybrid_config(config: dict, name: str, description: str | None) -> tuple[dict, list[str]]:
    """Return a config with both top-level doc keys and a ``sources`` array, plus warnings."""
    cfg = copy.deepcopy(config)
    warnings = []
    cfg["name"] = name
    if description:
        cfg["description"] = description
    sources = cfg.get("sources")
    if not sources:
        if "base_url" not in cfg:
            raise SkillStoreError("skill_seekers.config needs 'base_url' or a 'sources' array")
        src = {k: cfg[k] for k in _LIFT_KEYS if k in cfg}
        src["type"] = "documentation"
        cfg["sources"] = [src]
    elif "base_url" not in cfg:
        docs = [s for s in sources if isinstance(s, dict) and s.get("type", "documentation") == "documentation"]
        if not docs:
            raise SkillStoreError("skill_seekers.config has no documentation source to lift")
        for k in _LIFT_KEYS:
            if k in docs[0]:
                cfg[k] = copy.deepcopy(docs[0][k])
    if not cfg.get("categories"):
        warnings.append(
            "no explicit 'categories': skill-seekers 3.1.3 may lump versioned URLs into one huge "
            "reference file (the post-processor will split it, but categories read better)"
        )
    if any(isinstance(s, dict) and s.get("type") == "github" for s in cfg["sources"]):
        warnings.append("github sources crash in skill-seekers 3.1.3 language detection on some repos")
    return cfg, warnings


def skill_seekers_command() -> list[str]:
    override = os.environ.get("SKILL_STORE_SKILL_SEEKERS")
    if override:
        return [sys.executable, override] if override.endswith(".py") else [override]
    exe = shutil.which("skill-seekers")
    if exe:
        return [exe]
    try:
        import skill_seekers  # noqa: F401
    except ImportError:
        raise SkillStoreError(
            f"skill-seekers is not installed: pip install skill-seekers=={SKILL_SEEKERS_VERSION}"
        ) from None
    return [sys.executable, "-c", "import sys; from skill_seekers.cli.main import main; sys.exit(main())"]


def check_version(cmd: list[str]) -> str:
    try:
        res = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=120,
                             stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        raise SkillStoreError(f"cannot run skill-seekers: {exc}") from exc
    out = (res.stdout + res.stderr).strip()
    return out


def _safe_build_subdir(build_dir: Path, sub: Path) -> None:
    """Delete stale skill-seekers output inside build/<name>/ (never anything else)."""
    if not os.path.lexists(sub):
        return
    if is_link(sub) or is_link(build_dir):
        raise SkillStoreError(f"refusing to clear {sub}: it is a link")
    real_sub, real_build = Path(os.path.realpath(sub)), Path(os.path.realpath(build_dir))
    if real_sub == real_build or not real_sub.is_relative_to(real_build):
        raise SkillStoreError(f"refusing to clear {sub}: not inside {build_dir}")
    shutil.rmtree(sub)


def run_skill_seekers(build_dir: Path, name: str, skip_scrape: bool, allow_any_version: bool,
                      log=print) -> Path:
    cmd = skill_seekers_command()
    version_out = check_version(cmd)
    if SKILL_SEEKERS_VERSION not in version_out:
        msg = (f"skill-seekers {SKILL_SEEKERS_VERSION} is required (found: {version_out or 'unknown'}); "
               f"pip install skill-seekers=={SKILL_SEEKERS_VERSION}")
        if not allow_any_version:
            raise SkillStoreError(msg + " (or pass --any-skill-seekers-version)")
        log("warning: " + msg)

    out_skill = build_dir / "output" / name
    data_dir = build_dir / "output" / f"{name}_data"
    args = ["scrape", "--config", "config.json", "--enhance-level", "0"]
    if skip_scrape:
        if not data_dir.is_dir():
            raise SkillStoreError(f"--skip-scrape: no cached pages at {data_dir}; build without it first")
        args.append("--skip-scrape")
    elif data_dir.exists():
        args.append("--fresh")
    _safe_build_subdir(build_dir, out_skill)  # stale references would otherwise linger

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # enhance-level 0: never spend API credit
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log(f"running: {' '.join(cmd[:1] + args)} (cwd {build_dir})")
    log_path = build_dir / "build.log"
    with open(log_path, "w", encoding="utf-8") as logf:
        proc = subprocess.Popen(cmd + args, cwd=str(build_dir), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                text=True, encoding="utf-8", errors="replace")
        assert proc.stdout is not None
        for line in proc.stdout:
            logf.write(line)
            log("  | " + line.rstrip("\n"))
        rc = proc.wait()
    if rc != 0:
        raise SkillStoreError(f"skill-seekers failed with exit code {rc}; see {log_path}")
    if not (out_skill / "SKILL.md").is_file():
        raise SkillStoreError(f"skill-seekers reported success but wrote no {out_skill / 'SKILL.md'}")
    refs = [p for p in (out_skill / "references").glob("*.md") if p.name != "index.md"] \
        if (out_skill / "references").is_dir() else []
    if not refs:
        raise SkillStoreError(
            "skill-seekers produced no reference files (a known 3.1.3 failure: check selectors "
            f"such as main_content, and url_patterns); see {log_path}"
        )
    return out_skill


def process_and_register(store: Store, name: str, src: Path, recipe: dict | None,
                         run_claude_validate: bool = True, log=print) -> dict:
    """Post-process ``src`` into skills/<name>/ and record it in store.json."""
    validate_name(name)
    recipe = recipe or {}
    bdir = store.paths.build_dir(name)
    bdir.mkdir(parents=True, exist_ok=True)
    staging = bdir / "staging"
    _safe_build_subdir(bdir, staging)
    try:
        report = postprocess(
            src, staging, name,
            description=recipe.get("description"),
            extra_frontmatter=recipe.get("frontmatter"),
            target_tokens=recipe.get("max_reference_tokens", DEFAULT_TARGET_TOKENS),
            run_claude_validate=run_claude_validate,
        )
    except Exception:
        if staging.exists() and not is_link(staging):
            shutil.rmtree(staging)
        raise
    promote(staging, store.paths.skill(name), bdir / "previous")

    data = store.load()
    is_new = name not in data["skills"]
    entry = store.entry(data, name, create=True)
    cfg = (recipe.get("skill_seekers") or {}).get("config") or {}
    entry["source"] = recipe.get("source") or cfg.get("base_url") or entry.get("source") or "manual"
    entry["version"] = recipe.get("version", entry.get("version"))
    entry["pinned_url"] = recipe.get("pinned_url", entry.get("pinned_url"))
    entry["built_at"] = now_iso()
    if is_new:
        entry["approved"] = False
        entry["approved_at"] = None
    store.save(data)
    report["registered"] = "new (not approved)" if is_new else (
        "updated (approved)" if entry["approved"] else "updated (not approved)")
    return report


def build(store: Store, name: str, recipe_path: Path, skip_scrape: bool = False,
          allow_any_version: bool = False, run_claude_validate: bool = True, log=print) -> dict:
    validate_name(name)
    store.require()
    recipe = load_recipe(recipe_path)
    if recipe.get("name", name) != name:
        raise SkillStoreError(f"recipe is for {recipe['name']!r}, not {name!r}")
    ss = recipe.get("skill_seekers") or {}
    if not isinstance(ss.get("config"), dict):
        raise SkillStoreError("recipe needs a 'skill_seekers.config' object (a skill-seekers config)")
    cfg, warnings = hybrid_config(ss["config"], name, recipe.get("description"))
    for w in warnings:
        log("warning: " + w)

    bdir = store.paths.build_dir(name)
    if is_link(bdir):
        raise SkillStoreError(f"refusing to build into {bdir}: it is a link")
    bdir.mkdir(parents=True, exist_ok=True)
    write_json(bdir / "recipe.json", recipe)
    write_json(bdir / "config.json", cfg)
    out_skill = run_skill_seekers(bdir, name, skip_scrape, allow_any_version, log=log)
    report = process_and_register(store, name, out_skill, recipe, run_claude_validate, log=log)
    report["warnings"] = warnings
    return report
