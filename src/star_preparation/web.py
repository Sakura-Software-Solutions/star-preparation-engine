"""Local and authenticated shared HTTP adapters for one preparation application."""
from __future__ import annotations

import json
import mimetypes
import os
import socket
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .reader import MAX_BYTES
from .store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = Path(__file__).with_name("web_assets")


class Application:
    def __init__(self, data_dir: str | Path | None = None, *, shared: bool = False, secure_cookies: bool = False):
        if os.environ.get("VERCEL"):
            raise ValueError("This storage backend requires a persistent private volume; Vercel runs are disabled")
        self.shared = shared
        self.secure_cookies = secure_cookies
        self.store = Store(data_dir or os.environ.get("STAR_DATA_DIR", PROJECT_ROOT / "data"))
        self.store.seed(PROJECT_ROOT / "profiles")
        if shared and (not secure_cookies or not self.store.has_users()):
            raise ValueError("Shared mode requires secure cookies, HTTPS reverse proxy, and at least one provisioned user")

    def response(self, method: str, target: str, headers: dict, body: bytes = b""):
        headers = {k.lower(): v for k, v in headers.items()}
        status, payload, extra = 200, None, {}
        request = urlparse(target)
        parts = [unquote(p) for p in request.path.split("/") if p]
        query = {k: v[-1] for k, v in parse_qs(request.query).items()}
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            pass
        token = cookie["star_session"].value if "star_session" in cookie else ""
        security = {
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        }
        if self.secure_cookies:
            security["Strict-Transport-Security"] = "max-age=31536000"
        try:
            if not self.shared and urlparse("//" + headers.get("host", "")).hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise PermissionError("Local mode accepts loopback hosts only")
            if method not in {"GET", "POST"}:
                return 405, {**security, "Content-Type": "application/json"}, b'{"error":"Method not allowed"}'
            if method == "POST":
                origin = urlparse(headers.get("origin", ""))
                if origin.netloc != headers.get("host") or origin.scheme not in {"http", "https"}:
                    raise PermissionError("A same-origin request is required")
                if len(body) > MAX_BYTES:
                    raise ValueError("Upload exceeds 25 MiB")
            if method == "GET" and (request.path == "/" or request.path in {"/app.js", "/style.css"}):
                name = "index.html" if request.path == "/" else request.path[1:]
                data = (ASSET_DIR / name).read_bytes()
                return 200, {**security, "Content-Type": mimetypes.guess_type(name)[0] or "text/plain"}, data
            user = self.store.user(token) if self.shared else {"name": "local", "role": "admin"}
            if request.path == "/api/session" and method == "GET":
                payload = {"user": user, "shared": self.shared}
            elif request.path == "/api/login" and method == "POST":
                data = self._json(body)
                token = self.store.login(str(data.get("username", "")), str(data.get("password", "")))
                extra["Set-Cookie"] = self._cookie(token)
                payload = {"user": self.store.user(token)}
            elif request.path == "/api/logout" and method == "POST":
                self.store.logout(token)
                extra["Set-Cookie"] = self._cookie("", age=0)
                payload = {"ok": True}
            elif request.path == "/api/health" and method == "GET":
                payload = {"status": "ok", "shared": self.shared, "storage": "persistent-filesystem"}
            elif not user:
                status, payload = 401, {"error": "Sign in to access preparation data"}
            elif method == "POST" and user["role"] == "viewer":
                raise PermissionError("A preparer or administrator account is required")
            elif request.path == "/api/profiles" and method == "GET":
                payload = {"profiles": self.store.profiles()}
            elif request.path == "/api/profiles" and method == "POST":
                data = self._json(body)
                payload = self.store.save_profile(data["profile"], user["name"], data.get("base_revision", 0))
                status = 201
            elif parts[:2] == ["api", "profiles"] and len(parts) == 3 and method == "GET":
                payload = self.store.profile(parts[2], int(query["revision"]) if "revision" in query else None)
            elif request.path == "/api/uploads" and method == "POST":
                payload = self.store.upload(unquote(headers.get("x-filename", "source.csv")), body, user["name"])
                status = 201
            elif request.path == "/api/inspect" and method == "POST":
                data = self._json(body)
                payload = self.store.inspect(data["upload_id"], data.get("options", {}))
            elif request.path == "/api/prepare" and method == "POST":
                data = self._json(body)
                payload = self.store.create_run(data["upload_id"], data["profile"], user["name"],
                                                data.get("resolutions"), data.get("parent_id"))
                status = 201
            elif request.path == "/api/runs" and method == "GET":
                payload = {"runs": self.store.history()}
            elif parts[:2] == ["api", "runs"] and len(parts) == 3 and method == "GET":
                payload = self.store.run(parts[2])
            elif parts[:2] == ["api", "runs"] and len(parts) == 4 and parts[3] == "records" and method == "GET":
                payload = self.store.records(parts[2], decision=query.get("decision", ""), release=query.get("release", ""),
                                             query=query.get("query", ""), offset=int(query.get("offset", 0)),
                                             limit=int(query.get("limit", 100)))
            elif parts[:1] == ["runs"] and len(parts) == 3 and method == "GET":
                path = self.store.artifact(parts[1], parts[2])
                return 200, {**security, "Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                             "Content-Disposition": f'attachment; filename="{path.name}"'}, path.read_bytes()
            else:
                status, payload = 404, {"error": "Not found"}
        except PermissionError as error:
            status, payload = 403, {"error": str(error)}
        except FileNotFoundError:
            status, payload = 404, {"error": "Requested data was not found or has expired"}
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
            status, payload = 400, {"error": str(error)[:500]}
        return status, {**security, **extra, "Content-Type": "application/json; charset=utf-8"}, json.dumps(payload).encode()

    @staticmethod
    def _json(body: bytes):
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
        return payload

    def _cookie(self, token: str, age: int = 28800):
        return f"star_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}" + ("; Secure" if self.secure_cookies else "")


def make_server(host="127.0.0.1", port=8080, *, application: Application):
    if host not in {"localhost", "127.0.0.1", "::1"} and not application.shared:
        raise ValueError("Non-loopback binding requires --shared, provisioned users and HTTPS proxy")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Never log request bodies, filenames, credentials, or customer data.

        def handle_request(self):
            self.connection.settimeout(30)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > MAX_BYTES or self.headers.get("Transfer-Encoding"):
                    self.send_error(413, "Unsupported or oversized request")
                    return
                body = self.rfile.read(length) if length else b""
                if len(body) != length:
                    self.send_error(400, "Incomplete request")
                    return
                status, headers, data = application.response(self.command, self.path, dict(self.headers), body)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionError, TimeoutError):
                return
            except ValueError:
                self.send_error(400, "Invalid request length")

        do_GET = handle_request
        do_POST = handle_request

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if host == "::1" else socket.AF_INET

    return Server((host, port), Handler)


def serve(host="127.0.0.1", port=8080, *, data_dir=None, shared=False, secure_cookies=False):
    application = Application(data_dir, shared=shared, secure_cookies=secure_cookies)
    server = make_server(host, port, application=application)
    print(f"STAR Preparation: http://{host}:{port} ({'shared / HTTPS proxy required' if shared else 'local'})", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
