"""Typed output for the scene tool. A scene is a labelled game state saved for training data."""

from typing import Annotated, Literal, Union

from pydantic import Field

from dmg_cartographer.investigate_outputs import ButtonName
from dmg_cartographer.outputs import DecodedField, Out

SceneSchemaVersion = Literal[1]


class SceneFiles(Out):
    scene_json: str
    capture_png: str
    state: str


class SceneRecord(Out):
    """The contents of scene.json. Values are decoded from memory at the time the scene was saved."""
    schema_version: SceneSchemaVersion
    name: str
    rom: str
    rom_sha1: str
    frame: int = Field(ge=0)
    held_buttons: list[ButtonName]
    screen_fresh: bool
    note: str = Field(max_length=500)
    values: dict[str, DecodedField]


class SceneSave(SceneRecord):
    action: Literal["save"]
    folder: str
    files: SceneFiles


class SceneRead(SceneRecord):
    action: Literal["read"]


class SceneList(Out):
    action: Literal["list"]
    scenes: list[str]


class SceneExport(Out):
    """A dataset written to datasets/<rom>/<name>/. Read it with dmg_cartographer.dataset, no MCP needed."""
    action: Literal["export"]
    name: str
    folder: str
    format: Literal["dmg-cartographer-scenes"]
    format_version: Literal[1]
    scene_count: int
    include_states: bool
    manifest_file: str
    index_file: str
    scenes: list[str]


SceneData = Annotated[Union[SceneSave, SceneRead, SceneList, SceneExport], Field(discriminator="action")]
