# Copyright (c) 2026, Sanjay Kumar and contributors
# For license information, please see license.txt
app_name = "ai_chatbot"
app_title = "AI Chatbot"
app_publisher = "Sanjay Kumar"
app_description = "Intelligent AI Chatbot with ERPNext Integration"
app_email = "sanjay.kumar001@gmail.com"
app_license = "mit"

# Tool Plugins — external apps can register chatbot tools by extending this list.
# Example (in another_app/hooks.py):
#   ai_chatbot_tool_modules = ["another_app.chatbot_tools.manufacturing"]
ai_chatbot_tool_modules = []

# Seeds the known az-agent-* records on install/migrate (foundry_assistant_id
# ships blank — fill it in per environment via the Foundry Agent list).
fixtures = ["Foundry Agent"]

# Apps
# ------------------

required_apps = ["erpnext"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "ai_chatbot",
# 		"logo": "/assets/ai_chatbot/logo.png",
# 		"title": "AI Chatbot",
# 		"route": "/ai_chatbot",
# 		"has_permission": "ai_chatbot.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/ai_chatbot/css/ai_chatbot.css"
# app_include_js = "/assets/ai_chatbot/js/ai_chatbot.js"

# include js, css files in header of web template
# web_include_css = "/assets/ai_chatbot/css/ai_chatbot.css"
# web_include_js = "/assets/ai_chatbot/js/ai_chatbot.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "ai_chatbot/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "ai_chatbot/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "ai_chatbot.utils.jinja_methods",
# 	"filters": "ai_chatbot.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "ai_chatbot.install.before_install"
# after_install = "ai_chatbot.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "ai_chatbot.uninstall.before_uninstall"
# after_uninstall = "ai_chatbot.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "ai_chatbot.utils.before_app_install"
# after_app_install = "ai_chatbot.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "ai_chatbot.utils.before_app_uninstall"
# after_app_uninstall = "ai_chatbot.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "ai_chatbot.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"Chatbot Conversation": "ai_chatbot.core.permissions.conversation_query",
	"Chatbot Message": "ai_chatbot.core.permissions.message_query",
}

has_permission = {
	"Chatbot Conversation": "ai_chatbot.core.permissions.conversation_has_permission",
	"Chatbot Message": "ai_chatbot.core.permissions.message_has_permission",
}

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		# Run every 15 minutes — checks which reports are actually due
		# based on their individual schedule configuration.
		"*/15 * * * *": [
			"ai_chatbot.automation.scheduled_reports.run_scheduled_reports",
		],
	},
	"daily": [
		# Phase 13F: Purge audit log entries older than 90 days
		"ai_chatbot.core.audit.cleanup_old_audit_logs",
	],
}

# Testing
# -------

# before_tests = "ai_chatbot.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "ai_chatbot.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "ai_chatbot.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "ai_chatbot.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["ai_chatbot.utils.before_request"]
# after_request = ["ai_chatbot.utils.after_request"]

# Job Events
# ----------
# before_job = ["ai_chatbot.utils.before_job"]
# after_job = ["ai_chatbot.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"ai_chatbot.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
