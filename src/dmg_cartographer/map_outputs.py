"""Typed output for the state_map tool: validate a map.json, or read its values from the loaded game."""

from typing import Annotated, Literal, Union

from pydantic import Field
from typing_extensions import TypeAliasType

from dmg_cartographer.outputs import Out

# A decoded value: a number or label, or a nested row-major array of them.
MappedValue = TypeAliasType("MappedValue", Annotated[Union[int, str, list["MappedValue"]], Field()])


class MapFieldSummary(Out):
    name: str
    address: str
    type: Literal["uint8", "int8", "uint16", "int16", "bits"]
    shape: list[int] | None
    status: Literal["hypothesis", "verified"]


class MapValidate(Out):
    action: Literal["validate"]
    path: str
    format: Literal["dmg-cartographer-map"]
    format_version: Literal[1]
    rom: str
    rom_sha1: str
    field_count: int
    fields: list[MapFieldSummary]


class MapRead(Out):
    action: Literal["read"]
    path: str
    labelled: bool
    values: dict[str, MappedValue]


MapData = Annotated[Union[MapValidate, MapRead], Field(discriminator="action")]
