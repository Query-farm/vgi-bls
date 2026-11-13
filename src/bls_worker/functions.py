"""BLS labor statistics with native period codes and explicit registration limits."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar
from urllib.parse import quote

import pyarrow as pa
from vgi.arguments import Arg
from vgi.invocation import BindResponse
from vgi.table_function import BindParams, OutputCollector, ProcessParams, TableFunctionGenerator, init_single_worker
from vgi_rpc import ArrowSerializableDataclass

from .api import SECRET_TYPE, call, credential, records
from .meta import column, function_meta
from .transport import ApiError

SURVEYS = pa.schema(
    [
        column("survey_abbreviation", pa.string(), "BLS survey abbreviation.", nullable=False),
        column("survey_name", pa.string(), "Published survey name."),
        column(
            "allows_net_change",
            pa.bool_(),
            "Whether the survey supports net-change calculations; NULL if not supplied.",
        ),
        column("allows_percent_change", pa.bool_(), "Whether percent changes are supported; NULL if not supplied."),
        column(
            "has_annual_averages", pa.bool_(), "Whether optional annual averages are supported; NULL if not supplied."
        ),
    ]
)
POPULAR = pa.schema(
    [
        column("series_id", pa.string(), "BLS series identifier to pass to observations.", nullable=False),
    ]
)
CATALOG = pa.schema(
    [
        column("series_id", pa.string(), "BLS series identifier.", nullable=False),
        column("series_title", pa.string(), "Full published description."),
        column("survey_name", pa.string(), "Survey name."),
        column("survey_abbreviation", pa.string(), "Survey abbreviation."),
        column("seasonality", pa.string(), "Seasonal adjustment description."),
        column("measure_data_type", pa.string(), "Published measurement type."),
        column("area", pa.string(), "Published geography."),
        column("catalog_json", pa.string(), "Complete source catalog, including survey-specific dimensions."),
    ]
)
OBSERVATIONS = pa.schema(
    [
        column("series_id", pa.string(), "BLS series identifier.", nullable=False),
        column("year", pa.int64(), "Year of the observation.", nullable=False),
        column(
            "period",
            pa.string(),
            "Native period code; M13 denotes an annual average, not a thirteenth month.",
            nullable=False,
        ),
        column("period_name", pa.string(), "Source period label."),
        column(
            "value", pa.float64(), "Published numeric value; recognized unavailable/suppressed markers become NULL."
        ),
        column(
            "value_text",
            pa.string(),
            "Original value text, preserving numeric precision and missing markers.",
            nullable=False,
        ),
        column("latest", pa.bool_(), "Source latest flag; NULL when BLS does not supply it."),
        column("footnotes_json", pa.string(), "Source footnotes including preliminary/revised flags."),
        column("calculations_json", pa.string(), "Net/percent changes when requested and available."),
        column("aspects_json", pa.string(), "Survey-specific aspects, such as relative standard errors."),
        column("catalog_json", pa.string(), "Source series catalog when include_catalog is enabled."),
    ]
)


@dataclass(frozen=True, kw_only=True)
class BaseArgs:
    """A cap on emitted rows; does not change the upstream request."""

    max_rows: Annotated[
        int,
        Arg(
            "max_rows",
            default=100000,
            ge=1,
            le=1000000,
            doc="Explicit cap on emitted rows; select series and years to reduce upstream work.",
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class SurveyArgs(BaseArgs):
    """Optional survey selection."""

    survey: Annotated[
        str,
        Arg(
            "survey",
            default="",
            pattern=r"^(?:[A-Z]{2})?$",
            doc="Optional two-letter survey abbreviation; empty requests all surveys or overall popular series.",
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class SeriesArgs(BaseArgs):
    """A bounded batch of series and an explicit year window."""

    series_ids: Annotated[
        list[str],
        Arg(
            0,
            arrow_type=pa.list_(pa.string()),
            doc="Nonempty unique BLS series identifiers; up to 50 registered or 25 unregistered.",
        ),
    ]
    start_year: Annotated[int, Arg(1, ge=1000, le=9999, doc="First observation year, inclusive.")]
    end_year: Annotated[
        int,
        Arg(
            2, ge=1000, le=9999, doc="Last observation year, inclusive; at most 20 registered or 10 unregistered years."
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class ObservationArgs(SeriesArgs):
    """Registered BLS optional fields, disabled by default."""

    annual_average: Annotated[
        bool,
        Arg(
            "annual_average", default=False, doc="Include annual averages where supported; requires a registration key."
        ),
    ]
    calculations: Annotated[
        bool, Arg("calculations", default=False, doc="Request source net/percent changes; requires a registration key.")
    ]
    aspects: Annotated[
        bool, Arg("aspects", default=False, doc="Request survey-specific aspects; requires a registration key.")
    ]
    include_catalog: Annotated[
        bool,
        Arg(
            "include_catalog", default=False, doc="Include source series catalog metadata; requires a registration key."
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class LatestArgs(BaseArgs):
    """A single series for the latest published observation."""

    series_id: Annotated[
        str, Arg(0, pattern=r"^[A-Z0-9_#-]{1,128}$", doc="BLS series identifier, such as LNS14000000.")
    ]


@dataclass(kw_only=True)
class State(ArrowSerializableDataclass):
    """Portable completion marker with no secrets or response data."""

    done: bool = False


def text(row: dict[str, Any], key: str, *, required: bool = False) -> str | None:
    """Read source strings without coercing objects or silently losing required fields."""
    value = row.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value):
        raise ApiError(f"BLS response has invalid {key}")
    return value


def flag(row: dict[str, Any], key: str) -> bool | None:
    """Decode BLS boolean strings or JSON booleans."""
    value = row.get(key)
    if value is None:
        return None
    if type(value) is bool:
        return bool(value)
    if isinstance(value, str) and value in ("true", "false"):
        return value == "true"
    raise ApiError(f"BLS response has invalid {key}")


def numeric(value: str) -> float | None:
    """Retain recognized missing markers as NULL; reject corrupt numeric data."""
    if value.strip() in {"-", "--", "NA", "N/A", "(S)", "(D)"}:
        return None
    try:
        if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value.strip()) is None:
            raise ValueError
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        return number
    except ValueError:
        raise ApiError("BLS response contains an unrecognized numeric value") from None


def validate_series(args: SeriesArgs) -> None:
    """Validate identifiers and the widest supported window before network access."""
    ids = args.series_ids
    if not 1 <= len(ids) <= 50 or any(
        not isinstance(s, str) or re.fullmatch(r"[A-Z0-9_#-]{1,128}", s) is None for s in ids
    ):
        raise ApiError("series_ids must contain 1-50 valid uppercase BLS identifiers")
    if len(set(ids)) != len(ids):
        raise ApiError("series_ids must be unique")
    if not 1 <= args.end_year - args.start_year + 1 <= 20:
        raise ApiError("Year range must be ordered and contain no more than 20 inclusive years")


def series_request(args: SeriesArgs, key: str | None, *, catalog: bool = False) -> list[dict[str, Any]]:
    """Enforce registration limits without splitting or silently truncating requests."""
    validate_series(args)
    options = {"catalog": catalog, "calculations": False, "annualaverage": False, "aspects": False}
    if isinstance(args, ObservationArgs):
        options.update(
            catalog=args.include_catalog,
            calculations=args.calculations,
            annualaverage=args.annual_average,
            aspects=args.aspects,
        )
    if key is None and (len(args.series_ids) > 25 or args.end_year - args.start_year + 1 > 10 or any(options.values())):
        raise ApiError(
            "BLS_API_KEY or a bls_api secret is required for these options; unregistered limits "
            "are 25 series and 10 years"
        )
    payload: dict[str, Any] = {
        "seriesid": args.series_ids,
        "startyear": str(args.start_year),
        "endyear": str(args.end_year),
        **options,
    }
    rows = [row for node in call("timeseries/data/", key, payload=payload) for row in records(node, "series")]
    returned = [text(row, "seriesID", required=True) for row in rows]
    if len(returned) != len(set(returned)) or set(returned) != set(args.series_ids):
        raise ApiError("BLS returned missing, duplicate or unexpected series; refusing a partial result")
    return rows


def observations(rows: list[dict[str, Any]], args: SeriesArgs | None = None) -> list[dict[str, Any]]:
    """Decode observations while preserving non-monthly periods and source annotations."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[str | None, int, str | None]] = set()
    for series in rows:
        series_id = text(series, "seriesID", required=True)
        catalog = series.get("catalog")
        if catalog is not None and not isinstance(catalog, dict):
            raise ApiError("BLS response has invalid catalog metadata")
        for row in records(series, "data"):
            year_text = text(row, "year", required=True)
            if year_text is None or re.fullmatch(r"[0-9]{4}", year_text) is None:
                raise ApiError("BLS response has invalid year")
            year = int(year_text)
            if args is not None and not args.start_year <= year <= args.end_year:
                raise ApiError("BLS returned an observation outside the requested years")
            period = text(row, "period", required=True)
            if period is None or re.fullmatch(r"[A-Z][0-9]{2}", period) is None:
                raise ApiError("BLS response has invalid period")
            identity = (series_id, year, period)
            if identity in seen:
                raise ApiError("BLS returned a duplicate observation")
            seen.add(identity)
            value = text(row, "value", required=True)
            assert value is not None
            notes = records({"footnotes": row.get("footnotes", [])}, "footnotes")
            calculations = row.get("calculations")
            aspects = row.get("aspects")
            if calculations is not None and not isinstance(calculations, dict):
                raise ApiError("BLS response has invalid calculations")
            if aspects is not None and not isinstance(aspects, list):
                raise ApiError("BLS response has invalid aspects")
            result.append(
                {
                    "series_id": series_id,
                    "year": year,
                    "period": period,
                    "period_name": text(row, "periodName"),
                    "value": numeric(value),
                    "value_text": value,
                    "latest": flag(row, "latest"),
                    "footnotes_json": json.dumps(notes, ensure_ascii=False),
                    "calculations_json": json.dumps(calculations) if calculations is not None else None,
                    "aspects_json": json.dumps(aspects) if aspects is not None else None,
                    "catalog_json": json.dumps(catalog, ensure_ascii=False) if catalog is not None else None,
                }
            )
    return result


@init_single_worker
class BlsFunction[T: BaseArgs](TableFunctionGenerator[T, State]):
    """One bounded BLS request per invocation."""

    FIXED_SCHEMA: ClassVar[pa.Schema]

    @classmethod
    def on_bind(cls, params: BindParams[T]) -> BindResponse:
        """Validate identifiers and declare the optional upstream secret."""
        if isinstance(params.args, SeriesArgs):
            validate_series(params.args)
        params.secrets.get(SECRET_TYPE)
        return BindResponse(output_schema=cls.FIXED_SCHEMA)

    @classmethod
    def initial_state(cls, params: ProcessParams[T]) -> State:
        """Create credential-free completion state."""
        return State()

    @classmethod
    def fetch(cls, args: T, key: str | None) -> list[dict[str, Any]]:
        """Implement a concrete BLS request."""
        raise NotImplementedError

    @classmethod
    def process(cls, params: ProcessParams[T], state: State, out: OutputCollector) -> None:
        """Emit a validated typed response without retaining its credential."""
        if state.done:
            out.finish()
            return
        rows = cls.fetch(params.args, credential(params))[: params.args.max_rows]
        try:
            batch = pa.RecordBatch.from_pylist(rows, schema=params.output_schema)
        except (pa.ArrowException, ValueError, OverflowError):
            raise ApiError("BLS response cannot be represented by the declared schema") from None
        state.done = True
        out.emit(batch)


class Surveys(BlsFunction[SurveyArgs]):
    """Discover surveys and supported calculation capabilities."""

    FIXED_SCHEMA = SURVEYS
    Meta = function_meta(
        "surveys",
        "Discover BLS surveys",
        "List BLS surveys or request capability flags for a specific survey using the optional survey argument.",
        SURVEYS,
        "SELECT survey_abbreviation, survey_name FROM bls.main.surveys() ORDER BY survey_abbreviation",
    )

    @classmethod
    def fetch(cls, args: SurveyArgs, key: str | None) -> list[dict[str, Any]]:
        """Call all-surveys or single-survey discovery."""
        return [
            {
                "survey_abbreviation": text(row, "survey_abbreviation", required=True),
                "survey_name": text(row, "survey_name"),
                "allows_net_change": flag(row, "allowsNetChange"),
                "allows_percent_change": flag(row, "allowsPercentChange"),
                "has_annual_averages": flag(row, "hasAnnualAverages"),
            }
            for node in call("surveys" + ("/" + args.survey if args.survey else ""), key)
            for row in records(node, "survey")
        ]


class PopularSeries(BlsFunction[SurveyArgs]):
    """Discover a bounded list of popular series identifiers."""

    FIXED_SCHEMA = POPULAR
    Meta = function_meta(
        "popular_series",
        "Discover popular series",
        (
            "List popular series identifiers overall or for a specific survey. Only series_id is returned; "
            "titles are not included. Call series() with registration to retrieve titles and descriptions. "
            "This is not an exhaustive series catalog."
        ),
        POPULAR,
        "SELECT series_id FROM bls.main.popular_series(survey := 'CU') ORDER BY series_id",
    )

    @classmethod
    def fetch(cls, args: SurveyArgs, key: str | None) -> list[dict[str, Any]]:
        """Call the popular-series endpoint with an optional survey filter."""
        return [
            {"series_id": text(row, "seriesID", required=True)}
            for node in call("timeseries/popular", key, query={"survey": args.survey} if args.survey else {})
            for row in records(node, "series")
        ]


class Observations(BlsFunction[ObservationArgs]):
    """Retrieve an explicit batch of time series over an inclusive year window."""

    FIXED_SCHEMA = OBSERVATIONS
    Meta = function_meta(
        "observations",
        "Retrieve labor statistics",
        (
            "Retrieve observations for a batch of unique series IDs and inclusive start/end "
            "years. Registered access supports 50 series and 20 years; unregistered access "
            "supports 25 and 10. Optional annual averages, catalog, calculations and aspects "
            "require registration. BLS warnings fail the query to prevent partial results."
        ),
        OBSERVATIONS,
        ("SELECT year, period, value FROM bls.main.observations(['LNS14000000'], 2024, 2024) ORDER BY year, period"),
    )

    @classmethod
    def fetch(cls, args: ObservationArgs, key: str | None) -> list[dict[str, Any]]:
        """Send a single batch POST and decode native period observations."""
        return observations(series_request(args, key), args)


class Series(BlsFunction[SeriesArgs]):
    """Retrieve registered series catalog metadata."""

    FIXED_SCHEMA = CATALOG
    Meta = function_meta(
        "series",
        "Inspect series metadata",
        (
            "Retrieve source catalog metadata for series IDs and an explicit year window. "
            "Requires BLS registration. The BLS API returns catalog metadata alongside "
            "observations; this function emits only the catalog."
        ),
        CATALOG,
        (
            "SELECT series_id, series_title, seasonality FROM bls.main.series(['LNS14000000'], "
            "2024, 2024) ORDER BY series_id"
        ),
    )

    @classmethod
    def fetch(cls, args: SeriesArgs, key: str | None) -> list[dict[str, Any]]:
        """Request catalog=true and require catalog metadata for every requested series."""
        result = []
        for row in series_request(args, key, catalog=True):
            catalog = row.get("catalog")
            if not isinstance(catalog, dict):
                raise ApiError("BLS did not return requested catalog metadata; check registration and series support")
            result.append(
                {
                    "series_id": text(row, "seriesID", required=True),
                    **{
                        name: text(catalog, name) for name in CATALOG.names if name not in {"series_id", "catalog_json"}
                    },
                    "catalog_json": json.dumps(catalog, ensure_ascii=False),
                }
            )
        return result


class Latest(BlsFunction[LatestArgs]):
    """Retrieve the latest published observation for one series."""

    FIXED_SCHEMA = OBSERVATIONS
    Meta = function_meta(
        "latest",
        "Read latest observation",
        (
            "Retrieve BLS's latest observation for a single series. Latest refers to observation "
            "period, not a real-time publication timestamp; use observations for reproducible "
            "year windows."
        ),
        OBSERVATIONS,
        "SELECT series_id, year, period, value FROM bls.main.latest('LNS14000000') ORDER BY year, period",
    )

    @classmethod
    def fetch(cls, args: LatestArgs, key: str | None) -> list[dict[str, Any]]:
        """Use the latest=true signature and validate returned series identity."""
        rows = [
            row
            for node in call("timeseries/data/" + quote(args.series_id, safe=""), key, query={"latest": "true"})
            for row in records(node, "series")
        ]
        if len(rows) != 1 or text(rows[0], "seriesID", required=True) != args.series_id:
            raise ApiError("BLS returned an unexpected series for latest")
        return observations(rows)


FUNCTIONS: list[type[Any]] = [Surveys, PopularSeries, Observations, Series, Latest]
