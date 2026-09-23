# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Session Context Management Tools

Tools that allow the AI to manage per-conversation session variables:
- include_subsidiaries: toggle child company inclusion
- target_currency: set/reset display currency
"""

import frappe

from ai_chatbot.core.config import get_company_currency, get_default_company
from ai_chatbot.core.consolidation import get_child_companies, is_parent_company
from ai_chatbot.core.session_context import get_session_context, set_session_context
from ai_chatbot.tools.registry import register_tool


def _get_current_conversation_id():
	"""Get the current conversation ID from the frappe request context.

	The conversation_id is passed via the chat API and stored in frappe.flags.
	"""
	return getattr(frappe.flags, "current_conversation_id", None)


@register_tool(
	name="set_include_subsidiaries",
	category="finance",
	description=(
		"Enable or disable child company inclusion for this chat session. "
		"When enabled, all subsequent finance/analytics queries will automatically "
		"include data from all subsidiary companies. "
		"Use when the user says things like 'include subsidiaries', 'include child companies', "
		"'show consolidated data', 'group level data'. "
		"Disable when user says 'don't include subsidiaries', 'only parent company', "
		"'exclude child companies'."
	),
	parameters={
		"include": {
			"type": "boolean",
			"description": "True to include subsidiaries, False to exclude them.",
		},
	},
	doctypes=["Company"],
	row_permission_safe=True,
)
def set_include_subsidiaries(include=True):
	"""Toggle subsidiary inclusion for the current chat session."""
	conversation_id = _get_current_conversation_id()
	if not conversation_id:
		return {"error": "No active conversation context"}

	company = get_default_company()

	if include and not is_parent_company(company):
		return {
			"success": True,
			"include_subsidiaries": False,
			"message": f"{company} has no subsidiaries. Setting has no effect.",
			"company": company,
		}

	ctx = set_session_context(conversation_id, "include_subsidiaries", bool(include))

	children = get_child_companies(company) if include else []
	message = (
		f"Subsidiaries {'included' if include else 'excluded'} for this session. "
		f"All subsequent queries will {'include' if include else 'exclude'} child company data."
	)
	if include and children:
		message += f" Subsidiaries: {', '.join(children)}"

	return {
		"success": True,
		"include_subsidiaries": ctx["include_subsidiaries"],
		"company": company,
		"subsidiaries": list(children) if include else [],
		"message": message,
	}


@register_tool(
	name="set_target_currency",
	category="finance",
	description=(
		"Set or reset the display currency for this chat session. "
		"When set, all subsequent monetary values will be shown in this currency. "
		"Use when the user uses @Currency mention or says 'show in USD', 'convert to EUR', etc. "
		"Reset when user says 'don't use target currency', 'use default currency', "
		"'reset currency'."
	),
	parameters={
		"currency": {
			"type": "string",
			"description": (
				"Currency code (e.g. 'USD', 'EUR', 'INR'). "
				"Pass null or empty string to reset to company default."
			),
		},
	},
	doctypes=["Company"],
	row_permission_safe=True,
)
def set_target_currency(currency=None):
	"""Set or reset the target display currency for the current chat session."""
	conversation_id = _get_current_conversation_id()
	if not conversation_id:
		return {"error": "No active conversation context"}

	company = get_default_company()
	company_currency = get_company_currency(company)

	# Reset if empty or same as company currency
	if not currency or currency == company_currency:
		currency = None

	ctx = set_session_context(conversation_id, "target_currency", currency)

	if currency:
		message = (
			f"Display currency set to {currency} for this session. All monetary values will be converted."
		)
	else:
		message = f"Display currency reset to company default ({company_currency})."

	return {
		"success": True,
		"target_currency": ctx["target_currency"],
		"company_currency": company_currency,
		"company": company,
		"message": message,
	}
