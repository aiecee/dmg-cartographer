"""Scene tool: save, list, read and export labelled game states for training data. Part of the raw group."""

from typing import Any, Literal

from dmg_cartographer.mcp_app import CONTROLLER, Envelope, _expose, envelope
from dmg_cartographer.scene_outputs import SceneData


def _as(action: str, core: dict[str, Any]) -> dict[str, Any]:
    return {**core, "action": action}


@_expose("raw")
def scene(action: Literal["save", "list", "read", "export"], name: str | None = None, address: int | None = None,
          length: int = 16, fields: list[dict] | None = None, note: str = "",
          include_states: bool = False) -> Envelope[SceneData]:
    """Label the current game state as a scene, and export saved scenes as a dataset.

    save   -> decodes `fields` from `length` bytes at `address` (the same field types as memory decode), then
              writes scenes/<rom>/<name>/ with scene.json (schema_version 1), capture.png and state.state.
              Advances no frames. Saving under an existing name overwrites that scene.
    list   -> scene names for the loaded ROM.
    read   -> the scene.json contents for name.
    export -> writes every saved scene for the ROM to datasets/<rom>/<name>/: manifest.json (format_version 1),
              scenes.jsonl, and images/. include_states also copies each save state. Read it with
              dmg_cartographer.dataset.iter_scenes, which needs no MCP.
    """
    if action == "list":
        return envelope({"action": "list", "scenes": CONTROLLER.list_scenes()}, SceneData)
    if name is None:
        raise ValueError(f"scene(action={action!r}) needs name")
    if action == "read":
        return envelope(_as("read", CONTROLLER.read_scene(name)), SceneData)
    if action == "export":
        return envelope(_as("export", CONTROLLER.export_scenes(name, include_states)), SceneData)
    if address is None or not fields:
        raise ValueError("scene(action='save') needs address and fields")
    return envelope(_as("save", CONTROLLER.save_scene(name, address, length, fields, note)), SceneData)
