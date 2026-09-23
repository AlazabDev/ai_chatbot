#!/usr/bin/env python3
"""Production repository invariants that do not require a running Frappe site."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "ai_chatbot"
errors: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


# Backend source must not be duplicated into the frontend tree.
frontend_dir = ROOT / "frontend"
for path in sorted(frontend_dir.rglob("*.py")):
    fail(f"{path.relative_to(ROOT)}: Python backend source must not live under frontend/")


# Legacy direct-write tools are forbidden. All LLM writes must go through
# propose_* -> confirmation -> api.crud execution.
for relative in (
    "ai_chatbot/tools/operations/create.py",
    "ai_chatbot/tools/operations/update.py",
):
    if (ROOT / relative).exists():
        fail(f"{relative}: legacy direct-write tool must not be present")

registry_path = APP / "tools/registry.py"
if registry_path.exists():
    registry_text = registry_path.read_text(encoding="utf-8")
    for legacy_import in (
        "ai_chatbot.tools.operations.create",
        "ai_chatbot.tools.operations.update",
    ):
        if legacy_import in registry_text:
            fail(f"ai_chatbot/tools/registry.py: legacy write module still imported: {legacy_import}")


# ERPNext is a runtime dependency.
hooks_path = APP / "hooks.py"
if hooks_path.exists():
    hooks_text = hooks_path.read_text(encoding="utf-8")
    if 'required_apps = ["erpnext"]' not in hooks_text:
        fail("ai_chatbot/hooks.py: ERPNext must be declared in required_apps")
    for expected in (
        '"Chatbot Conversation": "ai_chatbot.core.permissions.conversation_query"',
        '"Chatbot Message": "ai_chatbot.core.permissions.message_query"',
        '"Chatbot Conversation": "ai_chatbot.core.permissions.conversation_has_permission"',
        '"Chatbot Message": "ai_chatbot.core.permissions.message_has_permission"',
    ):
        if expected not in hooks_text:
            fail(f"ai_chatbot/hooks.py: missing row-level permission hook: {expected}")


# Every committed JSON file must parse.
for path in sorted(APP.rglob("*.json")):
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")


# Frappe fixture importer dereferences doc["doctype"] and doc["name"] before
# autoname executes.
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


# Foundry seeds must be install-safe and environment-neutral.
foundry_fixture = fixtures_dir / "foundry_agent.json"
if foundry_fixture.exists():
    try:
        agents = json.loads(foundry_fixture.read_text(encoding="utf-8"))
        if len(agents) != 8:
            fail(f"foundry_agent.json: expected 8 seed agents, found {len(agents)}")
        for index, agent in enumerate(agents):
            if agent.get("name") != agent.get("agent_key"):
                fail(f"foundry_agent.json[{index}]: name must equal agent_key")
            if agent.get("enabled") and not str(agent.get("foundry_assistant_id") or "").strip():
                fail(f"foundry_agent.json[{index}]: unconfigured agent cannot be enabled")
    except Exception as exc:
        fail(f"foundry_agent.json: validation failed: {exc}")

foundry_doctype = APP / "chatbot/doctype/foundry_agent/foundry_agent.json"
if foundry_doctype.exists():
    try:
        doc = json.loads(foundry_doctype.read_text(encoding="utf-8"))
        field = next(
            (f for f in doc.get("fields", []) if f.get("fieldname") == "foundry_assistant_id"),
            None,
        )
        if not field:
            fail("Foundry Agent DocType: foundry_assistant_id field missing")
        elif field.get("reqd"):
            fail("Foundry Agent DocType: foundry_assistant_id must be optional at fixture import time")
    except Exception as exc:
        fail(f"Foundry Agent DocType: validation failed: {exc}")


# Security contracts that must not regress.
idp_path = APP / "idp/extractors/base.py"
if idp_path.exists():
    idp_text = idp_path.read_text(encoding="utf-8")
    if 'frappe.db.exists("File", {"file_name": file_name})' in idp_text:
        fail("IDP: filename-only File lookup fallback must not be restored")
    if 'file_doc.has_permission("read")' not in idp_text:
        fail("IDP: File read permission check is missing")

formatter_path = APP / "automation/formatters.py"
if formatter_path.exists():
    formatter_text = formatter_path.read_text(encoding="utf-8")
    if "frappe.utils.md_to_html(content)" in formatter_text:
        fail("formatters.py: unsanitized md_to_html path must not be used")
    if "frappe.utils.markdown(content, sanitize=True)" not in formatter_text:
        fail("formatters.py: sanitized markdown conversion is missing")

markdown_path = ROOT / "frontend/src/utils/markdown.js"
if markdown_path.exists():
    markdown_text = markdown_path.read_text(encoding="utf-8")
    for expected in ("function sanitizeHtml", "DROP_CONTENT_TAGS", "isSafeUrl"):
        if expected not in markdown_text:
            fail(f"frontend markdown sanitizer invariant missing: {expected}")

search_path = APP / "tools/operations/search.py"
if search_path.exists():
    search_text = search_path.read_text(encoding="utf-8")
    if "frappe.get_all(" in search_text:
        fail("operations/search.py: permission-bypassing frappe.get_all must not be used")
    if 'frappe.has_permission(doctype, "read"' not in search_text:
        fail("operations/search.py: dynamic DocType read permission check is missing")


if errors:
    print("Repository validation FAILED:", file=sys.stderr)
    for error in errors:
        print(f" - {error}", file=sys.stderr)
    raise SystemExit(1)

print("Repository validation PASS")
