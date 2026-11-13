"""BLS registration credentials and strict response validation."""

from __future__ import annotations

import os
import re
from typing import Any

import pyarrow as pa
from vgi.catalog import SecretTypeSpec
from vgi.table_function import ProcessParams

from .transport import ApiError, Config, request_json

SECRET_TYPE = "bls_api"
API_SECRET_TYPES = [
    SecretTypeSpec(
        name=SECRET_TYPE,
        description="Optional BLS API registration key for higher limits and catalog/calculation features.",
        schema=pa.schema(
            [pa.field("api_key", pa.string(), metadata={"redact": "true", "vgi_doc": "Your BLS registration key."})],
            metadata={"vgi_doc_url": "https://www.bls.gov/developers/"},
        ),
    )
]


def validate_key(key: str | None) -> str | None:
    """Allow absent registration, rejecting explicitly malformed keys safely."""
    if key is None:
        return None
    if re.fullmatch(r"[A-Za-z0-9]{32}", key) is None:
        raise ApiError("BLS API key must contain 32 letters or digits; omit BLS_API_KEY for unregistered access")
    return key


def credential(params: ProcessParams[Any]) -> str | None:
    """Resolve a DuckDB secret before an optional environment credential."""
    secrets = list(params.secrets.of_type(SECRET_TYPE))
    if len(secrets) > 1:
        raise ApiError("Multiple bls_api secrets matched; keep one applicable secret")
    key = None
    if secrets:
        scalar = secrets[0].get("api_key")
        key = scalar.as_py() if scalar is not None else None
    return validate_key(key if key is not None else os.environ.get("BLS_API_KEY"))


def records(node: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Require an explicitly present array of source objects."""
    rows = node.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ApiError(f"BLS response has invalid {key} records")
    return rows


def call(
    endpoint: str, key: str | None, *, query: dict[str, str] | None = None, payload: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Reject API warnings and partial results without echoing server messages or keys."""
    key = validate_key(key)
    params = dict(query or {})
    if payload is not None:
        payload = dict(payload)
        if key:
            payload["registrationkey"] = key
    elif key:
        params["registrationkey"] = key
    body = request_json(endpoint, list(params.items()), Config.from_env(), payload=payload)
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise ApiError("BLS rejected the request; check registration, identifiers, years and daily quota")
    messages = body.get("message")
    if not isinstance(messages, list):
        raise ApiError("BLS response has invalid message metadata")
    if messages:
        raise ApiError(
            "BLS returned warnings or partial results; check series identifiers, years and registration limits"
        )
    result = body.get("Results")
    nodes = result if isinstance(result, list) else [result]
    validated: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ApiError("BLS response is missing Results")
        validated.append(node)
    if not validated:
        raise ApiError("BLS response is missing Results")
    return validated
