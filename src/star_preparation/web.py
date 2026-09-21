"""Local and authenticated shared HTTP adapters for one preparation application."""
from __future__ import annotations

import errno
import ipaddress
import json
import mimetypes
import os
import socket
import ssl
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
            raise ValueError("Shared mode requires secure cookies, HTTPS (direct TLS or proxy), and at least one provisioned user; run user-add first")

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


def validate_lan_host(host: str) -> None:
    """Bind LAN mode to one private interface, never every/public interface."""
    networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError("--lan requires a numeric private LAN or VPN address in --host") from error
    if not any(address in ipaddress.ip_network(network) for network in networks):
        raise ValueError("--lan requires a private LAN/VPN IP; public, loopback and wildcard addresses are not allowed")


def make_server(host="127.0.0.1", port=8080, *, application: Application, tls_cert=None, tls_key=None):
    if host not in {"localhost", "127.0.0.1", "::1"} and not application.shared:
        raise ValueError("Non-loopback binding requires --shared, provisioned users and HTTPS (direct TLS or proxy)")
    if bool(tls_cert) != bool(tls_key):
        raise ValueError("Supply both --tls-cert and --tls-key")
    context = None
    if tls_cert:
        if not application.secure_cookies:
            raise ValueError("Direct HTTPS requires secure cookies")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(tls_cert, tls_key)

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
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

        def get_request(self):
            connection, address = super().get_request()
            if context:
                # The worker performs the handshake, so one slow client cannot
                # block the main accept loop. Bound the worker's handshake wait.
                try:
                    connection.settimeout(10)
                    connection = context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False)
                except Exception:
                    connection.close()
                    raise
            return connection, address

    return Server((host, port), Handler)


def serve(host="127.0.0.1", port=8080, *, data_dir=None, shared=False, secure_cookies=False,
          lan=False, tls_cert=None, tls_key=None):
    if lan:
        validate_lan_host(host)
        if not tls_cert or not tls_key:
            raise ValueError("--lan requires --tls-cert and --tls-key for HTTPS")
        shared = secure_cookies = True
    if tls_cert:
        secure_cookies = True
    application = Application(data_dir, shared=shared, secure_cookies=secure_cookies)
    try:
        server = make_server(host, port, application=application, tls_cert=tls_cert, tls_key=tls_key)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            raise ValueError(f"Port {port} is already in use on {host}; stop the existing server or choose --port {port + 1}") from error
        if error.errno == errno.EADDRNOTAVAIL:
            raise ValueError(f"{host} is not assigned to this machine; choose its private LAN/VPN address") from error
        raise
    scheme = "https" if tls_cert else "http"
    address = f"[{host}]" if ":" in host else host
    mode = "shared / sign-in required" if shared and tls_cert else "shared / HTTPS proxy required" if shared else "local"
    print(f"STAR Preparation: {scheme}://{address}:{server.server_address[1]} ({mode})", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
