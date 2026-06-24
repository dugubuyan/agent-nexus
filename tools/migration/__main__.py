"""
CLI entry point for the migration tool.

Configuration is read from the project's .env file automatically.
No environment setup needed beyond what the server already uses.

Run from the project root (where .env lives):

Subcommands
-----------
list-spaces                     List all spaces in the current database
dump    <space_id>              Export a space to a zip archive
restore <snapshot.zip>          Import a zip archive into a (possibly different) instance

Archive format
--------------
The dump produces a .zip file containing:
  manifest.json           space / subprojects / subscriptions metadata
  docs/<subproject_id>/   one .md file per document (human-readable, diffable)

The restore reads the zip directly — no manual extraction needed.

Examples
--------
# Step 1: find the space ID you want to export
python tools/migration list-spaces

# Step 2: export it (reads DB and docs_root from .env)
python tools/migration dump d9ef3896-2cc3-4c7d-b44e-505dcd07db1e -o my_space.zip

# Step 3: copy my_space.zip to the target machine, then run restore there
#   (reads AGENT_NEXUS_DB_URL and AGENT_NEXUS_DOCS_ROOT from .env on that machine)
python tools/migration restore my_space.zip

Options for each subcommand: pass --help to see them, e.g.
    python tools/migration dump --help
    python tools/migration restore --help
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure the project's src/ directory is on sys.path so that `agent_nexus`
# can be imported regardless of whether the package is installed.
_HERE = os.path.dirname(os.path.abspath(__file__))          # tools/migration/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))     # project root
_SRC = os.path.join(_PROJECT_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from dotenv import load_dotenv

load_dotenv()


def _make_session(db_url: str | None = None):
    """Open a DB session, using AGENT_NEXUS_DB_URL from .env by default."""
    from agent_nexus.mcp.dependencies import make_session_factory

    url = db_url or os.environ.get("AGENT_NEXUS_DB_URL", "sqlite:///agent_nexus.db")
    session = make_session_factory(url)()
    return session


def cmd_list_spaces(args: argparse.Namespace) -> int:
    """List all ProjectSpaces in the current database."""
    session = _make_session()
    try:
        from agent_nexus.models.entities import ProjectSpace
        spaces = session.query(ProjectSpace).order_by(ProjectSpace.created_at).all()
        if not spaces:
            print("No spaces found.")
            return 0
        print(f"{'ID':<38}  {'STATUS':<10}  NAME")
        print("-" * 70)
        for sp in spaces:
            print(f"{sp.id:<38}  {sp.status:<10}  {sp.name}")
        return 0
    finally:
        session.close()


def cmd_dump(args: argparse.Namespace) -> int:
    """Dump a space to a zip archive (reads DB and docs_root from .env)."""
    from dumper import dump_space

    docs_root = os.environ.get("AGENT_NEXUS_DOCS_ROOT", "./workspace")
    session = _make_session()
    try:
        output = args.output or f"{args.space_id}.zip"
        dump_space(
            session,
            args.space_id,
            output,
            docs_root=docs_root,
            include_history=args.include_history,
            include_deleted=args.include_deleted,
        )
        return 0
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore a zip snapshot into the current instance (reads config from .env)."""
    from restorer import restore_from_file

    docs_root = os.environ.get("AGENT_NEXUS_DOCS_ROOT", "./workspace")
    session = _make_session()
    try:
        result = restore_from_file(
            session,
            docs_root,
            args.input,
            new_space_id=args.new_space_id,
            new_space_name=args.new_space_name,
            keep_ids=args.keep_ids,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            session.commit()
        print(result)
        return 1 if result.errors else 0
    except Exception as exc:
        session.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python tools/migration",
        description="AgentNexus space migration tool (reads config from .env)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── list-spaces ───────────────────────────────────────────────────────────
    sub.add_parser("list-spaces", help="List all spaces in the current database")

    # ── dump ──────────────────────────────────────────────────────────────────
    dump_parser = sub.add_parser(
        "dump",
        help="Export a space to a zip archive",
        description="Export a space to a zip archive. Reads AGENT_NEXUS_DB_URL and "
                    "AGENT_NEXUS_DOCS_ROOT from .env automatically.",
    )
    dump_parser.add_argument("space_id", help="ID of the ProjectSpace to export")
    dump_parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        default=None,
        help="Output zip file path (default: <space_id>.zip)",
    )
    dump_parser.add_argument(
        "--include-history",
        action="store_true",
        default=False,
        help="Include all historical versions (default: latest only)",
    )
    dump_parser.add_argument(
        "--include-deleted",
        action="store_true",
        default=False,
        help="Include soft-deleted documents",
    )

    # ── restore ───────────────────────────────────────────────────────────────
    restore_parser = sub.add_parser(
        "restore",
        help="Import a zip archive into the current instance",
        description="Import a zip archive. Reads AGENT_NEXUS_DB_URL and "
                    "AGENT_NEXUS_DOCS_ROOT from .env automatically.",
    )
    restore_parser.add_argument("input", metavar="FILE", help="Path to the zip snapshot file")
    restore_parser.add_argument(
        "--new-space-id",
        metavar="UUID",
        default=None,
        help="Override the space UUID in the target (default: generate a new UUID)",
    )
    restore_parser.add_argument(
        "--new-space-name",
        metavar="NAME",
        default=None,
        help="Override the space name in the target",
    )
    restore_parser.add_argument(
        "--keep-ids",
        action="store_true",
        default=False,
        help="Preserve original UUIDs (safe only when no ID collisions exist in target)",
    )
    restore_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate the snapshot without writing anything",
    )

    args = parser.parse_args()

    dispatch = {
        "list-spaces": cmd_list_spaces,
        "dump": cmd_dump,
        "restore": cmd_restore,
    }
    sys.exit(dispatch[args.command](args))


if __name__ == "__main__":
    main()
