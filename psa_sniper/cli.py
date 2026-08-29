from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import ROOT, state_path
from .crypto import decrypt_file, encrypt_file
from .dashboard import build_dashboard
from .demo import demo_state
from .doctor import print_checks, run_doctor
from .quota import prepare_scan_quota
from .repricing import run_repricing_queue
from .scanner import run_scan
from .state import default_state, save_state


def _password() -> str:
    return os.getenv("DASHBOARD_PASSWORD", "")


def _cmd_doctor(args: argparse.Namespace) -> int:
    checks, ok = run_doctor(live=args.live)
    print_checks(checks)
    return 0 if ok else 2


def _cmd_dashboard(args: argparse.Namespace) -> int:
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    build_dashboard(state_path(), output, password=_password(), plain=args.plain)
    print(f"Dashboard gebaut: {output}")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    path = state_path()
    save_state(path, demo_state())
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    build_dashboard(path, output, password=_password(), plain=not args.encrypted)
    print(f"Demo-Dashboard gebaut: {output}")
    return 0


def _cmd_state(args: argparse.Namespace) -> int:
    input_path = Path(args.input) if getattr(args, "input", None) else None
    output_path = Path(args.output)
    if args.state_action == "init":
        save_state(output_path, default_state())
    elif args.state_action == "encrypt":
        if not input_path:
            raise ValueError("--input fehlt")
        encrypt_file(input_path, output_path, _password())
    elif args.state_action == "decrypt":
        if not input_path:
            raise ValueError("--input fehlt")
        decrypt_file(input_path, output_path, _password())
    print(f"State {args.state_action}: {output_path}")
    return 0


def _cmd_scan() -> int:
    quota = prepare_scan_quota()
    print(f"Quota-Guard: {quota.note}; rollierend genutzt={quota.rolling_used}.")
    if quota.skipped:
        print("Scan wegen Sicherheitsreserve übersprungen.")
        return 0
    result = run_scan()
    if result != 0:
        return result
    try:
        run_repricing_queue()
    except Exception as exc:  # optional maintenance must never kill a successful scan
        print(f"Repricing-Warnung: {exc.__class__.__name__}", file=sys.stderr)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PSA Sniper Free")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("scan", help="eBay live scannen")
    doctor = sub.add_parser("doctor", help="Konfiguration und optional Live-Zugriff prüfen")
    doctor.add_argument("--live", action="store_true", help="eBay OAuth + Browse API testen")
    dashboard = sub.add_parser("dashboard", help="statisches Dashboard aus State bauen")
    dashboard.add_argument("--output", default="site/dist")
    dashboard.add_argument("--plain", action="store_true", help="nur lokal: Daten unverschlüsselt")
    demo = sub.add_parser("demo", help="Demo-State und Dashboard erzeugen")
    demo.add_argument("--output", default="site/dist")
    demo.add_argument("--encrypted", action="store_true", help="Demo mit DASHBOARD_PASSWORD verschlüsseln")
    state_parser = sub.add_parser("state", help="State initialisieren/ver- oder entschlüsseln")
    state_sub = state_parser.add_subparsers(dest="state_action", required=True)
    init = state_sub.add_parser("init")
    init.add_argument("--output", default="data/state.json")
    encrypt = state_sub.add_parser("encrypt")
    encrypt.add_argument("--input", required=True)
    encrypt.add_argument("--output", required=True)
    decrypt = state_sub.add_parser("decrypt")
    decrypt.add_argument("--input", required=True)
    decrypt.add_argument("--output", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command in {None, "scan"}:
            return _cmd_scan()
        if args.command == "doctor":
            return _cmd_doctor(args)
        if args.command == "dashboard":
            return _cmd_dashboard(args)
        if args.command == "demo":
            return _cmd_demo(args)
        if args.command == "state":
            return _cmd_state(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 1
