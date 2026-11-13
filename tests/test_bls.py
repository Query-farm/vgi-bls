"""BLS contracts through real worker processes, using deterministic local data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from vgi.arguments import Arguments
from vgi.client import Client
from vgi.invocation import BindResponse

from bls_worker.api import validate_key
from bls_worker.auth import validate_http_auth
from bls_worker.functions import State, numeric
from bls_worker.transport import ApiError
from test.mock_api.api_server import API_KEY, ApiServer


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[ApiServer]:
    server = ApiServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("BLS_API_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("BLS_ALLOW_INSECURE_HTTP", "1")
    monkeypatch.setenv("BLS_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("BLS_API_KEY", API_KEY)
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def bls_client(api: ApiServer) -> Iterator[Client]:
    with Client([sys.executable, "-m", "bls_worker.worker"], pool=None) as client:
        yield client


def table(client: Client, name: str, *positional: Any, secret_key: str | None = None, **named: Any) -> pa.Table:
    bound: list[BindResponse] = []
    batches = list(
        client.table_function(
            function_name=name,
            schema_path=["main"],
            secrets={"fixture": {"type": "bls_api", "api_key": secret_key}},
            bind_result_callback=bound.append,
            arguments=Arguments(
                positional=tuple(pa.scalar(v) for v in positional), named={k: pa.scalar(v) for k, v in named.items()}
            ),
        )
    )
    return pa.Table.from_batches(batches, schema=bound[-1].output_schema)


def test_discovery(bls_client: Client, api: ApiServer) -> None:
    assert table(bls_client, "surveys")["survey_abbreviation"].to_pylist() == ["CU", "LN"]
    survey = table(bls_client, "surveys", survey="CU")
    assert survey["allows_percent_change"].to_pylist() == [True]
    assert survey["allows_net_change"].to_pylist() == [False]
    assert table(bls_client, "popular_series", survey="CU")["series_id"].to_pylist() == ["CUUR0000SA0"]
    assert api.requests[-1]["query"]["survey"] == "CU"


def test_batch_preserves_periods_suppression_and_annotations(bls_client: Client, api: ApiServer) -> None:
    result = table(
        bls_client,
        "observations",
        ["LNS14000000", "CUUR0000SA0"],
        2024,
        2024,
        annual_average=True,
        calculations=True,
        aspects=True,
        include_catalog=True,
    )
    assert result.num_rows == 8
    assert result["value"].to_pylist() == [2.5, None, 3.75, 3.25] * 2
    assert result["period"].to_pylist() == ["M01", "M02", "M12", "M13"] * 2
    assert result["value_text"][0].as_py() == "2.50"
    assert json.loads(result["footnotes_json"][0].as_py())[0]["code"] == "P"
    assert json.loads(result["calculations_json"][0].as_py())["pct_changes"]["1"] == "0.2"
    assert json.loads(result["aspects_json"][0].as_py())[0]["name"] == "Relative Standard Error"
    assert json.loads(result["catalog_json"][0].as_py())["seasonality"] == "Seasonally Adjusted"
    assert api.requests[-1]["payload"]["registrationkey"] == API_KEY
    assert "registrationkey" not in api.requests[-1]["query"]


def test_series_latest_empty_and_list_envelopes(bls_client: Client) -> None:
    assert table(bls_client, "series", ["LNS14000000"], 2024, 2024)["series_title"].to_pylist() == [
        "Fixture LNS14000000"
    ]
    assert table(bls_client, "latest", "LNS14000000")["value"].to_pylist() == [3.75]
    assert table(bls_client, "observations", ["EMPTY"], 2024, 2024).num_rows == 0
    assert table(bls_client, "observations", ["LIST"], 2024, 2024).num_rows == 3
    assert table(bls_client, "observations", ["LNS14000000"], 2024, 2024, max_rows=1).num_rows == 1


def test_unregistered_access_and_limits(api: ApiServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLS_API_KEY")
    with Client([sys.executable, "-m", "bls_worker.worker"], pool=None) as client:
        assert table(client, "observations", ["LNS14000000"], 2020, 2024).num_rows == 3
        assert api.requests[-1]["key"] is None
        count = len(api.requests)
        for options in ({"annual_average": True}, {"include_catalog": True}, {"calculations": True}, {"aspects": True}):
            named: dict[str, Any] = options
            with pytest.raises(Exception, match="BLS_API_KEY"):
                table(client, "observations", ["LNS14000000"], 2024, 2024, **named)
        with pytest.raises(Exception, match="BLS_API_KEY"):
            table(client, "observations", ["LNS14000000"], 2000, 2010)
        with pytest.raises(Exception, match="BLS_API_KEY"):
            table(client, "observations", [f"SERIES{i}" for i in range(26)], 2024, 2024)
        with pytest.raises(Exception, match="BLS_API_KEY"):
            table(client, "series", ["LNS14000000"], 2024, 2024)
        assert len(api.requests) == count


def test_secret_precedes_environment(api: ApiServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLS_API_KEY", "f" * 32)
    with Client([sys.executable, "-m", "bls_worker.worker"], pool=None) as client:
        assert table(client, "observations", ["LNS14000000"], 2024, 2024, secret_key=API_KEY).num_rows == 3
    assert api.requests[-1]["key"] == API_KEY


@pytest.mark.parametrize("identifier", ["WARNING", "ERROR", "MISSING", "BADVALUE", "OUTSIDE", "DUPLICATE"])
def test_incomplete_and_corrupt_responses_fail_safely(bls_client: Client, identifier: str) -> None:
    with pytest.raises(Exception) as error:
        table(bls_client, "observations", [identifier], 2024, 2024)
    assert API_KEY not in str(error.value)


@pytest.mark.parametrize(
    "ids,first,last",
    [
        (["lowercase"], 2024, 2024),
        (["A", "A"], 2024, 2024),
        (["A"], 2024, 2023),
        (["A"], 2000, 2020),
        ([f"A{i}" for i in range(51)], 2024, 2024),
    ],
)
def test_invalid_arguments_make_no_request(
    bls_client: Client, api: ApiServer, ids: list[str], first: int, last: int
) -> None:
    with pytest.raises(Exception, match=r"series_ids|Year range"):
        table(bls_client, "observations", ids, first, last)
    assert not api.requests


def test_max_registered_batch_and_window(bls_client: Client, api: ApiServer) -> None:
    assert table(bls_client, "observations", [f"A{i}" for i in range(50)], 2000, 2019).num_rows == 150
    assert len(api.requests) == 1


def test_rate_limit_retry_and_encoded_series_identifier(bls_client: Client, api: ApiServer) -> None:
    api.mode = "RETRY"
    assert table(bls_client, "latest", "TEST#ID").num_rows == 1
    assert api.attempts == 2
    assert api.requests[-1]["endpoint"].endswith("TEST#ID")


@pytest.mark.parametrize("value", ["", "secret", "x" * 31, "x" * 33])
def test_invalid_registration_key(value: str) -> None:
    with pytest.raises(ApiError):
        validate_key(value)
    assert validate_key(None) is None


@pytest.mark.parametrize("value,expected", [("2.50", 2.5), ("-1.2", -1.2), (".5", 0.5), ("-", None), ("(S)", None)])
def test_numeric_semantics(value: str, expected: float | None) -> None:
    assert numeric(value) == expected


def test_portable_state_has_no_credentials() -> None:
    state = State(done=True)
    encoded = state.serialize_to_bytes()
    assert State.deserialize_from_bytes(encoded) == state
    assert API_KEY.encode() not in encoded


WORKER_TOKEN = "fixture-only-worker-token-with-32-characters"


@pytest.fixture()
def bls_http(api: ApiServer, tmp_path: Path) -> Iterator[str]:
    port_file = tmp_path / "port"
    environment = os.environ.copy()
    environment.update(VGI_BEARER_TOKENS=f"{WORKER_TOKEN}=analyst", VGI_PROXY_PROOF_MODE="off")
    environment.pop("VGI_JWT_ISSUER", None)
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "bls_worker.worker", "--http", "--port", "0", "--port-file", str(port_file)],
            stdout=log,
            stderr=log,
            env=environment,
        )
        try:
            deadline = time.monotonic() + 20
            while not port_file.exists():
                assert process.poll() is None, "HTTP worker exited before publishing its port"
                if time.monotonic() >= deadline:
                    pytest.fail("HTTP worker did not start within 20 seconds")
                time.sleep(0.05)
            location = "http://127.0.0.1:" + port_file.read_text().strip()
            while True:
                try:
                    with urllib.request.urlopen(location + "/health", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("HTTP worker did not become ready")
                    time.sleep(0.05)
            yield location
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.seek(0)
            messages = log.read()
            assert WORKER_TOKEN not in messages
            assert API_KEY not in messages


@pytest.mark.parametrize("token", [None, "incorrect-token", API_KEY])
def test_http_rpc_rejects_missing_or_wrong_bearer(bls_http: str, api: ApiServer, token: str | None) -> None:
    headers = {"Authorization": "Bearer " + token} if token else {}
    request = urllib.request.Request(bls_http + "/__describe__", headers=headers)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 401
    exc.value.close()
    with (
        pytest.raises(Exception, match="authentication rejected"),
        Client(transport="http", base_url=bls_http, bearer_token=token, pool=None) as client,
    ):
        table(client, "observations", ["LNS14000000"], 2024, 2024)
    assert api.requests == []


def test_http_bearer_authenticated_pages_and_secrets(bls_http: str, api: ApiServer) -> None:
    with Client(transport="http", base_url=bls_http, bearer_token=WORKER_TOKEN, pool=None) as client:
        result = table(client, "observations", ["LNS14000000"], 2024, 2024, secret_key=API_KEY)
    assert result.num_rows == 3
    assert len(api.requests) == 1
    assert all(query["key"] == API_KEY for query in api.requests)
    assert all(WORKER_TOKEN not in str(query) for query in api.requests)


def test_http_health_remains_available_without_bearer(bls_http: str) -> None:
    with urllib.request.urlopen(bls_http + "/health", timeout=5) as response:
        assert response.status == 200


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        ",",
        WORKER_TOKEN,
        "=analyst",
        f"{WORKER_TOKEN}=",
        "short=analyst",
        f"{WORKER_TOKEN}=analyst,",
        f"{WORKER_TOKEN}=analyst,{WORKER_TOKEN}=other",
    ],
)
def test_bad_bearer_configuration_fails_without_exposing_token(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VGI_BEARER_TOKENS", value)
    with pytest.raises(ValueError, match="Invalid VGI_BEARER_TOKENS") as exc:
        validate_http_auth()
    assert WORKER_TOKEN not in str(exc.value)


def test_bearer_configuration_accepts_multiple_principals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VGI_BEARER_TOKENS", f"{WORKER_TOKEN}=analyst,{WORKER_TOKEN}-second=service")
    validate_http_auth()
    monkeypatch.delenv("VGI_BEARER_TOKENS")
    validate_http_auth()


def test_bad_bearer_configuration_stops_http_startup(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["VGI_BEARER_TOKENS"] = WORKER_TOKEN
    port_file = tmp_path / "port"
    result = subprocess.run(
        [sys.executable, "-m", "bls_worker.worker", "--http", "--port-file", str(port_file)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Invalid VGI_BEARER_TOKENS" in result.stderr
    assert WORKER_TOKEN not in result.stderr + result.stdout
    assert not port_file.exists()
