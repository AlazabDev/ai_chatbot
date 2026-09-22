# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Selling Tools Module
Sales and customer analytics tools for AI Chatbot
"""

import frappe
from frappe.query_builder import functions as fn
from frappe.utils import flt

from ai_chatbot.core.config import get_fiscal_year_dates, get_top_n_limit
from ai_chatbot.core.session_context import get_company_filter
from ai_chatbot.data.analytics import get_grouped_sum, get_time_series
from ai_chatbot.data.charts import build_bar_chart, build_horizontal_bar, build_line_chart, build_pie_chart
from ai_chatbot.data.currency import build_currency_response
from ai_chatbot.tools.registry import register_tool


@register_tool(
	name="get_sales_analytics",
	category="selling",
	description="Get sales analytics including revenue, orders, and growth trends",
	parameters={
		"from_date": {
			"type": "string",
			"description": "Start date (YYYY-MM-DD). Optional — omit to use current fiscal year start.",
		},
		"to_date": {
			"type": "string",
			"description": "End date (YYYY-MM-DD). Optional — omit to use current fiscal year end.",
		},
		"customer": {"type": "string", "description": "Filter by customer name"},
		"company": {
			"type": "string",
			"description": "Company name. Optional — omit to use user's default company.",
		},
	},
	doctypes=["Sales Invoice"],
)
def get_sales_analytics(from_date=None, to_date=None, customer=None, company=None):
	"""Get sales analytics with multi-company and base currency support."""
	company = get_company_filter(company)

	if not from_date or not to_date:
		fy_from, fy_to = get_fiscal_year_dates(company[0] if isinstance(company, list) else company)
		from_date = from_date or fy_from
		to_date = to_date or fy_to

	# Build list filters for date range to support both from_date and to_date
	if isinstance(company, list):
		list_filters = [["docstatus", "=", 1], ["company", "in", company]]
	else:
		list_filters = [["docstatus", "=", 1], ["company", "=", company]]
	if from_date:
		list_filters.append(["posting_date", ">=", from_date])
	if to_date:
		list_filters.append(["posting_date", "<=", to_date])
	if customer:
		list_filters.append(["customer", "=", customer])

	invoices = frappe.get_list(
		"Sales Invoice",
		filters=list_filters,
		fields=["base_grand_total"],
	)

	total_revenue = sum(flt(inv.base_grand_total) for inv in invoices)
	invoice_count = len(invoices)

	# Monthly revenue breakdown for the queried period (time-series bucketed by month)
	from frappe.utils import getdate, month_diff

	months_in_range = max(1, month_diff(to_date, from_date) + 1)
	monthly_series = get_time_series(
		doctype="Sales Invoice",
		value_field="base_grand_total",
		date_field="posting_date",
		filters={"docstatus": 1, **({"customer": customer} if customer else {})},
		company=company,
		months=months_in_range,
	)

	# Filter to only months within the requested date range
	from_month = str(getdate(from_date))[:7]
	to_month = str(getdate(to_date))[:7]
	monthly_series = [d for d in monthly_series if from_month <= d["month"] <= to_month]

	categories = [d["month"] for d in monthly_series]
	values = [flt(d["total"], 2) for d in monthly_series]

	result = {
		"total_revenue": total_revenue,
		"invoice_count": invoice_count,
		"average_order_value": total_revenue / invoice_count if invoice_count else 0,
		"period": {"from": from_date, "to": to_date},
		"monthly_revenue": [{"month": d["month"], "revenue": flt(d["total"], 2)} for d in monthly_series],
		"echart_option": build_bar_chart(
			title="Monthly Sales Revenue",
			categories=categories,
			series_data=values,
			y_axis_name="Revenue",
			series_name="Revenue",
		)
		if categories
		else None,
	}
	# Strip None echart_option to keep response clean
	if result["echart_option"] is None:
		del result["echart_option"]

	return build_currency_response(result, company[0] if isinstance(company, list) else company)


@register_tool(
	name="get_top_customers",
	category="selling",
	description="Get top customers by revenue",
	parameters={
		"limit": {"type": "integer", "description": "Number of customers to return (default 10)"},
		"from_date": {
			"type": "string",
			"description": "Start date (YYYY-MM-DD). Optional — omit to use current fiscal year start.",
		},
		"to_date": {
			"type": "string",
			"description": "End date (YYYY-MM-DD). Optional — omit to use current fiscal year end.",
		},
		"company": {
			"type": "string",
			"description": "Company name. Optional — omit to use user's default company.",
		},
	},
	doctypes=["Sales Invoice"],
)
def get_top_customers(limit=10, from_date=None, to_date=None, company=None):
	"""Get top customers by revenue using the analytics data layer (no raw SQL)."""
	limit = get_top_n_limit(limit)
	company = get_company_filter(company)

	if not from_date or not to_date:
		fy_from, fy_to = get_fiscal_year_dates(company[0] if isinstance(company, list) else company)
		from_date = from_date or fy_from
		to_date = to_date or fy_to

	filters = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}

	customers = get_grouped_sum(
		doctype="Sales Invoice",
		sum_field="base_grand_total",
		group_field="customer",
		filters=filters,
		company=company,
		order_by_sum=True,
		limit=limit,
	)

	customer_list = [
		{
			"customer": c.customer,
			"total_revenue": flt(c.total),
			"order_count": c.count,
		}
		for c in customers
	]

	# Horizontal bar chart — reversed so highest revenue is at the top
	chart_customers = list(reversed(customer_list))
	categories = [c["customer"] for c in chart_customers]
	values = [flt(c["total_revenue"], 2) for c in chart_customers]

	result = {
		"top_customers": customer_list,
		"echart_option": build_horizontal_bar(
			title="Top Customers by Revenue",
			categories=categories,
			series_data=values,
			x_axis_name="Revenue",
			series_name="Revenue",
		)
		if customer_list
		else None,
	}
	if result["echart_option"] is None:
		del result["echart_option"]

	return build_currency_response(result, company[0] if isinstance(company, list) else company)


@register_tool(
	name="get_transaction_trend",
	category="selling",
	description=(
		"Get monthly transaction trend over time. "
		"Use type='sales' for revenue trend (Sales Invoice) or type='purchase' for spending trend (Purchase Invoice)."
	),
	parameters={
		"type": {
			"type": "string",
			"description": "Transaction type: 'sales' or 'purchase'",
			"enum": ["sales", "purchase"],
		},
		"months": {"type": "integer", "description": "Number of months to show (default 12)"},
		"company": {
			"type": "string",
			"description": "Company name. Optional — omit to use user's default company.",
		},
	},
	doctypes=["Sales Invoice", "Purchase Invoice"],
)
def get_transaction_trend(type="sales", months=12, company=None):
	"""Monthly revenue/spending time series — unified sales + purchase trend.

	Replaces get_sales_trend and get_purchase_trend (Phase 11D consolidation).
	"""
	company = get_company_filter(company)

	is_sales = type == "sales"
	doctype = "Sales Invoice" if is_sales else "Purchase Invoice"
	value_label = "revenue" if is_sales else "spending"
	chart_title = "Monthly Sales Revenue" if is_sales else "Monthly Purchase Spending"

	data = get_time_series(
		doctype=doctype,
		value_field="base_grand_total",
		date_field="posting_date",
		filters={"docstatus": 1},
		company=company,
		months=months,
	)

	total = sum(flt(d.get("total", 0)) for d in data)
	categories = [d["month"] for d in data]
	values = [flt(d["total"], 2) for d in data]

	result = {
		"type": type,
		"months": [
			{"month": d["month"], value_label: flt(d["total"], 2), "invoice_count": d.get("count", 0)}
			for d in data
		],
		f"total_{value_label}": flt(total, 2),
		"period_months": months,
		"echart_option": build_line_chart(
			title=chart_title,
			categories=categories,
			series_data=values,
			y_axis_name=value_label.title(),
			series_name=value_label.title(),
		),
	}
	return build_currency_response(result, company[0] if isinstance(company, list) else company)


@register_tool(
	name="get_sales_by_territory",
	category="selling",
	description="Get sales breakdown by territory/region",
	parameters={
		"from_date": {
			"type": "string",
			"description": "Start date (YYYY-MM-DD). Optional — omit to use current fiscal year start.",
		},
		"to_date": {
			"type": "string",
			"description": "End date (YYYY-MM-DD). Optional — omit to use current fiscal year end.",
		},
		"company": {
			"type": "string",
			"description": "Company name. Optional — omit to use user's default company.",
		},
	},
	doctypes=["Sales Invoice"],
)
def get_sales_by_territory(from_date=None, to_date=None, company=None):
	"""Sales grouped by territory from Sales Invoice."""
	company = get_company_filter(company)

	if not from_date or not to_date:
		fy_from, fy_to = get_fiscal_year_dates(company[0] if isinstance(company, list) else company)
		from_date = from_date or fy_from
		to_date = to_date or fy_to

	filters = {"docstatus": 1, "posting_date": ["between", [from_date, to_date]]}

	territories = get_grouped_sum(
		doctype="Sales Invoice",
		sum_field="base_grand_total",
		group_field="territory",
		filters=filters,
		company=company,
		order_by_sum=True,
	)

	territory_data = [
		{
			"territory": t.territory or "Unknown",
			"total_revenue": flt(t.total, 2),
			"invoice_count": t.count,
		}
		for t in territories
	]

	pie_data = [{"name": t["territory"], "value": t["total_revenue"]} for t in territory_data]

	result = {
		"territories": territory_data,
		"period": {"from": from_date, "to": to_date},
		"echart_option": build_pie_chart(
			title="Sales by Territory",
			data=pie_data,
		),
	}
	return build_currency_response(result, company[0] if isinstance(company, list) else company)


@register_tool(
	name="get_by_item_group",
	category="selling",
	description=(
		"Get transaction breakdown by item group/product category. "
		"Use type='sales' for sales breakdown or type='purchase' for purchase breakdown."
	),
	parameters={
		"type": {
			"type": "string",
			"description": "Transaction type: 'sales' or 'purchase'",
			"enum": ["sales", "purchase"],
		},
		"from_date": {
			"type": "string",
			"description": "Start date (YYYY-MM-DD). Optional — omit to use current fiscal year start.",
		},
		"to_date": {
			"type": "string",
			"description": "End date (YYYY-MM-DD). Optional — omit to use current fiscal year end.",
		},
		"limit": {"type": "integer", "description": "Number of item groups to return (default 10)"},
		"company": {
			"type": "string",
			"description": "Company name. Optional — omit to use user's default company.",
		},
	},
	doctypes=["Sales Invoice", "Purchase Invoice"],
)
def get_by_item_group(type="sales", from_date=None, to_date=None, limit=10, company=None):
	"""Transactions grouped by item_group — unified sales + purchase by item group.

	Replaces get_sales_by_item_group and get_purchase_by_item_group (Phase 11D consolidation).
	"""
	limit = get_top_n_limit(limit)
	company = get_company_filter(company)

	is_sales = type == "sales"
	parent_doctype = "Sales Invoice" if is_sales else "Purchase Invoice"
	child_doctype = f"{parent_doctype} Item"
	chart_title = "Sales by Item Group" if is_sales else "Purchases by Item Group"
	series_name = "Sales" if is_sales else "Purchases"

	if not from_date or not to_date:
		fy_from, fy_to = get_fiscal_year_dates(company[0] if isinstance(company, list) else company)
		from_date = from_date or fy_from
		to_date = to_date or fy_to

	parent = frappe.qb.DocType(parent_doctype)
	child = frappe.qb.DocType(child_doctype)

	# Sales Invoice Item uses stock_qty; Purchase Invoice Item uses qty
	qty_field = child.stock_qty if is_sales else child.qty

	query = (
		frappe.qb.from_(child)
		.join(parent)
		.on(child.parent == parent.name)
		.select(
			child.item_group,
			fn.Sum(child.base_amount).as_("total_amount"),
			fn.Sum(qty_field).as_("total_qty"),
			fn.Count("*").as_("line_count"),
		)
		.where(parent.docstatus == 1)
	)

	if isinstance(company, list):
		query = query.where(parent.company.isin(company))
	else:
		query = query.where(parent.company == company)

	rows = (
		query.where(parent.posting_date >= from_date)
		.where(parent.posting_date <= to_date)
		.groupby(child.item_group)
		.orderby(fn.Sum(child.base_amount), order=frappe.qb.desc)
		.limit(limit)
		.run(as_dict=True)
	)

	item_groups = [
		{
			"item_group": r.item_group or "Unknown",
			"total_amount": flt(r.total_amount, 2),
			"total_qty": flt(r.total_qty),
		}
		for r in rows
	]

	categories = [ig["item_group"] for ig in item_groups]
	values = [ig["total_amount"] for ig in item_groups]

	result = {
		"type": type,
		"item_groups": item_groups,
		"period": {"from": from_date, "to": to_date},
		"echart_option": build_bar_chart(
			title=chart_title,
			categories=categories,
			series_data=values,
			y_axis_name="Amount",
			series_name=series_name,
		),
	}
	return build_currency_response(result, company[0] if isinstance(company, list) else company)
