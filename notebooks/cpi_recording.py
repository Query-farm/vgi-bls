"""Replay retained public CPI observations, explicitly distinct from original HTTP bytes."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bls_example import BLSSource

RECORDING_SHA256 = "3dea57fa43238cbde1306eb42907550ed5a7a340a6d3059e4d45d00cce01e6e3"


class CPISource(BLSSource):
    """Choose recorded or live explicitly; a failed live request never falls back."""

    def __init__(self, mode: str) -> None:
        if mode not in {"recorded", "live"}:
            raise ValueError("DATA_MODE must be recorded or live")
        path = Path(__file__).with_name("fixtures") / "cpi-observations-derived.json"
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != RECORDING_SHA256:
            raise ValueError("Derived CPI recording hash mismatch")
        fixture = json.loads(raw)
        rows = fixture["rows"]
        encoded = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
        if (
            len(rows) != 144
            or hashlib.sha256(encoded).hexdigest() != fixture["provenance"]["source_fingerprint_sha256"]
        ):
            raise ValueError("Derived CPI observation audit is incomplete or changed")
        if len({tuple(r[:3]) for r in rows}) != len(rows):
            raise ValueError("Duplicate recorded CPI observation")
        super().__init__("live")
        self.mode = mode
        self.manifest = fixture["provenance"]
        if mode == "live":
            return
        series = []
        for sid in fixture["provenance"]["request"]["seriesid"]:
            data = []
            for series_id, year, period, value_text, footnotes_json in rows:
                if series_id != sid:
                    continue
                notes = json.loads(footnotes_json)
                if json.dumps(notes, ensure_ascii=False) != footnotes_json:
                    raise ValueError("Footnote JSON would change during replay")
                data.append({"year": str(year), "period": period, "value": value_text, "footnotes": notes})
            series.append({"seriesID": sid, "data": data})
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def reply(self, status: int, body: dict) -> None:
                raw = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length < 4096:
                        raise ValueError
                    request = json.loads(self.rfile.read(length))
                    if self.path != "/publicAPI/v2/timeseries/data/" or request != owner.manifest["request"]:
                        raise ValueError
                except (ValueError, TypeError, json.JSONDecodeError):
                    self.reply(400, {"error": "Request is outside this derived CPI recording"})
                    return
                owner.audit.append({"method": "POST", "path": self.path, "series_count": 2, "rows": 144})
                self.reply(200, {"status": "REQUEST_SUCCEEDED", "message": [], "Results": {"series": series}})

            def do_GET(self) -> None:
                self.reply(400, {"error": "This derived recording accepts only its bounded POST"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        os.environ.update(
            BLS_API_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/publicAPI/v2",
            BLS_ALLOW_INSECURE_HTTP="1",
            BLS_MIN_INTERVAL_SECONDS="0",
        )
