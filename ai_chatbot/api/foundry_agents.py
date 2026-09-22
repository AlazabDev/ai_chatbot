# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
"""Foundry Agent picker API — used by the chat frontend to let the user
choose which az-agent-* deployment a conversation talks to, instead of
hard-coding a single agent in Chatbot Settings.
"""

import frappe

from ai_chatbot.core.logger import log_error


@frappe.whitelist()
def get_foundry_agents() -> dict:
	"""List enabled Foundry Agents for the chat UI's agent picker.

	Deliberately omits foundry_assistant_id — the frontend only ever needs
	the display key/description/icon; the actual Foundry assistant id stays
	server-side and is resolved from Chatbot Conversation.foundry_agent when
	AzureAIFoundryAgentProvider runs.
	"""
	try:
		agents = frappe.get_all(
			"Foundry Agent",
			filters={"enabled": 1},
			fields=["name", "description", "icon"],
			order_by="creation",
		)
		return {"success": True, "agents": agents}
	except Exception as e:
		log_error(f"Error getting foundry agents: {e!s}", title="Foundry Agents API")
		return {"success": False, "error": str(e)}
