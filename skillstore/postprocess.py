"""Post-processor for skill-seekers 3.1.3 output (also usable on hand-written skills).

Steps, applied to a *copy* of the source directory (a staging dir), never in place:

1. copy, dropping junk (``quality_report.json``);
2. rewrite the SKILL.md frontmatter: ``name`` = the store name, a real ``description`` from the
   recipe (skill-seekers' generic one is refused), every string scalar quoted;
3. split each reference file larger than the target (default 20k tokens, hard limit 25k) at
   headings / paragraph boundaries into ``<stem>-partN.md`` files, each with a table of
   contents; the original ``<stem>.md`` becomes a small index that lists the parts, so links
   from SKILL.md keep working. Long unsplit references get a table of contents inserted;
4. validate: strict YAML parse plus every admission rule (``skillstore.admission``), and
   ``claude plugin validate`` through a throwaway plugin wrapper when ``claude`` is on PATH.

skill-seekers' ``quality`` score is deliberately not used: it is a filename heuristic.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from skillstore import admission, frontmatter
from skillstore.links import is_link
from skillstore.paths import SkillStoreError

DEFAULT_TARGET_TOKENS = 20_000
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


class PostprocessError(SkillStoreError):
    def __init__(self, message: str, problems: list[str] | None = None):
        self.problems = problems or []
        if self.problems:
            message = message + "\n" + "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(message)


# --------------------------------------------------------------------------------------
# Markdown helpers
# --------------------------------------------------------------------------------------
def _headings(text: str, max_level: int = 3) -> list[tuple[int, str]]:
    out, fence = [], None
    for line in text.splitlines():
        fm = _FENCE_RE.match(line)
        if fm:
            marker = fm.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
            continue
        if fence:
            continue
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) <= max_level:
            out.append((len(m.group(1)), m.group(2)))
    return out


def _toc(text: str, max_chars: int) -> str:
    heads = [(lvl, t) for lvl, t in _headings(text, 3) if lvl >= 2]
    if not heads:
        return "## Table of Contents\n\n- (this file has no section headings)\n"
    lines = [("  " if lvl == 3 else "") + f"- {t}" for lvl, t in heads]
    if sum(len(x) + 1 for x in lines) > max_chars:
        lines = [f"- {t}" for lvl, t in heads if lvl == 2] or lines
    if sum(len(x) + 1 for x in lines) > max_chars:
        kept, size = [], 0
        for x in lines:
            if size + len(x) + 1 > max_chars:
                break
            kept.append(x)
            size += len(x) + 1
        kept.append(f"- ... and {len(lines) - len(kept)} more sections")
        lines = kept
    return "## Table of Contents\n\n" + "\n".join(lines) + "\n"


def _split_title(text: str) -> tuple[str | None, str]:
    """Pull a leading ``# Title`` line (within the first 5 lines) off the text."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines[:5]):
        if line.strip() == "":
            continue
        m = _HEADING_RE.match(line.rstrip("\r\n"))
        if m and len(m.group(1)) == 1:
            return m.group(2), "".join(lines[:i] + lines[i + 1:])
        break
    return None, text


def _units(body: str) -> list[tuple[int | None, str]]:
    """Split markdown into units (paragraphs, headings, whole code blocks), fence-aware.
    Returns (heading_level_or_None, text) pairs; concatenating the texts gives ``body``."""
    units: list[tuple[int | None, str]] = []
    cur: list[str] = []
    fence = None

    def flush():
        nonlocal cur
        if cur:
            units.append((None, "".join(cur)))
        cur = []

    for line in body.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        fm = _FENCE_RE.match(stripped)
        if fence is None:
            m = _HEADING_RE.match(stripped)
            if m:
                flush()
                units.append((len(m.group(1)), line))
                continue
            if stripped.strip() == "":
                cur.append(line)
                flush()
                continue
            if fm:
                fence = fm.group(1)[0]
        elif fm and fm.group(1)[0] == fence:
            fence = None
        cur.append(line)
    flush()
    return units


def _split_oversize(text: str, budget: int) -> list[str]:
    """Split one unit that is larger than the budget by lines (re-opening code fences)."""
    first = text.splitlines()[0] if text else ""
    fm = _FENCE_RE.match(first)
    opener = first.strip() if fm else None
    chunks, cur, size = [], [], 0
    for line in text.splitlines(keepends=True):
        while len(line) > budget:  # a single giant line: hard split
            if cur:
                chunks.append("".join(cur))
                cur, size = [], 0
            chunks.append(line[:budget])
            line = line[budget:]
        if size + len(line) > budget and cur:
            chunks.append("".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line)
    if cur:
        chunks.append("".join(cur))
    if opener and len(chunks) > 1:
        fence_char = opener[0] * 3
        out = []
        for i, c in enumerate(chunks):
            if i > 0:
                c = opener + "\n" + c
            if i < len(chunks) - 1:
                c = c.rstrip("\n") + "\n" + fence_char + "\n"
            out.append(c)
        chunks = out
    return chunks


def _pack(body: str, budget: int) -> list[str]:
    units = _units(body)
    # Group into sections that start at level-1/2 headings.
    sections: list[list[str]] = []
    for level, text in units:
        if level is not None and level <= 2 or not sections:
            sections.append([text])
        else:
            sections[-1].append(text)

    parts: list[str] = []
    cur: list[str] = []
    size = 0

    def flush():
        nonlocal cur, size
        if cur and "".join(cur).strip():
            parts.append("".join(cur))
        cur, size = [], 0

    for sec in sections:
        sec_len = sum(len(u) for u in sec)
        if size + sec_len <= budget:
            cur.extend(sec)
            size += sec_len
            continue
        if sec_len <= budget:
            flush()
            cur, size = list(sec), sec_len
            continue
        # Oversized section: keep filling the current part with its units.
        for u in sec:
            pieces = _split_oversize(u, budget) if len(u) > budget else [u]
            for p in pieces:
                if size + len(p) > budget and cur:
                    flush()
                cur.append(p)
                size += len(p)
    flush()
    return parts


def split_reference(path: Path, target_tokens: int = DEFAULT_TARGET_TOKENS) -> list[Path]:
    """Split ``path`` in place if it exceeds ``target_tokens``; add a TOC if it needs one.
    Returns the files that now hold its content (the index is not included when split)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    n_lines = text.count("\n") + 1
    if admission.tokens(text) <= target_tokens:
        if n_lines > admission.TOC_REQUIRED_OVER_LINES and not admission.has_toc(text):
            title, body = _split_title(text)
            toc = _toc(body, 8_000)
            head = f"# {title}\n\n" if title else ""
            path.write_text(head + toc + "\n" + body.lstrip("\r\n"), encoding="utf-8", newline="\n")
        return [path]

    title, body = _split_title(text)
    title = title or path.stem.replace("_", " ").replace("-", " ").title()
    hard_chars = admission.MAX_REFERENCE_TOKENS * 4
    budget = target_tokens * 4
    for _ in range(20):
        toc_allowance = max(2_000, budget // 10)
        bodies = _pack(body, budget - toc_allowance)
        n = len(bodies)
        rendered = []
        for i, b in enumerate(bodies, 1):
            rendered.append(
                f"# {title} (part {i} of {n})\n\n{_toc(b, toc_allowance - 200)}\n{b.lstrip(chr(10))}"
            )
        if all(len(r) <= hard_chars for r in rendered):
            break
        budget = int(budget * 0.85)
    else:  # pragma: no cover - defensive
        raise PostprocessError(f"could not split {path.name} under the reference limit")

    stem = path.stem
    out_paths = []
    index_lines = [
        f"# {title}",
        "",
        f"This reference was split by skill-store into {len(rendered)} parts so that each fits "
        "in one Read call. Read only the part that covers your question.",
        "",
        "## Table of Contents",
        "",
    ]
    for i, (r, b) in enumerate(zip(rendered, bodies), 1):
        part = path.with_name(f"{stem}-part{i}{path.suffix}")
        part.write_text(r, encoding="utf-8", newline="\n")
        out_paths.append(part)
        heads = [t for lvl, t in _headings(b, 2) if lvl == 2]
        shown = "; ".join(heads[:12]) + (f"; and {len(heads) - 12} more" if len(heads) > 12 else "")
        index_lines.append(f"- [{part.name}]({part.name})" + (f": {shown}" if shown else ""))
    path.write_text("\n".join(index_lines) + "\n", encoding="utf-8", newline="\n")
    return out_paths


# --------------------------------------------------------------------------------------
# Whole-skill processing
# --------------------------------------------------------------------------------------
def _copy_skill(src: Path, dest: Path) -> list[str]:
    dropped: list[str] = []

    def ignore(directory, names):
        skip = [n for n in names if n in admission.JUNK_FILES]
        dropped.extend(os.path.join(directory, n) for n in skip)
        return skip

    shutil.copytree(src, dest, ignore=ignore, symlinks=False)
    return dropped


def rewrite_frontmatter(skill_md: Path, name: str, description: str | None,
                        extra: dict | None = None) -> dict:
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    try:
        data, body = frontmatter.parse_lenient(text)
    except frontmatter.FrontmatterError:
        data, body = {}, text
    existing = data.get("description")
    if description:
        desc = description.strip()
    elif isinstance(existing, str) and existing.strip() and not admission.is_generic_description(existing):
        desc = existing.strip()
    else:
        raise PostprocessError(
            "no usable description: skill-seekers wrote a generic one; set 'description' in "
            "the recipe (use case first, at most 500 chars)"
        )
    data["name"] = name
    data["description"] = " ".join(desc.split())
    for k, v in (extra or {}).items():
        if k in ("name", "description"):
            continue
        data[k] = v
    skill_md.write_text(frontmatter.render(data, body), encoding="utf-8", newline="\n")
    return data


def find_claude() -> str | None:
    return os.environ.get("SKILL_STORE_CLAUDE") or shutil.which("claude")


def claude_validate(skill_dir: Path, name: str, claude: str | None = None) -> tuple[bool, bool, str]:
    """Run ``claude plugin validate`` on a throwaway plugin wrapping the skill.

    ``claude plugin validate`` rejects a bare skill directory ("No manifest found"), so the
    skill is copied into ``<tmp>/skills/<name>`` next to a minimal ``.claude-plugin/plugin.json``.
    Returns (ran, ok, output). Not running is not a failure.
    """
    claude = claude or find_claude()
    if not claude:
        return False, True, "claude not found on PATH; skipped 'claude plugin validate'"
    with tempfile.TemporaryDirectory(prefix="skill-store-validate-") as tmp:
        wrap = Path(tmp)
        (wrap / ".claude-plugin").mkdir()
        (wrap / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({
                "name": "skill-store-validate",
                "version": "0.0.0",
                "description": "Temporary wrapper used by skill-store to validate one skill.",
                "author": {"name": "skill-store"},
            }),
            encoding="utf-8",
        )
        shutil.copytree(skill_dir, wrap / "skills" / name)
        try:
            res = subprocess.run(
                [claude, "plugin", "validate", str(wrap)],
                capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
                encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return True, False, f"claude plugin validate could not run: {exc}"
    return True, res.returncode == 0, (res.stdout + res.stderr).strip()


def postprocess(src: Path, staging: Path, name: str, description: str | None = None,
                extra_frontmatter: dict | None = None,
                target_tokens: int = DEFAULT_TARGET_TOKENS,
                run_claude_validate: bool = True) -> dict:
    """Produce a validated skill in ``staging`` (which must not exist) from ``src``."""
    src, staging = Path(src), Path(staging)
    if not (src / "SKILL.md").is_file():
        raise PostprocessError(f"no SKILL.md in {src}")
    if os.path.lexists(staging):
        raise PostprocessError(f"staging dir already exists: {staging}")
    target_tokens = min(int(target_tokens), admission.MAX_REFERENCE_TOKENS)

    report: dict = {"dropped": [], "split": {}, "toc_added": [], "claude_validate": None}
    report["dropped"] = [Path(os.path.relpath(p, src)).as_posix() for p in _copy_skill(src, staging)]
    rewrite_frontmatter(staging / "SKILL.md", name, description, extra_frontmatter)

    refs = staging / "references"
    if refs.is_dir():
        for ref in sorted(p for p in refs.rglob("*") if p.is_file()):
            if ref.suffix.lower() not in admission.REFERENCE_SUFFIXES:
                continue
            before = ref.read_text(encoding="utf-8", errors="replace")
            parts = split_reference(ref, target_tokens)
            rel = ref.relative_to(staging).as_posix()
            if parts != [ref]:
                report["split"][rel] = [p.relative_to(staging).as_posix() for p in parts]
            elif ref.read_text(encoding="utf-8", errors="replace") != before:
                report["toc_added"].append(rel)

    result = admission.check_skill(staging, expected_name=name)
    report["check"] = result.as_dict()
    if not result.ok:
        raise PostprocessError(f"post-processed skill {name!r} fails admission:", result.errors)

    if run_claude_validate:
        ran, ok, out = claude_validate(staging, name)
        report["claude_validate"] = {"ran": ran, "ok": ok, "output": out}
        if ran and not ok:
            raise PostprocessError(f"'claude plugin validate' failed for {name!r}:", [out])
    return report


def promote(staging: Path, dest: Path, previous: Path) -> None:
    """Move ``staging`` to ``dest``; an existing ``dest`` is kept as ``previous``.

    Links into ``dest`` keep working: junctions/symlinks refer to the path, which is reused.
    All three paths live inside the store (never under a project's .claude/skills).
    """
    for p in (dest, previous):
        if is_link(p):
            raise SkillStoreError(f"refusing to replace {p}: it is a link, expected a store directory")
    if previous.exists():
        shutil.rmtree(previous)  # an old backup inside the store's build/<name>/ dir
    if dest.exists():
        previous.parent.mkdir(parents=True, exist_ok=True)
        os.replace(dest, previous)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, dest)
