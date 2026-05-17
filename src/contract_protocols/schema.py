from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from contract_protocols.config import load_json, service_path

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError as JsonSchemaDefinitionError
    from jsonschema.exceptions import ValidationError
except ImportError as error:  # pragma: no cover
    raise RuntimeError(
        "Jurist requires jsonschema. Install project dependencies first."
    ) from error


class SchemaError(ValueError):
    pass


def load_schema(name: str) -> dict:
    return load_json(service_path("schemas", name))


def validate(instance: object, schema: dict, path: str = "$") -> None:
    try:
        _validator_for_schema(json.dumps(schema, sort_keys=True)).validate(instance)
    except JsonSchemaDefinitionError as error:
        raise SchemaError(f"{path}: invalid schema: {error.message}") from error
    except ValidationError as error:
        location = _json_path(error.absolute_path, base=path)
        raise SchemaError(f"{location}: {error.message}") from error


def validate_named(instance: object, schema_name: str, path: str = "$") -> None:
    validate(instance, load_schema(schema_name), path=path)


def validate_schema_file(path: Path) -> None:
    payload = load_json(path)
    try:
        Draft202012Validator.check_schema(payload)
    except JsonSchemaDefinitionError as error:
        raise SchemaError(f"{path}: invalid schema: {error.message}") from error


@lru_cache(maxsize=128)
def _validator_for_schema(schema_json: str) -> Draft202012Validator:
    schema = json.loads(schema_json)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _json_path(parts: object, base: str = "$") -> str:
    result = base
    for part in parts:
        if isinstance(part, int):
            result = f"{result}[{part}]"
        else:
            result = f"{result}.{part}"
    return result
