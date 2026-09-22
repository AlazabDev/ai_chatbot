# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
import frappe

no_cache = 1


def get_context(context):
	"""Set context for AI Chatbot page."""
	if frappe.session.user == "Guest":
		frappe.throw("Please login to access AI Chatbot", frappe.PermissionError)

	csrf_token = frappe.sessions.get_csrf_token()
	frappe.db.commit()
	context.csrf_token = csrf_token
	context.site_name = frappe.local.site
	context.boot = get_boot()
	context.dev_server = 1 if frappe.conf.developer_mode else 0
	return context


def get_boot():
	"""Build boot data for the AI Chatbot frontend."""
	desk_theme = (
		frappe.db.get_value("User", frappe.session.user, "desk_theme") or "Light"
	)

	return frappe._dict(
		{
			"site_name": frappe.local.site,
			"csrf_token": frappe.sessions.get_csrf_token(),
			"desk_theme": desk_theme.lower(),
			"socketio_port": frappe.conf.socketio_port,
		}
	)
