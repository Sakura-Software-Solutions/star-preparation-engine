"""Private filesystem artifacts and transactional, versioned team metadata."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .engine import fingerprint, inspect_source, prepare, validate_profile
from .export import write_run
from .reader import MAX_BYTES


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        for name in ("uploads", "runs"):
            (self.root / name).mkdir(exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profiles (
                  id TEXT, revision INTEGER, payload TEXT, actor TEXT, created REAL,
                  PRIMARY KEY(id, revision));
                CREATE TABLE IF NOT EXISTS uploads (
                  id TEXT PRIMARY KEY, filename TEXT, suffix TEXT, actor TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, upload_id TEXT, profile_id TEXT, actor TEXT,
                  created REAL, approved INTEGER, parent_id TEXT);
                CREATE TABLE IF NOT EXISTS users (
                  name TEXT PRIMARY KEY, salt TEXT, password TEXT, role TEXT);
                CREATE TABLE IF NOT EXISTS sessions (
                  token TEXT PRIMARY KEY, username TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS attempts (username TEXT PRIMARY KEY, failures INTEGER, until REAL);
            """)
        (self.root / "metadata.sqlite3").chmod(0o600)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.root / "metadata.sqlite3", timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def seed(self, directory: Path):
        with self.db() as db:
            for path in sorted(directory.glob("*.json")):
                profile = json.loads(path.read_text())
                if not db.execute("SELECT 1 FROM profiles WHERE id=?", (profile["profile_id"],)).fetchone():
                    db.execute("INSERT INTO profiles VALUES (?,1,?,?,?)",
                               (profile["profile_id"], json.dumps(profile), "starter", time.time()))

    def profiles(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT id, MAX(revision) AS revision FROM profiles GROUP BY id ORDER BY id")]

    def profile(self, name: str, revision: int | None = None):
        with self.db() as db:
            rows = db.execute("SELECT * FROM profiles WHERE id=? ORDER BY revision DESC", (name,)).fetchall()
        selected = next((r for r in rows if revision is None or r["revision"] == revision), None)
        if not selected:
            raise FileNotFoundError("Profile not found")
        return {"profile": json.loads(selected["payload"]), "revision": selected["revision"],
                "history": [{"revision": r["revision"], "actor": r["actor"], "created": r["created"]} for r in rows]}

    def save_profile(self, profile: dict, actor: str, base_revision: int | None):
        validate_profile(profile)
        name = profile.get("profile_id", "").strip()
        if not name or len(name) > 100:
            raise ValueError("Enter a profile name of 1–100 characters")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT MAX(revision) FROM profiles WHERE id=?", (name,)).fetchone()[0] or 0
            if base_revision != previous:
                raise ValueError("Profile changed since it was loaded. Reload it or save under a new name.")
            revision = previous + 1
            db.execute("INSERT INTO profiles VALUES (?,?,?,?,?)",
                       (name, revision, json.dumps(profile), actor, time.time()))
        return {"profile": profile, "revision": revision, "sha256": fingerprint(profile)}

    def upload(self, filename: str, body: bytes, actor: str):
        suffix = Path(filename).suffix.lower()
        if suffix not in {".xlsx", ".csv"} or not body or len(body) > MAX_BYTES:
            raise ValueError("Choose a nonempty CSV or XLSX file up to 25 MiB")
        identifier = uuid.uuid4().hex
        folder = self.root / "uploads" / identifier
        folder.mkdir(mode=0o700)
        (folder / ("source" + suffix)).write_bytes(body)
        with self.db() as db:
            db.execute("INSERT INTO uploads VALUES (?,?,?,?,?)",
                       (identifier, Path(filename).name[:255], suffix, actor, time.time()))
        return {"upload_id": identifier, "filename": Path(filename).name[:255]}

    def source(self, upload_id: str):
        with self.db() as db:
            record = db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
        if not record:
            raise FileNotFoundError("Upload not found or expired")
        return self.root / "uploads" / record["id"] / ("source" + record["suffix"]), dict(record)

    def inspect(self, upload_id: str, options: dict):
        path, metadata = self.source(upload_id)
        return {**inspect_source(path, options), "upload_id": upload_id, "filename": metadata["filename"]}

    def create_run(self, upload_id: str, profile: dict, actor: str, resolutions: dict | None = None,
                   parent_id: str | None = None):
        source, metadata = self.source(upload_id)
        if parent_id:
            parent = self.run(parent_id)
            if parent["upload_id"] != upload_id:
                raise ValueError("A revision must use its parent's source upload")
        result = prepare(source, profile, resolutions=resolutions)
        identifier = uuid.uuid4().hex
        result["metadata"].update(run_id=identifier, upload_id=upload_id, actor=actor,
                                  original_filename=metadata["filename"], parent_id=parent_id)
        output = self.root / "runs" / identifier
        output.mkdir(mode=0o700)
        try:
            shutil.copyfile(source, output / ("source" + source.suffix))
            write_run(result, output)
            with self.db() as db:
                db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
                           (identifier, upload_id, profile.get("profile_id", ""), actor, time.time(),
                            int(result["validation_report"]["approved"]), parent_id))
        except Exception:
            shutil.rmtree(output)  # Only this newly created, unpublished run.
            raise
        return self.run(identifier)

    def run(self, identifier: str):
        with self.db() as db:
            record = db.execute("SELECT r.*, u.filename FROM runs r JOIN uploads u ON u.id=r.upload_id WHERE r.id=?",
                                (identifier,)).fetchone()
        if not record:
            raise FileNotFoundError("Run not found or expired")
        folder = self.root / "runs" / record["id"]
        result = json.loads((folder / "result.json").read_text())
        return {**dict(record), "validation": result["validation_report"],
                "profile": result["profile"], "resolutions": result["resolutions"],
                "series": result["series"], "release_totals": result["release_totals"],
                "metadata": result["metadata"],
                "artifacts": [{"name": p.name, "url": f"/runs/{identifier}/{p.name}"}
                              for p in sorted(folder.iterdir()) if p.name != "result.json"]}

    def records(self, identifier: str, *, decision: str = "", release: str = "", query: str = "",
                offset: int = 0, limit: int = 100):
        self.run(identifier)
        rows = json.loads((self.root / "runs" / identifier / "result.json").read_text())["decisions"]
        filtered = [r for r in rows if (not decision or r["decision"] == decision)
                    and (not release or r["release"] == release)
                    and (not query or query.casefold() in json.dumps(r).casefold())]
        offset, limit = max(0, offset), max(1, min(500, limit))
        return {"total": len(filtered), "rows": filtered[offset:offset + limit]}

    def history(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("""SELECT r.*, u.filename FROM runs r
                JOIN uploads u ON u.id=r.upload_id ORDER BY r.created DESC LIMIT 500""")]

    def artifact(self, identifier: str, name: str):
        run = self.run(identifier)
        if name not in {a["name"] for a in run["artifacts"]}:
            raise FileNotFoundError("Artifact not found")
        return self.root / "runs" / identifier / name

    def add_user(self, name: str, password: str, role: str):
        if role not in {"viewer", "preparer", "admin"} or not name.strip() or len(password) < 12:
            raise ValueError("Use a username, a 12+ character password, and viewer/preparer/admin role")
        salt = secrets.token_hex(16)
        hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?)", (name, salt, hashed, role))
            db.execute("DELETE FROM sessions WHERE username=?", (name,))

    def has_users(self):
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM users LIMIT 1").fetchone())

    def login(self, name: str, password: str):
        now = time.time()
        with self.db() as db:
            attempt = db.execute("SELECT * FROM attempts WHERE username=?", (name,)).fetchone()
            if attempt and attempt["failures"] >= 10 and attempt["until"] > now:
                raise PermissionError("Too many sign-in attempts; try again in 15 minutes")
            row = db.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
            salt = row["salt"] if row else "0" * 32
            hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()
            valid = row is not None and hmac.compare_digest(row["password"], hashed)
            if not valid:
                failures = attempt["failures"] + 1 if attempt and attempt["until"] > now else 1
                db.execute("INSERT OR REPLACE INTO attempts VALUES (?,?,?)", (name, failures, now + 900))
            else:
                token = secrets.token_urlsafe(32)
                db.execute("DELETE FROM attempts WHERE username=? OR until<?", (name, now))
                db.execute("DELETE FROM sessions WHERE expires<?", (now,))
                db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), name, now + 28800))
        if not valid:
            raise PermissionError("Invalid username or password")
        return token

    def user(self, token: str):
        with self.db() as db:
            row = db.execute("""SELECT u.name, u.role FROM sessions s JOIN users u ON u.name=s.username
                WHERE s.token=? AND s.expires>?""", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return dict(row) if row else None

    def logout(self, token: str):
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def purge(self, days: int, execute: bool = False):
        if days < 1:
            raise ValueError("Retention must be at least one day")
        cutoff = time.time() - days * 86400
        with self.db() as db:
            runs = [r[0] for r in db.execute("SELECT id FROM runs WHERE created<?", (cutoff,))]
            uploads = [r[0] for r in db.execute("""SELECT id FROM uploads WHERE created<? AND id NOT IN
                (SELECT upload_id FROM runs WHERE created>=?)""", (cutoff, cutoff))]
            if execute:
                for identifier in runs:
                    shutil.rmtree(self.root / "runs" / identifier, ignore_errors=False)
                    db.execute("DELETE FROM runs WHERE id=?", (identifier,))
                for identifier in uploads:
                    shutil.rmtree(self.root / "uploads" / identifier, ignore_errors=False)
                    db.execute("DELETE FROM uploads WHERE id=?", (identifier,))
                db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        return {"runs": len(runs), "uploads": len(uploads), "deleted": execute}
