"""Explicit official-live or synthetic loopback input for BLS teaching notebooks."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FAKE_KEY = "N" * 32


class BLSSource:
    """Scope BLS environment settings and stop the optional local service on close."""

    def __init__(self, mode: str) -> None:
        if mode not in {"live", "synthetic"}:
            raise ValueError("mode must be live or synthetic")
        self.mode = mode
        self.audit: list[dict] = []
        self.server = None
        self.closed = False
        self.saved = {k: v for k, v in os.environ.items() if k.startswith("BLS_")}
        for name in self.saved:
            os.environ.pop(name)
        os.environ["BLS_API_BASE_URL"] = "https://api.bls.gov/publicAPI/v2"
        if mode == "live":
            return
        self.fixture = json.loads(Path(__file__).with_name("fixtures").joinpath("discovery.json").read_text())
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def reply(self, status: int, results: dict) -> None:
                body = json.dumps(
                    {"status": "REQUEST_SUCCEEDED", "message": [], "Results": results}
                    if status == 200
                    else {"error": "Unsupported synthetic request"}
                ).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                if query.pop("registrationkey", None) != [FAKE_KEY]:
                    self.reply(403, {})
                    return
                if parsed.path == "/publicAPI/v2/surveys" and not query:
                    result = {"survey": owner.fixture["surveys"]}
                elif parsed.path == "/publicAPI/v2/timeseries/popular" and not query:
                    result = {"series": [{"seriesID": s} for s in owner.fixture["popular_ids"]]}
                elif parsed.path.startswith("/publicAPI/v2/timeseries/data/") and query == {"latest": ["true"]}:
                    sid = parsed.path.rsplit("/", 1)[-1]
                    matches = [s for s in owner.fixture["series"] if s["seriesID"] == sid]
                    if len(matches) != 1:
                        self.reply(400, {})
                        return
                    result = {"series": matches}
                else:
                    self.reply(400, {})
                    return
                owner.audit.append({"method": "GET", "path": parsed.path})
                self.reply(200, result)

            def do_POST(self) -> None:
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size < 4096:
                        raise ValueError
                    payload = json.loads(self.rfile.read(size))
                    if payload.pop("registrationkey", None) != FAKE_KEY:
                        self.reply(403, {})
                        return
                    expected = {
                        "seriesid": owner.fixture["popular_ids"],
                        "startyear": "2024",
                        "endyear": "2024",
                        "catalog": True,
                        "calculations": False,
                        "annualaverage": False,
                        "aspects": False,
                    }
                    if self.path != "/publicAPI/v2/timeseries/data/" or payload != expected:
                        raise ValueError
                except (ValueError, TypeError, json.JSONDecodeError):
                    self.reply(400, {})
                    return
                owner.audit.append({"method": "POST", "path": self.path, "series_count": len(payload["seriesid"])})
                self.reply(200, {"series": owner.fixture["series"]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        os.environ.update(
            BLS_API_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/publicAPI/v2",
            BLS_ALLOW_INSECURE_HTTP="1",
            BLS_MIN_INTERVAL_SECONDS="0",
        )

    def close(self) -> None:
        """Restore caller settings without logging credentials; safe to call twice."""
        if self.closed:
            return
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
        for name in list(os.environ):
            if name.startswith("BLS_"):
                os.environ.pop(name)
        os.environ.update(self.saved)
        self.closed = True
