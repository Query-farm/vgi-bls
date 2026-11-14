# vgi-bls

Query Bureau of Labor Statistics employment, inflation, wage and productivity
series from DuckDB through a Python VGI worker.

## Install and query

Python 3.13 or newer is required. Install a wheel from GitHub Releases or run
`make build` in the locked source checkout.

```sql
SET allow_extension_repositories = 'allowed';
CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm
    WITH PREFIX 'https://duckdb-extensions.query.farm';
INSTALL vgi FROM query_farm;
LOAD vgi FROM query_farm;
ATTACH 'bls' (TYPE vgi, LOCATION '/absolute/path/to/.venv/bin/vgi-bls-worker');

SELECT year, period, value, value_text
FROM bls.main.observations(['LNS14000000'], 2024, 2024)
ORDER BY year, period;

SELECT survey_abbreviation, survey_name
FROM bls.main.surveys() ORDER BY survey_abbreviation;
SELECT series_id FROM bls.main.popular_series(survey := 'CU') ORDER BY series_id;
```

Unregistered calls use BLS's basic limits: 25 series and 10 inclusive years per
request. Registered calls support 50 series and 20 years, and enable catalog
metadata, annual averages, calculations and aspects. The worker uses compatible
API v2 signatures for both modes; it does not split oversized requests or silently
truncate them. BLS documents daily limits of 25 unregistered and 500 registered
queries, plus 50 requests per 10 seconds. Limits are upstream policy and can change.

[Register with BLS](https://data.bls.gov/registrationEngine/) and supply the key
through a secret manager. The key must be renewed at least annually. This SQL
illustrates the secret's shape; do not commit real credentials:

```sql
CREATE SECRET bls_key (TYPE bls_api, API_KEY 'your-32-character-registration-key');
SELECT series_id, series_title, seasonality
FROM bls.main.series(['LNS14000000'], 2024, 2024) ORDER BY series_id;
```

A single applicable `bls_api` secret takes precedence over `BLS_API_KEY` in the
worker environment. An invalid explicit key does not fall back to another key.
Omit the environment variable for unregistered access; an explicitly empty or
malformed key fails. Environment files are not loaded automatically.

## Functions

All functions are in `bls.main`. Every function accepts `max_rows := 100000`
(1–1,000,000) as an explicit output cap. It does not reduce the API request size.
SQL NULL arguments are rejected.

| Function | Purpose |
| --- | --- |
| `observations(series_ids, start_year, end_year, annual_average := false, calculations := false, aspects := false, include_catalog := false)` | Batch series data; IDs are a VARCHAR array and years are inclusive |
| `series(series_ids, start_year, end_year)` | Registered source catalog metadata; BLS returns it alongside data |
| `surveys(survey := '')` | List surveys; a two-letter abbreviation requests that survey's capability flags |
| `popular_series(survey := '')` | Popular IDs overall or for one survey; not an exhaustive series catalog |
| `latest(series_id)` | Latest published observation for one series |

Series IDs must be unique uppercase identifiers containing only letters, digits,
underscores, dashes or `#`. Empty batches and oversized windows fail before an
upstream request. SQL WHERE clauses are local; select IDs and years in function
arguments to reduce upstream work.

```sql
SELECT series_id, year, period, value, footnotes_json
FROM bls.main.observations(['LNS14000000', 'CUUR0000SA0'], 2023, 2024,
    annual_average := true, include_catalog := true)
ORDER BY series_id, year, period;
```

### Data semantics and complete results

- `year` is BIGINT and `period` retains BLS's native code. `M13` is an annual
  average, not a thirteenth month. Annual, quarterly, semiannual and other native
  codes remain intact; the worker does not manufacture dates or publication times.
- `value` is DOUBLE in source units. `value_text` preserves exact published text
  for decimal conversion and missing-marker inspection. Recognized unavailable or
  suppressed markers become NULL; corrupt numeric text fails the query.
- Footnotes retain preliminary/revised annotations. `calculations_json`,
  `aspects_json` and `catalog_json` retain the corresponding source metadata when
  requested and available. Missing optional metadata becomes NULL.
- Empty data arrays return empty typed relations. Source order is not guaranteed;
  use ORDER BY for reproducible results. The worker validates series identities,
  year bounds and duplicate observation keys.
- A successful HTTP response can still contain a BLS error or warnings about
  truncated/partial results. **Any nonempty BLS message array fails the query.**
  Check series IDs, years, registration validity and quotas. The upstream message
  itself is not echoed because it may include credentials.
- Each call uses one bounded upstream request and one Arrow batch. There is no
  automatic cross-request batching, historical vintage reconstruction, cache,
  or atomic snapshot across separate calls.

## HTTP authentication and deployment

The same executable supports stdio, Unix sockets and HTTP. Configure the SDK's
bearer authentication for an HTTP service:

```sh
export VGI_BEARER_TOKENS="$(openssl rand -hex 32)=analyst"
vgi-bls-worker --http --host 127.0.0.1 --port 8000
```

Use a secret manager for persistent tokens. Tokens contain 32–4096 characters;
configure multiple principals as `token1=principal1,token2=principal2`. An explicitly
empty or malformed configuration fails startup without printing its contents.
Omitting the variable leaves HTTP unauthenticated for local development. Each
configured principal can access the entire catalog. `/health` remains public.
Terminate TLS at your service ingress and avoid exposing an unauthenticated port.

```sql
INSTALL httpfs FROM core;
LOAD httpfs;
ATTACH 'bls' (TYPE vgi, LOCATION 'https://your-worker.example',
              bearer_token 'your-worker-bearer-token');
CREATE SECRET bls_key (TYPE bls_api, API_KEY 'your-BLS-registration-key');
```

The worker bearer token and the BLS API key serve separate purposes. Bearer tokens
are never sent to BLS. API keys are resolved for each invocation and kept out of
continuation state. Errors omit upstream bodies, URLs and exception chains that
could expose query-string credentials. Worker stdout is reserved for Arrow RPC.

A nonroot Docker image is defined in `Dockerfile`; build with `docker build -t
vgi-bls .`. It starts HTTP on port 8000. Pass secrets through your deployment
platform. Use `docker run -i vgi-bls stdio` for containerized stdio.

## Operations

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `BLS_API_KEY` | unset | Fallback upstream API key |
| `BLS_TIMEOUT_SECONDS` | 30 | Per-request socket timeout, 0.1–120 seconds |
| `BLS_MAX_ATTEMPTS` | 4 | Total attempts per request, 1–6 |
| `BLS_RETRY_BUDGET_SECONDS` | 120 | Retry/read budget, 1–600 seconds |
| `BLS_MIN_INTERVAL_SECONDS` | 1.2 | Minimum spacing between request starts in one process |
| `BLS_MAX_RESPONSE_BYTES` | 16777216 | Maximum response size; 1 KiB–64 MiB |
| `BLS_API_BASE_URL` | `https://api.bls.gov/publicAPI/v2` | Operator-controlled HTTPS endpoint |
| `BLS_ALLOW_INSECURE_HTTP` | unset | Set to `1` only for loopback HTTP fixtures |

Network failures, HTTP 429 and HTTP 5xx are retried with bounded backoff and
jitter. `Retry-After` seconds and HTTP dates are honored; a wait beyond the budget
fails the query instead of retrying early. Redirects are rejected. Other HTTP
errors and BLS API errors fail immediately. The read budget is checked between
chunks; a blocking socket operation can extend wall time by its timeout.

Request spacing is process-local, not a global account quota. Coordinate
concurrency across replicas sharing a key. BLS applies daily account quotas and request-rate limits; registration must be renewed annually. There is no automatic persistent data cache. Configured API
base URLs are trusted operator settings; do not let untrusted users change them.

## Verification and releases

```sh
make check          # Ruff, strict mypy, pydoclint, pytest, SQL transports, metadata, stock DuckDB
make smoke-live     # Official public BLS checks; a registration key is optional
make package        # Wheel and source distribution
python3 ci/verify-artifacts.py --python 3.13
```

Deterministic tests use synthetic local data and a deliberately invalid public
registration key. Real-engine checks exercise array binding, annual averages,
registered SQL secrets, HTTP bearer authentication, empty data, partial-response
rejection and original source annotations. Starter assertions remain under
`examples/starter_samples` and run alongside the product suite.

CI covers Python 3.13/3.14, Ubuntu stdio/launch/HTTP, Windows stdio/HTTP, metadata
lint, stock DuckDB, both installed artifact formats, and Docker startup. Version
tags gate GitHub releases on CI and attach signed wheel/source artifacts and
build provenance. Nothing is published to PyPI. See `VERIFICATION.md` for results
and live-service limitations.

Starter: [Query-farm/vgi-starter-python](https://github.com/Query-farm/vgi-starter-python),
revision `9cca9105474d6e0052b8e343c8ea21f3d1a7503c`, template 1.3.0.
Runtime: `vgi-python>=0.51.1,<0.52`; the checked lock resolves vgi-python 0.51.1
and vgi-rpc 0.53.1. Engines: stock DuckDB 2.0.0 and Haybarn 2.0.0rc11.
Use `make template-diff` for compatible SDK updates.

Report issues through this repository's GitHub issue tracker. Software license:
BSD Zero Clause (`0BSD`), see `LICENSE`. Source data and footnotes remain
attributable to BLS; the software license does not replace source-data terms.

API references: [BLS signatures](https://www.bls.gov/developers/api_signature_v2.htm)
and [limits and registration FAQ](https://www.bls.gov/developers/api_faqs.htm).

Metadata checks run against local API fixtures so repeated lint runs do not consume public BLS quota. Live public API verification is a separate `make smoke-live` target.

## Notebooks

[Price levels and inflation](notebooks/README.md) — an executed public-data walkthrough that separates CPI levels from 12-month changes and tests the effect of changing the display reference month.
