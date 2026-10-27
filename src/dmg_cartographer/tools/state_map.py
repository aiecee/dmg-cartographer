"""State map tool: validate a map.json, or read its fields from the loaded game. Part of the raw group."""

from typing import Any, Literal

from dmg_cartographer.map_outputs import MapData
from dmg_cartographer.mcp_app import CONTROLLER, Envelope, _expose, envelope


@_expose("raw")
def state_map(action: Literal["validate", "read"], path: str | None = None,
              labelled: bool = False) -> Envelope[MapData]:
    """Work with a state map (map.json): where the game keeps its state, as fields with addresses and types.

    validate -> checks the file against the map format and lists its fields. Needs no game loaded if path is given.
    read     -> decodes every field from the loaded game's memory at the current frame. Advances no frames.
               Refuses a map made for a different ROM. labelled=true returns lookup labels where the map has them.
    path     -> an absolute path to the map file. Left out, the loaded ROM's own map is used:
               <rom folder>/maps/map.json.
    """
    if path is None:
        path = str(CONTROLLER.default_map_path())
    if action == "validate":
        return envelope({"action": "validate", **CONTROLLER.validate_state_map(path)}, MapData)
    data: dict[str, Any] = {"action": "read", **CONTROLLER.read_state_map(path, labelled)}
    return envelope(data, MapData)
