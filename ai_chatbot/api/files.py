# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""
File Upload API for AI Chatbot.

Handles file upload via Frappe's File DocType and returns
file metadata + base64 content for vision-capable AI providers.
"""

import base64
import io
import json
import mimetypes
import zipfile
from pathlib import PurePath

import frappe

from ai_chatbot.core.logger import log_error

# Allowed MIME types for upload
ALLOWED_MIME_TYPES = {
	# Images (for Vision API)
	"image/jpeg",
	"image/png",
	"image/gif",
	"image/webp",
	# Documents
	"application/pdf",
	"text/plain",
	"text/csv",
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
}

IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Max file size: 10MB
MAX_FILE_SIZE = 10 * 1024 * 1024


def _validate_file_signature(file_content: bytes, mime_type: str) -> None:
	"""Verify uploaded bytes match the claimed supported document type."""
	if not file_content:
		frappe.throw("Empty files are not allowed.", frappe.ValidationError)

	if mime_type == "application/pdf":
		valid = file_content.startswith(b"%PDF-")
	elif mime_type == "image/jpeg":
		valid = file_content.startswith(b"\xff\xd8\xff")
	elif mime_type == "image/png":
		valid = file_content.startswith(b"\x89PNG\r\n\x1a\n")
	elif mime_type == "image/gif":
		valid = file_content.startswith((b"GIF87a", b"GIF89a"))
	elif mime_type == "image/webp":
		valid = len(file_content) >= 12 and file_content[:4] == b"RIFF" and file_content[8:12] == b"WEBP"
	elif mime_type in {
		"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	}:
		valid = False
		try:
			with zipfile.ZipFile(io.BytesIO(file_content)) as archive:
				names = set(archive.namelist())
				if mime_type.endswith("spreadsheetml.sheet"):
					valid = "[Content_Types].xml" in names and "xl/workbook.xml" in names
				else:
					valid = "[Content_Types].xml" in names and "word/document.xml" in names
		except (zipfile.BadZipFile, OSError):
			valid = False
	elif mime_type in {"text/plain", "text/csv"}:
		try:
			text = file_content.decode("utf-8-sig")
			valid = "\x00" not in text
		except UnicodeDecodeError:
			valid = False
	else:
		valid = False

	if not valid:
		frappe.throw("The uploaded file content does not match its declared file type.", frappe.ValidationError)


@frappe.whitelist()
def upload_chat_file(conversation_id: str) -> dict:
	"""Upload a file for a chat message.

	Uses Frappe's built-in file handling. The file is stored as a private
	File document linked to the Chatbot Conversation.

	The file is received via the standard multipart form upload (frappe.request.files).

	Returns:
		dict with file_url, file_name, mime_type, size, and for images: base64 data.
	"""
	try:
		# Validate conversation ownership
		conversation = frappe.get_doc("Chatbot Conversation", conversation_id)
		if conversation.user != frappe.session.user:
			frappe.throw("Unauthorized access to conversation")

		# Get the uploaded file from request
		if not frappe.request or not frappe.request.files:
			frappe.throw("No file uploaded")

		uploaded_file = frappe.request.files.get("file")
		if not uploaded_file:
			frappe.throw("No file found in request")

		# Normalize the user-controlled filename and determine type from extension.
		file_name = PurePath(str(uploaded_file.filename or "upload")).name[:255]
		guessed_type = mimetypes.guess_type(file_name)[0]
		declared_type = uploaded_file.content_type
		mime_type = guessed_type or declared_type
		if mime_type not in ALLOWED_MIME_TYPES:
			frappe.throw(
				f"File type '{mime_type}' is not allowed. Allowed: images, PDF, text, CSV, XLSX, DOCX",
				frappe.ValidationError,
			)
		if declared_type and declared_type not in {"application/octet-stream", mime_type}:
			frappe.throw("The uploaded file extension and content type do not match.", frappe.ValidationError)

		# Read content and validate size/signature before creating a File record.
		file_content = uploaded_file.read(MAX_FILE_SIZE + 1)
		if len(file_content) > MAX_FILE_SIZE:
			frappe.throw(
				f"File size exceeds maximum of {MAX_FILE_SIZE // (1024 * 1024)}MB",
				frappe.ValidationError,
			)
		_validate_file_signature(file_content, mime_type)

		# Save using Frappe's File DocType (private)
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"content": file_content,
				"is_private": 1,
				"attached_to_doctype": "Chatbot Conversation",
				"attached_to_name": conversation_id,
			}
		)
		file_doc.save(ignore_permissions=True)
		frappe.db.commit()

		result = {
			"success": True,
			"file_url": file_doc.file_url,
			"file_name": file_name,
			"mime_type": mime_type,
			"size": len(file_content),
		}

		# For images, also return base64 for Vision API
		if mime_type in IMAGE_MIME_TYPES:
			result["base64"] = base64.b64encode(file_content).decode("utf-8")
			result["is_image"] = True
		else:
			result["is_image"] = False

		return result

	except (frappe.ValidationError, frappe.PermissionError) as e:
		return {"success": False, "error": str(e)}
	except Exception as e:
		log_error(f"File upload error: {e!s}", title="File Upload")
		return {"success": False, "error": "The file could not be uploaded."}


def get_file_base64(file_url: str) -> tuple[str, str]:
	"""Read a Frappe File and return (base64_data, mime_type).

	Used by the AI provider integration to build vision messages.
	"""
	from ai_chatbot.idp.extractors.base import _get_file_doc

	file_doc = _get_file_doc(file_url)
	file_path = file_doc.get_full_path()

	with open(file_path, "rb") as f:
		content = f.read()

	mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
	return base64.b64encode(content).decode("utf-8"), mime_type


def build_vision_content(message_text: str, attachments: list[dict] | str) -> list[dict]:
	"""Build multi-modal content array for Vision API.

	Converts a text message + attachments list into the OpenAI-format
	content array with text and image_url parts. For non-image files,
	appends a text description.

	Args:
		message_text: The user's text message.
		attachments: List of dicts (or JSON string) with file_url, file_name, mime_type, base64 (optional).

	Returns:
		list of content parts in OpenAI format:
		[{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "data:..."}}]
	"""
	if isinstance(attachments, str):
		try:
			attachments = json.loads(attachments)
		except (json.JSONDecodeError, TypeError):
			return [{"type": "text", "text": message_text}]

	if not attachments:
		return [{"type": "text", "text": message_text}]

	parts = [{"type": "text", "text": message_text}]

	for att in attachments:
		mime_type = att.get("mime_type", "application/octet-stream")

		if mime_type in IMAGE_MIME_TYPES:
			# Image: embed as base64 data URL
			b64_data = att.get("base64")
			if not b64_data:
				try:
					b64_data, mime_type = get_file_base64(att["file_url"])
				except Exception:
					# If we can't read the file, skip it
					parts.append(
						{
							"type": "text",
							"text": f"[Attached image: {att.get('file_name', 'unknown')} — could not load]",
						}
					)
					continue

			parts.append(
				{
					"type": "image_url",
					"image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
				}
			)
			# Include file_id so the LLM can pass it to IDP tools
			file_id = att.get("_file_id", "")
			file_name = att.get("file_name", "unknown")
			if file_id:
				parts.append(
					{
						"type": "text",
						"text": f"[Attached image: {file_name}, file_id: {file_id}]",
					}
				)
		else:
			# Non-image: add file reference with file_id for IDP tools
			file_id = att.get("_file_id", "")
			file_name = att.get("file_name", "unknown")
			parts.append(
				{
					"type": "text",
					"text": (f"[Attached file: {file_name} ({mime_type}), file_id: {file_id}]"),
				}
			)

	return parts
