"""Operational CLI: ``mind-api <command>``."""

from __future__ import annotations

import argparse
import sys

from .config import get_settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mind-api")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    w = sub.add_parser("worker", help="run the background worker")
    w.add_argument("--concurrency", type=int, default=2)
    sub.add_parser("run-jobs", help="process queued jobs once and exit")
    sub.add_parser("gen-key", help="print a new Fernet encryption key")
    sub.add_parser("openapi", help="print the OpenAPI schema as JSON")
    pa = sub.add_parser("make-admin", help="grant platform admin to a user")
    pa.add_argument("email")
    args = ap.parse_args(argv)

    if args.cmd == "migrate":
        from pathlib import Path

        from alembic import command
        from alembic.config import Config

        cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
        command.upgrade(cfg, "head")
    elif args.cmd == "worker":
        from .worker import main as worker_main

        worker_main(args.concurrency)
    elif args.cmd == "run-jobs":
        from .jobs import run_pending
        from .services import register_handlers

        register_handlers()
        print(f"processed {run_pending()} job(s)")
    elif args.cmd == "gen-key":
        from cryptography.fernet import Fernet

        print(Fernet.generate_key().decode())
    elif args.cmd == "openapi":
        import json

        from .main import app

        print(json.dumps(app.openapi(), indent=2))
    elif args.cmd == "make-admin":
        from sqlalchemy import func, select

        from .db import session_scope
        from .models import User

        with session_scope() as db:
            u = db.scalar(select(User).where(func.lower(User.email) == args.email.lower()))
            if u is None:
                print("no such user", file=sys.stderr)
                return 1
            u.is_platform_admin = True
        print("ok")
    _ = get_settings()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
