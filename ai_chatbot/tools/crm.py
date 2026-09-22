# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
CRM Tools Module
Customer Relationship Management tools for AI Chatbot

Provides lead analytics, opportunity pipeline, conversion rates, source analysis,
sales funnel, and stage-based reporting. Requires ERPNext to be installed.
"""

import frappe
from frappe.query_builder import functions as fn
from frappe.utils import flt

from ai_chatbot.core.config import get_company_currency, get_fiscal_year_dates
from ai_chatbot.core.session_context import get_company_filter
from ai_chatbot.data.charts import build_bar_chart, build_horizontal_bar, build_pie_chart
from ai_chatbot.data.currency import build_company_context, build_currency_response
from ai_chatbot.tools.common import primary as _primary
from ai_chatbot.tools.registry import register_tool


# ---------------------------------------------------------------------------
# 1. Lead Statistics (existing — updated with ECharts)
# ---------------------------------------------------------------------------
@register_tool(
	name="get_lead_statistics",
	category="crm",
	description="Get statistics about leads including count, status breakdown, and conversion rates",
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
	doctypes=["Lead"],
)
def get_lead_statistics(from_date=None, to_date=None, company=None):
	"""Get lead statistics with multi-company support and pie chart."""
	company = get_company_filter(company)

	if not from_date or not to_date:
		fy_from, fy_to = get_fiscal_year_dates(_primary(company))
		from_date = from_date or fy_from
		to_date = to_date or fy_to

	company_filter = ["company", "in", company] if isinstance(company, list) else ["company", "=", company]
	filters = [company_filter]
	if from_date:
		filters.append(["creation", ">=", from_date])
	if to_date:
		filters.append(["creation", "<=", to_date])

	leads = frappe.get_list("Lead", filters=filters, fields=["status"])

	status_count = {}
	for lead in leads:
		status = lead.status
		status_count[status] = status_count.get(status, 0) + 1

	result = {
		"total_leads": len(leads),
		"status_breakdown": status_count,
		"period": {"from": from_date, "to": to_date},
	}

	# Add ECharts pie chart for status breakdown
	if status_count:
		pie_data = [{"name": s, "value": c} for s, c in status_count.items()]
		result["echart_option"] = build_pie_chart(
			title="Lead Status Breakdown",
			data=pie_data,
		)

	return build_company_context(result, _primary(company))


# ---------------------------------------------------------------------------
# 2. Opportunity Analytics (Phase 11D: merged pipeline + by_stage)
# ---------------------------------------------------------------------------
@register_tool(
	name="get_opportunity_analytics",
	category="crm",
	description=(
		"Analyze sales opportunities. Use mode='pipeline' for pipeline summary with top opportunities, "
		"or mode='by_stage' for stage-grouped analysis over a date range."
	),
	parameters={
		"mode": {
			"type": "string",
			"description": "Analysis mode: 'pipeline' for overall pipeline, 'by_stage' for stage breakdown",
			"enum": ["pipeline", "by_stage"],
		},
		"from_date": {
			"type": "string",
			"description": "Start date (YYYY-MM-DD). Optional — omit to use current fiscal year start. Used only in 'by_stage' mode.",
		},
		"to_date": {
			"type": "string",
			"description": "End date (YYYY-MM-DD). Optional — omit to use current fiscal year end. Used only in 'by_stage' mode.",
		},
		"status": {
			"type": "string",
			"description": "Filter by status (Open, Converted, Lost, Quotation, Replied, Closed)",
		},
		"company": {
			"type": "string",
			"description": "Company name. Optional — omit to use user's default company.",
		},
	},
	doctypes=["Opportunity"],
)
def get_opportunity_analytics(mode="pipeline", from_date=None, to_date=None, status=None, company=None):
	"""Unified opportunity analytics — replaces get_opportunity_pipeline
	and get_opportunity_by_stage (Phase 11D consolidation).
	"""
	company = get_company_filter(company)

	if mode == "by_stage":
		return _opportunity_by_stage(from_date, to_date, status, company)
	return _opportunity_pipeline(status, company)


def _opportunity_pipeline(status, company):
	"""Pipeline mode: current opportunity pipeline with stages and top opportunities."""
	currency = get_company_currency(_primary(company))

	company_filter = {"company": ["in", company]} if isinstance(company, list) else {"company": company}
	filters = {**company_filter}
	if status:
		filters["status"] = status

	opportunities = frappe.get_list(
		"Opportunity",
		filters=filters,
		fields=["name", "opportunity_amount", "currency", "status", "sales_stage", "party_name"],
	)

	# Convert amounts to company currency and group by stage
	total_value = 0
	stage_data = {}
	for opp in opportunities:
		opp_currency = opp.get("currency")
		amount = flt(opp.get("opportunity_amount", 0))
		if opp_currency and opp_currency != currency and amount:
			from ai_chatbot.data.currency import get_exchange_rate

			rate = get_exchange_rate(opp_currency, currency)
			amount = amount * rate
		total_value += amount

		stage = opp.get("sales_stage") or "Unassigned"
		stage_data[stage] = stage_data.get(stage, 0) + amount

	stage_summary = [
		{
			"stage": stage,
			"value": flt(val, 2),
			"count": sum(1 for o in opportunities if (o.get("sales_stage") or "Unassigned") == stage),
		}
		for stage, val in stage_data.items()
	]
	stage_summary.sort(key=lambda s: s["value"], reverse=True)

	# Top 10 opportunities by amount for context
	top_opps = sorted(opportunities, key=lambda o: flt(o.get("opportunity_amount", 0)), reverse=True)[:10]
	top_opportunities = [
		{
			"name": o.name,
			"party_name": o.party_name,
			"amount": flt(o.opportunity_amount, 2),
			"currency": o.currency,
			"status": o.status,
			"sales_stage": o.sales_stage,
		}
		for o in top_opps
	]

	result = {
		"mode": "pipeline",
		"stage_summary": stage_summary,
		"top_opportunities": top_opportunities,
		"total_value": flt(total_value, 2),
		"count": len(opportunities),
	}

	if stage_data:
		categories = [s["stage"] for s in stage_summary]
		values = [s["value"] for s in stage_summary]
		result["echart_option"] = build_bar_chart(
			title="Opportunity Pipeline by Stage",
			categories=categories,
			series_data=values,
			y_axis_name=currency,
			series_name="Pipeline Value",
		)

	return build_currency_response(result, _primary(company))


def _opportunity_by_stage(from_date, to_date, status, company):
	"""By-stage mode: opportunities grouped by sales stage over a date range."""
	if not from_date or not to_date:
		fy_from, fy_to = get_fiscal_year_dates(_primary(company))
		from_date = from_date or fy_from
		to_date = to_date or fy_to

	opp = frappe.qb.DocType("Opportunity")

	query = (
		frappe.qb.from_(opp)
		.select(
			opp.sales_stage,
			fn.Count("*").as_("count"),
			fn.Sum(opp.base_opportunity_amount).as_("total_value"),
		)
		.where(opp.transaction_date >= from_date)
		.where(opp.transaction_date <= to_date)
		.groupby(opp.sales_stage)
		.orderby(fn.Sum(opp.base_opportunity_amount), order=frappe.qb.desc)
	)
	if isinstance(company, list):
		query = query.where(opp.company.isin(company))
	else:
		query = query.where(opp.company == company)

	if status:
		query = query.where(opp.status == status)

	rows = query.run(as_dict=True)

	stages = [
		{
			"sales_stage": r.sales_stage or "Unassigned",
			"count": r["count"],
			"total_value": flt(r.total_value, 2),
		}
		for r in rows
	]

	categories = [s["sales_stage"] for s in stages]
	values = [s["total_value"] for s in stages]

	result = {
		"mode": "by_stage",
		"stages": stages,
		"total_opportunities": sum(s["count"] for s in stages),
		"total_pipeline_value": flt(sum(s["total_value"] for s in stages), 2),
		"period": {"from": from_date, "to": to_date},
	}

	if categories:
		result["echart_option"] = build_bar_chart(
			title="Opportunities by Sales Stage",
			categories=categories,
			series_data=values,
			y_axis_name="Value",
			series_name="Pipeline Value",
		)

	return build_currency_response(result, _primary(company))


# ---------------------------------------------------------------------------
# 3. Lead Conversion Rate
# ---------------------------------------------------------------------------
@register_tool(
	name="get_lead_conversion_rate",
	category="crm",
	description="Get lead conversion rate showing how many leads converted to opportunities or customers",
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
	doctypes=["Lead"],
)
def get_lead_conversion_rate(from_date=None, to_date=None, company=None):
	"""Get lead-to-opportunity conversion rate."""
	company = get_company_filter(company)

	if not from_date or not to_date:
		fy_from, fy_to = get_fiscal_year_dates(_primary(company))
		from_date = from_date or fy_from
		to_date = to_date or fy_to

	company_filter = ["company", "in", company] if isinstance(company, list) else ["company", "=", company]
	filters = [
		company_filter,
		["creation", ">=", from_date],
		["creation", "<=", to_date],
	]

	leads = frappe.get_list("Lead", filters=filters, fields=["status"])

	total = len(leads)
	converted_statuses = {"Opportunity", "Converted", "Quotation"}
	converted = sum(1 for l in leads if l.status in converted_statuses)
	replied = sum(1 for l in leads if l.status == "Replied")
	lost = sum(1 for l in leads if l.status in ("Lost Quotation", "Do Not Contact"))

	conversion_rate = round(converted / total * 100, 1) if total > 0 else 0

	return build_company_context(
		{
			"total_leads": total,
			"converted_leads": converted,
			"conversion_rate": conversion_rate,
			"replied": replied,
			"lost": lost,
			"period": {"from": from_date, "to": to_date},
		},
		_primary(company),
	)


# ---------------------------------------------------------------------------
# 4. Lead Source Analysis (NEW)
# ---------------------------------------------------------------------------
@register_tool(
	name="get_lead_source_analysis",
	category="crm",
	description="Analyze leads by source/campaign to identify the best-performing lead channels",
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
	doctypes=["Lead"],
)
def get_lead_source_analysis(from_date=None, to_date=None, company=None):
	"""Get lead source analysis with pie chart."""
	company = get_company_filter(company)

	if not from_date or not to_date:
		fy_from, fy_to = get_fiscal_year_dates(_primary(company))
		from_date = from_date or fy_from
		to_date = to_date or fy_to

	lead = frappe.qb.DocType("Lead")

	# Check if utm_source field exists (newer ERPNext) or fall back to source
	source_field = lead.utm_source if hasattr(lead, "utm_source") else lead.source

	query = (
		frappe.qb.from_(lead)
		.select(
			source_field.as_("source"),
			fn.Count("*").as_("total"),
		)
		.where(lead.creation >= from_date)
		.where(lead.creation <= to_date)
		.groupby(source_field)
		.orderby(fn.Count("*"), order=frappe.qb.desc)
	)
	if isinstance(company, list):
		query = query.where(lead.company.isin(company))
	else:
		query = query.where(lead.company == company)
	rows = query.run(as_dict=True)

	sources = [{"source": r.source or "Unknown", "total_leads": r.total} for r in rows]

	pie_data = [{"name": s["source"], "value": s["total_leads"]} for s in sources]

	result = {
		"sources": sources,
		"total_sources": len(sources),
		"period": {"from": from_date, "to": to_date},
	}

	if pie_data:
		result["echart_option"] = build_pie_chart(
			title="Leads by Source",
			data=pie_data,
		)

	return build_company_context(result, _primary(company))


# ---------------------------------------------------------------------------
# 5. Sales Funnel (NEW)
# ---------------------------------------------------------------------------
@register_tool(
	name="get_sales_funnel",
	category="crm",
	description=(
		"Get sales funnel showing conversion from leads to opportunities to quotations to sales orders"
	),
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
	doctypes=["Lead", "Opportunity", "Quotation", "Sales Order"],
)
def get_sales_funnel(from_date=None, to_date=None, company=None):
	"""Get sales funnel: Lead → Opportunity → Quotation → Sales Order."""
	company = get_company_filter(company)

	if not from_date or not to_date:
		fy_from, fy_to = get_fiscal_year_dates(_primary(company))
		from_date = from_date or fy_from
		to_date = to_date or fy_to

	company_filter = ["in", company] if isinstance(company, list) else company

	# Stage 1: Leads created in the period
	leads_count = frappe.db.count(
		"Lead",
		[
			["company", "=", company_filter] if not isinstance(company, list) else ["company", "in", company],
			["creation", ">=", from_date],
			["creation", "<=", to_date],
		],
	)

	# Stage 2: Opportunities created in the period
	opps_count = frappe.db.count(
		"Opportunity",
		{
			"company": company_filter,
			"transaction_date": ["between", [from_date, to_date]],
		},
	)

	# Stage 3: Quotations submitted in the period
	quotations_count = frappe.db.count(
		"Quotation",
		{
			"company": company_filter,
			"docstatus": 1,
			"transaction_date": ["between", [from_date, to_date]],
		},
	)

	# Stage 4: Sales Orders submitted in the period
	orders_count = frappe.db.count(
		"Sales Order",
		{
			"company": company_filter,
			"docstatus": 1,
			"transaction_date": ["between", [from_date, to_date]],
		},
	)

	# Conversion rates between stages
	lead_to_opp = round(opps_count / leads_count * 100, 1) if leads_count > 0 else 0
	opp_to_quote = round(quotations_count / opps_count * 100, 1) if opps_count > 0 else 0
	quote_to_order = round(orders_count / quotations_count * 100, 1) if quotations_count > 0 else 0
	overall = round(orders_count / leads_count * 100, 1) if leads_count > 0 else 0

	# Funnel data: widest at top → narrowest at bottom (horizontal bar, reversed order)
	stages = ["Sales Orders", "Quotations", "Opportunities", "Leads"]
	counts = [orders_count, quotations_count, opps_count, leads_count]

	result = {
		"funnel": [
			{"stage": "Leads", "count": leads_count},
			{"stage": "Opportunities", "count": opps_count},
			{"stage": "Quotations", "count": quotations_count},
			{"stage": "Sales Orders", "count": orders_count},
		],
		"conversion_rates": {
			"lead_to_opportunity": lead_to_opp,
			"opportunity_to_quotation": opp_to_quote,
			"quotation_to_order": quote_to_order,
			"overall_lead_to_order": overall,
		},
		"period": {"from": from_date, "to": to_date},
		"echart_option": build_horizontal_bar(
			title="Sales Funnel",
			categories=stages,
			series_data=counts,
			x_axis_name="Count",
			series_name="Funnel",
		),
	}

	return build_company_context(result, _primary(company))
