#!/usr/bin/env python3
"""Repository invariants that can be checked without a running Frappe site."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "ai_chatbot"
errors: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


# Every committed JSON file must parse.
for path in sorted(APP.rglob("*.json")):
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")


# Frappe's fixture importer dereferences both doc["doctype"] and doc["name"]
# before autoname executes, so exported fixture rows must contain both keys.
fixtures_dir = APP / "fixtures"
if fixtures_dir.exists():
    for path in sorted(fixtures_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        docs = payload if isinstance(payload, list) else [payload]
        for index, doc in enumerate(docs):
            if not isinstance(doc, dict):
                fail(f"{path.relative_to(ROOT)}[{index}]: fixture row is not an object")
                continue
            if not doc.get("doctype"):
                fail(f"{path.relative_to(ROOT)}[{index}]: missing doctype")
            if not doc.get("name"):
                fail(f"{path.relative_to(ROOT)}[{index}]: missing name")


# Foundry seeds are environment-neutral: no blank assistant id may be enabled.
foundry_fixture = fixtures_dir / "foundry_agent.json"
if foundry_fixture.exists():
    try:
        agents = json.loads(foundry_fixture.read_text(encoding="utf-8"))
        for index, agent in enumerate(agents):
            if agent.get("name") != agent.get("agent_key"):
                fail(f"foundry_agent.json[{index}]: name must equal agent_key")
            if agent.get("enabled") and not str(agent.get("foundry_assistant_id") or "").strip():
                fail(f"foundry_agent.json[{index}]: unconfigured agent cannot be enabled")
    except Exception as exc:
        fail(f"foundry_agent.json: validation failed: {exc}")


# The DocType must permit post-install environment configuration. Its controller
# enforces that an assistant id exists before the record can be enabled.
foundry_doctype = APP / "chatbot/doctype/foundry_agent/foundry_agent.json"
if foundry_doctype.exists():
    try:
        doc = json.loads(foundry_doctype.read_text(encoding="utf-8"))
        field = next((f for f in doc.get("fields", []) if f.get("fieldname") == "foundry_assistant_id"), None)
        if not field:
            fail("Foundry Agent DocType: foundry_assistant_id field missing")
        elif field.get("reqd"):
            fail("Foundry Agent DocType: foundry_assistant_id must not be mandatory at fixture import time")
    except Exception as exc:
        fail(f"Foundry Agent DocType: validation failed: {exc}")


if errors:
    print("Repository validation FAILED:", file=sys.stderr)
    for error in errors:
        print(f" - {error}", file=sys.stderr)
    raise SystemExit(1)

print("Repository validation PASS")
