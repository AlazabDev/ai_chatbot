# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""Validation helpers for public chatbot API payloads."""

from __future__ import annotations

import json

import frappe

MAX_MESSAGE_CHARS = 20_000
MAX_TITLE_CHARS = 200
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_METADATA_CHARS = 32_000


def bounded_int(value, default: int, minimum: int = 1, maximum: int = 100) -> int:
	"""Convert an API value to an integer within a safe range."""
	try:
		value = int(value)
	except (TypeError, ValueError):
		value = default
	return max(minimum, min(value, maximum))


def clean_title(title: str | None) -> str:
	"""Normalize and bound a user-controlled conversation title."""
	title = str(title or "").strip()
	if not title:
		title = "New Chat"
	if len(title) > MAX_TITLE_CHARS:
		title = title[:MAX_TITLE_CHARS].rstrip()
	return title


def validate_message_payload(message: str | None, attachments=None) -> tuple[str, str | None]:
	"""Validate message text and return canonical attachment JSON.

	Only local Frappe file URLs are accepted. Client-only fields and arbitrary
	metadata are discarded before persistence.
	"""
	message = "" if message is None else str(message)
	if len(message) > MAX_MESSAGE_CHARS:
		frappe.throw(
			f"Message exceeds the maximum length of {MAX_MESSAGE_CHARS:,} characters.",
			frappe.ValidationError,
		)

	if not attachments:
		if not message.strip():
			frappe.throw("Message cannot be empty.", frappe.ValidationError)
		return message, None

	if isinstance(attachments, str):
		if len(attachments) > MAX_ATTACHMENT_METADATA_CHARS:
			frappe.throw("Attachment metadata is too large.", frappe.ValidationError)
		try:
			attachments = json.loads(attachments)
		except (json.JSONDecodeError, TypeError):
			frappe.throw("Invalid attachment metadata.", frappe.ValidationError)

	if not isinstance(attachments, list):
		frappe.throw("Attachments must be a list.", frappe.ValidationError)
	if len(attachments) > MAX_ATTACHMENTS:
		frappe.throw(
			f"A maximum of {MAX_ATTACHMENTS} attachments is allowed per message.",
			frappe.ValidationError,
		)

	canonical = []
	for index, att in enumerate(attachments, start=1):
		if not isinstance(att, dict):
			frappe.throw(f"Attachment {index} is invalid.", frappe.ValidationError)

		file_url = str(att.get("file_url") or "").strip()
		if not file_url.startswith(("/private/files/", "/files/")):
			frappe.throw(f"Attachment {index} has an invalid file URL.", frappe.ValidationError)

		canonical.append(
			{
				"file_url": file_url,
				"file_name": str(att.get("file_name") or file_url.rsplit("/", 1)[-1])[:255],
				"mime_type": str(att.get("mime_type") or "application/octet-stream")[:255],
				"size": int(att.get("size") or 0),
				"is_image": bool(att.get("is_image")),
			}
		)

	if not message.strip() and not canonical:
		frappe.throw("Message cannot be empty.", frappe.ValidationError)

	return message, json.dumps(canonical, separators=(",", ":"))
