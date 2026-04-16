# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_url
from urllib.parse import quote
from frappe.utils.password import get_decrypted_password

from .client import BobGoUtils, get_bobgo_utils
from .constants import BOBGO_PROVIDER
from .webhooks import handle_submission_status_webhook, handle_tracking_webhook


class BobGo(Document):
	def validate(self):
		if self.enabled and not self.get_password("bearer_token"):
			frappe.throw(_("Bearer Token is required when Bob Go is enabled."))
		if self.enabled and self.get("webhook_secret") and not self.get_password("webhook_secret"):
			frappe.throw(_("Webhook Secret could not be read. Please re-enter it."), title=_("Bob Go"))


@frappe.whitelist()
def copy_tracking_update_url():
	frappe.only_for("System Manager")
	return get_bobgo_webhook_url("tracking")


@frappe.whitelist()
def copy_shipment_submission_status_update_url():
	frappe.only_for("System Manager")
	return get_bobgo_webhook_url("submission")


def get_bobgo_webhook_url(webhook_type: str) -> str:
	webhook_secret = get_decrypted_password("BobGo", "BobGo", "webhook_secret")
	if not webhook_secret:
		frappe.throw(_("Please set the Bob Go Webhook Secret first."), title=_("Bob Go"))

	base_url = get_url().rstrip("/")
	secret = quote(webhook_secret, safe="")

	if webhook_type == "tracking":
		return (
			f"{base_url}/api/method/"
			"erpnext_shipping.erpnext_shipping.doctype.bobgo.bobgo.handle_tracking_webhook"
			f"?secret={secret}"
		)

	if webhook_type == "submission":
		return (
			f"{base_url}/api/method/"
			"erpnext_shipping.erpnext_shipping.doctype.bobgo.bobgo.handle_submission_status_webhook"
			f"?secret={secret}"
		)

	frappe.throw(_("Unknown Bob Go webhook type: {0}").format(webhook_type), title=_("Bob Go"))
