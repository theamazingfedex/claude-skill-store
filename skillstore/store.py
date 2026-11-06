"""The store registry (store.json) and the usage log (usage.jsonl)."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from skillstore.paths import SkillStoreError, StorePaths, validate_name

STORE_VERSION = 1

ENTRY_DEFAULTS = {
    "source": "manual",
    "version": None,
    "pinned_url": None,
    "built_at": None,
    "approved": False,
    "approved_at": None,
    "last_used": None,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then os.replace (retrying on Windows,
    where a reader holding the file open makes replace fail transiently)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path: Path, data) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


class Store:
    def __init__(self, root: Path):
        self.paths = StorePaths(root)

    # ----- layout -----------------------------------------------------------------
    def init(self) -> list[str]:
        """Create the store layout. Idempotent; returns what was created."""
        created = []
        for d in (self.paths.root, self.paths.skills, self.paths.build):
            if not d.is_dir():
                d.mkdir(parents=True, exist_ok=True)
                created.append(str(d))
        if not self.paths.store_json.exists():
            write_json(self.paths.store_json, {"version": STORE_VERSION, "skills": {}})
            created.append(str(self.paths.store_json))
        if not self.paths.usage.exists():
            self.paths.usage.touch()
            created.append(str(self.paths.usage))
        return created

    def require(self) -> None:
        if not self.paths.exists():
            raise SkillStoreError(
                f"no skill store at {self.paths.root} (run 'skill-store init', or set "
                "SKILL_STORE_HOME / --store)"
            )

    # ----- registry -----------------------------------------------------------------
    def load(self) -> dict:
        self.require()
        try:
            data = json.loads(self.paths.store_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SkillStoreError(f"cannot read {self.paths.store_json}: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
            raise SkillStoreError(f"{self.paths.store_json}: unsupported store.json (want version 1)")
        skills = data.setdefault("skills", {})
        if not isinstance(skills, dict):
            raise SkillStoreError(f"{self.paths.store_json}: 'skills' must be an object")
        for entry in skills.values():
            for key, default in ENTRY_DEFAULTS.items():
                entry.setdefault(key, default)
        return data

    def save(self, data: dict) -> None:
        """Write store.json, folding the latest use from usage.jsonl into ``last_used``
        (the usage hook itself only appends to usage.jsonl and never writes store.json)."""
        latest = self.last_used_map(data)
        for name, entry in data["skills"].items():
            if name in latest:
                entry["last_used"] = latest[name].isoformat().replace("+00:00", "Z")
        write_json(self.paths.store_json, data)

    def entry(self, data: dict, name: str, create: bool = False) -> dict | None:
        validate_name(name)
        skills = data["skills"]
        if name not in skills and create:
            skills[name] = dict(ENTRY_DEFAULTS)
        return skills.get(name)

    def skill_names_on_disk(self) -> list[str]:
        if not self.paths.skills.is_dir():
            return []
        out = []
        for p in sorted(self.paths.skills.iterdir()):
            if p.name.startswith(".") or not p.is_dir():
                continue
            if (p / "SKILL.md").is_file():
                out.append(p.name)
        return out

    def has_skill(self, name: str) -> bool:
        return (self.paths.skill(name) / "SKILL.md").is_file()

    def is_approved(self, data: dict, name: str) -> bool:
        e = data["skills"].get(name)
        return bool(e and e.get("approved") is True)

    # ----- usage --------------------------------------------------------------------
    def append_usage(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        # One write() call in append mode: concurrent sessions don't interleave lines.
        with open(self.paths.usage, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)

    def last_used_map(self, data: dict | None = None) -> dict[str, datetime]:
        """Latest use per skill: the usage log, falling back to store.json ``last_used``."""
        out: dict[str, datetime] = {}
        if data is not None:
            for name, entry in data["skills"].items():
                dt = parse_iso(entry.get("last_used"))
                if dt:
                    out[name] = dt
        try:
            fh = open(self.paths.usage, encoding="utf-8", errors="replace")
        except OSError:
            return out
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                skill, dt = rec.get("skill"), parse_iso(rec.get("time"))
                if isinstance(skill, str) and dt and (skill not in out or dt > out[skill]):
                    out[skill] = dt
        return out
