# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""Validation helpers for public chatbot API payloads."""

from __future__ import annotations

import json
import mimetypes

from ai_chatbot.core.exceptions import RequestValidationError

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


def validate_message_payload(message: str | None, attachments=None, conversation_id: str | None = None) -> tuple[str, str | None]:
	"""Validate message text and return canonical attachment JSON.

	Only local Frappe file URLs are accepted. Client-only fields and arbitrary
	metadata are discarded before persistence.
	"""
	message = "" if message is None else str(message)
	if len(message) > MAX_MESSAGE_CHARS:
		raise RequestValidationError(
			f"Message exceeds the maximum length of {MAX_MESSAGE_CHARS:,} characters."
		)

	if not attachments:
		if not message.strip():
			raise RequestValidationError("Message cannot be empty.")
		return message, None

	if isinstance(attachments, str):
		if len(attachments) > MAX_ATTACHMENT_METADATA_CHARS:
			raise RequestValidationError("Attachment metadata is too large.")
		try:
			attachments = json.loads(attachments)
		except (json.JSONDecodeError, TypeError):
			raise RequestValidationError("Invalid attachment metadata.")

	if not isinstance(attachments, list):
		raise RequestValidationError("Attachments must be a list.")
	if len(attachments) > MAX_ATTACHMENTS:
		raise RequestValidationError(
			f"A maximum of {MAX_ATTACHMENTS} attachments is allowed per message."
		)

	canonical = []
	for index, att in enumerate(attachments, start=1):
		if not isinstance(att, dict):
			raise RequestValidationError(f"Attachment {index} is invalid.")

		file_url = str(att.get("file_url") or "").strip()
		if not file_url.startswith(("/private/files/", "/files/")):
			raise RequestValidationError(f"Attachment {index} has an invalid file URL.")

		# Resolve the File now so inaccessible/private files are rejected before
		# their metadata is persisted into a conversation.
		from ai_chatbot.idp.extractors.base import _get_file_doc

		file_doc = _get_file_doc(file_url)
		if conversation_id and (
			file_doc.attached_to_doctype != "Chatbot Conversation"
			or file_doc.attached_to_name != conversation_id
		):
			raise RequestValidationError(
				f"Attachment {index} does not belong to this conversation."
			)

		try:
			size = max(0, int(file_doc.file_size or 0))
		except (TypeError, ValueError):
			size = 0

		mime_type = mimetypes.guess_type(str(file_doc.file_name or file_doc.file_url))[0] or "application/octet-stream"
		is_image = mime_type in {"image/jpeg", "image/png", "image/gif", "image/webp"}

		canonical.append(
			{
				"file_url": file_doc.file_url,
				"file_name": str(file_doc.file_name or file_url.rsplit("/", 1)[-1])[:255],
				"mime_type": mime_type,
				"size": size,
				"is_image": is_image,
			}
		)

	if not message.strip() and not canonical:
		raise RequestValidationError("Message cannot be empty.")

	return message, json.dumps(canonical, separators=(",", ":"))
