"""Discovery metadata: the one file to edit when you rename or extend the worker.

DuckDB, coding agents and the ``vgi-lint`` metadata checker all read these
tags. Keep every object documented and ``make lint`` stays at 100.

* Catalog-level tags (title, license, author, links, long description) live in
  ``CATALOG_TAGS`` below.
* Every function and table carries ``object_tags(...)``: a title, a category
  from ``SCHEMA_CATEGORIES``, search keywords, and LLM- and human-facing docs.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pyarrow as pa
from vgi.metadata import FunctionExample

CATALOG_NAME = "bls"

SOURCE_URL = "https://github.com/Query-farm/vgi-bls"

# Categories group functions in docs and agent tools. Every object's
# ``vgi.category`` must name one of these.
SCHEMA_CATEGORIES = [
    {
        "name": "api",
        "title": "Economic Data",
        "description": (
            "Retrieve labor, employment, price and productivity series with period codes, "
            "footnotes, calculations and survey metadata."
        ),
    },
]


def keywords_array(keywords: str) -> str:
    """Turn ``"a, b, c"`` into the JSON array string ``vgi.keywords`` requires."""
    return json.dumps([k.strip() for k in keywords.split(",") if k.strip()])


def object_tags(*, title: str, category: str, keywords: str, doc_llm: str, doc_md: str) -> dict[str, str]:
    """Build the standard per-object discovery tags."""
    if category not in {c["name"] for c in SCHEMA_CATEGORIES}:
        raise ValueError(f"unknown category {category!r}; add it to SCHEMA_CATEGORIES")
    return {
        "vgi.title": title,
        "vgi.category": category,
        "vgi.keywords": keywords_array(keywords),
        "vgi.doc_llm": doc_llm,
        "vgi.doc_md": doc_md,
    }


def column(name: str, type_: pa.DataType, comment: str, *, nullable: bool = True) -> pa.Field[Any]:
    """Build an Arrow field whose ``comment`` shows up in DuckDB's ``DESCRIBE`` and agent tools."""
    return pa.field(name, type_, nullable=nullable, metadata={b"comment": comment.encode()})


CATALOG_DOC_LLM = (
    "Retrieve labor, employment, price and productivity series with period codes, "
    "footnotes, calculations and survey metadata. Use a bls_api secret with api_key or "
    "BLS_API_KEY for registered access. Unregistered access has lower limits. Request "
    "explicit years and series identifiers; period codes retain annual averages and "
    "non-monthly frequencies without inventing dates. NULL arguments are rejected. "
    "Missing optional response fields return NULL. Upstream errors fail the query; SQL "
    "WHERE filters are local."
)
CATALOG_DOC_MD = (
    "# vgi-bls\n\n"
    + CATALOG_DOC_LLM
    + (
        "\n\nQueries use bounded HTTP responses and retries. Credentials stay out of "
        "continuation state. See README.md for deployment and operational limits."
    )
)
SCHEMA_DOC_LLM = CATALOG_DOC_LLM
SCHEMA_DOC_MD = (
    "## main\n\n"
    + CATALOG_DOC_LLM
    + (
        "\n\nDiscover source metadata before querying observations. Use named arguments to "
        "select upstream data and ORDER BY for deterministic output."
    )
)
CATALOG_TAGS = {
    "vgi.title": "Bureau of Labor Statistics",
    "vgi.subtitle": (
        "Retrieve labor, employment, price and productivity series with period codes, "
        "footnotes, calculations and survey metadata."
    ),
    "vgi.doc_llm": CATALOG_DOC_LLM,
    "vgi.doc_md": CATALOG_DOC_MD,
    "vgi.source_url": SOURCE_URL,
    "vgi.icon_url": "https://query.farm/media-kit/products/vgi/vgi-logo.png",
    "vgi.license": "0BSD",
    "vgi.author": "Query.Farm",
    "vgi.copyright": "Copyright 2026 Query.Farm contributors",
    "vgi.keywords": keywords_array("bls, economics, public data, time series, duckdb, sql"),
    "vgi.support_contact": "https://github.com/Query-farm/vgi-bls/issues",
    "vgi.support_policy_url": SOURCE_URL + "/blob/main/README.md",
    "vgi.executable_examples": json.dumps(
        [
            {
                "name": "discover_surveys",
                "description": "Discover available functions without credentials or API requests",
                "sql": (
                    "SELECT function_name FROM duckdb_functions() WHERE database_name = 'bls' AND "
                    "function_name = 'surveys' ORDER BY function_name"
                ),
                "expected_result": [{"function_name": "surveys"}],
            }
        ]
    ),
}


# Composed examples: each combines two of the worker's functions in one query.
# Each is listed on the function that drives it and on the schema-level list.
POPULAR_WITH_SURVEY_NAMES = FunctionExample(
    description=(
        "Label popular series with the name of the survey they belong to; a BLS series "
        "identifier starts with its two-letter survey abbreviation"
    ),
    sql=(
        "SELECT p.series_id, s.survey_abbreviation, s.survey_name "
        "FROM bls.main.popular_series() AS p "
        "JOIN bls.main.surveys() AS s ON s.survey_abbreviation = left(p.series_id, 2) "
        "ORDER BY p.series_id"
    ),
)
LATEST_VS_YEAR_EARLIER = FunctionExample(
    description=(
        "Compare the latest unemployment rate with the same period one year earlier and "
        "the change; the observations window (2023-2026) must include the year before the "
        "latest period, so move it forward as new years are published"
    ),
    sql=(
        "SELECT l.year, l.period, l.value AS latest_value, p.value AS year_earlier_value, "
        "l.value - p.value AS change "
        "FROM bls.main.latest('LNS14000000') AS l "
        "JOIN bls.main.observations(['LNS14000000'], 2023, 2026) AS p "
        "ON p.series_id = l.series_id AND p.period = l.period AND p.year = l.year - 1 "
        "ORDER BY l.year, l.period"
    ),
)
ANNUAL_AVERAGES_WITH_TITLES = FunctionExample(
    description=(
        "Label 2024 annual averages (period M13) with each series' published title and "
        "seasonality; BLS publishes annual averages for not seasonally adjusted series such as "
        "LNU04000000 and CUUR0000SA0, and they require a BLS registration key"
    ),
    sql=(
        "SELECT o.series_id, m.series_title, m.seasonality, o.value AS annual_average "
        "FROM bls.main.observations(['LNU04000000', 'CUUR0000SA0'], 2024, 2024, annual_average := true) AS o "
        "JOIN bls.main.series(['LNU04000000', 'CUUR0000SA0'], 2024, 2024) AS m USING (series_id) "
        "WHERE o.period = 'M13' "
        "ORDER BY o.series_id"
    ),
)

# Agent tasks that need two functions. Graders live in vgi-agent-tests.yaml.
COMPOSED_AGENT_TASKS = [
    {
        "name": "popular_series_per_survey",
        "prompt": (
            "Using bls.main.popular_series (overall, no survey argument) and bls.main.surveys, count the "
            "popular series in each survey, matching a series to its survey by the series identifier's "
            "two-letter prefix. Return columns survey_abbreviation (VARCHAR), survey_name (VARCHAR) and "
            "popular_count (BIGINT), one row per survey that has at least one popular series, ordered by "
            "survey_abbreviation."
        ),
    },
    {
        "name": "cpi_title_and_peak_2024",
        "prompt": (
            "For BLS series CUUR0000SA0 in 2024, return its published series title from bls.main.series "
            "together with the highest monthly value from bls.main.observations (monthly periods M01-M12 "
            "only; ignore NULL values). Return one row with columns series_id (VARCHAR), series_title "
            "(VARCHAR) and peak_value (DOUBLE)."
        ),
    },
]


def function_meta(
    name: str,
    title: str,
    description: str,
    schema: pa.Schema,
    sql: str,
    composed: Sequence[FunctionExample] = (),
) -> Any:
    """Build consistent, discoverable metadata for an authenticated table function.

    ``composed`` examples follow the function's own example, so they stay within
    the first five examples agents are shown.
    """
    types = {
        "string": "VARCHAR",
        "int64": "BIGINT",
        "double": "DOUBLE",
        "date32[day]": "DATE",
        "bool": "BOOLEAN",
        "timestamp[us, tz=UTC]": "TIMESTAMP WITH TIME ZONE",
    }
    docs = description + " Returns typed columns " + ", ".join(schema.names) + ". " + CATALOG_DOC_LLM

    class Meta:
        name: str
        description: str
        tags: dict[str, str]
        examples: list[FunctionExample]

    Meta.name = name
    Meta.description = description
    Meta.tags = {
        **object_tags(
            title=title,
            category="api",
            keywords=f"bls, economics, time series, {name}",
            doc_llm=docs,
            doc_md=f"## {name}\n\n{docs}",
        ),
        "vgi.result_columns_schema": json.dumps(
            [
                {
                    "name": f.name,
                    "type": types.get(str(f.type), str(f.type).upper()),
                    "description": (f.metadata or {}).get(b"comment", b"").decode(),
                }
                for f in schema
            ]
        ),
    }
    Meta.examples = [FunctionExample(sql=sql, description=title), *composed]
    return Meta
