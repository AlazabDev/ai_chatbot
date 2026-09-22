# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Output Formatters for Automation

Converts AI markdown responses into styled HTML for email and PDF.
The AI response already includes well-formatted markdown tables with
complete data, so email/PDF output uses the same content — just
converted to HTML with inline styles for compatibility.

Charts are rendered server-side via ECharts SSR (Node.js). For PDF,
inline SVG is used (works in WeasyPrint/wkhtmltopdf/Chrome). For
email, charts are rendered as styled HTML tables since Gmail strips
SVG and corrupts large base64 data URIs.

Email clients and some PDF renderers ignore <style> blocks, so every
HTML element needs inline CSS.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html import escape as escape_html
from pathlib import Path

import frappe
from frappe.utils import nowdate

# Path to the ECharts SSR Node.js script (relative to this file)
_ECHART_SSR_SCRIPT = Path(__file__).parent / "echart_ssr.cjs"

# node_modules location for ECharts (frontend dependency)
_NODE_MODULES = Path(__file__).resolve().parent.parent.parent / "frontend" / "node_modules"

# CSS <style> block for PDF output. wkhtmltopdf supports <style> blocks
# (unlike email clients), so we use !important to guarantee overrides.
_PDF_STYLE_BLOCK = """<style>
body, div, p, li, span {
  text-align: left !important;
}
h1, h2, h3, h4, h5, h6 {
  text-align: left !important;
  page-break-inside: avoid;
}
table {
  font-size: 11px;
  page-break-inside: auto;
}
th, td { font-size: 11px; }
tr { page-break-inside: avoid; }
div.chart-container {
  text-align: center !important;
}
/* Prevent wkhtmltopdf from centering after separators at page breaks */
p + p, div + p {
  text-align: left !important;
}
</style>"""


def format_html_email(
	content: str,
	tool_results: list | None = None,
	report_name: str = "",
	company: str = "",
	for_pdf: bool = False,
) -> str:
	"""Convert AI markdown response to a styled HTML email or PDF.

	The AI's text response already includes markdown tables with all data
	from tool calls (the same tables displayed in the chat UI). This
	function converts that markdown to HTML, renders any ECharts from
	tool results as charts, and adds inline styles for compatibility.

	For PDF output (for_pdf=True), charts are embedded as inline SVG.
	For email output (default), charts are converted to base64 PNG
	images since Gmail and other email clients strip SVG tags.

	Args:
		content: The AI's markdown response text.
		tool_results: List of tool result dicts (may contain echart_option).
		report_name: Name of the report for the email header.
		company: Company name for the email header.
		for_pdf: If True, use inline SVG for charts. If False (default),
			convert charts to base64 PNG for email compatibility.

	Returns:
		Complete HTML string.
	"""
	# Pre-process markdown structure, then convert to HTML
	content = _fix_markdown_structure(content)
	content = _fix_markdown_lists(content)
	body_html = _markdown_to_html(content)
	body_html = _replace_hr_tags(body_html)
	body_html = _style_html_tables(body_html)
	body_html = _style_html_headings(body_html)

	# Render ECharts from tool results
	charts_html = _render_charts(tool_results, as_svg=for_pdf)
	if charts_html:
		body_html += charts_html

	return _build_email_template(body_html, report_name, company)


def _fix_markdown_structure(content: str) -> str:
	"""Fix structural markdown issues that prevent correct HTML conversion.

	AI models sometimes produce markdown with missing newlines that
	``markdown2`` cannot parse correctly:

	1. **Inline headings** — ``"some text:# Heading"`` needs a newline
	   before the ``#`` marker so it becomes an actual ``<h1>``.
	2. **Lists without blank line** — ``markdown2`` requires a blank line
	   between a paragraph and a list. ``"**Label:**\\n- item"`` gets
	   wrapped in a single ``<p>`` instead of ``<p>`` + ``<ul>``.

	This runs *before* ``_fix_markdown_lists`` and ``_markdown_to_html``.
	"""
	if not content:
		return content

	# 1) Ensure heading markers (# ## ### etc.) that appear mid-line
	#    get their own line.  e.g. "text:# Heading" → "text:\n\n# Heading"
	#    The lookbehind [^\n#] avoids splitting "## " into "#" + "\n\n# ".
	content = re.sub(r"([^\n#])(#{1,6}\s)", r"\1\n\n\2", content)

	# 2) Ensure a blank line before a dash-list or asterisk-list that
	#    immediately follows a non-list, non-blank line.  markdown2
	#    requires the blank line to start list parsing.
	lines = content.split("\n")
	fixed: list[str] = []
	for i, line in enumerate(lines):
		# Is this line a list item (- or * at start)?
		is_list_item = bool(re.match(r"^\s*[-*]\s", line))
		if is_list_item and i > 0:
			prev = lines[i - 1]
			prev_is_blank = prev.strip() == ""
			prev_is_list = bool(re.match(r"^\s*[-*]\s", prev))
			prev_is_numbered = bool(re.match(r"^\s*\d+\.\s", prev))
			if not prev_is_blank and not prev_is_list and not prev_is_numbered:
				fixed.append("")  # insert blank line
		fixed.append(line)

	return "\n".join(fixed)


def _fix_markdown_lists(content: str) -> str:
	"""Fix inline markdown list items so they render as proper HTML lists.

	AI models sometimes write bullet points inline on a single line:
	  "Recommendations: * item1 * item2 * item3"

	The markdown parser (markdown2) requires each list item on its own
	line with a blank line before the list. This function detects inline
	`* ` patterns and reformats them with proper newlines.

	Also handles numbered lists: "1. item1 2. item2"
	"""
	if not content:
		return content

	lines = content.split("\n")
	fixed_lines = []

	for line in lines:
		# Check for inline bullet items: "text * item1 * item2"
		# Must have at least 2 occurrences of " * " to be an inline list
		if line.count(" * ") >= 2:
			# Split on " * " pattern, keeping the first part as a preamble
			parts = re.split(r"\s\*\s", line)
			if len(parts) >= 3:
				preamble = parts[0].rstrip()
				if preamble:
					fixed_lines.append(preamble)
					fixed_lines.append("")  # blank line before list
				for item in parts[1:]:
					item = item.strip()
					if item:
						fixed_lines.append(f"* {item}")
				continue

		# Check for inline dash-list items: "text - item1 - item2 - item3"
		# Must have at least 2 occurrences of " - " to be an inline list
		# Exclude lines that look like table rows (contain "|")
		if line.count(" - ") >= 2 and "|" not in line:
			parts = re.split(r"\s-\s", line)
			if len(parts) >= 3:
				preamble = parts[0].rstrip()
				if preamble:
					fixed_lines.append(preamble)
					fixed_lines.append("")  # blank line before list
				for item in parts[1:]:
					item = item.strip()
					if item:
						fixed_lines.append(f"- {item}")
				continue

		# Check for inline numbered lists: "1. item1 2. item2 3. item3"
		if re.search(r"\d+\.\s.+\d+\.\s", line):
			parts = re.split(r"(?:^|\s)(\d+\.)\s", line)
			# re.split with capture group returns: [preamble, '1.', text, '2.', text, ...]
			if len(parts) >= 5:
				items = []
				preamble = parts[0].strip()
				for i in range(1, len(parts) - 1, 2):
					num = parts[i]
					text = parts[i + 1].strip() if i + 1 < len(parts) else ""
					if text:
						items.append(f"{num} {text}")
				if items and len(items) >= 2:
					if preamble:
						fixed_lines.append(preamble)
						fixed_lines.append("")  # blank line before list
					for item in items:
						fixed_lines.append(item)
					continue

		fixed_lines.append(line)

	return "\n".join(fixed_lines)


def _markdown_to_html(content: str) -> str:
	"""Convert markdown to HTML.

	Uses frappe.utils.md_to_html when available, falls back to
	basic HTML wrapping.
	"""
	if not content:
		return ""

	try:
		# AI output is untrusted. Frappe's markdown() sanitizes the generated
		# HTML before it is embedded into email/PDF output.
		return frappe.utils.markdown(content, sanitize=True, linkify=True)
	except Exception:
		# Fail closed: escape the source before adding minimal paragraph markup.
		paragraphs = content.split("\n\n")
		html_parts = []
		for p in paragraphs:
			p = p.strip()
			if p:
				p = escape_html(p).replace("\n", "<br>")
				html_parts.append(f"<p>{p}</p>")
		return "\n".join(html_parts)


def _style_html_tables(html: str) -> str:
	"""Inject inline styles into HTML tables for email/PDF compatibility.

	Markdown-to-HTML converters like ``markdown2`` may produce tags with
	existing ``style=`` attributes (e.g. ``text-align:right`` for column
	alignment). This function merges our required styles into those
	existing attributes, and adds new ``style=`` attributes where none
	exist.

	Uses ``(?=[> ])`` lookahead on ``<th>`` and ``<td>`` to avoid
	matching ``<thead>``, ``<tbody>``, ``<tfoot>`` etc.
	"""
	if "<table" not in html:
		return html

	table_style = (
		"border-collapse: collapse; width: 100%; margin-bottom: 15px; "
		"table-layout: auto; word-wrap: break-word;"
	)
	th_style = "padding: 6px 8px; border: 1px solid #ddd; background-color: #f5f5f5; font-size: 11px;"
	td_style = "padding: 6px 8px; border: 1px solid #ddd; font-size: 11px;"

	# --- <table> ---
	html = _merge_or_add_style("table", table_style, html)

	# --- <th> (avoid <thead>) ---
	html = _merge_or_add_style("th", th_style, html, lookahead=True)

	# --- <td> (avoid <tbody>, <tfoot>) ---
	html = _merge_or_add_style("td", td_style, html, lookahead=True)

	return html


def _merge_or_add_style(tag: str, extra_css: str, html: str, *, lookahead: bool = False) -> str:
	"""Merge ``extra_css`` into existing ``style=`` or add a new attribute.

	Args:
		tag: HTML tag name (e.g. ``"th"``, ``"td"``, ``"table"``).
		extra_css: CSS declarations to inject (must end with ``;``).
		html: The HTML string.
		lookahead: If True, use ``(?=[> ])`` to avoid matching longer
			tag names like ``<thead>`` when styling ``<th>``.
	"""
	la = r"(?=[> ])" if lookahead else ""

	# 1) Tags that already have a style= attribute — prepend our styles
	html = re.sub(
		rf"(<{tag}{la}\b[^>]*style=['\"])([^'\"]*?)(['\"])",
		rf"\g<1>{extra_css} \2\3",
		html,
	)

	# 2) Tags without any style= — add one
	html = re.sub(
		rf"<{tag}{la}(?![^>]*style=)([^>]*)>",
		rf"<{tag} style='{extra_css}'\1>",
		html,
	)

	return html


def _style_html_headings(html: str) -> str:
	"""Force every heading to left-align in wkhtmltopdf.

	wkhtmltopdf has a persistent bug where headings near page breaks get
	centred, ignoring both inline ``text-align: left`` and CSS
	``!important`` in ``<style>`` blocks.

	The nuclear workaround: replace ``<h1>`` to ``<h6>`` tags with styled
	``<p>`` tags that visually match headings but are immune to the
	wkhtmltopdf centering bug (which only affects actual heading elements).
	"""
	# Font sizes that approximate default heading sizes for a 14px body
	sizes = {"h1": "26px", "h2": "22px", "h3": "18px", "h4": "16px", "h5": "14px", "h6": "13px"}
	for tag, size in sizes.items():
		style = (
			f"display: block; clear: both; font-size: {size}; font-weight: bold; "
			f"text-align: left !important; margin: 20px 0 10px 0; color: #2c3e50; "
			f"line-height: 1.3; page-break-inside: avoid;"
		)
		# Replace <hN ...>...</hN> with <p style="...">...</p>
		# Discard original attributes (id=, style=, class=) since they
		# don't matter for PDF rendering and could conflict.
		html = re.sub(
			rf"<{tag}\b[^>]*>(.*?)</{tag}>",
			rf'<p style="{style}">\1</p>',
			html,
			flags=re.DOTALL,
		)
	return html


def _replace_hr_tags(html: str) -> str:
	"""Replace ``<hr>`` tags with a lightweight visual separator.

	wkhtmltopdf has a known bug where ``<hr>`` elements and heavy
	block-level separators near page breaks can reset text alignment.
	This replaces them with a minimal styled ``<p>`` that reduces
	(but may not fully eliminate) the issue.

	.. note::
		WeasyPrint (now the primary PDF engine) does not have the
		centering bug. This workaround remains for the wkhtmltopdf
		fallback path and can be removed once wkhtmltopdf is fully
		dropped.
	"""
	hr_replacement = (
		'<p style="margin: 10px 0; padding: 0; line-height: 0; '
		"border-bottom: 1px solid #ddd; height: 1px; font-size: 1px; "
		'text-align: left;">&nbsp;</p>'
	)
	return re.sub(r"<hr\s*/?>", hr_replacement, html)


def _build_email_template(body_html: str, report_name: str, company: str) -> str:
	"""Wrap the body HTML in a styled email template with header and footer.

	Args:
		body_html: The main content HTML.
		report_name: Report name for the header.
		company: Company name for the header.

	Returns:
		Complete HTML email document.
	"""
	today = escape_html(str(nowdate()))
	safe_report_name = escape_html(str(report_name or ""))
	safe_company = escape_html(str(company or ""))
	subtitle = f" — {safe_company}" if safe_company else ""

	return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{_PDF_STYLE_BLOCK}
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; \
font-size: 14px; line-height: 1.6; color: #333; text-align: left; max-width: 800px; margin: 0 auto; padding: 20px;">

<div style="background-color: #f8f9fa; border-bottom: 3px solid #4a90d9; padding: 20px; \
margin-bottom: 20px; border-radius: 4px 4px 0 0;">
<h2 style="margin: 0 0 5px; color: #2c3e50; text-align: left;">{safe_report_name}</h2>
<p style="margin: 0; color: #7f8c8d; font-size: 14px;">{today}{subtitle}</p>
</div>

<div style="padding: 0 5px;">
{body_html}
</div>

<div style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #eee; \
color: #95a5a6; font-size: 12px;">
<p>This report was auto-generated by AI Chatbot. The data reflects the state at the time of generation.</p>
</div>

</body>
</html>"""


def _extract_echart_options(tool_results: list | None) -> list[dict]:
	"""Extract echart_option dicts from tool results.

	Tool registry wraps results as {"success": True, "data": {...}},
	so echart_option may be nested under "data".

	Args:
		tool_results: List of tool result dicts from the AI executor.

	Returns:
		List of echart_option dicts.
	"""
	if not tool_results:
		return []

	options = []
	for result in tool_results:
		if not isinstance(result, dict):
			continue
		data = result.get("data", result) if "success" in result else result
		if isinstance(data, dict) and "echart_option" in data:
			options.append(data["echart_option"])
	return options


def _render_charts(tool_results: list | None, as_svg: bool = False) -> str:
	"""Render ECharts from tool results as HTML.

	For PDF (as_svg=True): renders via ECharts SSR to inline SVG.
	For email (as_svg=False): renders as a styled HTML table (since
	Gmail strips SVG and corrupts large base64 data URIs).

	Args:
		tool_results: List of tool result dicts from the AI executor.
		as_svg: If True, embed SVG directly. If False, use HTML table.

	Returns:
		HTML string with chart content, or empty string if none.
	"""
	echart_options = _extract_echart_options(tool_results)
	if not echart_options:
		return ""

	html_parts = []
	for option in echart_options:
		if as_svg:
			svg = _echart_to_svg(option)
			if svg:
				html_parts.append(
					f'<div class="chart-container" style="margin: 20px 0; text-align: center;">{svg}</div>'
				)
		else:
			table = _echart_to_html_table(option)
			if table:
				html_parts.append(table)

	if html_parts and not as_svg:
		# Inform email recipients that charts have been rendered as tables
		html_parts.insert(
			0,
			'<p style="font-size: 12px; color: #7f8c8d; font-style: italic; '
			'margin-bottom: 10px;">Note: Charts are displayed as tables below '
			"for email compatibility.</p>",
		)

	return "\n".join(html_parts)


def _echart_to_svg(echart_option: dict) -> str | None:
	"""Render a single ECharts option to an SVG string via Node.js SSR.

	Calls the echart_ssr.cjs script with the option JSON on stdin.
	Returns the SVG string or None on failure.

	Args:
		echart_option: Complete ECharts option dict.

	Returns:
		SVG string or None if rendering fails.
	"""
	node_bin = _find_node()
	if not node_bin:
		frappe.log_error("Node.js not found in PATH", "ECharts SSR")
		return None

	if not _ECHART_SSR_SCRIPT.exists():
		frappe.log_error(
			f"ECharts SSR script not found: {_ECHART_SSR_SCRIPT}",
			"ECharts SSR",
		)
		return None

	try:
		# Strip any accidental border on the chart title (ECharts SSR may
		# render a visible rect around the title text by default).
		title_obj = echart_option.get("title")
		if isinstance(title_obj, dict):
			title_obj.setdefault("borderWidth", 0)
			title_obj.setdefault("borderColor", "transparent")

		input_json = json.dumps(echart_option)
		env = {"NODE_PATH": str(_NODE_MODULES), "PATH": str(Path(node_bin).parent)}

		result = subprocess.run(
			[node_bin, str(_ECHART_SSR_SCRIPT)],
			input=input_json,
			capture_output=True,
			text=True,
			timeout=10,
			env=env,
		)

		if result.returncode != 0:
			frappe.log_error(
				f"ECharts SSR failed: {result.stderr}",
				"ECharts SSR",
			)
			return None

		svg = result.stdout.strip()
		if not svg.startswith("<svg"):
			return None

		return svg

	except subprocess.TimeoutExpired:
		frappe.log_error("ECharts SSR timed out", "ECharts SSR")
		return None
	except Exception as e:
		frappe.log_error(f"ECharts SSR error: {e!s}", "ECharts SSR")
		return None


def _echart_to_html_table(echart_option: dict) -> str | None:
	"""Convert an ECharts option dict to a styled HTML table.

	Extracts chart data (categories + series values) and renders them
	as an inline-styled HTML table. This is the most reliable way to
	show chart data in email since Gmail strips SVG and corrupts large
	base64 images.

	Supports: bar, line, horizontal bar, pie, multi-series, stacked.

	Args:
		echart_option: Complete ECharts option dict.

	Returns:
		Styled HTML table string, or None if data cannot be extracted.
	"""
	title = ""
	title_obj = echart_option.get("title", {})
	if isinstance(title_obj, dict):
		title = title_obj.get("text", "")

	series_list = echart_option.get("series", [])
	if not series_list:
		return None

	# Detect chart type from first series
	chart_type = series_list[0].get("type", "bar")

	# --- Pie chart ---
	if chart_type == "pie":
		return _pie_to_table(title, series_list[0].get("data", []))

	# --- Bar / Line / Horizontal bar ---
	# Get categories from xAxis or yAxis (horizontal bar uses yAxis)
	x_axis = echart_option.get("xAxis", {})
	y_axis = echart_option.get("yAxis", {})

	if isinstance(x_axis, dict) and x_axis.get("type") == "category":
		categories = x_axis.get("data", [])
	elif isinstance(y_axis, dict) and y_axis.get("type") == "category":
		categories = y_axis.get("data", [])
	else:
		return None

	if not categories:
		return None

	return _series_to_table(title, categories, series_list)


def _pie_to_table(title: str, data: list[dict]) -> str:
	"""Render pie chart data as a styled HTML table."""
	if not data:
		return ""

	th = "padding: 8px 12px; border: 1px solid #ddd; background-color: #f5f5f5; text-align: left;"
	td = "padding: 8px 12px; border: 1px solid #ddd;"
	td_r = f"{td} text-align: right;"

	rows = []
	total = sum(d.get("value", 0) for d in data if isinstance(d.get("value"), int | float))
	for d in data:
		name = escape_html(str(d.get("name", "")))
		value = d.get("value", 0)
		pct = f"{(value / total * 100):.1f}%" if total else ""
		rows.append(
			f"<tr><td style='{td}'>{name}</td>"
			f"<td style='{td_r}'>{value:,.2f}</td>"
			f"<td style='{td_r}'>{pct}</td></tr>"
		)

	safe_title = escape_html(str(title or ""))
	title_html = f"<h4 style='margin: 15px 0 8px; color: #2c3e50;'>{safe_title}</h4>" if safe_title else ""

	return f"""{title_html}
<table style='border-collapse: collapse; width: 100%; margin-bottom: 15px;'>
<thead><tr>
<th style='{th}'>Category</th>
<th style='{th} text-align: right;'>Value</th>
<th style='{th} text-align: right;'>%</th>
</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>"""


def _series_to_table(title: str, categories: list, series_list: list[dict]) -> str:
	"""Render bar/line chart data as a styled HTML table."""
	if not categories or not series_list:
		return ""

	th = "padding: 8px 12px; border: 1px solid #ddd; background-color: #f5f5f5; text-align: left;"
	td = "padding: 8px 12px; border: 1px solid #ddd;"
	td_r = f"{td} text-align: right;"

	# Build header: Category + one column per series
	headers = [f"<th style='{th}'>Category</th>"]
	for s in series_list:
		name = escape_html(str(s.get("name") or title or "Value"))
		headers.append(f"<th style='{th} text-align: right;'>{name}</th>")

	# Build rows
	rows = []
	for i, cat in enumerate(categories):
		cells = [f"<td style='{td}'>{escape_html(str(cat))}</td>"]
		for s in series_list:
			data = s.get("data", [])
			val = data[i] if i < len(data) else ""
			# Handle colorized data: {"value": 1000, "itemStyle": {...}}
			if isinstance(val, dict):
				val = val.get("value", "")
			if isinstance(val, int | float):
				val = f"{val:,.2f}"
			cells.append(f"<td style='{td_r}'>{escape_html(str(val))}</td>")
		rows.append(f"<tr>{''.join(cells)}</tr>")

	safe_title = escape_html(str(title or ""))
	title_html = f"<h4 style='margin: 15px 0 8px; color: #2c3e50;'>{safe_title}</h4>" if safe_title else ""

	return f"""{title_html}
<table style='border-collapse: collapse; width: 100%; margin-bottom: 15px;'>
<thead><tr>{"".join(headers)}</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>"""


def _svg_to_png_img(svg_string: str) -> str | None:
	"""Convert an SVG string to a base64-encoded PNG <img> tag.

	Uses ImageMagick (convert) to rasterize the SVG. Returns an <img>
	tag with a data URI, or None if conversion fails.

	Note: Currently unused — Gmail corrupts large base64 data URIs via
	quoted-printable encoding. Kept for future use (e.g. hosted images
	or smaller charts).

	Args:
		svg_string: The SVG markup string.

	Returns:
		HTML <img> tag with base64 PNG data URI, or None.
	"""
	import base64

	convert_bin = shutil.which("convert")
	if not convert_bin:
		return svg_string

	try:
		result = subprocess.run(
			[convert_bin, "-density", "150", "svg:-", "png:-"],
			input=svg_string.encode("utf-8"),
			capture_output=True,
			timeout=15,
		)

		if result.returncode != 0:
			frappe.log_error(
				f"SVG to PNG conversion failed: {result.stderr.decode()[:500]}",
				"ECharts SSR",
			)
			return svg_string

		png_data = base64.b64encode(result.stdout).decode("ascii")
		return (
			f'<img src="data:image/png;base64,{png_data}" '
			f'alt="Chart" style="max-width: 100%; height: auto;" />'
		)

	except subprocess.TimeoutExpired:
		frappe.log_error("SVG to PNG conversion timed out", "ECharts SSR")
		return svg_string
	except Exception as e:
		frappe.log_error(f"SVG to PNG conversion error: {e!s}", "ECharts SSR")
		return svg_string


def _find_node() -> str | None:
	"""Find the Node.js binary.

	Background workers may not have nvm in PATH, so also check the
	bench's node location via Frappe conf and common nvm paths.
	"""
	# 1. Check PATH (works if nvm is loaded)
	node = shutil.which("node")
	if node:
		return node

	# 2. Check common nvm/fnm locations under the bench user's home
	home = Path.home()
	for pattern in [
		home / ".nvm" / "versions" / "node",
		home / ".fnm" / "node-versions",
	]:
		if pattern.exists():
			# Pick the latest version directory
			versions = sorted(pattern.iterdir(), reverse=True)
			for v in versions:
				candidate = v / "bin" / "node"
				if candidate.exists():
					return str(candidate)

	# 3. System node
	for path in ["/usr/bin/node", "/usr/local/bin/node"]:
		if Path(path).exists():
			return path

	return None
