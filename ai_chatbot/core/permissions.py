# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""Row-level permissions for chatbot-owned documents.

The Chatbot Conversation and Chatbot Message DocTypes intentionally grant the
"All" role enough rights for normal app usage. These hooks enforce tenant-like
row isolation so a logged-in user can only access their own conversations and
messages, including access through Desk and Frappe's generic REST resources.
"""

from __future__ import annotations

import frappe


def _is_system_manager(user: str | None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def conversation_query(user: str | None = None) -> str:
	"""Restrict conversation list queries to the owning user."""
	user = user or frappe.session.user
	if _is_system_manager(user):
		return ""
	return f"`tabChatbot Conversation`.`user` = {frappe.db.escape(user)}"


def conversation_has_permission(doc, user: str | None = None, permission_type: str | None = None) -> bool:
	"""Enforce ownership for document-level conversation access."""
	user = user or frappe.session.user
	if _is_system_manager(user):
		return True

	owner = getattr(doc, "user", None)
	if permission_type == "create":
		return not owner or owner == user
	return owner == user


def message_query(user: str | None = None) -> str:
	"""Restrict message list queries to conversations owned by the user."""
	user = user or frappe.session.user
	if _is_system_manager(user):
		return ""
	escaped_user = frappe.db.escape(user)
	return (
		"`tabChatbot Message`.`conversation` IN ("
		"SELECT `name` FROM `tabChatbot Conversation` "
		f"WHERE `user` = {escaped_user})"
	)


def message_has_permission(doc, user: str | None = None, permission_type: str | None = None) -> bool:
	"""Enforce parent-conversation ownership for message access."""
	user = user or frappe.session.user
	if _is_system_manager(user):
		return True

	conversation = getattr(doc, "conversation", None)
	if not conversation:
		return False

	owner = frappe.db.get_value("Chatbot Conversation", conversation, "user")
	return owner == user
