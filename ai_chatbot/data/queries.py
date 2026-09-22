# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Read-only Data Provider for AI Chatbot Tools

Generic query helpers that auto-inject company filters and use Frappe ORM.
All tool modules should use these helpers instead of permission-bypassing
queries to ensure consistent multi-company filtering and User Permissions.
"""

import frappe
from frappe.utils import cint

from ai_chatbot.core.config import get_default_company
from ai_chatbot.core.constants import DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT


def get_documents(
	doctype,
	filters=None,
	fields=None,
	company=None,
	order_by=None,
	limit=None,
	group_by=None,
):
	"""Fetch documents with automatic company filtering.

	Args:
		doctype: Frappe doctype name.
		filters: Dict or list of filters.
		fields: List of field names to return.
		company: Company name. Auto-resolved if not provided.
		order_by: ORDER BY clause string.
		limit: Max number of records.
		group_by: GROUP BY clause string.

	Returns:
		List of dicts.
	"""
	filters = _ensure_dict(filters)
	company = get_default_company(company)

	# Only add company filter if the doctype has a company field
	if _doctype_has_field(doctype, "company"):
		filters["company"] = company

	limit = min(cint(limit) or DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT)

	return frappe.get_list(
		doctype,
		filters=filters,
		fields=fields or ["name"],
		order_by=order_by,
		limit_page_length=limit,
		group_by=group_by,
	)


def get_document(doctype, name):
	"""Fetch a single document with permission check.

	Args:
		doctype: Frappe doctype name.
		name: Document name/ID.

	Returns:
		Document dict.

	Raises:
		frappe.PermissionError: If user lacks read permission.
	"""
	frappe.has_permission(doctype, doc=name, throw=True)
	return frappe.get_doc(doctype, name).as_dict()


def get_count(doctype, filters=None, company=None):
	"""Count documents with company filter.

	Args:
		doctype: Frappe doctype name.
		filters: Dict of filters.
		company: Company name.

	Returns:
		int count.
	"""
	filters = _ensure_dict(filters)
	company = get_default_company(company)

	if _doctype_has_field(doctype, "company"):
		filters["company"] = company

	rows = frappe.get_list(
		doctype,
		filters=filters,
		fields=["count(name) as count"],
		limit_page_length=1,
	)
	return int(rows[0].get("count") or 0) if rows else 0


def get_list(doctype, filters=None, fields=None, company=None, order_by=None, limit=None):
	"""Alias for get_documents — matches Frappe's get_list naming.

	Uses frappe.get_list which applies user permissions automatically.
	"""
	filters = _ensure_dict(filters)
	company = get_default_company(company)

	if _doctype_has_field(doctype, "company"):
		filters["company"] = company

	limit = min(cint(limit) or DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT)

	return frappe.get_list(
		doctype,
		filters=filters,
		fields=fields or ["name"],
		order_by=order_by,
		limit_page_length=limit,
	)


def _ensure_dict(filters):
	"""Ensure filters is a mutable dict."""
	if filters is None:
		return {}
	if isinstance(filters, dict):
		return dict(filters)
	return filters


def _doctype_has_field(doctype, fieldname):
	"""Check if a doctype has a specific field (cached)."""
	meta = frappe.get_meta(doctype)
	return meta.has_field(fieldname)
