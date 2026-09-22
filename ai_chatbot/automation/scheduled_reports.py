# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Scheduled Report Runner

Processes all enabled Chatbot Scheduled Report documents that are due
for execution. Called by the Frappe scheduler every 15 minutes.

Each due report is enqueued as a separate background job to avoid
blocking the scheduler and to isolate failures.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

import frappe
from frappe.utils import get_datetime, now_datetime


def run_scheduled_reports() -> None:
	"""Scheduler entry point — check and run due reports.

	Called every 15 minutes by Frappe's scheduler_events cron.
	Checks the enable_automation master toggle, then queries all
	enabled reports and enqueues those that are due.
	"""
	settings = frappe.get_single("Chatbot Settings")
	if not getattr(settings, "enable_automation", False):
		return

	reports = frappe.get_all(
		"Chatbot Scheduled Report",
		filters={"enabled": 1},
		fields=[
			"name",
			"schedule",
			"time_of_day",
			"day_of_week",
			"day_of_month",
			"cron_expression",
			"last_run",
		],
	)

	for report in reports:
		if _is_report_due(report):
			frappe.enqueue(
				"ai_chatbot.automation.scheduled_reports._execute_single_report",
				queue="long",
				timeout=600,
				now=False,
				report_name=report.name,
			)


def _is_report_due(report: dict) -> bool:
	"""Check if a report is due for execution based on its schedule.

	Args:
		report: Report dict with schedule, time_of_day, day_of_week,
			day_of_month, cron_expression, and last_run fields.

	Returns:
		True if the report should run now.
	"""
	now = now_datetime()
	last_run = get_datetime(report.last_run) if report.last_run else None

	# Parse time_of_day (defaults to 08:00)
	time_of_day = report.time_of_day or "08:00:00"
	if isinstance(time_of_day, str) and len(time_of_day) == 5:
		time_of_day = f"{time_of_day}:00"

	schedule = report.schedule

	if schedule == "Daily":
		return _is_daily_due(now, last_run, time_of_day)

	if schedule == "Weekly":
		day_of_week = report.day_of_week or "Monday"
		return _is_weekly_due(now, last_run, time_of_day, day_of_week)

	if schedule == "Monthly":
		day_of_month = report.day_of_month or 1
		return _is_monthly_due(now, last_run, time_of_day, day_of_month)

	if schedule == "Custom Cron":
		cron_expr = report.cron_expression
		if not cron_expr:
			return False
		return _is_cron_due(now, last_run, cron_expr)

	return False


def _is_daily_due(now: datetime, last_run: datetime | None, time_of_day: str) -> bool:
	"""Check if a daily report is due."""
	scheduled_time = _parse_time(time_of_day)
	if now.hour < scheduled_time.hour:
		return False
	if now.hour == scheduled_time.hour and now.minute < scheduled_time.minute:
		return False

	# Already ran today?
	if last_run and last_run.date() >= now.date():
		return False

	return True


def _is_weekly_due(now: datetime, last_run: datetime | None, time_of_day: str, day_of_week: str) -> bool:
	"""Check if a weekly report is due."""
	weekday_map = {
		"Monday": 0,
		"Tuesday": 1,
		"Wednesday": 2,
		"Thursday": 3,
		"Friday": 4,
		"Saturday": 5,
		"Sunday": 6,
	}
	target_weekday = weekday_map.get(day_of_week, 0)

	if now.weekday() != target_weekday:
		return False

	return _is_daily_due(now, last_run, time_of_day)


def _is_monthly_due(now: datetime, last_run: datetime | None, time_of_day: str, day_of_month: int) -> bool:
	"""Check if a monthly report is due."""
	if now.day != day_of_month:
		return False

	return _is_daily_due(now, last_run, time_of_day)


def _is_cron_due(now: datetime, last_run: datetime | None, cron_expression: str) -> bool:
	"""Check if a custom cron schedule is due."""
	try:
		from croniter import croniter

		# If never ran, check from the start of today
		base = last_run or now.replace(hour=0, minute=0, second=0, microsecond=0)
		cron = croniter(cron_expression, base)
		next_run = cron.get_next(datetime)
		return now >= next_run
	except Exception:
		return False


def _parse_time(time_str: str) -> datetime:
	"""Parse a time string (HH:MM or HH:MM:SS) into a datetime object."""
	try:
		parts = str(time_str).split(":")
		hour = int(parts[0]) if len(parts) > 0 else 0
		minute = int(parts[1]) if len(parts) > 1 else 0
		return datetime(2000, 1, 1, hour, minute)
	except (ValueError, IndexError):
		return datetime(2000, 1, 1, 8, 0)


def _execute_single_report(report_name: str) -> None:
	"""Execute a single scheduled report.

	1. Load the report document
	2. Run the prompt through the headless AI executor
	3. Format the response as HTML email
	4. Dispatch via email
	5. Update the report status

	Args:
		report_name: The Chatbot Scheduled Report document name.
	"""
	try:
		report = frappe.get_doc("Chatbot Scheduled Report", report_name)

		# Double-check it's still enabled
		if not report.enabled:
			return

		from ai_chatbot.automation.executor import execute_prompt
		from ai_chatbot.automation.formatters import format_html_email
		from ai_chatbot.automation.notifications.dispatcher import dispatch

		# Execute the prompt (AI provider resolved from Chatbot Settings)
		result = execute_prompt(
			prompt=report.prompt,
			company=report.company,
		)

		content = result.get("content", "")
		tool_results = result.get("tool_results", [])

		# Generate PDF attachment if requested (uses SVG charts)
		attachments = []
		output_format = report.format or "Email HTML"

		if output_format in ("PDF", "Both"):
			pdf_html = format_html_email(
				content=content,
				tool_results=tool_results,
				report_name=report.report_name,
				company=report.company,
				for_pdf=True,
			)
			pdf_data = _generate_pdf(pdf_html, report.report_name)
			if pdf_data:
				attachments.append(pdf_data)

		# Format for email (uses base64 PNG charts for Gmail compatibility)
		html_message = format_html_email(
			content=content,
			tool_results=tool_results,
			report_name=report.report_name,
			company=report.company,
			for_pdf=False,
		)

		# Build recipients list from child table
		recipients = [{"recipient_email": r.recipient_email, "user": r.user} for r in report.recipients]

		# For PDF-only, send a minimal email body with the PDF attached
		if output_format == "PDF":
			safe_report_name = escape(str(report.report_name or ""))
			safe_company = escape(str(report.company or ""))
			html_message = (
				f"<p>Please find the attached report: <b>{safe_report_name}</b></p>"
				f"<p style='color: #7f8c8d; font-size: 12px;'>"
				f"Generated on {frappe.utils.nowdate()} for {safe_company}.</p>"
			)

		# Dispatch email notification
		dispatch(
			subject=f"AI Scheduled Report: {report.report_name}",
			html_message=html_message,
			recipients=recipients,
			sender=report.sender or None,
			reference_doctype="Chatbot Scheduled Report",
			reference_name=report.name,
			attachments=attachments or None,
		)

		# Update report status
		report.last_run = now_datetime()
		report.last_run_status = "Success"
		report.last_error = ""
		report.run_count = (report.run_count or 0) + 1
		report.save(ignore_permissions=True)
		frappe.db.commit()

	except Exception as e:
		frappe.log_error(
			f"AI Scheduled report failed: {report_name}\n{e!s}",
			"AI Chatbot Scheduled Report",
		)

		# Update report with error status
		try:
			frappe.db.set_value(
				"Chatbot Scheduled Report",
				report_name,
				{
					"last_run": now_datetime(),
					"last_run_status": "Failed",
					"last_error": str(e)[:500],
				},
			)
			frappe.db.commit()
		except Exception:
			pass


def _generate_pdf(html_content: str, report_name: str) -> dict | None:
	"""Generate a PDF from HTML content using Frappe's PDF engine.

	Args:
		html_content: The HTML email body to convert to PDF.
		report_name: Used for the PDF filename.

	Returns:
		Dict with "fname" and "fcontent" keys for frappe.sendmail attachments,
		or None if PDF generation fails.
	"""
	try:
		from ai_chatbot.utils.pdf import html_to_pdf

		pdf_content = html_to_pdf(html_content)
		safe_name = report_name.replace(" ", "_").replace("/", "_")
		today = frappe.utils.nowdate()

		return {
			"fname": f"{safe_name}_{today}.pdf",
			"fcontent": pdf_content,
		}
	except Exception as e:
		frappe.log_error(
			f"PDF generation failed for report: {report_name}\n{e!s}",
			"AI Chatbot Scheduled Report",
		)
		return None
