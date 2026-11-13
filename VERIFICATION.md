# Release verification

Verified on 2026-11-13 on macOS arm64. Python 3.14.4 development environment;
installed wheel and source distribution also verified with Python 3.13.16.

- Starter: Query-farm/vgi-starter-python, revision `9cca9105474d6e0052b8e343c8ea21f3d1a7503c`.
  Its original baseline passed before adaptation; sample assertions remain in the suite.
- Runtime: vgi-python 0.51.1, vgi-rpc 0.53.1; starter SDK constraints retained.
- `make check`: Ruff formatting/lint, strict mypy, pydoclint, 90 pytest tests;
  Haybarn 2.0.0rc11 SQL checks over stdio (133 assertions), launch (133), HTTP (97), zero skips.
- Metadata: vgi-lint at `fail_on = "info"`, no waivers, 100/100, L2, zero findings.
  Behavioral probes run against local fixtures, independently of BLS daily quotas.
- Stock DuckDB 2.0.0 with signed Query.Farm VGI extension `e75482339d`:
  21 stdio checks and 24 authenticated HTTP checks. Checks include SQL array binding,
  public versus registered access, secret precedence, invalid/missing bearer rejection,
  preserved M13 annual averages and missing values, calculation metadata, and partial-response rejection.
- `make smoke-live`: three checks passed against the official public BLS API:
  survey discovery, 12 monthly unemployment observations in 2024, and popular series.
  No operator BLS registration key was available; registered live access is not claimed.
- Wheel and sdist: `uv build`, `twine check`, and isolated installs outside this checkout;
  both artifact types repeat the 21 stdio and 24 HTTP authentication checks.
- Optional documentation review with fixtures: zero findings. The optional L3 agent
  evaluation could not complete because its external agent backend rejected the evaluation
  prompt; no L3 assurance is claimed.

Reports are written to ignored `build/` files. CI repeats quality checks on Python 3.13
and 3.14, SQL transport checks on Linux/Windows, stock DuckDB, installed artifacts,
and Docker health checks. Release artifacts are gated by that workflow, signed with
Sigstore, and the wheel has a GitHub provenance attestation.
