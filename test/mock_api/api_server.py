"""Synthetic BLS response fixtures; no requests reach the live API."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

API_KEY = "0" * 32  # Public, deliberately invalid test credential.


class ApiServer(ThreadingHTTPServer):
    """Local BLS-shaped API with inspectable requests."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests: list[dict[str, Any]] = []
        self.mode = ""
        self.attempts = 0


class Handler(BaseHTTPRequestHandler):
    """Serve GET discovery and POST data without logging credentials."""

    server: ApiServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def send_json(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.respond(payload)

    def do_GET(self) -> None:
        self.respond(None)

    def respond(self, payload: dict[str, Any] | None) -> None:
        url = urlsplit(self.path)
        endpoint = unquote(url.path).strip("/")
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        key = (payload or query).get("registrationkey")
        self.server.requests.append({"endpoint": endpoint, "query": query, "payload": payload, "key": key})
        self.server.attempts += 1
        if key is not None and key != API_KEY:
            self.send_json(200, {"status": "REQUEST_NOT_PROCESSED", "message": ["Bad key " + key], "Results": {}})
            return
        if self.server.mode == "RETRY" and self.server.attempts == 1:
            self.send_json(429, {}, {"Retry-After": "0"})
            return
        if self.server.mode == "REDIRECT":
            self.send_json(302, {}, {"Location": "https://example.invalid/" + str(key)})
            return
        result: dict[str, Any]
        if endpoint.startswith("surveys"):
            surveys = [
                {"survey_abbreviation": "CU", "survey_name": "Fixture consumer prices"},
                {"survey_abbreviation": "LN", "survey_name": "Fixture labor force"},
            ]
            if endpoint != "surveys":
                surveys = [r for r in surveys if r["survey_abbreviation"] == endpoint.split("/")[-1]]
                for row in surveys:
                    row.update(allowsNetChange="false", allowsPercentChange="true", hasAnnualAverages="true")
            result = {"survey": surveys}
        elif endpoint == "timeseries/popular":
            ids = ["CUUR0000SA0"] if query.get("survey") == "CU" else ["LNS14000000", "CUUR0000SA0"]
            result = {"series": [{"seriesID": identifier} for identifier in ids]}
        elif endpoint.startswith("timeseries/data"):
            options = payload or {}
            identifiers = options.get("seriesid", [endpoint.split("/")[-1]])
            if "WARNING" in identifiers:
                self.send_json(
                    200, {"status": "REQUEST_SUCCEEDED", "message": ["Warning: " + str(key)], "Results": {"series": []}}
                )
                return
            if "ERROR" in identifiers:
                self.send_json(200, {"status": "REQUEST_FAILED", "message": [str(key)], "Results": {}})
                return
            series = []
            year = options.get("startyear", "2024")
            for identifier in identifiers:
                if identifier == "MISSING":
                    continue
                data = [
                    {
                        "year": year,
                        "period": "M01",
                        "periodName": "January",
                        "value": "2.50",
                        "footnotes": [{"code": "P", "text": "Preliminary"}],
                    },
                    {"year": year, "period": "M02", "periodName": "February", "value": "-", "footnotes": [{}]},
                    {
                        "year": year,
                        "period": "M12",
                        "periodName": "December",
                        "value": "3.75",
                        "latest": "true",
                        "footnotes": [],
                    },
                ]
                if query.get("latest") == "true":
                    data = data[-1:]
                if options.get("annualaverage"):
                    data.append(
                        {"year": year, "period": "M13", "periodName": "Annual", "value": "3.25", "footnotes": []}
                    )
                if identifier == "EMPTY":
                    data = []
                if identifier == "BADVALUE":
                    data[0]["value"] = "NaN"
                if identifier == "OUTSIDE":
                    data[0]["year"] = str(int(year) - 1)
                if identifier == "DUPLICATE":
                    data.append(dict(data[0]))
                for observation in data:
                    if options.get("calculations"):
                        observation["calculations"] = {"pct_changes": {"1": "0.2"}, "net_changes": {"1": "0.1"}}
                    if options.get("aspects"):
                        observation["aspects"] = [{"name": "Relative Standard Error", "value": "1.9"}]
                entry: dict[str, Any] = {"seriesID": identifier, "data": data}
                if options.get("catalog"):
                    entry["catalog"] = {
                        "series_title": "Fixture " + identifier,
                        "series_id": identifier,
                        "survey_name": "Fixture labor force",
                        "survey_abbreviation": "LN",
                        "seasonality": "Seasonally Adjusted",
                        "measure_data_type": "Rate",
                        "area": "United States",
                    }
                series.append(entry)
            result = {"series": series}
            if "LIST" in identifiers:
                self.send_json(200, {"status": "REQUEST_SUCCEEDED", "message": [], "Results": [result]})
                return
        else:
            self.send_json(404, {})
            return
        self.send_json(200, {"status": "REQUEST_SUCCEEDED", "responseTime": 1, "message": [], "Results": result})


if __name__ == "__main__":
    server = ApiServer()
    print(f"PORT:{server.server_port}", flush=True)
    server.serve_forever()
