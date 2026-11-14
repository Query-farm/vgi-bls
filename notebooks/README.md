# BLS notebooks

[01 · Can inflation slow while the price index stays elevated?](01_price_levels_and_inflation.ipynb) compares rebased CPI-U price levels with 12-month rates for all items and all items less food and energy. Both series are U.S. city average, not seasonally adjusted, official index 1982–84=100. The experiment changes only the displayed reference month 2019-12→2021-12.

## Run

Requirements: [uv](https://docs.astral.sh/uv/), Git, Python 3.13 or newer (uv can install it) and internet access to GitHub/package sources and the official BLS API. No local worker checkout is needed.

```sh
cd notebooks
uv sync --locked
uv run --locked python execute.py
```

The notebook environment locks Haybarn 2.0.0rc11 and plotting/Jupyter dependencies. Its literal launch is `uvx --from git+https://github.com/Query-farm/vgi-bls@v0.1.0 --with vgi-python==0.51.1 --with vgi-rpc==0.53.1 vgi-bls-worker`. The annotated tag resolves to source commit `d3a776e26b5d971909393fedf651b668e6f298cb`. The worker is installed in uv's cache, not discovered in a local checkout. Upgrade the pin deliberately and re-execute both runs.

Optional editor kernel:

```sh
uv run --locked python -m ipykernel install --user --name vgi-bls-notebooks --display-name "Python (vgi-bls notebooks)"
```

Use this directory as working directory and Restart Kernel → Run All. The runner registers a temporary kernel and preserves the previous successful notebook on failure.

## Separate experiment

```sh
uv run --locked python execute.py --parameters '{"BASE_MONTH":"2021-12-01"}' --output-dir artifacts/base-2021-12
```

Overrides require separate output, with branding, docs, runner and environment copied. Restore `2019-12-01` for defaults. Artifacts, logs and environments are ignored. Compare source fingerprints first: each execution makes a fresh public request, so input revisions must not be confused with rebasing effects.

## Data and interpretation

One bounded worker call requests two series for inclusive years 2019–2024 and materializes the returned rows once. The notebook displays 2020–2024, using 2019 as prior-year coverage. It explicitly selects the official public API and clears inherited registration/insecure-fixture settings. No registration key is required for the requested features; no synthetic or replay fallback is used. BLS availability and quota failures stop execution without replacing saved outputs. `max_rows` is an output guard, not a request bound.

Source pages: [all items](https://data.bls.gov/timeseries/CUUR0000SA0), [less food and energy](https://data.bls.gov/timeseries/CUUR0000SA0L1E), [CPI series IDs](https://www.bls.gov/cpi/factsheets/cpi-series-ids.htm), [CPI FAQ](https://www.bls.gov/cpi/questions-and-answers.htm), [API documentation](https://www.bls.gov/developers/). The notebook records UTC retrieval bounds, original value text and footnotes. Monthly dates label observation periods, not publication dates. M13 and other native nonmonthly codes are excluded from the monthly calendar rather than fabricated into dates.

Missing periods/values remain NULL and create chart gaps. Annual comparisons join the same calendar month a year earlier. Rebasing changes displayed levels but must preserve proportional changes; an invariant checks that equality. These aggregate indexes do not describe every household, identify causes, forecast future outcomes or reconstruct historical release vintages.
