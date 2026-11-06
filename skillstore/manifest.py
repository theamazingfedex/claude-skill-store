"""The per-project manifest: ``<project>/.claude/skills.manifest.json``."""

from __future__ import annotations

import json
from pathlib import Path

from skillstore import DEFAULT_CAP
from skillstore.paths import ProjectPaths, SkillStoreError, validate_name
from skillstore.store import atomic_write_text, write_json

MANIFEST_VERSION = 1
GITIGNORE_TEXT = "# Managed by skill-store: links into the skill store are never committed.\n*\n!.gitignore\n"


class Manifest:
    def __init__(self, path: Path, data: dict | None = None):
        self.path = Path(path)
        self.data = data if data is not None else {"version": MANIFEST_VERSION, "skills": []}

    @classmethod
    def load(cls, project: ProjectPaths) -> "Manifest | None":
        """The manifest, or None if the project has none."""
        if not project.manifest.is_file():
            return None
        try:
            data = json.loads(project.manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SkillStoreError(f"cannot read manifest {project.manifest}: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
            raise SkillStoreError(f"{project.manifest}: unsupported manifest (want version 1)")
        skills = data.get("skills", [])
        if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
            raise SkillStoreError(f"{project.manifest}: 'skills' must be a list of names")
        for s in skills:
            validate_name(s)
        cap = data.get("cap", DEFAULT_CAP)
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 0:
            raise SkillStoreError(f"{project.manifest}: 'cap' must be a non-negative integer")
        data["skills"] = list(dict.fromkeys(skills))  # drop duplicates, keep order
        return cls(project.manifest, data)

    @property
    def skills(self) -> list[str]:
        return self.data["skills"]

    @property
    def cap(self) -> int:
        return self.data.get("cap", DEFAULT_CAP)

    def add(self, name: str) -> bool:
        if name in self.skills:
            return False
        self.skills.append(name)
        return True

    def remove(self, name: str) -> bool:
        if name not in self.skills:
            return False
        self.skills.remove(name)
        return True

    def save(self) -> None:
        write_json(self.path, self.data)


def ensure_project_dirs(project: ProjectPaths) -> list[str]:
    """Create ``.claude/skills/`` and its ``.gitignore`` if missing. Returns what was created."""
    created = []
    if not project.skills.is_dir():
        project.skills.mkdir(parents=True, exist_ok=True)
        created.append(str(project.skills))
    if not project.gitignore.exists():
        atomic_write_text(project.gitignore, GITIGNORE_TEXT)
        created.append(str(project.gitignore))
    return created


def gitignore_ok(project: ProjectPaths) -> bool:
    try:
        lines = [ln.strip() for ln in project.gitignore.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return False
    return "*" in lines


def init_project(project: ProjectPaths) -> list[str]:
    created = ensure_project_dirs(project)
    if not project.manifest.exists():
        write_json(project.manifest, {"version": MANIFEST_VERSION, "skills": []})
        created.append(str(project.manifest))
    return created
