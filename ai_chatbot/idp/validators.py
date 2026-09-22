# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Post-Extraction Validators for IDP

Validates extracted data against ERPNext DocType schema and business rules.
Performs fuzzy matching on Link fields (customer, supplier, item) to resolve
names from the source document to existing ERPNext records.
"""

from __future__ import annotations

import frappe
from frappe.utils import getdate

from ai_chatbot.idp.schema import get_doctype_schema


def validate_extraction(
	extracted_data: dict,
	target_doctype: str,
	company: str | None = None,
) -> dict:
	"""Validate extracted data against ERPNext schema and business rules.

	Performs three levels of validation:
	1. Schema validation — required fields, correct types
	2. Link field resolution — fuzzy match names to existing records
	3. Business rule validation — logical consistency checks

	Args:
		extracted_data: Mapped data from the LLM extraction.
		target_doctype: ERPNext DocType name.
		company: Company context for scoped searches.

	Returns:
		dict:
			valid: bool — True if no hard errors
			errors: list[str] — hard errors that prevent record creation
			warnings: list[str] — soft issues (non-blocking)
			resolved_links: dict — {fieldname: resolved_value} for fuzzy-matched links
	"""
	schema = get_doctype_schema(target_doctype)
	errors = []
	warnings = []
	resolved_links = {}

	# 1. Schema validation
	_validate_required_fields(extracted_data, schema, errors, warnings)

	# 2. Link field resolution
	_resolve_link_fields(extracted_data, schema, company, errors, warnings, resolved_links)

	# 3. Business rule validation
	_validate_business_rules(extracted_data, target_doctype, errors, warnings)

	return {
		"valid": len(errors) == 0,
		"errors": errors,
		"warnings": warnings,
		"resolved_links": resolved_links,
	}


def _validate_required_fields(
	data: dict,
	schema: dict,
	errors: list,
	warnings: list,
) -> None:
	"""Check that required fields have values."""
	for field in schema.get("fields", []):
		if not field.get("reqd"):
			continue
		fieldname = field["fieldname"]
		# Skip auto-set fields
		if fieldname in ("naming_series", "docstatus", "company"):
			continue
		if fieldname not in data or data[fieldname] is None or data[fieldname] == "":
			errors.append(f"Required field '{field['label']}' ({fieldname}) is missing")


#: Fields whose value MUST come from the user/session context (Chatbot Settings
#: default or explicit user-supplied company), never from the document body.
#: The LLM sometimes mis-identifies the recipient block on an invoice as the
#: ERPNext "company", which then gets fuzzy-matched to an unrelated existing
#: Company record. Excluding these fields from fuzzy resolution prevents that
#: class of bug.
_CONTEXT_ONLY_LINK_FIELDS = frozenset({"company"})


def _resolve_link_fields(
	data: dict,
	schema: dict,
	company: str | None,
	errors: list,
	warnings: list,
	resolved_links: dict,
) -> None:
	"""Fuzzy-match Link field values to existing ERPNext records.

	For each Link field with a value, search the linked DocType for:
	1. Exact match on `name`
	2. Exact match on the name field (customer_name, supplier_name, item_name, etc.)
	3. LIKE match on the name field
	If a unique match is found, resolve it. Otherwise, report a warning.

	The `company` field (and any other context-only field) is intentionally
	excluded — it is authoritative from the user's session context and must
	never be inferred from document content.
	"""
	for field in schema.get("fields", []):
		if field.get("fieldtype") != "Link":
			continue

		fieldname = field["fieldname"]
		if fieldname in _CONTEXT_ONLY_LINK_FIELDS:
			continue

		value = data.get(fieldname)
		if not value:
			continue

		link_doctype = field.get("link_doctype")
		if not link_doctype:
			continue

		resolved = _fuzzy_resolve_link(value, link_doctype, company)
		if resolved:
			resolved_links[fieldname] = resolved
		else:
			warnings.append(
				f"Could not find {link_doctype} matching '{value}' for field '{field['label']}'. "
				f"You may need to create it first or correct the name."
			)

	# Also resolve Link fields in child table items
	for _table_name, child in schema.get("child_tables", {}).items():
		items_key = child.get("parentfield", _table_name)
		items = data.get(items_key, [])
		if not isinstance(items, list):
			continue

		for idx, item in enumerate(items):
			for cf in child.get("fields", []):
				if cf.get("fieldtype") != "Link":
					continue
				cfname = cf["fieldname"]
				cvalue = item.get(cfname)
				if not cvalue:
					continue

				resolved = _fuzzy_resolve_link(cvalue, cf.get("link_doctype", ""), company)
				if resolved:
					resolved_links[f"{items_key}[{idx}].{cfname}"] = resolved
				else:
					warnings.append(
						f"Could not find {cf.get('link_doctype')} matching '{cvalue}' "
						f"for item #{idx + 1} field '{cf['label']}'"
					)


def _fuzzy_resolve_link(
	value: str,
	link_doctype: str,
	company: str | None,
) -> str | None:
	"""Attempt to resolve a value to an existing record in a linked DocType.

	Search strategy:
	1. Exact match on `name`
	2. Exact match on display name field (e.g., customer_name)
	3. LIKE match on display name field
	4. LIKE match on `name`

	Args:
		value: Value to search for.
		link_doctype: DocType to search in.
		company: Optional company filter.

	Returns:
		Resolved document `name` or None.
	"""
	if not link_doctype or not value:
		return None

	# Check if the DocType even exists
	if not frappe.db.exists("DocType", link_doctype):
		return None

	# 1. Exact match on name
	if frappe.db.exists(link_doctype, value):
		return value

	# Determine the display name field for this DocType
	name_field = _get_name_field(link_doctype)

	# 2. Exact match on display name field
	if name_field:
		filters = {name_field: value}
		if company and _doctype_has_company_field(link_doctype):
			filters["company"] = company
		match = frappe.db.get_value(link_doctype, filters, "name")
		if match:
			return match

	# 3. LIKE match on display name field
	if name_field:
		filters = {name_field: ["like", f"%{value}%"]}
		if company and _doctype_has_company_field(link_doctype):
			filters["company"] = company
		matches = frappe.get_list(link_doctype, filters=filters, fields=["name"], limit=5)
		if len(matches) == 1:
			return matches[0].name

	# 4. LIKE match on name
	filters = {"name": ["like", f"%{value}%"]}
	matches = frappe.get_list(link_doctype, filters=filters, fields=["name"], limit=5)
	if len(matches) == 1:
		return matches[0].name

	return None


def _get_name_field(doctype: str) -> str | None:
	"""Get the human-readable name field for a DocType.

	Common patterns:
	- Customer → customer_name
	- Supplier → supplier_name
	- Item → item_name
	- Employee → employee_name

	Falls back to checking for standard name patterns.
	"""
	known = {
		"Customer": "customer_name",
		"Supplier": "supplier_name",
		"Item": "item_name",
		"Employee": "employee_name",
		"Lead": "lead_name",
		"Company": "company_name",
		"Warehouse": "warehouse_name",
		"Cost Center": "cost_center_name",
		"Department": "department_name",
		"Territory": "territory_name",
	}

	if doctype in known:
		return known[doctype]

	# Try common patterns
	meta = frappe.get_meta(doctype)
	slug = frappe.scrub(doctype)
	for candidate in [f"{slug}_name", "title", "subject"]:
		if meta.has_field(candidate):
			return candidate

	return None


def _doctype_has_company_field(doctype: str) -> bool:
	"""Check if a DocType has a company field."""
	try:
		meta = frappe.get_meta(doctype)
		return meta.has_field("company")
	except Exception:
		return False


def _validate_business_rules(
	data: dict,
	target_doctype: str,
	errors: list,
	warnings: list,
) -> None:
	"""Validate business logic rules on the extracted data.

	Checks:
	- posting_date ≤ due_date (if both present)
	- Numeric fields are positive (qty, rate)
	- At least one item exists for transaction documents
	"""
	# Date consistency
	posting_date = data.get("posting_date") or data.get("transaction_date")
	due_date = data.get("due_date") or data.get("payment_schedule_date")
	if posting_date and due_date:
		try:
			if getdate(posting_date) > getdate(due_date):
				warnings.append(f"Posting date ({posting_date}) is after due date ({due_date})")
		except Exception:
			pass

	# Items validation for transaction documents
	transaction_doctypes = {
		"Sales Invoice",
		"Purchase Invoice",
		"Sales Order",
		"Purchase Order",
		"Quotation",
		"Delivery Note",
		"Purchase Receipt",
	}
	if target_doctype in transaction_doctypes:
		items = data.get("items", [])
		if not items:
			warnings.append("No line items found in the document")
		else:
			for idx, item in enumerate(items):
				qty = item.get("qty")
				rate = item.get("rate")
				if qty is not None and isinstance(qty, int | float) and qty <= 0:
					warnings.append(f"Item #{idx + 1}: qty is {qty} (expected positive)")
				if rate is not None and isinstance(rate, int | float) and rate < 0:
					warnings.append(f"Item #{idx + 1}: rate is {rate} (expected non-negative)")
