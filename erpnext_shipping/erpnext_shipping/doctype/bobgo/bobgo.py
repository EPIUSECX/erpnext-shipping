# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_url

from .client import (
	BobGoUtils,
	get_bobgo_utils,
	get_expected_webhook_subscriptions,
	normalize_webhook_subscriptions,
)
from .constants import (
	BOBGO_PROVIDER,
	SUBMISSION_STATUS_WEBHOOK_TOPIC,
	TRACKING_WEBHOOK_TOPIC,
)
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


@frappe.whitelist()
def subscribe_webhooks():
	frappe.only_for("System Manager")

	tracking_url, submission_url, bobgo, bobgo_utils = get_webhook_sync_context()

	expected_subscriptions = get_expected_webhook_subscriptions(tracking_url, submission_url)
	existing_subscriptions = get_existing_webhook_subscriptions(bobgo_utils)
	missing_subscriptions = [
		subscription
		for subscription in expected_subscriptions
		if not has_matching_active_subscription(existing_subscriptions, subscription)
	]

	if missing_subscriptions:
		# Subscribe only the missing/inactive webhook topics so the button can be
		# re-run safely without creating noisy duplicates every time.
		bobgo_utils.request("POST", "webhooks", json={"webhook_subscriptions": missing_subscriptions})
		existing_subscriptions = get_existing_webhook_subscriptions(bobgo_utils)

	return save_webhook_subscription_statuses(bobgo, existing_subscriptions, tracking_url, submission_url)


@frappe.whitelist()
def refresh_webhook_status():
	frappe.only_for("System Manager")

	tracking_url, submission_url, bobgo, bobgo_utils = get_webhook_sync_context()
	existing_subscriptions = get_existing_webhook_subscriptions(bobgo_utils)
	return save_webhook_subscription_statuses(bobgo, existing_subscriptions, tracking_url, submission_url)


def get_bobgo_webhook_url(webhook_type: str) -> str:
	settings = frappe.get_single("BobGo")
	if not settings.get_password("webhook_secret"):
		frappe.throw(_("Please set the Bob Go Webhook Secret first."), title=_("Bob Go"))

	base_url = get_url().rstrip("/")

	if webhook_type == "tracking":
		return (
			f"{base_url}/api/method/"
			"erpnext_shipping.erpnext_shipping.doctype.bobgo.bobgo.handle_tracking_webhook"
		)

	if webhook_type == "submission":
		return (
			f"{base_url}/api/method/"
			"erpnext_shipping.erpnext_shipping.doctype.bobgo.bobgo.handle_submission_status_webhook"
		)

	frappe.throw(_("Unknown Bob Go webhook type: {0}").format(webhook_type), title=_("Bob Go"))


def get_webhook_subscription_statuses(
	subscriptions: list[dict], tracking_url: str, submission_url: str
) -> dict[str, int]:
	return {
		"tracking_webhook_subscribed": int(
			has_matching_active_subscription(
				subscriptions,
				{"topic": TRACKING_WEBHOOK_TOPIC, "delivery_url": tracking_url, "status": "active"},
			)
		),
		"shipment_submission_status_webhook_subscribed": int(
			has_matching_active_subscription(
				subscriptions,
				{
					"topic": SUBMISSION_STATUS_WEBHOOK_TOPIC,
					"delivery_url": submission_url,
					"status": "active",
				},
			)
		),
	}


def has_matching_active_subscription(subscriptions: list[dict], expected_subscription: dict) -> bool:
	expected_url = (expected_subscription.get("delivery_url") or "").strip()
	expected_topic = (expected_subscription.get("topic") or "").strip()
	expected_status = (expected_subscription.get("status") or "active").strip().lower()

	for subscription in subscriptions:
		if (subscription.get("topic") or "").strip() != expected_topic:
			continue
		if (subscription.get("delivery_url") or "").strip() != expected_url:
			continue
		if (subscription.get("status") or "").strip().lower() != expected_status:
			continue
		return True

	return False


def get_webhook_sync_context():
	tracking_url = get_bobgo_webhook_url("tracking")
	submission_url = get_bobgo_webhook_url("submission")
	return tracking_url, submission_url, frappe.get_single("BobGo"), get_bobgo_utils()


def get_existing_webhook_subscriptions(bobgo_utils: BobGoUtils) -> list[dict]:
	return normalize_webhook_subscriptions(bobgo_utils.request("GET", "webhooks"))


def save_webhook_subscription_statuses(
	bobgo: Document, subscriptions: list[dict], tracking_url: str, submission_url: str
):
	statuses = get_webhook_subscription_statuses(subscriptions, tracking_url, submission_url)
	bobgo.db_set(statuses)
	return statuses
