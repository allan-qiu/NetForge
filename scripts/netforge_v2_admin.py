#!/usr/bin/env python3
"""Offline-safe V2 migration, question import, backup, and restore utility."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from netforge_v2 import (  # noqa: E402
    MigrationManager,
    V2Error,
    backup_database,
    import_question_bank,
    restore_database_verified,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="NetForge V2 maintenance utility")
    parser.add_argument("--db", required=True, help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    importer = sub.add_parser("import-questions")
    importer.add_argument("--bank", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--destination", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--source", required=True)
    restore.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "migrate":
            result = {"applied": MigrationManager(args.db).apply()}
        elif args.command == "import-questions":
            MigrationManager(args.db).apply()
            result = import_question_bank(args.db, args.bank)
        elif args.command == "backup":
            result = backup_database(args.db, args.destination)
        else:
            result = restore_database_verified(args.source, args.db, confirm=args.confirm)
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
        return 0
    except (V2Error, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, V2Error) else "operation_failed"
        print(json.dumps({"ok": False, "error": {"code": code, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
