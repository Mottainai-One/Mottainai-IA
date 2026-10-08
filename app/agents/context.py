"""Unambiguous, compact serialization of domain evidence for models."""
import json
from decimal import Decimal
from typing import Any

NUMBER_GUIDANCE = (
    "Nos dados abaixo, ponto indica decimal, nunca milhar. "
    "48 significa 48 unidades; 7.5 significa sete e meia. "
    "Na resposta use vírgula decimal e preserve os valores e unidades. "
    "Uma lista limitada não representa todo o estoque."
    " Tabelas usam columns/rows: cada linha segue a ordem das colunas; "
    "shared contém campos iguais em todas as linhas."
)


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite domain number")
        if value == value.to_integral_value():
            return int(value)
        # Preserve fractional precision without binary float rounding.
        return format(value.normalize(), "f")
    return str(value)


def context_json(value: Any) -> str:
    return json.dumps(value, default=_encode, ensure_ascii=False, separators=(",", ":"))


def _pack_records(value: Any) -> Any:
    """Factor repeated field names and constants, preserving every row and value."""
    if isinstance(value, dict):
        return {key: _pack_records(item) for key, item in value.items()}
    if not isinstance(value, list):
        return value
    records = [_pack_records(item) for item in value]
    if len(records) < 3 or not all(isinstance(row, dict) for row in records):
        return records
    columns = list(records[0])
    if not columns or any(set(row) != set(columns) for row in records):
        return records
    shared = {
        key: records[0][key] for key in columns
        if all(context_json(row[key]) == context_json(records[0][key]) for row in records[1:])
    }
    varying = [key for key in columns if key not in shared]
    table = {"format": "table", "columns": varying,
             "rows": [[row[key] for key in varying] for row in records]}
    if shared:
        table["shared"] = shared
    # Tiny or irregular tables can cost more than their original dictionaries.
    return table if len(context_json(table)) < len(context_json(records)) * 0.85 else records


def model_context_json(value: Any) -> str:
    """Lossless model-only representation; storage and API contracts stay intact."""
    normalized = json.loads(context_json(value))
    return context_json(_pack_records(normalized))
