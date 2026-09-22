# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Chat Session Context Manager

Manages per-conversation session variables that persist across messages
within a single chat conversation. Stored as JSON in the Chatbot Conversation
document's `session_context` field.

Session variables:
- include_subsidiaries: bool — whether to include child company data
- target_currency: str | None — user-specified display currency (via @Currency)
- response_language: str | None — per-conversation response language override
"""

import json

import frappe

from ai_chatbot.core.config import get_company_currency, get_default_company, has_company_access
from ai_chatbot.core.consolidation import get_child_companies, is_parent_company


def get_session_context(conversation_id: str) -> dict:
	"""Get session context for a conversation.

	Returns a dict with at least:
		- include_subsidiaries: bool (default False)
		- target_currency: str | None (default None)
	"""
	default = {"include_subsidiaries": False, "target_currency": None, "response_language": None}

	try:
		raw = frappe.db.get_value("Chatbot Conversation", conversation_id, "session_context")
		if raw:
			ctx = json.loads(raw) if isinstance(raw, str) else raw
			return {**default, **ctx}
	except Exception:
		pass

	return default


def set_session_context(conversation_id: str, key: str, value) -> dict:
	"""Set a single session context value.

	Args:
		conversation_id: Conversation document name.
		key: Context key (e.g. "include_subsidiaries", "target_currency").
		value: Value to set.

	Returns:
		Updated session context dict.
	"""
	ctx = get_session_context(conversation_id)
	ctx[key] = value

	frappe.db.set_value(
		"Chatbot Conversation",
		conversation_id,
		"session_context",
		json.dumps(ctx),
		update_modified=False,
	)
	return ctx


def get_companies_for_query(company: str | None = None, conversation_id: str | None = None) -> list[str]:
	"""Get the list of companies to query based on session context.

	If include_subsidiaries is True and the company is a parent,
	returns [parent, child1, child2, ...]. Otherwise returns [company].

	Args:
		company: Company name. Defaults to user's default.
		conversation_id: Optional conversation ID to check session context.

	Returns:
		List of company names to include in queries.
	"""
	company = get_default_company(company)
	companies = [company]

	if conversation_id:
		ctx = get_session_context(conversation_id)
		if ctx.get("include_subsidiaries") and is_parent_company(company):
			children = [child for child in get_child_companies(company) if has_company_access(child)]
			companies = [company, *children]

	return companies


def get_display_currency(company: str | None = None, conversation_id: str | None = None) -> str:
	"""Get the display currency based on session context.

	If target_currency is set in session, use that.
	Otherwise use the company's default currency.

	Args:
		company: Company name.
		conversation_id: Optional conversation ID to check session context.

	Returns:
		Currency code string.
	"""
	company = get_default_company(company)

	if conversation_id:
		ctx = get_session_context(conversation_id)
		target = ctx.get("target_currency")
		if target:
			return target

	return get_company_currency(company)


def get_company_filter(company: str | None = None) -> list[str] | str:
	"""Get company value for tool queries — session-aware.

	Reads frappe.flags.current_conversation_id to check session context.
	Returns a list of companies when subsidiaries are included,
	or a single company string when they are not.

	This is the primary function tools should call instead of
	get_default_company() when building queries.

	Args:
		company: Company name. Defaults to user's default.

	Returns:
		Single company string or list of company strings.
	"""
	company = get_default_company(company)
	conversation_id = getattr(frappe.flags, "current_conversation_id", None)

	if conversation_id:
		companies = get_companies_for_query(company, conversation_id)
		if len(companies) > 1:
			return companies

	return company


def build_company_label(company: str, conversation_id: str | None = None) -> str:
	"""Build the company display label with subsidiary notation.

	If include_subsidiaries is True, appends " including its subsidiaries".

	Args:
		company: Company name.
		conversation_id: Optional conversation ID.

	Returns:
		Formatted company label string.
	"""
	company = get_default_company(company)
	label = company

	if conversation_id:
		ctx = get_session_context(conversation_id)
		if ctx.get("include_subsidiaries") and is_parent_company(company):
			label = f"{company} including its subsidiaries"

	return label
