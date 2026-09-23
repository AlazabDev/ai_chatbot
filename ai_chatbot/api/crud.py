# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
CRUD Confirmation API Endpoints (Phase 13B)

Provides ``@frappe.whitelist()`` endpoints that the frontend calls when
the user clicks Confirm, Cancel, or Undo on a ConfirmationCard.

- ``confirm_action`` — execute a previously proposed write action
- ``cancel_action`` — discard a proposed action (user declined)
- ``undo_action`` — reverse a recently confirmed action (5-min TTL)
"""

import json
import uuid

import frappe

from ai_chatbot.core.logger import log_error, log_info
from ai_chatbot.data.operations import (
	cancel_document,
	create_document,
	get_document_values,
	submit_document,
	update_document,
)
from ai_chatbot.data.prerequisites import execute_prerequisites
from ai_chatbot.data.validators import check_permission
from ai_chatbot.tools.crud import (
	delete_pending_confirmation,
	load_pending_confirmation,
)

# Redis prefix and TTL for undo tokens
_UNDO_PREFIX = "chatbot_undo:"
_UNDO_TTL = 300  # 5 minutes

_ACTION_FAILED = "The requested action could not be completed."


def _user_facing_error(error: Exception) -> str:
	if isinstance(
		error,
		(
			frappe.PermissionError,
			frappe.ValidationError,
			frappe.DoesNotExistError,
		),
	):
		return str(error)
	return _ACTION_FAILED


@frappe.whitelist()
def confirm_action(
	confirmation_id: str,
	user_overrides: str | None = None,
	submit_after_create: bool = False,
) -> dict:
	"""Execute a previously proposed write action after user confirmation.

	Loads the pending confirmation from Redis, re-validates permissions,
	executes the action, stores undo metadata, and returns the result.

	For ``create`` actions, also handles:
	- **Prerequisites**: creates missing Customer/Supplier, Items, UOMs
	  before the main document (with user-edited field overrides).
	- **Submit-after-create**: if the user clicked "Submit" instead of
	  "Save Draft", the document is created then immediately submitted.

	Args:
		confirmation_id: UUID of the pending confirmation.
		user_overrides: JSON string with user-edited prerequisite fields.
		submit_after_create: If True, submit the document after creating it.

	Returns:
		dict with success status, document info, and undo token.
	"""
	# Frappe may pass submit_after_create as a string from the POST body
	if isinstance(submit_after_create, str):
		submit_after_create = submit_after_create.lower() in ("true", "1")

	try:
		payload = load_pending_confirmation(confirmation_id)
		if not payload:
			return {
				"success": False,
				"error": "This confirmation has expired. Please ask the AI to propose the action again.",
			}

		action = payload.get("action")
		doctype = payload.get("doctype")
		name = payload.get("name")
		values = payload.get("values", {})

		# Check for blocking validation errors
		errors = payload.get("errors", [])
		if errors:
			return {
				"success": False,
				"error": f"Cannot proceed: {'; '.join(errors)}",
			}

		result = None
		undo_token = None
		undo_expires = None
		created_prerequisites = []

		if action == "create":
			# Re-check create permission
			if not check_permission(doctype, "create"):
				return {"success": False, "error": f"You no longer have permission to create {doctype}"}

			# Parse user overrides once (used for both prerequisites and mapping)
			overrides = {}
			if user_overrides:
				overrides = json.loads(user_overrides) if isinstance(user_overrides, str) else user_overrides

			# Execute prerequisites (missing parties, items, UOMs) if present
			prerequisites = payload.get("prerequisites")
			if prerequisites and prerequisites.get("has_prerequisites"):
				_merge_overrides_into_prerequisites(prerequisites, overrides)

				prereq_result = execute_prerequisites(prerequisites, values.get("company"))
				if not prereq_result["success"]:
					return {
						"success": False,
						"error": f"Failed to create prerequisites: {'; '.join(prereq_result['errors'])}",
					}

				# Fix up document values with actual created record names
				_apply_name_map(values, prereq_result["name_map"])
				created_prerequisites = prereq_result["created"]

			# Apply item mapping overrides (user changed item_code / uom / item_group)
			mapping_overrides = overrides.get("mapping_overrides")
			if mapping_overrides:
				_apply_mapping_overrides(values, mapping_overrides)

			# Create the main document
			result = create_document(doctype, values)

			# Optionally submit after creation
			if submit_after_create and result.get("name"):
				try:
					submit_document(doctype, result["name"])
					result["message"] = f"{doctype} '{result['name']}' created and submitted successfully."
					result["submitted"] = True
				except Exception as e:
					# Created OK but submit failed — inform user
					result["message"] = f"{doctype} '{result['name']}' saved as Draft. Submit failed: {e!s}"
					result["submitted"] = False

			# Undo is only available for draft documents (not submitted)
			if not submit_after_create:
				undo_token = _store_undo_metadata(action, doctype, result["name"], previous_values=None)
				undo_expires = _undo_expiry_iso()

		elif action == "update":
			if not name:
				return {"success": False, "error": "Document name is missing"}

			# Re-check write permission
			if not check_permission(doctype, "write", name):
				return {
					"success": False,
					"error": f"You no longer have permission to update {doctype} '{name}'",
				}

			# Capture current values for undo before updating
			if values:
				prev = get_document_values(doctype, name, list(values.keys()))
			else:
				prev = {}

			result = update_document(doctype, name, values)
			undo_token = _store_undo_metadata(action, doctype, name, previous_values=prev)
			undo_expires = _undo_expiry_iso()

		elif action == "submit":
			if not name:
				return {"success": False, "error": "Document name is missing"}

			result = submit_document(doctype, name)
			# Submit cannot be undone via chatbot
			undo_token = None

		elif action == "cancel":
			if not name:
				return {"success": False, "error": "Document name is missing"}

			result = cancel_document(doctype, name)
			# Cancel cannot be undone via chatbot
			undo_token = None

		else:
			return {"success": False, "error": f"Unknown action: {action}"}

		# Clean up the pending confirmation from Redis
		delete_pending_confirmation(confirmation_id)

		# Update the message's confirmation_state
		_update_confirmation_state(confirmation_id, "confirmed", result, undo_token, undo_expires)

		log_info(
			"CRUD action confirmed",
			action=action,
			doctype=doctype,
			name=result.get("name") if result else name,
			user=frappe.session.user,
		)

		response = {
			"success": True,
			"action": action,
			"doctype": doctype,
			"name": result.get("name") if result else name,
			"doc_url": result.get("doc_url", ""),
			"message": result.get("message", ""),
			"undo_token": undo_token,
			"undo_expires": undo_expires,
		}

		if created_prerequisites:
			response["created_prerequisites"] = created_prerequisites

		return response

	except Exception as e:
		log_error(f"CRUD confirm_action error: {e!s}", title="CRUD Confirm")
		return {"success": False, "error": _user_facing_error(e)}


@frappe.whitelist()
def cancel_action(confirmation_id: str) -> dict:
	"""Cancel a proposed action (user clicked Cancel).

	Removes the pending confirmation from Redis and updates the
	message's confirmation_state.

	Args:
		confirmation_id: UUID of the pending confirmation.

	Returns:
		dict with success status.
	"""
	try:
		delete_pending_confirmation(confirmation_id)
		_update_confirmation_state(confirmation_id, "declined")

		log_info("CRUD action declined", confirmation_id=confirmation_id, user=frappe.session.user)

		return {"success": True, "message": "Action cancelled."}

	except Exception as e:
		log_error(f"CRUD cancel_action error: {e!s}", title="CRUD Cancel")
		return {"success": False, "error": _user_facing_error(e)}


@frappe.whitelist()
def undo_action(undo_token: str) -> dict:
	"""Undo a previously confirmed action.

	Validates the undo token hasn't expired (5-minute TTL) and
	executes the reverse operation.

	- Create undo: deletes the draft document (if still docstatus=0)
	- Update undo: reverts to previous field values
	- Submit/Cancel: cannot be undone via chatbot

	Args:
		undo_token: UUID of the undo metadata.

	Returns:
		dict with success status.
	"""
	try:
		key = f"{_UNDO_PREFIX}{undo_token}"
		data = frappe.cache().get_value(key)
		if not data:
			return {
				"success": False,
				"error": "Undo is no longer available (expired after 5 minutes).",
			}

		metadata = json.loads(data) if isinstance(data, str) else data
		if metadata.get("user") != frappe.session.user:
			frappe.throw(
				"You do not have permission to use this undo token.",
				frappe.PermissionError,
			)

		action = metadata.get("action")
		doctype = metadata.get("doctype")
		name = metadata.get("name")

		if action == "create":
			# Undo create = delete the draft document
			if not frappe.db.exists(doctype, name):
				return {"success": False, "error": f"{doctype} '{name}' no longer exists."}

			doc = frappe.get_doc(doctype, name)
			if doc.docstatus != 0:
				return {
					"success": False,
					"error": f"Cannot undo: {doctype} '{name}' has been submitted (docstatus={doc.docstatus}).",
				}

			if not check_permission(doctype, "delete", name):
				return {
					"success": False,
					"error": f"You do not have permission to delete {doctype} '{name}'.",
				}

			frappe.delete_doc(doctype, name, force=True)
			frappe.db.commit()

			log_info("CRUD undo create", doctype=doctype, name=name, user=frappe.session.user)

		elif action == "update":
			# Undo update = revert to previous values
			previous_values = metadata.get("previous_values", {})
			if not previous_values:
				return {"success": False, "error": "No previous values stored for undo."}

			if not frappe.db.exists(doctype, name):
				return {"success": False, "error": f"{doctype} '{name}' no longer exists."}

			if not check_permission(doctype, "write", name):
				return {
					"success": False,
					"error": f"You do not have permission to update {doctype} '{name}'.",
				}

			update_document(doctype, name, previous_values)

			log_info("CRUD undo update", doctype=doctype, name=name, user=frappe.session.user)

		else:
			return {"success": False, "error": f"'{action}' actions cannot be undone via chatbot."}

		# Remove the undo token
		frappe.cache().delete_value(key)

		return {
			"success": True,
			"message": f"Successfully undone: {action} {doctype} '{name}'.",
		}

	except Exception as e:
		log_error(f"CRUD undo_action error: {e!s}", title="CRUD Undo")
		return {"success": False, "error": _user_facing_error(e)}


# ── Internal Helpers ─────────────────────────────────────────────────


def _store_undo_metadata(action, doctype, name, previous_values=None):
	"""Store undo metadata in Redis with 5-minute TTL.

	Args:
		action: "create" or "update".
		doctype: DocType name.
		name: Document name.
		previous_values: Dict of previous field values (for update undo).

	Returns:
		str: Undo token UUID.
	"""
	token = str(uuid.uuid4())
	key = f"{_UNDO_PREFIX}{token}"
	metadata = {
		"action": action,
		"doctype": doctype,
		"name": name,
		"previous_values": previous_values,
		"user": frappe.session.user,
	}
	frappe.cache().set_value(key, json.dumps(metadata, default=str), expires_in_sec=_UNDO_TTL)
	return token


def _undo_expiry_iso():
	"""Return the ISO timestamp when the undo token will expire (now + 5 min)."""
	from frappe.utils import add_to_date, now_datetime

	return add_to_date(now_datetime(), minutes=5).isoformat()


def _merge_overrides_into_prerequisites(prerequisites, overrides):
	"""Merge user-provided field values from the frontend into prerequisites.

	The *overrides* dict has the shape::

		{
			"parties":  {"Samson System": {"default_currency": "USD", ...}},
			"items":    {"Wireless Keyboard": {"is_stock_item": 1, ...}},
			"accounts": {"IGST - TT": {"account_name": "IGST", ...}}
		}
	"""
	party_overrides = overrides.get("parties", {})
	for party in prerequisites.get("missing_parties", []):
		user_vals = party_overrides.get(party["value"])
		if user_vals:
			party["user_overrides"] = user_vals

	item_overrides = overrides.get("items", {})
	for item in prerequisites.get("missing_items", []):
		user_vals = item_overrides.get(item["value"])
		if user_vals:
			item["user_overrides"] = user_vals

	account_overrides = overrides.get("accounts", {})
	for account in prerequisites.get("missing_accounts", []):
		user_vals = account_overrides.get(account["value"])
		if user_vals:
			account["user_overrides"] = user_vals


def _apply_name_map(values, name_map):
	"""Fix up document values after prerequisites are created.

	ERPNext auto-naming might produce a ``name`` different from the
	user-provided value (e.g. naming series).  The *name_map* maps
	``field → {user_value → actual_name}``.

	Mutates *values* in-place.
	"""
	for field, mapping in name_map.items():
		# Top-level field (e.g. customer)
		if field in values:
			old = values[field]
			if old in mapping:
				values[field] = mapping[old]

		# Child table rows (e.g. item_code in items)
		for _key, val in values.items():
			if isinstance(val, list):
				for row in val:
					if isinstance(row, dict) and field in row:
						old = row[field]
						if old in mapping:
							row[field] = mapping[old]


def _apply_mapping_overrides(values, mapping_overrides):
	"""Apply user-changed item mappings to child table row values.

	Called when the user edits item_code, uom, or item_group in the
	unified Item Mapping table on the ConfirmationCard.

	Args:
		values: The document values dict (mutated in-place).
		mapping_overrides: Dict of ``{child_table_field: {row_idx_str: {field: value}}}``.

	Example::

	        {"items": {"0": {"item_code": "ITEM-001", "uom": "Nos"}, "2": {"item_code": "ITEM-003"}}}
	"""
	for table_field, row_overrides in mapping_overrides.items():
		rows = values.get(table_field)
		if not isinstance(rows, list):
			continue
		for row_idx_str, field_overrides in row_overrides.items():
			try:
				row_idx = int(row_idx_str)
			except (ValueError, TypeError):
				continue
			if 0 <= row_idx < len(rows) and isinstance(rows[row_idx], dict):
				rows[row_idx].update(field_overrides)


def _update_confirmation_state(confirmation_id, state, result=None, undo_token=None, undo_expires=None):
	"""Update the confirmation_state JSON field on the associated Chatbot Message.

	Supports multiple confirmations per message by storing state as a dict
	keyed by ``confirmation_id``.  Each entry has the shape::

	        {
	            "state": "confirmed" | "declined" | "expired",
	            "result": {...},
	            "undo_token": "...",
	            "undo_expires": "...",
	        }

	Args:
		confirmation_id: UUID string.
		state: "confirmed", "declined", or "expired".
		result: Dict with action result (for confirmed state).
		undo_token: Undo token UUID (if applicable).
		undo_expires: ISO timestamp of undo expiry.
	"""
	try:
		# Find the message containing this confirmation_id in tool_results
		messages = frappe.get_all(
			"Chatbot Message",
			filters={
				"role": "assistant",
				"tool_results": ["like", f"%{confirmation_id}%"],
			},
			fields=["name", "confirmation_state"],
			limit=1,
			order_by="creation desc",
		)

		if not messages:
			return

		msg_name = messages[0].name

		# Load existing state (supports multiple confirmations per message)
		existing = {}
		raw = messages[0].confirmation_state
		if raw:
			try:
				parsed = json.loads(raw) if isinstance(raw, str) else raw
				# Migrate old single-confirmation format to multi-key format
				if isinstance(parsed, dict) and "confirmation_id" in parsed:
					old_id = parsed["confirmation_id"]
					existing[old_id] = {k: v for k, v in parsed.items() if k != "confirmation_id"}
				elif isinstance(parsed, dict):
					existing = parsed
			except (json.JSONDecodeError, TypeError):
				pass

		# Build entry for this confirmation_id
		entry = {"state": state}

		if result:
			entry["result"] = {
				"doctype": result.get("doctype"),
				"name": result.get("name"),
				"doc_url": result.get("doc_url"),
				"message": result.get("message"),
			}

		if undo_token:
			entry["undo_token"] = undo_token
			entry["undo_expires"] = undo_expires

		existing[confirmation_id] = entry

		frappe.db.set_value(
			"Chatbot Message",
			msg_name,
			"confirmation_state",
			json.dumps(existing, default=str),
		)
		frappe.db.commit()

	except Exception:
		# Non-critical — don't fail the main action
		log_error(
			f"Failed to update confirmation_state for {confirmation_id}",
			title="CRUD Confirmation State",
		)
