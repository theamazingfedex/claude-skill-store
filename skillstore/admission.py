"""Admission rules a skill must pass before it can be linked into a project.

- ``description``: present, quoted YAML, at most 500 chars, not the generic skill-seekers text;
- ``SKILL.md``: at most 4k tokens (measured as chars/4) and strictly valid YAML frontmatter;
- each reference file: at most 25k tokens (one Read call) and, when longer than 100 lines,
  a table of contents (a "Contents" / "Table of Contents" heading near the top).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from skillstore import frontmatter

MAX_DESCRIPTION_CHARS = 500
MAX_SKILL_MD_TOKENS = 4_000
MAX_REFERENCE_TOKENS = 25_000
TOC_REQUIRED_OVER_LINES = 100
TOC_SEARCH_LINES = 60
REFERENCE_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}
JUNK_FILES = {"quality_report.json"}

_TOC_RE = re.compile(r"^\s{0,3}#{1,6}\s*(table of contents|contents)\s*:?\s*$", re.I)
_GENERIC_RE = re.compile(r"^\s*use when working with\s+[\w.-]+\s*\.?\s*$", re.I)


def tokens(text: str) -> int:
    """Conservative token estimate used by every rule: characters / 4, rounded up."""
    return math.ceil(len(text) / 4)


def has_toc(text: str) -> bool:
    return any(_TOC_RE.match(line) for line in text.splitlines()[:TOC_SEARCH_LINES])


def is_generic_description(desc: str) -> bool:
    return bool(_GENERIC_RE.match(desc or ""))


@dataclass
class CheckResult:
    path: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "stats": self.stats,
        }


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def check_skill(skill_dir: Path, expected_name: str | None = None) -> CheckResult:
    skill_dir = Path(skill_dir)
    res = CheckResult(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        res.errors.append(f"{skill_md} not found")
        return res

    text = _read(skill_md)
    res.stats["skill_md_tokens"] = tokens(text)
    if tokens(text) > MAX_SKILL_MD_TOKENS:
        res.errors.append(
            f"SKILL.md is {tokens(text)} tokens (chars/4); the limit is {MAX_SKILL_MD_TOKENS}"
        )

    try:
        data, raw, _ = frontmatter.parse_strict(text)
    except frontmatter.FrontmatterError as exc:
        res.errors.append(f"SKILL.md: {exc}")
        data, raw = {}, ""

    if data:
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            res.errors.append("frontmatter: 'name' is missing")
        elif expected_name and name != expected_name:
            res.errors.append(
                f"frontmatter: name {name!r} does not match the skill directory {expected_name!r}"
            )
        desc = data.get("description")
        if not isinstance(desc, str) or not desc.strip():
            res.errors.append("frontmatter: 'description' is missing or not a string")
        else:
            res.stats["description_chars"] = len(desc)
            if len(desc) > MAX_DESCRIPTION_CHARS:
                res.errors.append(
                    f"description is {len(desc)} chars; the limit is {MAX_DESCRIPTION_CHARS}"
                )
            if not frontmatter.description_is_quoted(raw):
                res.errors.append("description must be a quoted YAML string (\"...\")")
            if is_generic_description(desc):
                res.errors.append(
                    "description is the generic skill-seekers text; write a real one "
                    "(use case first) so the skill can trigger"
                )

    for junk in sorted(JUNK_FILES):
        if (skill_dir / junk).exists():
            res.warnings.append(f"{junk} present (junk; the post-processor drops it)")

    refs_dir = skill_dir / "references"
    ref_stats = {}
    if refs_dir.is_dir():
        for ref in sorted(p for p in refs_dir.rglob("*") if p.is_file()):
            rel = ref.relative_to(skill_dir).as_posix()
            if ref.suffix.lower() not in REFERENCE_SUFFIXES:
                continue
            rtext = _read(ref)
            n_tok, n_lines = tokens(rtext), rtext.count("\n") + 1
            ref_stats[rel] = n_tok
            if n_tok > MAX_REFERENCE_TOKENS:
                res.errors.append(
                    f"{rel} is {n_tok} tokens; the limit is {MAX_REFERENCE_TOKENS} "
                    "(run 'skill-store postprocess' to split it)"
                )
            if n_lines > TOC_REQUIRED_OVER_LINES and not has_toc(rtext):
                res.errors.append(
                    f"{rel} has {n_lines} lines but no table of contents "
                    "(a 'Table of Contents' heading near the top)"
                )
    res.stats["references"] = ref_stats
    return res
