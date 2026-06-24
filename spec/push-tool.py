#!/usr/bin/env python3
"""
[TEMPLATE] AgentNexus document push tool.

Replace {{PROJECT_ID}} below with your actual project_id from register_project,
then run: python nexus_push.py <doc_type> <path/to/file.md>

This script uses plain HTTP (no MCP session required).
Requires: pip install requests
"""

import argparse
import json
import os
import sys

import requests

# ── Fill in your values ──────────────────────────────────────────────────────
SERVER_URL = "http://localhost:10086"  # AgentNexus server URL
PROJECT_ID = "{{PROJECT_ID}}"          # Your project UUID from register_project
# ─────────────────────────────────────────────────────────────────────────────

STATE_FILE = ".kiro/nexus-state.json"


def read_base_version(doc_id: str) -> int | None:
    """Read the local version anchor for this doc_id from nexus-state.json."""
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE) as f:
        state = json.load(f)
    entry = state.get(doc_id)
    return entry.get("local_version") if entry else None


def push_document(doc_id: str, content: str, base_version: int | None = None, metadata: dict | None = None) -> dict:
    """Push a document to AgentNexus via the REST endpoint."""
    payload: dict = {
        "project_id": PROJECT_ID,
        "doc_id": doc_id,
        "content": content,
    }
    if base_version is not None:
        payload["base_version"] = base_version
    if metadata:
        payload["metadata"] = metadata

    resp = requests.post(
        f"{SERVER_URL}/api/documents",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=30,
    )
    if resp.status_code == 409:
        data = resp.json()
        print(f"CONFLICT: {data['message']}", file=sys.stderr)
        print("Pull the latest version, reconcile, and retry.", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()
    return resp.json()


def update_state(doc_id: str, version: int, doc_type: str) -> None:
    """Record the pushed version in the local nexus-state.json file."""
    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
    state[doc_id] = {"local_version": version, "local_file_hint": doc_type}
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"Updated {STATE_FILE}")


def push_file(doc_type: str, file_path: str) -> None:
    """Read a local Markdown file and push it as a document."""
    doc_id = f"{PROJECT_ID}/{doc_type}"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    base_version = read_base_version(doc_id)
    result = push_document(doc_id, content, base_version=base_version)
    print(f"Pushed {doc_id} → version {result['version']} ({result['status']})")
    update_state(doc_id, result["version"], doc_type)


def main() -> None:
    parser = argparse.ArgumentParser(description="Push a document to AgentNexus")
    parser.add_argument("doc_type", help="Document type, e.g. requirement, design, api")
    parser.add_argument("file", help="Path to the Markdown file to push")
    args = parser.parse_args()

    if PROJECT_ID == "{{PROJECT_ID}}":
        print("ERROR: Replace {{PROJECT_ID}} with your actual project_id before running.")
        sys.exit(1)

    push_file(args.doc_type, args.file)


if __name__ == "__main__":
    main()
