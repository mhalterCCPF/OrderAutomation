"""Authenticated LAN HTTP API for eufyLoaderRobot listeners."""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from modules.config import KEYS_DIR

MAX_REQUEST_BYTES = 1024 * 1024
API_PREFIX = "/api/v1"
TOKEN_FILE = KEYS_DIR / "conductor_token.txt"


def load_or_create_token(path: Path = TOKEN_FILE) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    return token


class ConductorHTTPServer:
    def __init__(self, service, host: str, port: int, token: str):
        self.service = service
        self.token = token
        self.httpd = ThreadingHTTPServer((host, port), self._handler_type())
        self.httpd.daemon_threads = True
        self.thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        return self.httpd.server_address

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="conductor-http", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)

    def _handler_type(self):
        service = self.service
        expected_token = self.token

        class Handler(BaseHTTPRequestHandler):
            server_version = "OrderAutomationConductor/1"

            def do_GET(self):
                if not self._authorized():
                    return
                parts = self._parts()
                if parts == ["health"]:
                    self._json(200, {"version": 1, "status": "ok"})
                    return
                if len(parts) == 4 and parts[0] == "loaders" and parts[2:] == ["assignment", "bundle"]:
                    try:
                        bundle = service.bundle_for_loader(unquote(parts[1]))
                        self._file(bundle, "application/zip")
                    except (FileNotFoundError, KeyError, ValueError) as error:
                        self._json(404, {"error": str(error)})
                    return
                if len(parts) == 3 and parts[0] == "loaders" and parts[2] == "assignment":
                    loader_id = unquote(parts[1])
                    try:
                        service.heartbeat(loader_id)
                        assignment = service.assignment_for_loader(loader_id)
                    except Exception as error:
                        self._json(500, {"error": str(error)})
                        return
                    if assignment is None:
                        self.send_response(204)
                        self.end_headers()
                    else:
                        self._json(200, {"version": 1, "assignment": assignment})
                    return
                self._json(404, {"error": "Not found"})

            def do_POST(self):
                if not self._authorized():
                    return
                try:
                    payload = self._read_json()
                    parts = self._parts()
                    if parts == ["loaders", "register"]:
                        loader_id = str(payload.get("loader_id", ""))
                        details = {key: value for key, value in payload.items() if key != "loader_id"}
                        details.setdefault("last_seen", time.time())
                        service.register_loader(loader_id, details)
                        self._json(200, {"version": 1, "status": "registered", "loader_id": loader_id})
                        return
                    if len(parts) == 3 and parts[0] == "loaders" and parts[2] == "state":
                        loader_id = unquote(parts[1])
                        payload["last_seen"] = time.time()
                        service.update_loader(loader_id, payload)
                        self._json(200, {"version": 1, "status": "updated"})
                        return
                    if len(parts) == 5 and parts[0] == "loaders" and parts[2] == "assignments":
                        loader_id = unquote(parts[1])
                        order_id = unquote(parts[3])
                        action = parts[4]
                        if action == "unit-complete":
                            service.complete_print_unit(loader_id, order_id, payload.get("unit_index"))
                            self._json(200, {"version": 1, "status": "unit_completed"})
                            return
                        if action == "complete":
                            result = service.complete_assignment(loader_id, order_id, payload.get("status"))
                            self._json(200, {"version": 1, **result})
                            return
                        if action == "interrupted":
                            service.report_interrupted(loader_id, order_id, str(payload.get("reason", "interrupted")))
                            self._json(200, {"version": 1, "status": "interrupted"})
                            return
                    self._json(404, {"error": "Not found"})
                except (ValueError, KeyError, json.JSONDecodeError) as error:
                    self._json(400, {"error": str(error)})
                except Exception as error:
                    self._json(500, {"error": str(error)})

            def _authorized(self) -> bool:
                supplied = self.headers.get("Authorization", "")
                prefix = "Bearer "
                if not supplied.startswith(prefix) or not hmac.compare_digest(supplied[len(prefix):], expected_token):
                    self._json(401, {"error": "Unauthorized"})
                    return False
                return True

            def _parts(self) -> list[str]:
                path = urlparse(self.path).path
                if not path.startswith(API_PREFIX + "/"):
                    return []
                return [part for part in path[len(API_PREFIX) + 1:].split("/") if part]

            def _read_json(self) -> dict:
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError as error:
                    raise ValueError("Invalid Content-Length") from error
                if content_length < 0 or content_length > MAX_REQUEST_BYTES:
                    raise ValueError("Request body is too large")
                payload = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object")
                return payload

            def _json(self, status: int, payload: dict) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _file(self, path: Path, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(path.stat().st_size))
                self.end_headers()
                with path.open("rb") as handle:
                    while chunk := handle.read(64 * 1024):
                        self.wfile.write(chunk)

            def log_message(self, _format, *_args):
                return

        return Handler
