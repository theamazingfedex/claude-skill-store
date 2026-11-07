"""The example files validate against the shipped JSON Schemas, and the code agrees with them."""

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parent.parent


def _load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize("schema,example", [
    ("schemas/manifest.schema.json", "examples/skills.manifest.json"),
    ("schemas/global-manifest.schema.json", "examples/global.manifest.json"),
    ("schemas/store.schema.json", "examples/store.json"),
    ("schemas/recipe.schema.json", "examples/recipes/godot47-asset-pipeline.recipe.json"),
    ("schemas/recipe.schema.json", "examples/recipes/gltf2-spec.recipe.json"),
])
def test_examples_validate(schema, example):
    jsonschema.validate(_load(example), _load(schema))


def test_generated_files_validate(env):
    env.add_skill("alpha")
    env.run("init-project")
    env.run("link", "alpha")
    jsonschema.validate(env.manifest(), _load("schemas/manifest.schema.json"))
    jsonschema.validate(env.store.load(), _load("schemas/store.schema.json"))
    env.log_use("alpha", "2026-11-05T00:00:00Z")
    rec = json.loads(env.store.paths.usage.read_text().splitlines()[0])
    jsonschema.validate(rec, _load("schemas/usage-record.schema.json"))


def test_generated_global_manifest_validates(env):
    env.add_skill("alpha")
    env.run_raw("--global", "link", "alpha")
    jsonschema.validate(env.global_manifest(), _load("schemas/global-manifest.schema.json"))


def test_global_manifest_schema_matches_project_schema():
    """Same fields and rules; only the titles and descriptions differ."""
    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in ("title", "description", "$id")}
        return node
    assert strip(_load("schemas/global-manifest.schema.json")) == strip(_load("schemas/manifest.schema.json"))
