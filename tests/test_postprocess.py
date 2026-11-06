"""Post-processor on a synthetic skill-seekers 3.1.3 output fixture."""

import re

import pytest
import yaml

import fixture_output
from skillstore import admission, frontmatter, postprocess
from skillstore.postprocess import PostprocessError

DESC = "Use when importing example assets: suffixes, import options and retargeting."


def _fences_balanced(text):
    return len(re.findall(r"^\s{0,3}```", text, flags=re.M)) % 2 == 0


def test_postprocess_fixture(tmp_path):
    src = fixture_output.write(tmp_path / "build", "demo-skill", description="Example: unquoted desc")
    # sanity: the fixture reproduces the 3.1.3 problems
    with pytest.raises(frontmatter.FrontmatterError):
        frontmatter.parse_strict((src / "SKILL.md").read_text())
    assert admission.tokens((src / "references" / "importing.md").read_text()) > 25_000
    assert admission.tokens((src / "references" / "spec.md").read_text()) > 25_000

    staging = tmp_path / "staging"
    report = postprocess.postprocess(src, staging, "demo-skill", description=DESC,
                                     extra_frontmatter={"when_to_use": "importing: assets"})
    # junk dropped, other files kept
    assert not (staging / "quality_report.json").exists()
    assert (staging / ".skill_version.json").exists()
    assert any("quality_report.json" in d for d in report["dropped"])
    # frontmatter is strict YAML with a quoted description
    data, raw, _ = frontmatter.parse_strict((staging / "SKILL.md").read_text(encoding="utf-8"))
    assert data == {"name": "demo-skill", "description": DESC, "when_to_use": "importing: assets"}
    assert frontmatter.description_is_quoted(raw)
    yaml.safe_load(raw)
    # references split under the limit, each part with a TOC, index keeps the old name
    assert set(report["split"]) == {"references/importing.md", "references/spec.md"}
    for ref, parts in report["split"].items():
        assert len(parts) >= 2
        index = (staging / ref).read_text(encoding="utf-8")
        assert admission.has_toc(index)
        for p in parts:
            text = (staging / p).read_text(encoding="utf-8")
            assert admission.tokens(text) <= 25_000
            assert admission.has_toc(text)
            assert _fences_balanced(text), p
            assert p.split("/")[-1] in index
    # no content lost: every page heading of the big file survives in some part
    joined = "".join((staging / p).read_text(encoding="utf-8") for p in report["split"]["references/importing.md"])
    for i in range(60):
        assert f"## Page {i}: Importing things" in joined
    assert "references/small.md" in report["toc_added"]
    assert admission.check_skill(staging, "demo-skill").ok
    # source untouched
    assert (src / "quality_report.json").exists()


def test_generic_description_without_recipe_fails(tmp_path):
    src = fixture_output.write(tmp_path / "b", "demo-skill")
    with pytest.raises(PostprocessError, match="description"):
        postprocess.postprocess(src, tmp_path / "staging", "demo-skill")


def test_existing_good_description_is_kept_and_quoted(tmp_path):
    src = tmp_path / "s"
    (src / "references").mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: old\ndescription: Use when x: it works\n---\n\n# Body\n")
    postprocess.postprocess(src, tmp_path / "st", "new-name")
    data, _, body = frontmatter.parse_strict((tmp_path / "st" / "SKILL.md").read_text())
    assert data["description"] == "Use when x: it works" and data["name"] == "new-name"
    assert body.strip() == "# Body"


def test_oversized_skill_md_fails_admission(tmp_path):
    src = tmp_path / "s"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\n" + "z" * 20_000)
    with pytest.raises(PostprocessError, match="admission"):
        postprocess.postprocess(src, tmp_path / "st", "s", description=DESC)


def test_split_giant_code_block_and_giant_line(tmp_path):
    p = tmp_path / "r.md"
    p.write_text("# R\n\n## A\n\n```python\n" + "print(1)\n" * 30_000 + "```\n\n## B\n\n" + "w" * 150_000 + "\n")
    parts = postprocess.split_reference(p, 20_000)
    assert len(parts) > 2
    for part in parts:
        t = part.read_text()
        assert admission.tokens(t) <= 25_000 and admission.has_toc(t) and _fences_balanced(t)


def test_claude_validate_skipped_when_absent(tmp_path, monkeypatch):
    src = tmp_path / "s"
    src.mkdir()
    (src / "SKILL.md").write_text('---\nname: s\ndescription: "Use when s: x."\n---\n')
    r = postprocess.postprocess(src, tmp_path / "st", "s")
    assert r["claude_validate"]["ran"] is False


def test_claude_validate_failure_blocks(tmp_path, monkeypatch):
    src = tmp_path / "s"
    src.mkdir()
    (src / "SKILL.md").write_text('---\nname: s\ndescription: "Use when s: x."\n---\n')
    monkeypatch.setattr(postprocess, "claude_validate", lambda d, n: (True, False, "bad skill"))
    with pytest.raises(PostprocessError, match="claude plugin validate"):
        postprocess.postprocess(src, tmp_path / "st", "s")


def test_promote_keeps_links_valid(tmp_path):
    from skillstore import links
    dest = tmp_path / "store" / "skills" / "s"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("v1")
    lp = tmp_path / "proj" / "s"
    links.create_link(lp, dest)
    staging = tmp_path / "store" / "build" / "s" / "staging"
    staging.mkdir(parents=True)
    (staging / "SKILL.md").write_text("v2")
    postprocess.promote(staging, dest, tmp_path / "store" / "build" / "s" / "previous")
    assert (lp / "SKILL.md").read_text() == "v2"
    assert (tmp_path / "store" / "build" / "s" / "previous" / "SKILL.md").read_text() == "v1"
