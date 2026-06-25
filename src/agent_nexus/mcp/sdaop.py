"""SDAOP protocol version helpers.

The SDAOP (Service-Driven Agent Onboarding Protocol) ships two client-side
artifacts: a steering file (per client_type) and a push-tool script. When
the service updates either artifact, existing workspaces need to know so
they can regenerate.

This module computes a deterministic version string per client_type by
hashing the actual template content used to render that client's steering
file and push tool. Any change in those templates yields a new version.

See v4-ideas §15.3 / future §21 for the design rationale.
"""

from __future__ import annotations

import hashlib
import json
import os

_SPEC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "spec",
)
_INSTRUCTIONS_DIR = os.path.join(_SPEC_DIR, "instructions")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _clients_config() -> dict:
    return json.loads(_read(os.path.join(_INSTRUCTIONS_DIR, "clients.json")))


def compute_sdaop_version(client_type: str = "kiro") -> str:
    """Compute the SDAOP version for a given client_type.

    The version is a short hex digest derived from the concatenation of:
      - common.md
      - the client's specific template (kiro.md / claude.md / ...)
      - push-tool.py

    Identical templates yield identical versions. Any edit to any of the
    three files changes the version.
    """
    clients = _clients_config()
    client_key = client_type.lower()
    config = clients.get(client_key, clients["default"])

    parts = [
        _read(os.path.join(_INSTRUCTIONS_DIR, "common.md")),
        _read(os.path.join(_INSTRUCTIONS_DIR, config["template"])),
        _read(os.path.join(_SPEC_DIR, "push-tool.py")),
    ]
    combined = "\n---\n".join(parts).encode("utf-8")
    # Short prefix is enough for collision-resistant equality checks at
    # workspace scale (number of distinct versions ever published is small).
    return hashlib.sha256(combined).hexdigest()[:12]


def known_client_types() -> list[str]:
    """Return the list of client_type keys defined in clients.json."""
    return sorted(_clients_config().keys())
