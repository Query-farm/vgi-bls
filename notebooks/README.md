# Bureau of Labor Statistics notebooks

Read saved results on GitHub or choose **Open in Colab** at the top of each notebook.

| Notebook | Data and question | Default → experiment |
|---|---|---|
| [01 · Price levels and inflation](01_price_levels_and_inflation.ipynb) | Derived official CPI-U recording (optional live): can inflation slow while the price index remains elevated? | BASE_MONTH December 2019 → December 2021 |
| [02 · Popular does not mean comparable](02_popular_does_not_mean_comparable.ipynb) | Explicit synthetic discovery: do metadata and latest observation periods support a comparison? | MAX_LAG_MONTHS 0 → 1 |

01 retains the original analytical SQL and substantive CPI narrative, now with explicit recorded/live input selection. 02 is a separate synthetic teaching fixture. Neither path silently falls back from live access. Its five fabricated IDs, metadata and values are never presented as official BLS estimates.

## Run from this directory

Install [uv](https://docs.astral.sh/uv/). Initial packages, verified companions, VGI and the worker require internet; notebook 01 contacts the official public BLS API only when DATA_MODE is explicitly live. The shared base supports Python 3.11–3.14 kernels; uv provisions the separate Python 3.13+ worker environment. No source checkout or local worker build is needed.

```sh
uv sync --locked --extra runner
uv run --locked --extra runner vgi-notebooks
uv run --locked --extra runner vgi-notebooks 01_price_levels_and_inflation.ipynb --parameters '{"BASE_MONTH":"2021-12-01"}' --output-dir validation/base-2021-12
uv run --locked --extra runner vgi-notebooks 02_popular_does_not_mean_comparable.ipynb --parameters '{"MAX_LAG_MONTHS":1}' --output-dir validation/lag-one
```

For interactive Jupyter use this environment's kernel and this directory as cwd. Restart and Run All after changing parameters. Colab installs only the base shared package, preserving compatible preloaded plotting/data/kernel libraries. `[runner]` is for local publication. Shared tools supply canonical branding, isolated SQL sessions, SVG output, verified companions and atomic execution; overrides require a separate directory and failed execution preserves the last successful output. Hosted Colab itself has not been tested; fresh preloaded Python 3.12 notebook-only execution verifies the common setup path.

Both notebooks visibly launch the released worker:

```sql
INSTALL vgi FROM community;
LOAD vgi;
ATTACH 'bls' (TYPE vgi,
 LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-bls@v0.1.0 vgi-bls-worker');
```

The release resolves to `d3a776e26b5d971909393fedf651b668e6f298cb`; reviewed current source is `1b7045156ef1ff435fcb5db1aa8c02b497517b02`. Actual runtime catalogs contain the same five signatures and no tables/views. Current source adds composed metadata examples; runtime implementation and dependency declarations are unchanged. No `--with` overrides are needed. Worker dependency ranges remain owned by the release; notebook dependencies are separately locked.

## Derived public recording, live CPI and interpretation

01 defaults to DATA_MODE="recorded": one bounded local batch request for two series over inclusive years 2019–2024, retaining 2019 for prior-year comparisons and displaying 2020–2024. [cpi-observations-derived.json](fixtures/cpi-observations-derived.json) contains the complete 144-row decoded observation audit from a successful official live run at 2026-11-14T23:11:31.963614+00:00 through 23:11:32.468574+00:00. **This is a derived recording, not original HTTP bytes.** It preserves identifiers, years, native periods, original numeric text and normalized footnote JSON. The helper reconstructs the required API envelope; original period labels/latest flags were not retained and remain NULL rather than invented. The unchanged worker decodes numeric values from the retained source text. Both source fingerprints and all 120 displayed numeric values were verified against the successful live audit.

A later official rerun returned REQUEST_NOT_PROCESSED because the unregistered daily request threshold had been reached. This limitation is recorded explicitly; no additional live retries were made after diagnosis. No registration key is needed for the bounded basic options, but unregistered limits still apply. Optional live mode selects the official HTTPS endpoint, clears inherited BLS registration/fixture settings and creates no SQL secret:

```sh
uv run --locked --extra runner vgi-notebooks 01_price_levels_and_inflation.ipynb --parameters '{"DATA_MODE":"live"}' --output-dir validation/live
```

Live network/quota failures stop execution without substituting recorded/synthetic data or overwriting saved results. `max_rows` is only an output guard; explicit IDs/years bound upstream work. Source fingerprints are identical for the fixed-recording experiment; in live mode compare them to distinguish rebasing from revised inputs. Execution timestamps identify the current run, while the recording preserves its actual original acquisition interval.

The [all-items series](https://data.bls.gov/timeseries/CUUR0000SA0) and [less-food-and-energy series](https://data.bls.gov/timeseries/CUUR0000SA0L1E) are CPI-U, U.S. city average, not seasonally adjusted, index 1982–84=100. See [CPI IDs](https://www.bls.gov/cpi/factsheets/cpi-series-ids.htm), [CPI FAQ](https://www.bls.gov/cpi/questions-and-answers.htm) and [API documentation](https://www.bls.gov/developers/). UTC retrieval bounds, original value text and footnotes remain visible. Observation periods are not release timestamps; M13 is not a thirteenth month.

Missing months/values remain NULL; rates join the same calendar month a year earlier. Rebasing changes displayed levels while preserving proportional changes, independently checked. These aggregate indexes are not every household's expenses, causal explanations, forecasts or historical release vintages.

## Synthetic discovery and credentials

02 uses [discovery.json](fixtures/discovery.json), entirely authored synthetic input with explicit provenance. The unchanged released worker calls `surveys`, `popular_series`, registered `series`, and `latest` against `bls_example.py`'s ephemeral loopback server. The helper validates exact methods/paths/options, accepts only the fixed fake key, makes no upstream requests, and audits exactly eight calls without credential values. One POST retrieves all five catalogs; each of five latest reads is materialized before local display. Unknown requests are rejected. The helper restores inherited BLS settings at cleanup.

Five popular candidates become two measurement-compatible series after explicit survey/geography/adjustment/unit tests. Allowing a one-month lag admits the delayed series without relabeling its November period. Unknown survey capability is retained as NULL; it is not a false value. A popular list is not exhaustive, a common latest flag does not synchronize periods, and equal units alone do not establish comparability. No synthetic values are used to infer real inflation or labor-market conditions.

Both notebooks call shared `connect(globals())` before ATTACH: an empty temporary SQL-secret directory, persistence disabled, and JupySQL default-connection autoload blocked. 02 creates only an explicit **TEMPORARY** fake `bls_api` secret, verifies temporary-only inventory, drops it, rechecks empty inventory, and closes the connection/store/server. 01 creates no secret in either mode. Existing user SQL secrets are never read or changed.

To adapt registered catalog reads to real BLS, create a separate live copy: use `BLSSource("live")`, replace synthetic identifiers with chosen real IDs/year bounds, and supply **your own** valid 32-character registration key with a temporary `bls_api` secret or `BLS_API_KEY` after the helper's environment reset. Keep the empty SQL-secret configuration; do not reuse the fake fixture key. Request credentials interactively without echoing/saving them, and retain cleanup. Registered catalog availability and optional features were tested only against the synthetic service here; live notebook 01 validates unregistered observations separately. Never send fixture IDs or the fake key to the official endpoint.

## Coverage and validation

`coverage.json` records complete actual release/current catalog exports, separate runtime identities, final raw-JSON execution receipts and reviewed narrative mappings. All **five named surfaces** have meaningful SQL/result interpretation across the two stories. Registration requirements remain in the denominator; local fixture evidence is explicitly distinguished from live official access.

Defaults and both experiments execute with reviewed SVG figures; one fresh notebook-only/preloaded run exercises the shared release/companion path. Saved GitHub image links are checked in the actual renderer. Validation logs, review PNGs and experiment copies are ignored. The [shared standard](https://github.com/Query-farm/vgi-example-notebooks/blob/main/docs/standard.md) replaces duplicated runner/standard files. Shared tools are pinned to **v0.3.33**; registry `bls` verifies four companions at public commit `55d970b5d51d931c8e75ae7925483e08df76491b`.

Existing green source CI [43613554187](https://github.com/Query-farm/vgi-bls/actions/runs/43613554187) is reused, with actual released-worker SQL and focused notebook-helper checks. No worker source, root lock, source build/full test suite, engine build, deployment or container publication is part of this notebook-only update.
