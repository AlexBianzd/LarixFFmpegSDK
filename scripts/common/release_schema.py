"""Minimal validator for the repository-owned release-manifest schema."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate schema field: {key}")
        result[key] = value
    return result


def _matches_type(value: object, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in {int, float},
        "boolean": type(value) is bool,
        "null": value is None,
    }.get(expected, False)


def _validate(value: object, schema: dict[str, Any], path: str) -> None:
    supported = {
        "$schema", "type", "const", "enum", "required",
        "additionalProperties", "properties", "items", "pattern", "minimum",
    }
    if set(schema) - supported:
        raise ValueError(f"unsupported release schema keyword at {path}")
    expected_type = schema.get("type")
    if expected_type is not None and (
        not isinstance(expected_type, str) or not _matches_type(value, expected_type)
    ):
        raise ValueError(f"release manifest type mismatch at {path}")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"release manifest const mismatch at {path}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"release manifest enum mismatch at {path}")
    if "pattern" in schema and (
        not isinstance(value, str) or re.search(schema["pattern"], value) is None
    ):
        raise ValueError(f"release manifest pattern mismatch at {path}")
    if "minimum" in schema and (
        type(value) not in {int, float} or value < schema["minimum"]
    ):
        raise ValueError(f"release manifest minimum mismatch at {path}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if set(required) - set(value):
            raise ValueError(f"release manifest missing required field at {path}")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"release manifest unknown field at {path}")
        for key, child in value.items():
            if key in properties:
                _validate(child, properties[key], f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, child in enumerate(value):
            _validate(child, schema["items"], f"{path}[{index}]")


def validate_release_manifest_schema(
    manifest: dict[str, object], repo_root: Path
) -> None:
    schema_path = repo_root / "config" / "schema" / "release-manifest.schema.json"
    try:
        schema = json.loads(
            schema_path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid release manifest schema") from error
    if not isinstance(schema, dict):
        raise ValueError("release manifest schema is not an object")
    _validate(manifest, schema, "$")
