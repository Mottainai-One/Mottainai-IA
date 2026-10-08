"""Independent decoder for checking the facts carried by compact model tables."""
import json


def expand_tables(value):
    if isinstance(value, list):
        return [expand_tables(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("format") == "table" and "columns" in value and "rows" in value:
        return [
            {**expand_tables(value.get("shared", {})),
             **dict(zip(value["columns"], [expand_tables(item) for item in row], strict=True))}
            for row in value["rows"]
        ]
    return {key: expand_tables(item) for key, item in value.items()}


def read_json_block(prompt: str, heading: str):
    block = prompt.split(heading, 1)[1].lstrip().splitlines()[0]
    return expand_tables(json.loads(block))
