"""A deliberately local-only administration UI for preparation profiles."""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import tempfile
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .engine import load_profile, prepare
from .export import write_run

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = PROJECT_ROOT / "profiles"
RUN_DIR = PROJECT_ROOT / "runs"
ASSET_DIR = Path(__file__).with_name("web_assets")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _safe_name(value: str, suffix: str = "") -> str:
    name = Path(value).name
    if not SAFE_NAME.fullmatch(name):
        raise ValueError("Name may contain only letters, numbers, dots, hyphens, and underscores")
    return name if not suffix or name.endswith(suffix) else name + suffix


class PreparationHandler(BaseHTTPRequestHandler):
    server_version = "STARPreparation/0.1"

    def log_message(self, format: str, *args: object) -> None:
        # Browser assets are noisy; preparation actions return explicit results.
        return

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message}, status)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length))

    def do_GET(self) -> None:  # noqa: N802
        request = urlparse(self.path)
        if request.path == "/":
            self._send_file(ASSET_DIR / "index.html", "text/html; charset=utf-8")
            return
        if request.path == "/api/profiles":
            PROFILE_DIR.mkdir(exist_ok=True)
            self._send_json({"profiles": sorted(path.name for path in PROFILE_DIR.glob("*.json"))})
            return
        if request.path.startswith("/api/profiles/"):
            try:
                name = _safe_name(unquote(request.path.rsplit("/", 1)[-1]), ".json")
                self._send_json(load_profile(PROFILE_DIR / name))
            except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
                self._error(str(error), 404)
            return
        if request.path.startswith("/runs/"):
            try:
                relative = Path(unquote(request.path.removeprefix("/runs/")))
                target = (RUN_DIR / relative).resolve()
                if RUN_DIR.resolve() not in target.parents or not target.is_file():
                    raise ValueError("Artifact not found")
                self._send_file(target, mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            except ValueError as error:
                self._error(str(error), 404)
            return
        self._error("Not found", 404)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        request = urlparse(self.path)
        try:
            if request.path == "/api/profiles":
                payload = self._json_body()
                name = _safe_name(str(payload.pop("file_name", payload.get("profile_id", "profile"))), ".json")
                PROFILE_DIR.mkdir(exist_ok=True)
                (PROFILE_DIR / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                self._send_json({"file_name": name, "profile": payload}, 201)
                return
            if request.path == "/api/prepare":
                parameters = parse_qs(request.query)
                profile_name = _safe_name(parameters.get("profile", [""])[0], ".json")
                filename = _safe_name(self.headers.get("X-Filename", "input.xlsx"))
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise ValueError("Upload is empty")
                if Path(filename).suffix.lower() not in {".csv", ".xlsx"}:
                    raise ValueError("Only .csv and .xlsx uploads are supported")
                run_id = uuid.uuid4().hex
                output = RUN_DIR / run_id
                output.mkdir(parents=True, exist_ok=False)
                input_path = output / f"raw-{filename}"
                with input_path.open("wb") as destination:
                    shutil.copyfileobj(self.rfile, destination, length=64 * 1024)
                result = prepare(input_path, load_profile(PROFILE_DIR / profile_name))
                write_run(result, output)
                artifacts = sorted(path.name for path in output.iterdir() if path.name != input_path.name)
                self._send_json({
                    "run_id": run_id,
                    "validation": result["validation_report"],
                    "artifacts": [{"name": name, "url": f"/runs/{run_id}/{name}"} for name in artifacts],
                }, 201)
                return
            self._error("Not found", 404)
        except (ValueError, KeyError, FileNotFoundError, json.JSONDecodeError) as error:
            self._error(str(error))


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("This admin UI is intentionally local-only; use 127.0.0.1, localhost, or ::1")
    RUN_DIR.mkdir(exist_ok=True)
    print(f"STAR Preparation Admin: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    ThreadingHTTPServer((host, port), PreparationHandler).serve_forever()
