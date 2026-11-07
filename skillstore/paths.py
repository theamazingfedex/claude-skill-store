"""Resolution of the store root, the project root and well-known paths."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

STORE_ENV = "SKILL_STORE_HOME"
PROJECT_ENV = "CLAUDE_PROJECT_DIR"
CONFIG_ENV = "CLAUDE_CONFIG_DIR"
GLOBAL_MANIFEST_NAME = "global.manifest.json"

# Claude Code skill names: lowercase letters, digits and hyphens, at most 64 chars.
# Enforcing this also rules out path traversal ("..", separators) in every command.
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class SkillStoreError(Exception):
    """An expected, user-facing failure. The CLI prints the message and exits 1."""


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name) or "--" in name:
        raise SkillStoreError(
            f"invalid skill name {name!r}: use lowercase letters, digits and single hyphens "
            "(max 64 chars)"
        )
    return name


def resolve_store(flag: str | None = None) -> Path:
    """``--store`` flag > ``SKILL_STORE_HOME`` > ``~/.skill-store``."""
    if flag:
        return Path(flag).expanduser().absolute()
    env = os.environ.get(STORE_ENV)
    if env:
        return Path(env).expanduser().absolute()
    return (Path.home() / ".skill-store").absolute()


def _git_toplevel(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return Path(out.stdout.strip())


def resolve_project(flag: str | None = None, cwd: Path | None = None) -> Path:
    """``--project`` flag > ``CLAUDE_PROJECT_DIR`` > git toplevel of cwd > cwd."""
    if flag:
        return Path(flag).expanduser().absolute()
    env = os.environ.get(PROJECT_ENV)
    if env:
        return Path(env).expanduser().absolute()
    cwd = Path(cwd or os.getcwd()).absolute()
    top = _git_toplevel(cwd)
    return (top or cwd).absolute()


def resolve_claude_config() -> Path:
    """The user-level Claude Code config dir: ``CLAUDE_CONFIG_DIR`` > ``~/.claude``."""
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env).expanduser().absolute()
    return (Path.home() / ".claude").absolute()


def legacy_home_manifest() -> Path:
    """Where the old ``--project ~`` workaround wrote its manifest."""
    return (Path.home() / ".claude" / "skills.manifest.json").absolute()


def same_dir(a: Path, b: Path) -> bool:
    """True if two paths name the same directory (after resolving links), even if missing."""
    def norm(p):
        try:
            p = os.path.realpath(p)
        except OSError:
            p = os.path.abspath(p)
        return os.path.normcase(os.path.normpath(p))
    return norm(a) == norm(b)


class StorePaths:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.skills = self.root / "skills"
        self.build = self.root / "build"
        self.store_json = self.root / "store.json"
        self.usage = self.root / "usage.jsonl"

    def skill(self, name: str) -> Path:
        return self.skills / validate_name(name)

    def build_dir(self, name: str) -> Path:
        return self.build / validate_name(name)

    def exists(self) -> bool:
        return self.store_json.is_file()


class ProjectPaths:
    """Project scope: links in ``<project>/.claude/skills``, manifest committed in the project."""

    is_global = False
    scope = "project"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.claude = self.root / ".claude"
        self.skills = self.claude / "skills"
        self.manifest = self.claude / "skills.manifest.json"
        self.gitignore = self.skills / ".gitignore"

    def link(self, name: str) -> Path:
        return self.skills / validate_name(name)


class GlobalPaths:
    """Global scope: links in the user-level skills dir (``$CLAUDE_CONFIG_DIR/skills`` or
    ``~/.claude/skills``). The manifest lives in the store, never in the Claude config dir,
    and no ``.gitignore`` is ever written there."""

    is_global = True
    scope = "global"

    def __init__(self, store_root: Path, config_dir: Path):
        self.root = Path(config_dir)
        self.skills = self.root / "skills"
        self.manifest = Path(store_root) / GLOBAL_MANIFEST_NAME
        self.gitignore = None

    def link(self, name: str) -> Path:
        return self.skills / validate_name(name)
