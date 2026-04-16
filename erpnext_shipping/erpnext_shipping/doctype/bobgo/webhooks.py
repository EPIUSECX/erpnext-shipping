# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

from __future__ import annotations

import hmac
import json

import frappe
from frappe import _
from frappe.utils import flt

from .constants import BOBGO_PROVIDER
from .helpers import (
	format_tracking_status,
	get_bobgo_tracking_status_info,
	log_bobgo_webhook,
	normalize_erpnext_tracking_status,
)

MAX_WEBHOOK_BODY_BYTES = 128 * 1024


@frappe.whitelist(allow_guest=True)
def handle_tracking_webhook():
	# Bob Go calls this endpoint with the full tracking timeline whenever shipment
	# movement changes. We verify a shared secret before mutating any shipment data.
	verify_bobgo_webhook_request()
	payload = get_bobgo_webhook_payload()
	validate_tracking_webhook_payload(payload)
	tracking_reference = payload.get("shipment_tracking_reference") or payload.get("id")
	if not tracking_reference:
		frappe.throw(_("Missing shipment tracking reference in Bob Go webhook payload."))

	shipment_name = find_bobgo_shipment(tracking_reference=tracking_reference)
	if not shipment_name:
		log_bobgo_webhook("Bob Go Tracking Webhook", payload)
		return {"ok": True, "matched": False}

	shipment_doc = frappe.get_doc("Shipment", shipment_name)
	tracking_status = normalize_erpnext_tracking_status(
		[payload.get("status_friendly") or format_tracking_status(payload.get("status"))]
	)
	tracking_status_info = get_bobgo_tracking_status_info(payload)

	shipment_doc.db_set(
		{
			"awb_number": tracking_reference,
			"tracking_status": tracking_status,
			"tracking_status_info": tracking_status_info,
		}
	)

	update_related_delivery_notes(
		shipment_doc,
		{
			"awb_number": tracking_reference,
			"tracking_status": tracking_status,
			"tracking_status_info": tracking_status_info,
			"tracking_url": shipment_doc.tracking_url,
		},
	)
	return {"ok": True, "matched": True}


@frappe.whitelist(allow_guest=True)
def handle_submission_status_webhook():
	# Bob Go emits this whenever the booking submission lifecycle changes. This lets
	# us reflect booking success/failure without waiting for the scheduler.
	verify_bobgo_webhook_request()
	payload = get_bobgo_webhook_payload()
	validate_submission_status_webhook_payload(payload)
	tracking_reference = payload.get("tracking_reference")
	shipment_identifier = str(payload.get("id")) if payload.get("id") else None
	shipment_name = find_bobgo_shipment(
		tracking_reference=tracking_reference, shipment_id=shipment_identifier
	)
	if not shipment_name:
		log_bobgo_webhook("Bob Go Submission Webhook", payload)
		return {"ok": True, "matched": False}

	shipment_doc = frappe.get_doc("Shipment", shipment_name)
	submission_status = payload.get("submission_status")
	tracking_status = normalize_erpnext_tracking_status(
		[payload.get("status") or payload.get("status_friendly")]
	)
	tracking_status_info = payload.get("failed_reason") or payload.get("status") or submission_status or ""

	update_values = {
		"shipment_id": shipment_identifier or shipment_doc.shipment_id,
		"awb_number": tracking_reference or shipment_doc.awb_number,
		"shipment_amount": flt(payload.get("rate") or shipment_doc.shipment_amount),
		"tracking_status": tracking_status,
		"tracking_status_info": tracking_status_info,
	}
	if submission_status == "success":
		update_values["status"] = "Booked"

	shipment_doc.db_set(update_values)
	update_related_delivery_notes(
		shipment_doc,
		{
			"awb_number": update_values["awb_number"],
			"tracking_status": tracking_status,
			"tracking_status_info": tracking_status_info,
			"tracking_url": shipment_doc.tracking_url,
		},
	)
	return {"ok": True, "matched": True}


def verify_bobgo_webhook_request():
	if frappe.request.method != "POST":
		frappe.throw(_("Only POST is allowed for Bob Go webhooks."))

	content_type = (frappe.get_request_header("Content-Type") or "").lower()
	if "application/json" not in content_type:
		frappe.throw(_("Bob Go webhook requests must use application/json."), title=_("Bob Go"))

	settings = frappe.get_single("BobGo")
	expected_secret = settings.get_password("webhook_secret")
	if not expected_secret:
		frappe.throw(_("Bob Go webhook secret is not configured."), title=_("Bob Go"))

	provided_secret = (
		frappe.get_request_header("X-BobGo-Webhook-Secret")
		or frappe.get_request_header("X-Bobgo-Webhook-Secret")
	)
	if not provided_secret or not hmac.compare_digest(str(provided_secret), str(expected_secret)):
		frappe.throw(
			_("Invalid Bob Go webhook secret. Please send it in the X-BobGo-Webhook-Secret header."),
			title=_("Bob Go"),
		)


def get_bobgo_webhook_payload() -> dict:
	raw_body = frappe.request.get_data() or b""
	if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
		frappe.throw(_("Bob Go webhook payload is too large."), title=_("Bob Go"))

	body = raw_body.decode("utf-8") if raw_body else "{}"
	try:
		payload = json.loads(body)
	except ValueError:
		frappe.throw(_("Invalid JSON body received from Bob Go."), title=_("Bob Go"))

	if not isinstance(payload, dict):
		frappe.throw(_("Unexpected Bob Go webhook payload format."), title=_("Bob Go"))

	return payload


def validate_tracking_webhook_payload(payload: dict):
	required_fields = ("shipment_tracking_reference", "status")
	missing_fields = [field for field in required_fields if not payload.get(field)]
	if missing_fields:
		frappe.throw(
			_("Missing required Bob Go tracking webhook fields: {0}").format(", ".join(missing_fields)),
			title=_("Bob Go"),
		)

	checkpoints = payload.get("checkpoints")
	if checkpoints is not None and not isinstance(checkpoints, list):
		frappe.throw(_("Bob Go tracking webhook checkpoints must be a list."), title=_("Bob Go"))


def validate_submission_status_webhook_payload(payload: dict):
	if not payload.get("id"):
		frappe.throw(_("Missing shipment id in Bob Go submission webhook payload."), title=_("Bob Go"))
	if not payload.get("submission_status"):
		frappe.throw(_("Missing submission_status in Bob Go submission webhook payload."), title=_("Bob Go"))


def find_bobgo_shipment(tracking_reference: str | None = None, shipment_id: str | None = None) -> str | None:
	filters = {"service_provider": BOBGO_PROVIDER}
	shipment_name_by_tracking = None
	shipment_name_by_id = None

	if tracking_reference:
		shipment_names = frappe.get_all(
			"Shipment",
			filters={**filters, "awb_number": tracking_reference},
			pluck="name",
			limit=2,
		)
		if len(shipment_names) > 1:
			frappe.throw(
				_("Multiple Bob Go shipments matched the same tracking reference {0}.").format(
					tracking_reference
				),
				title=_("Bob Go"),
			)
		if shipment_names:
			shipment_name_by_tracking = shipment_names[0]

	if shipment_id:
		shipment_names = frappe.get_all(
			"Shipment",
			filters={**filters, "shipment_id": shipment_id},
			pluck="name",
			limit=2,
		)
		if len(shipment_names) > 1:
			frappe.throw(
				_("Multiple Bob Go shipments matched the same shipment id {0}.").format(shipment_id),
				title=_("Bob Go"),
			)
		if shipment_names:
			shipment_name_by_id = shipment_names[0]

	if shipment_name_by_tracking and shipment_name_by_id:
		if shipment_name_by_tracking != shipment_name_by_id:
			frappe.throw(
				_("Bob Go webhook identifiers do not match the same Shipment."), title=_("Bob Go")
			)
		return shipment_name_by_tracking

	return shipment_name_by_tracking or shipment_name_by_id


def update_related_delivery_notes(shipment_doc, tracking_info: dict):
	from erpnext_shipping.erpnext_shipping.shipping import update_delivery_note

	delivery_notes = [row.delivery_note for row in shipment_doc.shipment_delivery_note]
	if delivery_notes:
		update_delivery_note(delivery_notes=delivery_notes, tracking_info=tracking_info)
