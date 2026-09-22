# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Prerequisite Detection & Execution for CRUD Operations

Detects missing master records (Customer, Supplier, Item, UOM) referenced
in a document's values and provides:
  1. A structured description of what's missing (for the frontend to render
     editable fields).
  2. An execution function that creates all prerequisites in dependency
     order before the main document is created.

The editable-field definitions follow a Frappe-like schema so the frontend
can dynamically render form inputs (text, checkbox, link).
"""

from __future__ import annotations

import frappe
from frappe.utils import cstr

from ai_chatbot.data.validators import _resolve_link_value

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# DocType field → linked master DocType
_PARTY_FIELD_MAP: dict[str, str] = {
	"customer": "Customer",
	"supplier": "Supplier",
}

# Sales-family → Customer, Purchase-family → Supplier
_SALES_DOCTYPES = {
	"Sales Order",
	"Sales Invoice",
	"Quotation",
	"Delivery Note",
}
_PURCHASE_DOCTYPES = {
	"Purchase Order",
	"Purchase Invoice",
	"Purchase Receipt",
	"Supplier Quotation",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_prerequisites(doctype: str, values: dict, company: str | None = None) -> dict:
	"""Detect missing master records referenced in *values*.

	Checks:
	- Party fields (customer / supplier) against their master DocType
	- Item codes in child table rows against the Item master
	- UOM values in child table rows against the UOM master
	- Account heads in child table rows against the Account master

	Returns a structured dict suitable for the ConfirmationCard frontend::

	        {
	            "has_prerequisites": True / False,
	            "missing_parties": [...],
	            "missing_items": [...],
	            "missing_uoms": [...],
	            "missing_accounts": [...],
	        }
	"""
	company = company or frappe.defaults.get_user_default("Company")
	missing_parties = _detect_missing_parties(doctype, values, company)
	missing_items, missing_uoms, item_mapping = _detect_missing_items(doctype, values, company)
	missing_accounts = _detect_missing_accounts(doctype, values, company)

	has_prereqs = bool(missing_parties or missing_items or missing_uoms or missing_accounts)

	return {
		"has_prerequisites": has_prereqs,
		"missing_parties": missing_parties,
		"missing_items": missing_items,
		"missing_uoms": missing_uoms,
		"missing_accounts": missing_accounts,
		"item_mapping": item_mapping,
	}


def execute_prerequisites(prerequisites: dict, company: str | None = None, *, commit=True) -> dict:
	"""Create missing master records in dependency order.

	Order: UOMs → Parties → Items  (Items depend on UOM existing).

	Each prerequisite entry may carry a ``user_overrides`` dict with
	field values edited by the user on the frontend form.

	Returns::

	        {
	            "success": True / False,
	            "created": ["Customer: Acme Corp", "Item: Widget A", ...],
	            "errors": [],
	            "name_map": {
	                "customer": {"Acme Corp": "Acme Corp"},
	                "item_code": {"Widget A": "ITEM-00001"},
	            },
	        }
	"""
	company = company or frappe.defaults.get_user_default("Company")
	created: list[str] = []
	name_map: dict[str, dict[str, str]] = {}

	try:
		# Phase 0 — Accounts (tax/GL accounts in taxes child table)
		for account in prerequisites.get("missing_accounts", []):
			overrides = account.get("user_overrides", {})
			result_name = _create_account(account["value"], overrides, company)
			created.append(f"Account: {result_name}")
			name_map.setdefault(account["field"], {})[account["value"]] = result_name

		# Phase 1 — UOMs (Items reference stock_uom)
		for uom in prerequisites.get("missing_uoms", []):
			result_name = _create_uom(uom["value"])
			created.append(f"UOM: {result_name}")

		# Phase 2 — Parties (no dependency on items)
		for party in prerequisites.get("missing_parties", []):
			overrides = party.get("user_overrides", {})
			result_name = _create_party(party["doctype"], party["value"], overrides, company)
			created.append(f"{party['doctype']}: {result_name}")
			name_map.setdefault(party["field"], {})[party["value"]] = result_name

		# Phase 3 — Items
		for item in prerequisites.get("missing_items", []):
			overrides = item.get("user_overrides", {})
			result_name = _create_item(item["value"], overrides, company)
			created.append(f"Item: {result_name}")
			name_map.setdefault("item_code", {})[item["value"]] = result_name

		if commit:
			frappe.db.commit()
		return {
			"success": True,
			"created": created,
			"errors": [],
			"name_map": name_map,
		}

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(
			title="Prerequisite Creation Failed",
			message=f"Company: {company}\nCreated so far: {created}\nError: {cstr(e)}\n\n{frappe.get_traceback()}",
		)
		return {
			"success": False,
			"created": [],
			"errors": [cstr(e)],
			"name_map": {},
		}


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _detect_missing_parties(doctype: str, values: dict, company: str) -> list[dict]:
	"""Find party fields whose value does not exist as a master record."""
	missing = []

	for field, party_doctype in _PARTY_FIELD_MAP.items():
		value = values.get(field)
		if not value:
			continue

		# Check exact match
		if frappe.db.exists(party_doctype, value):
			continue

		# Try fuzzy resolution — if resolved, update values in-place and skip
		resolved = _resolve_link_value(party_doctype, field, value)
		if resolved:
			values[field] = resolved
			continue

		# Truly missing — build editable fields for the card
		editable = _get_party_editable_fields(party_doctype, company)
		missing.append(
			{
				"doctype": party_doctype,
				"field": field,
				"value": value,
				"editable_fields": editable,
			}
		)

	return missing


def _detect_missing_items(
	doctype: str, values: dict, company: str
) -> tuple[list[dict], list[dict], list[dict]]:
	"""Find item_code / uom values in child tables that don't exist.

	Also builds an ``item_mapping`` list — one entry **per child-table row**
	— that tracks the original extracted name, resolved ERPNext match,
	and row-level data (qty, rate).  The unified editable Item Mapping
	table in the ConfirmationCard renders this data.

	Returns (missing_items, missing_uoms, item_mapping).
	"""
	meta = frappe.get_meta(doctype)
	missing_items: list[dict] = []
	missing_uoms: list[dict] = []
	seen_items: set[str] = set()
	seen_uoms: set[str] = set()

	# Item mapping: one entry per child-table row (not per unique item).
	# We cache resolved metadata per unique extracted value to avoid
	# redundant DB queries when the same item appears in multiple rows.
	item_mapping: list[dict] = []
	item_meta_cache: dict[str, dict] = {}

	# Supplier context for Item Supplier resolution (purchase documents)
	supplier = values.get("supplier", "")

	for df in meta.fields:
		if df.fieldtype != "Table" or df.fieldname not in values:
			continue

		rows = values[df.fieldname]
		if not isinstance(rows, list):
			continue

		child_meta = frappe.get_meta(df.options)

		# Check which child fields are Link→Item and Link→UOM
		item_fields = [
			cdf.fieldname for cdf in child_meta.fields if cdf.fieldtype == "Link" and cdf.options == "Item"
		]
		uom_fields = [
			cdf.fieldname for cdf in child_meta.fields if cdf.fieldtype == "Link" and cdf.options == "UOM"
		]

		for row_idx, row in enumerate(rows):
			if not isinstance(row, dict):
				continue

			# --- Items ---
			for ifield in item_fields:
				val = row.get(ifield)
				if not val:
					continue

				extracted_uom = _get_row_uom(row, uom_fields)

				# Check cache first — avoids redundant DB lookups
				if val in item_meta_cache:
					cached = item_meta_cache[val]
					# Apply mutation for fuzzy/supplier resolved items
					if cached["resolved_item"] != val:
						row[ifield] = cached["resolved_item"]
					item_mapping.append(
						_build_mapping_entry(
							item_mapping, val, cached, extracted_uom, row, df.fieldname, row_idx
						)
					)
					# Still need to track for missing_items dedup
					if cached["is_new"] and val not in seen_items:
						seen_items.add(val)
						row_uom = _get_row_uom(row, uom_fields)
						editable = _get_item_editable_fields(val, row_uom, company)
						missing_items.append(
							{
								"doctype": "Item",
								"value": val,
								"row_indices": [row_idx],
								"child_table_field": df.fieldname,
								"editable_fields": editable,
							}
						)
					continue

				# Case A: Exact match exists in ERPNext
				if frappe.db.exists("Item", val):
					item_data = (
						frappe.get_cached_value(
							"Item", val, ["item_group", "is_stock_item", "stock_uom"], as_dict=True
						)
						or {}
					)
					cached = {
						"resolved_item": val,
						"item_group": item_data.get("item_group", ""),
						"is_stock_item": item_data.get("is_stock_item", 1),
						"stock_uom": item_data.get("stock_uom", "Nos"),
						"is_new": False,
					}
					item_meta_cache[val] = cached
					item_mapping.append(
						_build_mapping_entry(
							item_mapping, val, cached, extracted_uom, row, df.fieldname, row_idx
						)
					)
					continue

				# Case B: Fuzzy resolution by item_name / LIKE match
				resolved = _resolve_link_value("Item", ifield, val)
				if resolved:
					row[ifield] = resolved
					item_data = (
						frappe.get_cached_value(
							"Item", resolved, ["item_group", "is_stock_item", "stock_uom"], as_dict=True
						)
						or {}
					)
					cached = {
						"resolved_item": resolved,
						"item_group": item_data.get("item_group", ""),
						"is_stock_item": item_data.get("is_stock_item", 1),
						"stock_uom": item_data.get("stock_uom", "Nos"),
						"is_new": False,
					}
					item_meta_cache[val] = cached
					item_mapping.append(
						_build_mapping_entry(
							item_mapping, val, cached, extracted_uom, row, df.fieldname, row_idx
						)
					)
					continue

				# Case B2: Item Supplier resolution (supplier_part_no → item_code)
				supplier_match = _resolve_via_item_supplier(val, supplier)
				if supplier_match:
					row[ifield] = supplier_match
					item_data = (
						frappe.get_cached_value(
							"Item", supplier_match, ["item_group", "is_stock_item", "stock_uom"], as_dict=True
						)
						or {}
					)
					cached = {
						"resolved_item": supplier_match,
						"item_group": item_data.get("item_group", ""),
						"is_stock_item": item_data.get("is_stock_item", 1),
						"stock_uom": item_data.get("stock_uom", "Nos"),
						"is_new": False,
					}
					item_meta_cache[val] = cached
					item_mapping.append(
						_build_mapping_entry(
							item_mapping, val, cached, extracted_uom, row, df.fieldname, row_idx
						)
					)
					continue

				# Case C: Item doesn't exist — add to missing list
				default_group = frappe.db.get_single_value("Stock Settings", "item_group") or "Products"
				cached = {
					"resolved_item": val,
					"item_group": default_group,
					"is_stock_item": 1,
					"stock_uom": extracted_uom or "Nos",
					"is_new": True,
				}
				item_meta_cache[val] = cached
				item_mapping.append(
					_build_mapping_entry(item_mapping, val, cached, extracted_uom, row, df.fieldname, row_idx)
				)

				if val not in seen_items:
					seen_items.add(val)
					row_uom = _get_row_uom(row, uom_fields)
					editable = _get_item_editable_fields(val, row_uom, company)
					missing_items.append(
						{
							"doctype": "Item",
							"value": val,
							"row_indices": [row_idx],
							"child_table_field": df.fieldname,
							"editable_fields": editable,
						}
					)

			# --- UOMs ---
			for ufield in uom_fields:
				val = row.get(ufield)
				if not val or val in seen_uoms:
					continue
				if frappe.db.exists("UOM", val):
					continue

				# Try fuzzy resolution for UOM
				resolved_uom = _resolve_link_value("UOM", ufield, val)
				if resolved_uom:
					extracted_uom_name = val
					row[ufield] = resolved_uom
					# Update item_mapping entry's resolved_uom if applicable
					_update_mapping_uom(item_mapping, extracted_uom_name, resolved_uom)
					continue

				seen_uoms.add(val)
				missing_uoms.append(
					{
						"doctype": "UOM",
						"value": val,
					}
				)

	# Also check for items referenced in the same row with a
	# duplicate value (append row_indices)
	_deduplicate_items(missing_items)

	return missing_items, missing_uoms, item_mapping


def _build_mapping_entry(
	item_mapping: list[dict],
	extracted_item: str,
	cached: dict,
	extracted_uom: str,
	row: dict,
	child_table_field: str,
	row_idx: int,
) -> dict:
	"""Build a single item_mapping entry from cached metadata and row data."""
	return {
		"idx": len(item_mapping) + 1,
		"extracted_item": extracted_item,
		"resolved_item": cached["resolved_item"],
		"extracted_uom": extracted_uom,
		"resolved_uom": extracted_uom,
		"item_group": cached["item_group"],
		"is_new": cached["is_new"],
		"is_stock_item": cached.get("is_stock_item", 1),
		"stock_uom": cached.get("stock_uom", "Nos"),
		"qty": row.get("qty", 0),
		"rate": row.get("rate", 0),
		"child_table_field": child_table_field,
		"row_idx": row_idx,
	}


def _detect_missing_accounts(doctype: str, values: dict, company: str) -> list[dict]:
	"""Find Account link values in child tables that don't exist.

	Scans child tables (especially taxes/charges) for Link→Account fields
	whose value doesn't match any existing Account record.
	"""
	meta = frappe.get_meta(doctype)
	missing: list[dict] = []
	seen: set[str] = set()

	for df in meta.fields:
		if df.fieldtype != "Table" or df.fieldname not in values:
			continue

		rows = values[df.fieldname]
		if not isinstance(rows, list):
			continue

		child_meta = frappe.get_meta(df.options)

		account_fields = [
			cdf.fieldname for cdf in child_meta.fields if cdf.fieldtype == "Link" and cdf.options == "Account"
		]

		for row in rows:
			if not isinstance(row, dict):
				continue

			for afield in account_fields:
				val = row.get(afield)
				if not val or val in seen:
					continue

				if frappe.db.exists("Account", val):
					continue

				# Try fuzzy resolution
				resolved = _resolve_link_value("Account", afield, val)
				if resolved:
					row[afield] = resolved
					continue

				seen.add(val)
				editable = _get_account_editable_fields(val, company)
				missing.append(
					{
						"doctype": "Account",
						"field": afield,
						"value": val,
						"child_table_field": df.fieldname,
						"editable_fields": editable,
					}
				)

	return missing


def _deduplicate_items(missing_items: list[dict]) -> None:
	"""Merge entries with the same item value (different rows)."""
	by_value: dict[str, dict] = {}
	to_remove: list[int] = []

	for idx, entry in enumerate(missing_items):
		val = entry["value"]
		if val in by_value:
			by_value[val]["row_indices"].extend(entry["row_indices"])
			to_remove.append(idx)
		else:
			by_value[val] = entry

	for idx in reversed(to_remove):
		missing_items.pop(idx)


def _get_row_uom(row: dict, uom_fields: list[str]) -> str:
	"""Return the first non-empty UOM value from a child table row."""
	for uf in uom_fields:
		val = row.get(uf)
		if val:
			return val
	return ""


def _resolve_via_item_supplier(val: str, supplier: str) -> str | None:
	"""Try to resolve an extracted item name via the Item Supplier child table.

	ERPNext's Item master has a child table ``supplier_items`` (DocType:
	``Item Supplier``) with fields ``supplier`` and ``supplier_part_no``.
	For purchase documents the extracted item name often matches the
	supplier's own part number rather than the ERPNext item_code.

	Returns the parent ``item_code`` if a match is found, else ``None``.
	"""
	if not val:
		return None

	filters = {"supplier_part_no": val}
	if supplier:
		filters["supplier"] = supplier

	# Exact match on supplier_part_no
	parent = frappe.db.get_value("Item Supplier", filters, "parent")
	if parent and frappe.db.exists("Item", parent):
		return parent

	# Without supplier filter (any supplier)
	if supplier:
		parent = frappe.db.get_value("Item Supplier", {"supplier_part_no": val}, "parent")
		if parent and frappe.db.exists("Item", parent):
			return parent

	return None


def _update_mapping_uom(item_mapping: list[dict], extracted_uom: str, resolved_uom: str) -> None:
	"""Update ``resolved_uom`` in item_mapping entries whose extracted_uom matches."""
	if not extracted_uom or not resolved_uom:
		return
	for entry in item_mapping:
		if entry.get("extracted_uom") == extracted_uom:
			entry["resolved_uom"] = resolved_uom


# ---------------------------------------------------------------------------
# Editable field builders
# ---------------------------------------------------------------------------


def _get_party_editable_fields(party_doctype: str, company: str) -> list[dict]:
	"""Return editable field definitions for a missing party."""
	company_currency = frappe.get_cached_value("Company", company, "default_currency") if company else ""

	if party_doctype == "Customer":
		default_group = (
			frappe.db.get_single_value("Selling Settings", "customer_group") or "All Customer Groups"
		)
		default_territory = frappe.db.get_single_value("Selling Settings", "territory") or "All Territories"
		return [
			{
				"fieldname": "customer_group",
				"label": "Customer Group",
				"fieldtype": "Data",
				"default": default_group,
			},
			{
				"fieldname": "territory",
				"label": "Territory",
				"fieldtype": "Data",
				"default": default_territory,
			},
			{
				"fieldname": "default_currency",
				"label": "Billing Currency",
				"fieldtype": "Data",
				"default": company_currency or "",
			},
		]

	if party_doctype == "Supplier":
		default_group = (
			frappe.db.get_single_value("Buying Settings", "supplier_group") or "All Supplier Groups"
		)
		return [
			{
				"fieldname": "supplier_group",
				"label": "Supplier Group",
				"fieldtype": "Data",
				"default": default_group,
			},
			{
				"fieldname": "default_currency",
				"label": "Billing Currency",
				"fieldtype": "Data",
				"default": company_currency or "",
			},
		]

	return []


def _get_item_editable_fields(item_value: str, row_uom: str | None, company: str) -> list[dict]:
	"""Return editable field definitions for a missing item."""
	default_group = frappe.db.get_single_value("Stock Settings", "item_group") or "Products"
	default_uom = row_uom or "Nos"

	return [
		{
			"fieldname": "is_stock_item",
			"label": "Stock Item",
			"fieldtype": "Check",
			"default": 1,
		},
		{
			"fieldname": "is_fixed_asset",
			"label": "Fixed Asset",
			"fieldtype": "Check",
			"default": 0,
			"hidden_when": "is_stock_item",
		},
		{
			"fieldname": "item_group",
			"label": "Item Group",
			"fieldtype": "Data",
			"default": default_group,
		},
		{
			"fieldname": "stock_uom",
			"label": "Default UOM",
			"fieldtype": "Data",
			"default": default_uom,
		},
	]


def _get_account_editable_fields(account_value: str, company: str) -> list[dict]:
	"""Return editable field definitions for a missing Account.

	Attempts to infer sensible defaults:
	- account_name: the core name extracted from the value
	- parent_account: "Duties and Taxes - ABBR" for tax-like names, else
	  "Expenses - ABBR" as a generic default
	- account_type: "Tax" if the name suggests a tax, otherwise empty
	"""
	abbr = ""
	if company:
		abbr = frappe.get_cached_value("Company", company, "abbr") or ""

	# Strip company abbreviation if present
	core_name = account_value.rsplit(" - ", 1)[0].strip() if " - " in account_value else account_value.strip()

	# Infer if this is a tax account
	tax_keywords = {"tax", "gst", "igst", "sgst", "cgst", "vat", "cess", "tds", "tcs", "duty", "excise"}
	is_tax = bool(tax_keywords & set(core_name.lower().split()))

	# Try to find a suitable parent account
	default_parent = ""
	if is_tax and abbr:
		# Look for "Duties and Taxes" parent
		candidate = frappe.db.get_value(
			"Account",
			{"account_name": "Duties and Taxes", "company": company, "is_group": 1},
			"name",
		)
		if candidate:
			default_parent = candidate
	if not default_parent and abbr:
		# Fallback: "Indirect Expenses" or first Expense group
		candidate = frappe.db.get_value(
			"Account",
			{"account_name": "Indirect Expenses", "company": company, "is_group": 1},
			"name",
		)
		if candidate:
			default_parent = candidate

	return [
		{
			"fieldname": "account_name",
			"label": "Account Name",
			"fieldtype": "Data",
			"default": core_name,
		},
		{
			"fieldname": "parent_account",
			"label": "Parent Account",
			"fieldtype": "Data",
			"default": default_parent,
		},
		{
			"fieldname": "account_type",
			"label": "Account Type",
			"fieldtype": "Data",
			"default": "Tax" if is_tax else "",
		},
	]


# ---------------------------------------------------------------------------
# Creation helpers
# ---------------------------------------------------------------------------


def _create_party(party_doctype: str, value: str, overrides: dict, company: str) -> str:
	"""Create a Customer or Supplier master record.

	Returns the created document's ``name``.
	"""
	name_field = {
		"Customer": "customer_name",
		"Supplier": "supplier_name",
	}.get(party_doctype, "name")

	doc_values = {name_field: value}

	if party_doctype == "Customer":
		doc_values["customer_group"] = (
			overrides.get("customer_group")
			or frappe.db.get_single_value("Selling Settings", "customer_group")
			or "All Customer Groups"
		)
		doc_values["territory"] = (
			overrides.get("territory")
			or frappe.db.get_single_value("Selling Settings", "territory")
			or "All Territories"
		)
		if overrides.get("default_currency"):
			doc_values["default_currency"] = overrides["default_currency"]

	elif party_doctype == "Supplier":
		doc_values["supplier_group"] = (
			overrides.get("supplier_group")
			or frappe.db.get_single_value("Buying Settings", "supplier_group")
			or "All Supplier Groups"
		)
		if overrides.get("default_currency"):
			doc_values["default_currency"] = overrides["default_currency"]

	doc = frappe.new_doc(party_doctype)
	doc.update(doc_values)
	doc.insert(ignore_permissions=False)
	return doc.name


def _create_item(item_value: str, overrides: dict, company: str) -> str:
	"""Create an Item master record.

	If the user overrode the item_code to an existing Item, skip creation
	and return the existing Item's name directly.

	Returns the created (or existing) document's ``name``.
	"""
	item_code = overrides.pop("item_code", None) or item_value

	# User may have remapped to an existing item via the dropdown
	if frappe.db.exists("Item", item_code):
		return item_code

	doc_values = {
		"item_code": item_code,
		"item_name": item_code,
		"item_group": (
			overrides.get("item_group")
			or frappe.db.get_single_value("Stock Settings", "item_group")
			or "Products"
		),
		"stock_uom": overrides.get("stock_uom") or "Nos",
		"is_stock_item": int(overrides.get("is_stock_item", 1)),
		"is_fixed_asset": int(overrides.get("is_fixed_asset", 0)),
	}

	doc = frappe.new_doc("Item")
	doc.update(doc_values)
	doc.insert(ignore_permissions=False)
	return doc.name


def _create_uom(uom_name: str) -> str:
	"""Create a UOM record if it doesn't exist.

	Returns the UOM name.
	"""
	if frappe.db.exists("UOM", uom_name):
		return uom_name

	doc = frappe.new_doc("UOM")
	doc.uom_name = uom_name
	doc.insert(ignore_permissions=False)
	return doc.name


def _create_account(account_value: str, overrides: dict, company: str) -> str:
	"""Create an Account master record.

	ERPNext Account ``name`` is auto-generated as ``account_name - ABBR``.

	Multi-company hierarchy handling:
	If the target company is a child in a company hierarchy, ERPNext requires
	the account to exist in the root company first.  We detect this and create
	the account in the root company — ERPNext auto-syncs it to all children.

	Args:
		account_value: The original value from the extracted data.
		overrides: User-edited fields (account_name, parent_account, account_type).
		company: Company for the account.

	Returns:
		The created Account's ``name`` (e.g., "IGST - TT").
	"""
	account_name = overrides.get("account_name") or account_value
	# Strip abbreviation suffix if the user left it in
	if " - " in account_name:
		account_name = account_name.rsplit(" - ", 1)[0].strip()

	parent_account = overrides.get("parent_account", "")
	account_type = overrides.get("account_type", "")

	# --- Multi-company hierarchy detection ---
	# If the target company is a child, create the account in the root company
	# first.  ERPNext will auto-sync it to all descendants.
	root_company = _get_root_company(company)
	creation_company = root_company or company

	# Resolve parent_account for the creation company.  The user-provided
	# parent_account has the *target* company's abbreviation (e.g.,
	# "Duties and Taxes - TTD").  If we're creating in the root company
	# instead, we need the root-company equivalent.
	if root_company and parent_account:
		parent_account = _find_parent_account_in_company(parent_account, root_company) or parent_account

	# Determine root_type from the parent account if possible
	root_type = ""
	if parent_account:
		root_type = frappe.get_cached_value("Account", parent_account, "root_type") or ""

	doc = frappe.new_doc("Account")
	doc.account_name = account_name
	doc.company = creation_company
	doc.parent_account = parent_account
	doc.account_type = account_type
	doc.root_type = root_type
	doc.is_group = 0
	doc.insert(ignore_permissions=False)

	# If created in the root company, return the auto-synced account
	# name in the target (child) company.
	if root_company:
		child_abbr = frappe.get_cached_value("Company", company, "abbr") or ""
		child_account_name = f"{account_name} - {child_abbr}" if child_abbr else account_name
		# Wait for the auto-sync: check if the account now exists in the child
		if frappe.db.exists("Account", child_account_name):
			return child_account_name
		# Fallback: return the root account name (the child may need a manual sync)
		return doc.name

	return doc.name


def _get_root_company(company: str) -> str | None:
	"""Return the root (topmost) company if *company* is a child, else None."""
	from frappe.utils.nestedset import get_ancestors_of

	try:
		ancestors = get_ancestors_of("Company", company, "lft asc")
		return ancestors[0] if ancestors else None
	except Exception:
		return None


def _find_parent_account_in_company(parent_account: str, target_company: str) -> str | None:
	"""Find the equivalent of *parent_account* in *target_company*.

	``parent_account`` is typically like "Duties and Taxes - TTD" (child company).
	We need the same account_name in the target (root) company.
	"""
	# Extract account_name from the parent_account identifier
	parent_name = frappe.get_cached_value("Account", parent_account, "account_name")
	if not parent_name:
		# parent_account might already be just a name without abbreviation
		parent_name = (
			parent_account.rsplit(" - ", 1)[0].strip() if " - " in parent_account else parent_account
		)

	# Look for the same account_name in the target company
	return frappe.db.get_value(
		"Account",
		{"account_name": parent_name, "company": target_company, "is_group": 1},
		"name",
	)
