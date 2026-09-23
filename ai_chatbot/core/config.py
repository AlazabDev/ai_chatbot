# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Centralized Configuration for AI Chatbot
Reads from Chatbot Settings DocType and user defaults.
"""

import frappe
from frappe.utils import add_days, nowdate


def is_app_installed(app_name):
	"""Check if a Frappe app is installed.

	Args:
		app_name: The app name (e.g. "hrms", "erpnext").

	Returns:
		bool
	"""
	return app_name in frappe.get_installed_apps()


def is_hrms_installed():
	"""Check if the HRMS app is installed."""
	return is_app_installed("hrms")


def is_erpnext_installed():
	"""Check if ERPNext is installed."""
	return is_app_installed("erpnext")


def _assert_company_access(company: str) -> str:
	"""Return *company* only when the current user may read that Company document."""
	if not company:
		return company

	if not frappe.has_permission(
		"Company",
		"read",
		doc=company,
		user=frappe.session.user,
	):
		frappe.throw(
			"Company is unavailable or you do not have permission to access it.",
			frappe.PermissionError,
		)
	return company


def get_default_company(company=None):
	"""Resolve an explicit or configured Company without bypassing User Permissions."""
	if company:
		return _resolve_company_name(company)

	user_default = frappe.defaults.get_user_default("Company")
	if user_default:
		return _assert_company_access(user_default)

	global_default = frappe.defaults.get_global_default("company")
	if global_default:
		return _assert_company_access(global_default)

	from ai_chatbot.core.exceptions import CompanyRequiredError

	raise CompanyRequiredError()


def _resolve_company_name(name: str) -> str:
	"""Resolve an AI-provided company name only across Companies visible to the user."""
	exact = frappe.get_list(
		"Company",
		filters={"name": name},
		pluck="name",
		limit_page_length=1,
	)
	if exact:
		return exact[0]

	matches = frappe.get_list(
		"Company",
		filters={"name": ["like", f"%{name}%"]},
		pluck="name",
		limit_page_length=5,
	)
	if len(matches) == 1:
		return matches[0]
	if matches:
		return min(matches, key=len)

	# Distinguish a denied existing company from a genuinely unknown name
	# without ever returning a denied Company into downstream direct queries.
	if frappe.db.exists("Company", name):
		frappe.throw(
			"Company is unavailable or you do not have permission to access it.",
			frappe.PermissionError,
		)

	return name


def get_fiscal_year_dates(company=None):
	"""Get the current fiscal year start and end dates for a company.

	Uses ERPNext's get_fiscal_year utility which respects company-specific
	fiscal year configurations via the Fiscal Year Company child table.

	Args:
		company: Company name. Resolved via get_default_company if not provided.

	Returns:
		Tuple of (from_date, to_date) as strings in YYYY-MM-DD format.
		Falls back to (today - 365 days, today) if no fiscal year is configured.
	"""
	company = get_default_company(company)

	try:
		from erpnext.accounts.utils import get_fiscal_year

		fy = get_fiscal_year(date=nowdate(), company=company)
		return (str(fy[1]), str(fy[2]))
	except Exception:
		# Fallback if no fiscal year is configured
		today = nowdate()
		return (str(add_days(today, -365)), today)


def get_company_currency(company):
	"""Get a Company's currency after enforcing document-level read permission."""
	_assert_company_access(company)
	return frappe.get_cached_value("Company", company, "default_currency")


def get_chatbot_settings():
	"""Get the Chatbot Settings singleton (cached per request).

	Returns:
		Chatbot Settings document.
	"""
	return frappe.get_single("Chatbot Settings")


def is_tool_category_enabled(category):
	"""Check if a tool category is enabled in settings.

	Args:
		category: Setting field name (e.g. "enable_crm_tools").

	Returns:
		bool
	"""
	settings = get_chatbot_settings()
	return bool(getattr(settings, category, False))


def get_query_limit(requested=None):
	"""Get effective query limit, capped at the configured maximum.

	Args:
		requested: Caller-requested limit (e.g. from a tool parameter).

	Returns:
		int: The effective limit.
	"""
	from ai_chatbot.core.constants import DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT

	settings = get_chatbot_settings()
	max_limit = getattr(settings, "max_query_limit", 0) or MAX_QUERY_LIMIT
	default = getattr(settings, "default_query_limit", 0) or DEFAULT_QUERY_LIMIT
	return min(requested or default, max_limit)


def get_top_n_limit(requested=None):
	"""Get effective top-N limit, capped at the configured maximum.

	Args:
		requested: Caller-requested limit (e.g. from a tool parameter).

	Returns:
		int: The effective limit.
	"""
	from ai_chatbot.core.constants import DEFAULT_TOP_N_LIMIT, MAX_QUERY_LIMIT

	settings = get_chatbot_settings()
	max_limit = getattr(settings, "max_query_limit", 0) or MAX_QUERY_LIMIT
	default = getattr(settings, "default_top_n_limit", 0) or DEFAULT_TOP_N_LIMIT
	return min(requested or default, max_limit)
