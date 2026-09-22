"""Production smoke assertions executed by CI after install + migrate."""

from __future__ import annotations

import frappe


def _ensure_user(email: str, first_name: str) -> None:
	if frappe.db.exists("User", email):
		return
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"enabled": 1,
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)


def _make_conversation(user: str, title: str):
	return frappe.get_doc(
		{
			"doctype": "Chatbot Conversation",
			"title": title,
			"user": user,
			"ai_provider": "OpenAI",
			"status": "Active",
			"created_at": frappe.utils.now(),
			"updated_at": frappe.utils.now(),
		}
	).insert(ignore_permissions=True)


def run() -> dict:
	"""Verify install contracts and row-level isolation on a real Frappe site."""
	assert "ai_chatbot" in frappe.get_installed_apps(), "ai_chatbot is not installed"
	assert "erpnext" in frappe.get_installed_apps(), "ERPNext dependency is missing"

	agents = frappe.get_all(
		"Foundry Agent",
		fields=["name", "agent_key", "enabled", "foundry_assistant_id"],
		order_by="name asc",
	)
	assert len(agents) == 8, f"expected 8 Foundry Agent seeds, found {len(agents)}"
	for agent in agents:
		assert agent.name == agent.agent_key, f"invalid Foundry Agent name: {agent}"
		if not (agent.foundry_assistant_id or "").strip():
			assert not agent.enabled, f"unconfigured Foundry Agent is enabled: {agent.name}"

	assistant_field = frappe.get_meta("Foundry Agent").get_field("foundry_assistant_id")
	assert assistant_field and not assistant_field.reqd, "Foundry Assistant ID must be environment-configurable"

	user_a = "ci-chat-user-a@example.com"
	user_b = "ci-chat-user-b@example.com"
	original_user = frappe.session.user

	try:
		frappe.set_user("Administrator")
		_ensure_user(user_a, "CI User A")
		_ensure_user(user_b, "CI User B")

		conv_a = _make_conversation(user_a, "CI private conversation A")
		conv_b = _make_conversation(user_b, "CI private conversation B")

		frappe.get_doc(
			{
				"doctype": "Chatbot Message",
				"conversation": conv_a.name,
				"role": "user",
				"content": "private A",
				"timestamp": frappe.utils.now(),
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Chatbot Message",
				"conversation": conv_b.name,
				"role": "user",
				"content": "private B",
				"timestamp": frappe.utils.now(),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.set_user(user_a)
		visible_conversations = frappe.get_list("Chatbot Conversation", pluck="name")
		assert conv_a.name in visible_conversations, "owner cannot see own conversation"
		assert conv_b.name not in visible_conversations, "conversation row isolation failed"

		visible_messages = frappe.get_list("Chatbot Message", pluck="conversation")
		assert conv_a.name in visible_messages, "owner cannot see own message"
		assert conv_b.name not in visible_messages, "message row isolation failed"

		from ai_chatbot.api.chat import get_conversation_messages

		foreign = get_conversation_messages(conv_b.name)
		assert not foreign.get("success"), "conversation API IDOR protection failed"
	finally:
		frappe.set_user(original_user)

	print("AI Chatbot production smoke PASS")
	return {
		"status": "pass",
		"foundry_agents": len(agents),
		"row_isolation": True,
	}


if __name__ == "__main__":
	run()
