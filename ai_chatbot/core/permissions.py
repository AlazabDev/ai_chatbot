# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""Row-level permissions for chatbot-owned documents."""

from __future__ import annotations

import frappe


def _is_system_manager(user: str | None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def conversation_query(user: str | None = None) -> str:
	"""Restrict conversation list queries to their owning user."""
	user = user or frappe.session.user
	if _is_system_manager(user):
		return ""
	return f"`tabChatbot Conversation`.`user` = {frappe.db.escape(user)}"


def conversation_has_permission(
	doc,
	user: str | None = None,
	permission_type: str | None = None,
) -> bool:
	"""Allow users to operate only on conversations they own."""
	user = user or frappe.session.user
	if _is_system_manager(user):
		return True

	owner = getattr(doc, "user", None)
	if permission_type == "create":
		return not owner or owner == user
	return owner == user


def message_query(user: str | None = None) -> str:
	"""Restrict message list queries through the parent conversation owner."""
	user = user or frappe.session.user
	if _is_system_manager(user):
		return ""

	escaped_user = frappe.db.escape(user)
	return (
		"`tabChatbot Message`.`conversation` IN ("
		"SELECT `name` FROM `tabChatbot Conversation` "
		f"WHERE `user` = {escaped_user})"
	)


def message_has_permission(
	doc,
	user: str | None = None,
	permission_type: str | None = None,
) -> bool:
	"""Resolve message access through its parent conversation."""
	user = user or frappe.session.user
	if _is_system_manager(user):
		return True

	conversation = getattr(doc, "conversation", None)
	if not conversation:
		return False

	owner = frappe.db.get_value("Chatbot Conversation", conversation, "user")
	return owner == user
