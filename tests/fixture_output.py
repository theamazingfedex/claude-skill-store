"""Synthetic skill-seekers 3.1.3-shaped output (no third-party text, no network).

Reproduces the shape of real 3.1.3 output: an *unquoted* description containing ``: ``,
a generic body, ``quality_report.json``, ``.skill_version.json``, ``assets/``/``scripts/``,
per-page ``## Title`` / ``**URL:**`` / ``**Contents:**`` blocks with code fences, one oversized
category file, and one huge single-heading file (like the glTF spec scrape).
"""

from __future__ import annotations

import json
from pathlib import Path


def _page(i: int, paras: int = 30) -> str:
    out = [f"## Page {i}: Importing things\n",
           f"**URL:** https://docs.example.invalid/en/1.0/page_{i}.html\n",
           "**Contents:**\n- Overview\n- Options\n"]
    for j in range(paras):
        out.append(f"Paragraph {j} of page {i}. " + "Lorem ipsum dolor sit amet, consectetur "
                   "adipiscing elit, sed do eiusmod tempor incididunt ut labore. " * 3 + "\n")
        if j % 7 == 3:
            out.append("```gdscript\n" + "".join(f"var x{k} = {k}\n\n" for k in range(12)) + "```\n")
        if j % 10 == 5:
            out.append(f"### Options {i}.{j}\n")
    return "\n".join(out)


def write(dest: Path, name: str, description: str | None = None,
          big_pages: int = 60, blob_paras: int = 1600) -> Path:
    """Write ``dest/output/<name>/`` like skill-seekers would. Returns the skill dir."""
    skill = Path(dest) / "output" / name
    refs = skill / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (skill / "assets").mkdir(exist_ok=True)
    (skill / "scripts").mkdir(exist_ok=True)
    desc = description or f"Use when working with {name}"
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n"
        f"# {name.title()} Skill\n\n## When to Use This Skill\n\n- Working with {name}\n\n"
        "## Reference Files\n\nThis skill includes documentation in `references/`:\n\n"
        "- `references/importing.md`\n- `references/spec.md`\n- `references/small.md`\n",
        encoding="utf-8")
    (skill / "quality_report.json").write_text(json.dumps({"total": 72.75, "grade": "B-"}))
    (skill / ".skill_version.json").write_text(json.dumps({"version": 1}))
    (refs / "index.md").write_text(f"# {name} Documentation Index\n\n## Categories\n\n### Importing\n"
                                   "**File:** `importing.md`\n", encoding="utf-8")
    (refs / "importing.md").write_text(
        f"# {name.title()} - Importing\n\n**Pages:** {big_pages}\n\n---\n\n"
        + "\n---\n\n".join(_page(i) for i in range(big_pages)), encoding="utf-8")
    blob = ["# Spec - Other\n", "## The Specification\n"]
    blob += [f"Section text {k}. " + "Accessors describe typed views into buffer views. " * 4 + "\n"
             for k in range(blob_paras)]
    (refs / "spec.md").write_text("\n".join(blob), encoding="utf-8")
    (refs / "small.md").write_text("# Small\n\n" + "".join(f"line {k}\n" for k in range(150)),
                                   encoding="utf-8")
    data = Path(dest) / "output" / f"{name}_data"
    (data / "pages").mkdir(parents=True, exist_ok=True)
    (data / "pages" / "page_0.json").write_text("{}")
    return skill
