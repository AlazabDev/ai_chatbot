# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Generic CRUD Proposal Tools (Phase 13B)

Provides four ``propose_*`` tools that the LLM can call to propose
creating, updating, submitting, or cancelling ERPNext documents.
These tools perform validation but do NOT execute the action — they
return a confirmation payload that the frontend renders as a
ConfirmationCard.  The actual execution happens when the user clicks
"Confirm" in the card, which calls the ``ai_chatbot.api.crud``
endpoints.

Confirmation payloads are stored in Redis with a 15-minute TTL.
"""

import json
import uuid

import frappe

from ai_chatbot.core.config import get_default_company
from ai_chatbot.data.prerequisites import detect_prerequisites
from ai_chatbot.data.validators import (
	check_permission,
	validate_child_table_items,
	validate_link_fields,
	validate_mandatory_fields,
)
from ai_chatbot.tools.registry import register_tool

# Redis key prefix and TTL for pending confirmations
_CACHE_PREFIX = "chatbot_confirmation:"
_CONFIRMATION_TTL = 900  # 15 minutes


# ── Proposal Tools (exposed to LLM) ─────────────────────────────────


@register_tool(
	name="propose_create_document",
	category="operations",
	description=(
		"Propose creating a new ERPNext document. Validates permissions and "
		"field data, then returns a confirmation card for the user to review. "
		"The document is NOT created until the user clicks Confirm."
	),
	parameters={
		"doctype": {
			"type": "string",
			"description": (
				"DocType name (e.g. 'Sales Order', 'Purchase Order', 'Lead', "
				"'Opportunity', 'Customer', 'Supplier', 'Item', 'ToDo')"
			),
		},
		"values": {
			"type": "object",
			"description": (
				"Field values for the new document. Include all known fields. "
				"Child tables (e.g. 'items') should be arrays of objects."
			),
		},
		"company": {
			"type": "string",
			"description": "Company name. Defaults to user's default company.",
		},
	},
	doctypes=[],
)
def propose_create_document(doctype, values, company=None):
	"""Propose creating a new document — validate without executing."""
	if not doctype:
		return {"error": "doctype parameter is required"}
	if not isinstance(values, dict):
		return {"error": "values must be an object with field-value pairs"}

	errors = []
	warnings = []

	# Permission check
	if not check_permission(doctype, "create"):
		return {"error": f"You do not have permission to create {doctype}"}

	# Auto-inject company
	if _doctype_has_field(doctype, "company") and "company" not in values:
		values["company"] = get_default_company(company)

	# Mandatory field check — treated as warnings, not errors.
	# ERPNext often auto-fills mandatory fields during doc.insert() via
	# Property Setters, controller hooks, or global defaults that we
	# cannot detect from DocType meta alone.  If a field is truly missing
	# the actual insert will raise a validation error at confirm time.
	missing = validate_mandatory_fields(doctype, values)
	if missing:
		labels = _get_field_labels(doctype, missing)
		warnings.append(f"Missing required fields: {', '.join(labels)}")

	# Detect missing prerequisites (Customer, Supplier, Items, UOMs).
	# This also fuzzy-resolves values in-place where possible.
	resolved_company = values.get("company") or get_default_company(company)
	prerequisites = detect_prerequisites(doctype, values, resolved_company)

	# Extract item_mapping before passing prerequisites to the card
	item_mapping = prerequisites.pop("item_mapping", [])

	# Collect names of all missing prerequisite values so we can filter
	# them out of the link/child validation errors (they will be created).
	prereq_values = _collect_prerequisite_values(prerequisites)

	# Link field validation — filter out errors for prerequisite records
	link_errors = validate_link_fields(doctype, values)
	link_errors = [e for e in link_errors if not _is_prerequisite_error(e, prereq_values)]
	errors.extend(link_errors)

	# Child table validation — filter out prerequisite-related errors
	child_errors = validate_child_table_items(doctype, values)
	child_errors = [e for e in child_errors if not _is_prerequisite_error(e, prereq_values)]
	errors.extend(child_errors)

	# Build display preview — suppress child tables covered by item_mapping
	display_fields = _build_display_fields(doctype, values)
	exclude_tables = {e["child_table_field"] for e in item_mapping} if item_mapping else None
	child_tables = _build_child_table_preview(doctype, values, exclude_tables=exclude_tables)

	# Check if the DocType is submittable (has workflow: Draft → Submit → Cancel)
	is_submittable = bool(frappe.get_meta(doctype).is_submittable)

	confirmation_id = str(uuid.uuid4())

	has_prereqs = prerequisites.get("has_prerequisites", False)

	payload = {
		"confirmation_required": True,
		"confirmation_id": confirmation_id,
		"action": "create",
		"doctype": doctype,
		"name": None,
		"values": values,
		"display_fields": display_fields,
		"child_tables": child_tables,
		"previous_values": None,
		"is_submittable": is_submittable,
		"prerequisites": prerequisites if has_prereqs else None,
		"item_mapping": item_mapping if item_mapping else None,
		"warnings": warnings,
		"errors": errors,
		"message": _build_create_message(doctype, errors, prerequisites),
	}

	# Store in Redis
	_store_pending_confirmation(confirmation_id, payload)

	return payload


@register_tool(
	name="propose_update_document",
	category="operations",
	description=(
		"Propose updating an existing ERPNext document. Shows a diff of "
		"changes for the user to review. The update is NOT applied until "
		"the user clicks Confirm."
	),
	parameters={
		"doctype": {
			"type": "string",
			"description": "DocType name (e.g. 'Lead', 'Sales Order', 'Customer')",
		},
		"name": {
			"type": "string",
			"description": "Document name/ID to update (e.g. 'CRM-LEAD-00001')",
		},
		"values": {
			"type": "object",
			"description": "Fields to update with new values",
		},
	},
	doctypes=[],
)
def propose_update_document(doctype, name, values):
	"""Propose updating a document — validate and show diff without executing."""
	if not doctype or not name:
		return {"error": "doctype and name parameters are required"}
	if not isinstance(values, dict) or not values:
		return {"error": "values must be a non-empty object with field-value pairs"}

	errors = []
	warnings = []

	# Existence check
	if not frappe.db.exists(doctype, name):
		return {"error": f"{doctype} '{name}' does not exist"}

	# Permission check
	if not check_permission(doctype, "write", name):
		return {"error": f"You do not have permission to update {doctype} '{name}'"}

	# Link field validation
	link_errors = validate_link_fields(doctype, values)
	errors.extend(link_errors)

	# Child table validation
	child_errors = validate_child_table_items(doctype, values)
	errors.extend(child_errors)

	# Capture current values for diff display and undo
	doc = frappe.get_doc(doctype, name)
	previous_values = {}
	for field in values:
		current_val = doc.get(field)
		if current_val is not None:
			previous_values[field] = current_val

	# Build display fields showing old → new
	display_fields = _build_update_display_fields(doctype, name, values, previous_values)
	child_tables = _build_child_table_preview(doctype, values)

	confirmation_id = str(uuid.uuid4())

	payload = {
		"confirmation_required": True,
		"confirmation_id": confirmation_id,
		"action": "update",
		"doctype": doctype,
		"name": name,
		"values": values,
		"display_fields": display_fields,
		"child_tables": child_tables,
		"previous_values": previous_values,
		"warnings": warnings,
		"errors": errors,
		"message": (
			f"Ready to update {doctype} '{name}'. Please review the changes in the confirmation card."
			if not errors
			else f"Cannot update {doctype} '{name}': {'; '.join(errors)}"
		),
	}

	_store_pending_confirmation(confirmation_id, payload)
	return payload


@register_tool(
	name="propose_submit_document",
	category="operations",
	description=(
		"Propose submitting a Draft document. Submitting makes the document "
		"permanent (cannot be easily reverted). Returns a confirmation card."
	),
	parameters={
		"doctype": {
			"type": "string",
			"description": "DocType name (e.g. 'Sales Invoice', 'Purchase Order')",
		},
		"name": {
			"type": "string",
			"description": "Document name/ID to submit",
		},
	},
	doctypes=[],
)
def propose_submit_document(doctype, name):
	"""Propose submitting a draft document — validate without executing."""
	if not doctype or not name:
		return {"error": "doctype and name parameters are required"}

	errors = []
	warnings = []

	# Existence check
	if not frappe.db.exists(doctype, name):
		return {"error": f"{doctype} '{name}' does not exist"}

	# Permission check
	if not check_permission(doctype, "submit", name):
		return {"error": f"You do not have permission to submit {doctype} '{name}'"}

	# Docstatus check
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus != 0:
		return {"error": f"{doctype} '{name}' is not a Draft (docstatus={doc.docstatus})"}

	warnings.append("Submitting a document is a permanent action and cannot be easily undone.")

	# Build display fields from the document
	display_fields = _build_display_fields_from_doc(doc)

	confirmation_id = str(uuid.uuid4())

	payload = {
		"confirmation_required": True,
		"confirmation_id": confirmation_id,
		"action": "submit",
		"doctype": doctype,
		"name": name,
		"values": {},
		"display_fields": display_fields,
		"child_tables": [],
		"previous_values": None,
		"warnings": warnings,
		"errors": errors,
		"message": f"Ready to submit {doctype} '{name}'. This action is permanent.",
	}

	_store_pending_confirmation(confirmation_id, payload)
	return payload


@register_tool(
	name="propose_cancel_document",
	category="operations",
	description=(
		"Propose cancelling a submitted document. Cancellation creates an "
		"amendment entry. Returns a confirmation card."
	),
	parameters={
		"doctype": {
			"type": "string",
			"description": "DocType name (e.g. 'Sales Invoice', 'Purchase Order')",
		},
		"name": {
			"type": "string",
			"description": "Document name/ID to cancel",
		},
	},
	doctypes=[],
)
def propose_cancel_document(doctype, name):
	"""Propose cancelling a submitted document — validate without executing."""
	if not doctype or not name:
		return {"error": "doctype and name parameters are required"}

	errors = []
	warnings = []

	# Existence check
	if not frappe.db.exists(doctype, name):
		return {"error": f"{doctype} '{name}' does not exist"}

	# Permission check
	if not check_permission(doctype, "cancel", name):
		return {"error": f"You do not have permission to cancel {doctype} '{name}'"}

	# Docstatus check
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus != 1:
		return {"error": f"{doctype} '{name}' is not Submitted (docstatus={doc.docstatus})"}

	warnings.append("Cancelling a submitted document is permanent and cannot be undone via chatbot.")

	display_fields = _build_display_fields_from_doc(doc)

	confirmation_id = str(uuid.uuid4())

	payload = {
		"confirmation_required": True,
		"confirmation_id": confirmation_id,
		"action": "cancel",
		"doctype": doctype,
		"name": name,
		"values": {},
		"display_fields": display_fields,
		"child_tables": [],
		"previous_values": None,
		"warnings": warnings,
		"errors": errors,
		"message": f"Ready to cancel {doctype} '{name}'. This action is permanent.",
	}

	_store_pending_confirmation(confirmation_id, payload)
	return payload


# ── Redis Cache Helpers ──────────────────────────────────────────────


def _store_pending_confirmation(confirmation_id, payload):
	"""Store a pending confirmation payload in Redis with TTL.

	Also stamps ``expires_at`` (ISO datetime) into the payload so the
	frontend can display a countdown timer to the user.

	Args:
		confirmation_id: Unique UUID string.
		payload: Dict to store (will be JSON-serialized).
	"""
	from frappe.utils import add_to_date, now_datetime

	expires_at = add_to_date(now_datetime(), seconds=_CONFIRMATION_TTL).isoformat()
	payload["expires_at"] = expires_at

	key = f"{_CACHE_PREFIX}{confirmation_id}"
	stored = {
		"user": frappe.session.user,
		"payload": payload,
	}
	frappe.cache().set_value(key, json.dumps(stored, default=str), expires_in_sec=_CONFIRMATION_TTL)


def load_pending_confirmation(confirmation_id, user=None):
	"""Load a pending confirmation payload and enforce token ownership."""
	key = f"{_CACHE_PREFIX}{confirmation_id}"
	data = frappe.cache().get_value(key)
	if not data:
		return None

	stored = json.loads(data) if isinstance(data, str) else data
	current_user = user or frappe.session.user

	# New format keeps ownership metadata outside the user-visible payload.
	# The legacy shape is accepted only when it contains an explicit owner.
	if isinstance(stored, dict) and "payload" in stored:
		stored_user = stored.get("user")
		payload = stored.get("payload")
	else:
		stored_user = stored.get("user") if isinstance(stored, dict) else None
		payload = stored

	if not stored_user or stored_user != current_user:
		frappe.throw("You do not have permission to use this confirmation.", frappe.PermissionError)
	if not isinstance(payload, dict):
		return None
	return payload


def delete_pending_confirmation(confirmation_id, user=None):
	"""Delete a pending confirmation after verifying token ownership."""
	payload = load_pending_confirmation(confirmation_id, user=user)
	if not payload:
		return False
	key = f"{_CACHE_PREFIX}{confirmation_id}"
	frappe.cache().delete_value(key)
	return True


# ── Display Field Builders ───────────────────────────────────────────


def _build_display_fields(doctype, values):
	"""Build a list of display-friendly field dicts from values.

	Reads DocType meta to get labels and field types. Only includes
	fields that have a value and are not child tables or system fields.

	Args:
		doctype: DocType name.
		values: Dict of field→value.

	Returns:
		List of dicts: [{label, fieldname, value, fieldtype}, ...]
	"""
	meta = frappe.get_meta(doctype)
	fields = []
	field_map = {df.fieldname: df for df in meta.fields}

	# System fields to skip
	skip_fields = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "doctype"}

	for fieldname, value in values.items():
		if fieldname in skip_fields:
			continue

		df = field_map.get(fieldname)
		if not df:
			continue

		# Skip child table fields (handled separately)
		if df.fieldtype == "Table":
			continue

		# Skip empty values
		if value is None or value == "":
			continue

		fields.append(
			{
				"label": df.label or fieldname,
				"fieldname": fieldname,
				"value": value,
				"fieldtype": df.fieldtype,
			}
		)

	return fields


def _build_update_display_fields(doctype, name, new_values, previous_values):
	"""Build display fields for an update showing old → new values.

	Args:
		doctype: DocType name.
		name: Document name.
		new_values: Dict of field→new_value.
		previous_values: Dict of field→old_value.

	Returns:
		List of dicts with old_value and value fields.
	"""
	meta = frappe.get_meta(doctype)
	fields = []
	field_map = {df.fieldname: df for df in meta.fields}

	for fieldname, new_value in new_values.items():
		df = field_map.get(fieldname)
		if not df or df.fieldtype == "Table":
			continue

		old_value = previous_values.get(fieldname)
		fields.append(
			{
				"label": df.label or fieldname,
				"fieldname": fieldname,
				"value": new_value,
				"old_value": old_value,
				"fieldtype": df.fieldtype,
			}
		)

	return fields


def _build_display_fields_from_doc(doc):
	"""Build display fields from an existing document for submit/cancel preview.

	Shows key identifying fields from the document.

	Args:
		doc: Frappe document object.

	Returns:
		List of display field dicts.
	"""
	meta = frappe.get_meta(doc.doctype)

	# Show key fields: title, name-like fields, status, amounts
	priority_fields = []
	for df in meta.fields:
		if df.fieldtype in ("Table", "Section Break", "Column Break", "Tab Break"):
			continue
		if df.hidden or df.fieldname.startswith("_"):
			continue

		# Prioritize: title fields, name-like fields, status, monetary fields
		is_priority = (
			df.in_list_view
			or df.reqd
			or df.fieldname in ("title", "status", "grand_total", "total", "customer", "supplier")
			or df.fieldname.endswith("_name")
		)
		if is_priority:
			value = doc.get(df.fieldname)
			if value is not None and value != "":
				priority_fields.append(
					{
						"label": df.label or df.fieldname,
						"fieldname": df.fieldname,
						"value": value,
						"fieldtype": df.fieldtype,
					}
				)

	# Cap at 12 fields to keep the card compact
	return priority_fields[:12]


def _build_child_table_preview(doctype, values, exclude_tables=None):
	"""Build structured child table previews for the confirmation card.

	Args:
		doctype: Parent DocType name.
		values: Dict of field→value including child table lists.
		exclude_tables: Optional set of child table fieldnames to skip
			(e.g. when the unified Item Mapping table covers them).

	Returns:
		List of child table dicts with label, fieldname, columns, rows.
	"""
	meta = frappe.get_meta(doctype)
	tables = []

	for df in meta.fields:
		if df.fieldtype != "Table" or df.fieldname not in values:
			continue
		if exclude_tables and df.fieldname in exclude_tables:
			continue

		rows = values[df.fieldname]
		if not isinstance(rows, list) or not rows:
			continue

		child_meta = frappe.get_meta(df.options)

		# Determine visible columns from child meta
		columns = []
		for child_df in child_meta.fields:
			if child_df.in_list_view or child_df.reqd:
				columns.append(
					{
						"fieldname": child_df.fieldname,
						"label": child_df.label or child_df.fieldname,
						"fieldtype": child_df.fieldtype,
					}
				)

		# If no in_list_view columns, use first 5 non-hidden fields
		if not columns:
			for child_df in child_meta.fields[:5]:
				if child_df.fieldtype not in ("Section Break", "Column Break", "Tab Break"):
					columns.append(
						{
							"fieldname": child_df.fieldname,
							"label": child_df.label or child_df.fieldname,
							"fieldtype": child_df.fieldtype,
						}
					)

		# Build row data (only include column fields)
		col_names = [c["fieldname"] for c in columns]
		table_rows = []
		for row in rows[:10]:  # Cap at 10 rows for preview
			if isinstance(row, dict):
				table_rows.append({c: row.get(c, "") for c in col_names})

		if table_rows:
			tables.append(
				{
					"label": df.label or df.fieldname,
					"fieldname": df.fieldname,
					"columns": columns,
					"rows": table_rows,
					"total_rows": len(rows),
				}
			)

	return tables


# ── Internal Helpers ─────────────────────────────────────────────────


def _doctype_has_field(doctype, fieldname):
	"""Check if a DocType has a specific field."""
	meta = frappe.get_meta(doctype)
	return meta.has_field(fieldname)


def _get_field_labels(doctype, fieldnames):
	"""Get human-readable labels for a list of field names.

	Args:
		doctype: DocType name.
		fieldnames: List of field names.

	Returns:
		List of label strings.
	"""
	meta = frappe.get_meta(doctype)
	field_map = {df.fieldname: df.label or df.fieldname for df in meta.fields}
	return [field_map.get(f, f) for f in fieldnames]


def _collect_prerequisite_values(prerequisites):
	"""Extract the set of all missing value strings from prerequisites.

	Used to filter out validation errors that refer to records which
	will be created as part of the confirmation flow.
	"""
	values = set()
	if not prerequisites or not prerequisites.get("has_prerequisites"):
		return values
	for p in prerequisites.get("missing_parties", []):
		values.add(p["value"])
	for p in prerequisites.get("missing_items", []):
		values.add(p["value"])
	for p in prerequisites.get("missing_uoms", []):
		values.add(p["value"])
	for p in prerequisites.get("missing_accounts", []):
		values.add(p["value"])
	return values


def _is_prerequisite_error(error_str, prerequisite_values):
	"""Check if a validation error refers to a known prerequisite value."""
	for val in prerequisite_values:
		if val in error_str:
			return True
	return False


def _build_create_message(doctype, errors, prerequisites):
	"""Build a user-facing message for a create proposal."""
	if errors:
		return f"Cannot create {doctype}: {'; '.join(errors)}"

	parts = [f"Ready to create {doctype}."]

	if prerequisites and prerequisites.get("has_prerequisites"):
		prereq_items = []
		for p in prerequisites.get("missing_parties", []):
			prereq_items.append(f"{p['doctype']} '{p['value']}'")
		for p in prerequisites.get("missing_items", []):
			prereq_items.append(f"Item '{p['value']}'")
		for p in prerequisites.get("missing_accounts", []):
			prereq_items.append(f"Account '{p['value']}'")
		if prereq_items:
			parts.append(f"Will also create: {', '.join(prereq_items)}.")

	parts.append("Please review the details in the confirmation card.")
	return " ".join(parts)
