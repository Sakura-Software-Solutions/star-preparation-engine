from __future__ import annotations

import argparse
import json
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
    serve_command = commands.add_parser("serve", help="Run the local preparation admin UI")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", default=8080, type=int)
    arguments = parser.parse_args()

    if arguments.command == "profile":
        print(json.dumps(profile_records(read_records(arguments.input)), indent=2))
        return 0
    if arguments.command == "serve":
        from .web import serve

        serve(host=arguments.host, port=arguments.port)
        return 0
    result = prepare(arguments.input, load_profile(arguments.profile))
    write_run(result, arguments.output)
    print(json.dumps(result["validation_report"], indent=2))
    print(f"Artifacts written to {Path(arguments.output)}")
    return 2 if result["validation_report"]["approval_required"] else 0
