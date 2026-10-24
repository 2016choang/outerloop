"""Check the shared plugin's packaging without installing either harness."""

import json
from pathlib import Path

import yaml


def test_agent_plugin_layout() -> None:
    root = Path(__file__).resolve().parents[1]
    marketplace = json.loads((root / ".claude-plugin/marketplace.json").read_text())
    assert marketplace["name"] == "outerloop"
    assert marketplace["owner"]["name"]
    (entry,) = marketplace["plugins"]
    assert entry["name"] == "outerloop"
    assert entry["source"] == "./plugins/outerloop"
    plugin = root / entry["source"]
    for harness in ("claude", "codex"):
        manifest = json.loads((plugin / f".{harness}-plugin/plugin.json").read_text())
        assert manifest["name"] == entry["name"]
        assert "mcpServers" not in manifest
        assert (plugin / manifest["skills"]).is_dir()
    skills = sorted((plugin / "skills").glob("*/SKILL.md"))
    assert [path.parent.name for path in skills] == ["contract", "operate", "setup"]
    for path in skills:
        opening, frontmatter, body = path.read_text().split("---", 2)
        assert not opening
        metadata = yaml.safe_load(frontmatter)
        assert metadata["name"] == path.parent.name
        assert isinstance(metadata["description"], str) and metadata["description"].strip()
        assert body.strip()
