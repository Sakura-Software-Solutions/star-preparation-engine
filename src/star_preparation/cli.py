from __future__ import annotations

import argparse
import json
import getpass
import os
from pathlib import Path

from .engine import load_profile, prepare
from .export import write_run
from .profiler import profile_records
from .reader import read_records


def main() -> int:
    parser = argparse.ArgumentParser(prog="star-prep")
    commands = parser.add_subparsers(dest="command", required=True)
    profile_command = commands.add_parser("profile", help="Inspect a source CSV/XLSX file")
    profile_command.add_argument("input")
    prepare_command = commands.add_parser("prepare", help="Prepare STAR input and audit artifacts")
    prepare_command.add_argument("input")
    prepare_command.add_argument("--profile", required=True)
    prepare_command.add_argument("--output", required=True)
    serve_command = commands.add_parser("serve", help="Run the local or authenticated shared dashboard")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, help="Default: 8080 locally, 8443 with --lan")
    serve_command.add_argument("--data-dir", default=os.environ.get("STAR_DATA_DIR"))
    serve_command.add_argument("--shared", action="store_true")
    serve_command.add_argument("--secure-cookies", action="store_true")
    serve_command.add_argument("--lan", action="store_true", help="Authenticated HTTPS on the private LAN/VPN IP specified by --host")
    serve_command.add_argument("--tls-cert", help="PEM server certificate, valid for the chosen IP or hostname")
    serve_command.add_argument("--tls-key", help="PEM private key for the server certificate")
    user_command = commands.add_parser("user-add", help="Provision or reset an internal account")
    user_command.add_argument("username")
    user_command.add_argument("--role", choices=["viewer", "preparer", "admin"], default="preparer")
    user_command.add_argument("--data-dir", required=True)
    purge_command = commands.add_parser("purge", help="Preview or execute retention cleanup")
    purge_command.add_argument("--data-dir", required=True)
    purge_command.add_argument("--older-than-days", type=int, required=True)
    purge_command.add_argument("--execute", action="store_true")
    arguments = parser.parse_args()

    if arguments.command == "profile":
        print(json.dumps(profile_records(read_records(arguments.input)), indent=2))
        return 0
    if arguments.command == "serve":
        from .web import serve

        try:
            serve(host=arguments.host, port=arguments.port if arguments.port is not None else 8443 if arguments.lan else 8080,
                  data_dir=arguments.data_dir, shared=arguments.shared, secure_cookies=arguments.secure_cookies,
                  lan=arguments.lan, tls_cert=arguments.tls_cert, tls_key=arguments.tls_key)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        return 0
    if arguments.command in {"user-add", "purge"}:
        from .store import Store
        store = Store(arguments.data_dir)
        if arguments.command == "user-add":
            password = getpass.getpass("Password (12+ characters): ")
            if password != getpass.getpass("Confirm password: "):
                parser.error("Passwords do not match")
            store.add_user(arguments.username, password, arguments.role)
            print("Account saved; previous sessions revoked.")
        else:
            print(json.dumps(store.purge(arguments.older_than_days, arguments.execute), indent=2))
        return 0
    result = prepare(arguments.input, load_profile(arguments.profile))
    write_run(result, arguments.output)
    print(json.dumps(result["validation_report"], indent=2))
    print(f"Artifacts written to {Path(arguments.output)}")
    return 0 if result["validation_report"]["approved"] else 2
