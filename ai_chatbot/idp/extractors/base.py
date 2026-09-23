# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
Base extractor and content extraction factory.

Dispatches to the appropriate extractor based on file MIME type
and returns a unified content structure for LLM processing.
"""

from __future__ import annotations

import base64
import mimetypes
from urllib.parse import quote, unquote

import frappe

# MIME type groups
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
PDF_MIME_TYPES = {"application/pdf"}
EXCEL_MIME_TYPES = {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
CSV_MIME_TYPES = {"text/csv"}
DOCX_MIME_TYPES = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
TEXT_MIME_TYPES = {"text/plain"}


def extract_content(file_url: str) -> dict:
	"""Extract text or image data from a file stored in Frappe.

	Reads the file from the Frappe File DocType, determines the MIME type,
	and dispatches to the appropriate extractor.

	Args:
		file_url: Frappe file URL (e.g., "/private/files/invoice.pdf").

	Returns:
		dict with keys:
			content_type: "text" or "image"
			text: str — extracted text (for text-based files)
			base64: str — base64-encoded data (for images)
			mime_type: str — detected MIME type
			file_name: str — original file name
	"""
	file_doc = _get_file_doc(file_url)
	file_path = file_doc.get_full_path()
	file_name = file_doc.file_name or file_url.split("/")[-1]
	mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

	with open(file_path, "rb") as f:
		file_bytes = f.read()

	if mime_type in IMAGE_MIME_TYPES:
		return _extract_image(file_bytes, mime_type, file_name)

	if mime_type in PDF_MIME_TYPES:
		return _extract_pdf(file_bytes, mime_type, file_name)

	if mime_type in EXCEL_MIME_TYPES:
		return _extract_excel(file_bytes, mime_type, file_name)

	if mime_type in CSV_MIME_TYPES:
		return _extract_csv(file_bytes, mime_type, file_name)

	if mime_type in DOCX_MIME_TYPES:
		return _extract_docx(file_bytes, mime_type, file_name)

	if mime_type in TEXT_MIME_TYPES:
		return _extract_text(file_bytes, mime_type, file_name)

	frappe.throw(f"Unsupported file type for IDP extraction: {mime_type}")


def _get_file_doc(file_url: str):
	"""Resolve a Frappe File by exact URL and enforce read permission.

	URL-encoding variants are accepted, but filename-only fallback is
	intentionally forbidden because two unrelated files can share a filename.
	"""
	from urllib.parse import quote, unquote, urlparse

	if file_url.startswith(("http://", "https://")):
		file_url = urlparse(file_url).path

	decoded_url = unquote(file_url)
	encoded_url = quote(decoded_url, safe="/")
	urls_to_try = tuple(dict.fromkeys((file_url, decoded_url, encoded_url)))

	for url in urls_to_try:
		file_name = frappe.db.get_value("File", {"file_url": url}, "name")
		if not file_name:
			continue

		file_doc = frappe.get_doc("File", file_name)
		if not file_doc.has_permission("read"):
			frappe.throw(
				"You do not have permission to access this file.",
				frappe.PermissionError,
			)
		return file_doc

	frappe.throw(
		f"File not found for URL: {file_url}",
		frappe.DoesNotExistError,
	)


def _extract_image(file_bytes: bytes, mime_type: str, file_name: str) -> dict:
	"""Return base64-encoded image for Vision API processing."""
	return {
		"content_type": "image",
		"base64": base64.b64encode(file_bytes).decode("utf-8"),
		"mime_type": mime_type,
		"file_name": file_name,
	}


def _extract_pdf(file_bytes: bytes, mime_type: str, file_name: str) -> dict:
	"""Extract text from PDF using pypdf."""
	from ai_chatbot.idp.extractors.pdf_extractor import extract_pdf_text

	text = extract_pdf_text(file_bytes)
	if not text.strip():
		# Scanned PDF with no selectable text — fall back to image extraction
		# Return base64 so the caller can use Vision API
		return {
			"content_type": "image",
			"base64": base64.b64encode(file_bytes).decode("utf-8"),
			"mime_type": mime_type,
			"file_name": file_name,
		}
	return {
		"content_type": "text",
		"text": text,
		"mime_type": mime_type,
		"file_name": file_name,
	}


def _extract_excel(file_bytes: bytes, mime_type: str, file_name: str) -> dict:
	"""Extract tabular data from XLSX."""
	from ai_chatbot.idp.extractors.excel_extractor import extract_excel_text

	text = extract_excel_text(file_bytes)
	return {
		"content_type": "text",
		"text": text,
		"mime_type": mime_type,
		"file_name": file_name,
	}


def _extract_csv(file_bytes: bytes, mime_type: str, file_name: str) -> dict:
	"""Extract tabular data from CSV."""
	from ai_chatbot.idp.extractors.excel_extractor import extract_csv_text

	text = extract_csv_text(file_bytes)
	return {
		"content_type": "text",
		"text": text,
		"mime_type": mime_type,
		"file_name": file_name,
	}


def _extract_docx(file_bytes: bytes, mime_type: str, file_name: str) -> dict:
	"""Extract text from DOCX."""
	from ai_chatbot.idp.extractors.docx_extractor import extract_docx_text

	text = extract_docx_text(file_bytes)
	return {
		"content_type": "text",
		"text": text,
		"mime_type": mime_type,
		"file_name": file_name,
	}


def _extract_text(file_bytes: bytes, mime_type: str, file_name: str) -> dict:
	"""Return plain text content."""
	text = file_bytes.decode("utf-8", errors="replace")
	return {
		"content_type": "text",
		"text": text,
		"mime_type": mime_type,
		"file_name": file_name,
	}
