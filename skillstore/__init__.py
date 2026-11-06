"""skill-store: a user-level store of Claude Code skills, linked into projects on demand.

Skills live in a persistent store (``~/.skill-store`` by default). Each project lists the
skills it wants in ``.claude/skills.manifest.json``; ``skill-store sync`` links exactly those
(approved, admitted, within the cap) into ``<project>/.claude/skills/`` as NTFS junctions on
Windows or symlinks elsewhere. Unlinking removes only the link, never the store copy.
"""

__version__ = "0.1.0"

SKILL_SEEKERS_VERSION = "3.1.3"
DEFAULT_CAP = 8
