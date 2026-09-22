# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class FoundryAgent(Document):
	"""One az-agent-* deployment on Azure AI Foundry, selectable per conversation
	when Chatbot Settings.ai_provider == 'Azure AI Foundry Agent'.

	foundry_assistant_id is the only sensitive-ish bit here (an internal Foundry
	id, not a secret) — api/foundry_agents.get_foundry_agents() deliberately
	omits it from what's sent to the chat frontend.
	"""

	def validate(self):
		if self.foundry_assistant_id:
			self.foundry_assistant_id = self.foundry_assistant_id.strip()
