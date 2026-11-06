"""A stand-in for the skill-seekers 3.1.3 CLI used by the build tests (no network).

Behaviour knobs via env vars:
  MOCK_SS_VERSION   version string to print for --version (default 3.1.3)
  MOCK_SS_NO_REFS   if set, write a skeleton SKILL.md with no references (bug 2 in 3.1.3)
  MOCK_SS_FAIL      if set, exit 1
Each invocation's argv and cwd are appended to ./mock_calls.jsonl.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture_output  # noqa: E402


def main(argv):
    if argv == ["--version"]:
        print(f"skill-seekers {os.environ.get('MOCK_SS_VERSION', '3.1.3')}")
        return 0
    with open("mock_calls.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\n")
    if os.environ.get("MOCK_SS_FAIL"):
        print("boom", file=sys.stderr)
        return 1
    assert argv[0] == "scrape" and "--config" in argv
    assert argv[argv.index("--enhance-level") + 1] == "0"
    if not sys.stdin.isatty():
        assert sys.stdin.read() == ""  # stdin must be closed/empty: no interactive prompts
    cfg = json.loads(Path(argv[argv.index("--config") + 1]).read_text(encoding="utf-8"))
    assert "base_url" in cfg and cfg.get("sources"), "config must be hybrid"
    name = cfg["name"]
    data = Path("output") / f"{name}_data"
    if data.exists() and "--fresh" not in argv and "--skip-scrape" not in argv:
        print("Use existing data? (y/n): ", end="")
        input()  # EOF -> crash, like the real thing when non-interactive
    if os.environ.get("MOCK_SS_NO_REFS"):
        skill = Path("output") / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n")
        print(f"Scraped {name} successfully")
        return 0
    fixture_output.write(Path("."), name, description=cfg.get("description"), big_pages=20,
                         blob_paras=400)
    print(f"Scraped {name} successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
