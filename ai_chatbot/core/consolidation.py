# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Multi-Company Consolidation Helpers
Parent company detection, child company discovery, and consolidated data execution.
"""

import frappe

from ai_chatbot.core.config import get_company_currency


def is_parent_company(company):
	"""Return True when the user can read at least one child Company."""
	return bool(get_child_companies(company))


def get_child_companies(parent_company):
	"""Return only descendant Companies visible to the current user."""
	if not parent_company:
		return []

	if not frappe.has_permission(
		"Company",
		"read",
		doc=parent_company,
		user=frappe.session.user,
	):
		frappe.throw(
			"You do not have permission to access this Company.",
			frappe.PermissionError,
		)

	try:
		descendants = frappe.db.get_descendants("Company", parent_company) or []
	except Exception:
		return []

	if not descendants:
		return []

	allowed = set(
		frappe.get_list(
			"Company",
			filters={"name": ["in", descendants]},
			pluck="name",
			limit_page_length=0,
		)
	)
	return [company for company in descendants if company in allowed]


def get_consolidated_data(tool_func, parent_company, **kwargs):
	"""Execute a tool function across a parent company and all its subsidiaries.

	Collects results from each company and includes exchange rate information
	for currency conversion.

	Args:
		tool_func: The tool function to call (e.g. get_sales_analytics).
		parent_company: The parent company name.
		**kwargs: Additional keyword arguments to pass to the tool function.

	Returns:
		dict with keys:
			- companies: list of {company, data, currency, exchange_rate}
			- target_currency: The parent company's currency
			- parent_company: The parent company name
	"""
	target_currency = get_company_currency(parent_company)
	children = get_child_companies(parent_company)
	all_companies = [parent_company, *list(children)]

	results = []
	for company in all_companies:
		try:
			result = tool_func(company=company, **kwargs)
			company_currency = get_company_currency(company)
			rate = 1.0
			if company_currency != target_currency:
				rate = _get_exchange_rate(company_currency, target_currency)
			results.append(
				{
					"company": company,
					"data": result,
					"currency": company_currency,
					"exchange_rate": rate,
				}
			)
		except Exception as e:
			frappe.log_error(
				f"Consolidation error for {company}: {e}",
				"AI Chatbot Consolidation",
			)
			results.append(
				{
					"company": company,
					"data": {"error": "Unable to load data for this Company."},
					"currency": None,
					"exchange_rate": 1.0,
				}
			)

	return {
		"companies": results,
		"target_currency": target_currency,
		"parent_company": parent_company,
	}


def _get_exchange_rate(from_currency, to_currency):
	"""Get exchange rate using ERPNext's utility.

	Falls back to 1.0 if the rate cannot be determined.
	"""
	if from_currency == to_currency:
		return 1.0
	try:
		from erpnext.setup.utils import get_exchange_rate

		return get_exchange_rate(from_currency, to_currency) or 1.0
	except Exception:
		return 1.0
