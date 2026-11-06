"""SKILL.md frontmatter: strict parsing (PyYAML) and lenient parsing/rewriting.

skill-seekers 3.1.3 writes descriptions unquoted, so any ``: `` in them makes invalid YAML.
The lenient parser reads such frontmatter line by line so the post-processor can rewrite it
with every scalar quoted.
"""

from __future__ import annotations

import json
import re

import yaml

_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


class FrontmatterError(ValueError):
    pass


def split(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, body). Raises FrontmatterError if there is none."""
    text = text.lstrip("﻿")
    m = _FM_RE.match(text)
    if not m:
        raise FrontmatterError("SKILL.md has no YAML frontmatter (--- ... ---) at the top")
    return m.group(1), text[m.end():]


def parse_strict(text: str) -> tuple[dict, str, str]:
    """Strict YAML parse. Returns (data, raw_frontmatter, body)."""
    raw, body = split(text)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter must be a YAML mapping")
    return data, raw, body


def parse_lenient(text: str) -> tuple[dict, str]:
    """Best-effort parse: strict first, else ``key: rest-of-line`` per top-level line."""
    try:
        data, _, body = parse_strict(text)
        return data, body
    except FrontmatterError:
        pass
    raw, body = split(text)
    data: dict = {}
    key = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):(?:\s(.*))?$", line)
        if m and not line[0].isspace():
            key = m.group(1)
            data[key] = _unquote((m.group(2) or "").strip())
        elif key is not None and isinstance(data.get(key), str):
            data[key] = (data[key] + " " + line.strip()).strip()
    return data, body


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            try:
                return json.loads(value)
            except ValueError:
                return inner
        return inner.replace("''", "'")
    return value


def quote(value: str) -> str:
    """A double-quoted YAML scalar. JSON string syntax is a subset of YAML's."""
    return json.dumps(value, ensure_ascii=False)


def render(data: dict, body: str) -> str:
    """Render frontmatter with ``name`` and ``description`` first and string scalars quoted."""
    order = [k for k in ("name", "description") if k in data] + [
        k for k in data if k not in ("name", "description")
    ]
    lines = ["---"]
    for key in order:
        value = data[key]
        if isinstance(value, str):
            lines.append(f"{key}: {quote(value)}")
        elif isinstance(value, (bool, int, float)) or value is None:
            lines.append(f"{key}: {json.dumps(value)}")
        else:
            dumped = yaml.safe_dump({key: value}, default_flow_style=False, allow_unicode=True)
            lines.append(dumped.rstrip("\n"))
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.lstrip("\r\n")


def description_is_quoted(raw_frontmatter: str) -> bool:
    for line in raw_frontmatter.splitlines():
        m = re.match(r"^description:\s*(.*)$", line)
        if m:
            v = m.group(1).strip()
            return len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]
    return False
