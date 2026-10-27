"""The plugin manifests agree with each other, with pyproject.toml, and with the skills folder."""
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_claude_and_codex_manifests_share_identity_with_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    claude = load(".claude-plugin/plugin.json")
    codex = load(".codex-plugin/plugin.json")
    for manifest in (claude, codex):
        assert manifest["name"] == project["name"]
        assert manifest["version"] == project["version"]
        assert manifest["license"] == "MIT"
    assert claude["description"] == codex["description"]


def test_codex_manifest_points_at_skills_and_mcp_config():
    codex = load(".codex-plugin/plugin.json")
    assert codex["skills"] == "./skills/" and (ROOT / "skills").is_dir()
    assert codex["mcpServers"] == "./.mcp.json" and (ROOT / ".mcp.json").is_file()


def test_mcp_config_launches_the_declared_console_script():
    config = load(".mcp.json")["mcpServers"]["dmg-cartographer"]
    assert config["type"] == "stdio"
    assert config["command"] == "uvx"
    assert config["args"][:2] == ["--from", "git+https://github.com/aiecee/dmg-cartographer"]
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert config["args"][-1] in scripts
    module, _, function = scripts[config["args"][-1]].partition(":")
    assert module == "dmg_cartographer.server" and function == "main"
