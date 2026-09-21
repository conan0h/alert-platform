"""The spec schema must validate without reaching the network.

tools/validate.py is not only a developer convenience: it is gate 1 of every
`alertctl apply`, so it runs on the host, against whatever `jsonschema` that
host has, at the moment of a deploy. Two properties keep that honest, and
neither is checked by validating a spec — a schema can be wrong in these ways
and still pass on the machine that wrote it.

The first is why CI was red: `$id` was `alertplatform/v1/service`, a bare
relative path. Resolvers old enough to use the deprecated `RefResolver` join
`#/$defs/secretName` onto that base, get `alertplatform/v1/alertplatform/v1/
service`, and try to fetch it:

    ValueError: unknown url type: 'alertplatform/v1/alertplatform/v1/service'

Newer `jsonschema` tolerates it, so the failure only appeared where the old
one was installed — the CI job that has no `pip install jsonschema` and falls
back to the distro package. A URN fixes it and cannot be mistaken for
something fetchable.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "schema" / "service.schema.json"


def load() -> dict:
    return json.loads(SCHEMA.read_text())


def test_id_is_an_absolute_uri() -> None:
    """A relative $id is not a usable base, and old resolvers dereference it."""
    schema_id = load()["$id"]
    assert urlparse(schema_id).scheme, (
        f"$id {schema_id!r} is relative; it must be an absolute URI so that "
        "local $refs resolve against it instead of being fetched"
    )


def test_every_ref_is_local() -> None:
    """A deploy gate must never depend on a network fetch to validate a spec."""
    remote: list[str] = []

    def walk(node, trail="") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str) and not value.startswith("#"):
                    remote.append(f"{trail}: {value}")
                walk(value, f"{trail}.{key}" if trail else key)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{trail}[{i}]")

    walk(load())
    assert not remote, f"schema has non-local $ref(s), which a host may not be able to fetch: {remote}"
