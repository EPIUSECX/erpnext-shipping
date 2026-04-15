# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

from __future__ import annotations

import json
from typing import Any

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt
from frappe.utils.data import get_link_to_form
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.utils import show_error_alert

BOBGO_PROVIDER = "BobGo"
PROD_BASE_URL = "https://api.bobgo.co.za/v2"
TEST_BASE_URL = "https://api.sandbox.bobgo.co.za/v2"

SUCCESSFUL_SUBMISSION_STATUSES = {"success"}
RETRYABLE_SUBMISSION_STATUSES = {"pending-rates", "pending-submission", "failed-will-retry"}
FAILED_SUBMISSION_STATUSES = {"no-rates", "failed-indefinitely"}


class BobGo(Document):
	def validate(self):
		if self.enabled and not self.get_password("bearer_token"):
			frappe.throw(_("Bearer Token is required when Bob Go is enabled."))


class BobGoUtils:
	def __init__(self):
		settings = frappe.get_single("BobGo")
		self.enabled = settings.enabled
		self.base_url = TEST_BASE_URL if settings.use_test_environment else PROD_BASE_URL
		self.bearer_token = settings.get_password("bearer_token")
		self.default_timeout_ms = cint(settings.default_timeout_ms) or 10000

		if not self.enabled:
			link = get_link_to_form("BobGo", "BobGo", _("Bob Go Settings"))
			frappe.throw(_("Please enable Bob Go Integration in {0}").format(link))
		if not self.bearer_token:
			frappe.throw(_("Please set the Bearer Token in Bob Go Settings."), title=_("Bob Go"))

	def request(
		self,
		method: str,
		endpoint: str,
		json: dict | None = None,
		params: dict | None = None,
		expect_json: bool = True,
	):
		response = requests.request(
			method,
			f"{self.base_url}/{endpoint.lstrip('/')}",
			headers={
				"Authorization": f"Bearer {self.bearer_token}",
				"Accept": "application/json" if expect_json else "*/*",
				"Content-Type": "application/json",
			},
			json=json,
			params=params,
			timeout=60,
		)

		try:
			response.raise_for_status()
		except HTTPError as exc:
			self.raise_api_error(response, exc)

		if not expect_json:
			return response.content

		return response.json()

	def raise_api_error(self, response: requests.Response, _exc: HTTPError):
		try:
			error_payload = response.json()
		except ValueError:
			error_payload = None

		message = _("Bob Go API request failed with HTTP Status Code: {0}").format(response.status_code)
		if isinstance(error_payload, dict):
			message = (
				error_payload.get("message")
				or error_payload.get("detail")
				or error_payload.get("error")
				or message
			)

		frappe.throw(message, title=_("Bob Go"))

	def get_available_services(
		self,
		pickup_address,
		delivery_address,
		parcels: list[dict],
		pickup_contact,
		delivery_contact,
		value_of_goods,
	):
		if not self.enabled or not self.bearer_token:
			return []

		payload = {
			"collection_address": self.get_address_dict(pickup_address),
			"delivery_address": self.get_address_dict(delivery_address),
			"parcels": self.get_parcel_list(parcels),
			"collection_contact_mobile_number": pickup_contact.phone,
			"collection_contact_email": pickup_contact.email_id,
			"collection_contact_full_name": self.get_contact_full_name(pickup_contact),
			"delivery_contact_mobile_number": delivery_contact.phone,
			"delivery_contact_email": delivery_contact.email_id,
			"delivery_contact_full_name": self.get_contact_full_name(delivery_contact),
			"declared_value": flt(value_of_goods),
			"timeout": self.default_timeout_ms,
		}

		try:
			response_data = self.request("POST", "rates", json=payload)
			rates = self.extract_rates(response_data)
			return [self.get_service_dict(rate) for rate in rates]
		except Exception:
			show_error_alert("fetching Bob Go prices")
			return []

	def create_shipment(
		self,
		shipment: str,
		pickup_address,
		delivery_address,
		shipment_parcel,
		value_of_goods,
		pickup_contact,
		delivery_contact,
		service_info,
		pickup_date=None,
	):
		payload = {
			"collection_address": self.get_address_dict(pickup_address),
			"collection_contact_name": self.get_contact_full_name(pickup_contact),
			"collection_contact_mobile_number": pickup_contact.phone,
			"collection_contact_email": pickup_contact.email_id,
			"delivery_address": self.get_address_dict(delivery_address),
			"delivery_contact_name": self.get_contact_full_name(delivery_contact),
			"delivery_contact_mobile_number": delivery_contact.phone,
			"delivery_contact_email": delivery_contact.email_id,
			"parcels": self.get_parcel_list(json.loads(shipment_parcel)),
			"declared_value": flt(value_of_goods),
			"timeout": self.default_timeout_ms,
			"custom_tracking_reference": shipment,
			"custom_order_number": shipment,
			"service_level_code": service_info["service_level_code"],
			"provider_slug": service_info["provider_slug"],
		}

		if pickup_date:
			payload["collection_min_date"] = pickup_date

		try:
			response_data = self.request("POST", "shipments", json=payload)
		except Exception:
			show_error_alert("creating Bob Go Shipment")
			return None

		submission_status = response_data.get("submission_status")
		failed_reason = response_data.get("failed_reason")
		if submission_status in FAILED_SUBMISSION_STATUSES:
			frappe.throw(
				failed_reason
				or _("Bob Go shipment creation failed with status {0}.").format(submission_status),
				title=_("Bob Go"),
			)

		return {
			"service_provider": BOBGO_PROVIDER,
			"shipment_id": str(response_data.get("id") or ""),
			"carrier": response_data.get("provider_name")
			or service_info.get("carrier")
			or response_data.get("provider_slug")
			or service_info.get("provider_slug"),
			"carrier_service": service_info.get("service_name")
			or response_data.get("service_level_name")
			or response_data.get("service_level_code"),
			"shipment_amount": flt(response_data.get("rate") or service_info.get("total_price")),
			"awb_number": response_data.get("tracking_reference") or shipment,
			"tracking_url": response_data.get("tracking_url"),
			"submission_status": submission_status,
			"provider_slug": response_data.get("provider_slug") or service_info.get("provider_slug"),
			"service_level_code": response_data.get("service_level_code")
			or service_info.get("service_level_code"),
			"provider_shipment_id": response_data.get("provider_shipment_id"),
			"tracking_status": self.format_tracking_status(response_data.get("status")),
			"tracking_status_info": submission_status or response_data.get("status"),
		}

	def get_label(self, tracking_reference: str):
		tracking_references = [item.strip() for item in tracking_reference.split(",") if item.strip()]
		return self.request(
			"GET",
			"shipments/waybill",
			params={"tracking_references": json.dumps(tracking_references)},
			expect_json=False,
		)

	def get_tracking_data(self, tracking_reference: str):
		tracking_references = [item.strip() for item in tracking_reference.split(",") if item.strip()]
		if not tracking_references:
			return None

		tracking_statuses = []
		tracking_status_info = []
		awb_numbers = []

		for current_reference in tracking_references:
			try:
				response_data = self.request(
					"GET", "tracking", params={"tracking_reference": current_reference}
				)
			except Exception:
				show_error_alert("updating Bob Go Shipment")
				continue

			awb_numbers.append(
				response_data.get("shipment_tracking_reference")
				or response_data.get("tracking_reference")
				or current_reference
			)
			tracking_statuses.append(
				response_data.get("status_friendly")
				or self.format_tracking_status(response_data.get("status"))
				or ""
			)

			checkpoints = response_data.get("checkpoints") or []
			latest_checkpoint = checkpoints[0] if checkpoints else {}
			tracking_status_info.append(
				latest_checkpoint.get("message")
				or latest_checkpoint.get("status_friendly")
				or response_data.get("status_friendly")
				or response_data.get("status")
				or ""
			)

		if not awb_numbers:
			return None

		return {
			"awb_number": ", ".join(awb_numbers),
			"tracking_status": ", ".join(filter(None, tracking_statuses)),
			"tracking_status_info": ", ".join(filter(None, tracking_status_info)),
			"tracking_url": "",
		}

	def extract_rates(self, response_data: Any) -> list[dict]:
		if isinstance(response_data, list):
			return response_data

		if not isinstance(response_data, dict):
			return []

		for key in ("rates", "data", "results"):
			value = response_data.get(key)
			if isinstance(value, list):
				return value

		return []

	def get_service_dict(self, rate: dict):
		available_service = frappe._dict()
		available_service.service_provider = BOBGO_PROVIDER
		available_service.carrier = rate.get("provider_name") or rate.get("courier_name") or rate.get(
			"provider_slug"
		)
		available_service.carrier_name = available_service.carrier
		available_service.service_name = rate.get("service_name") or rate.get("service_level_code")
		available_service.service_id = rate.get("service_code") or rate.get("id")
		available_service.provider_slug = rate.get("provider_slug")
		available_service.service_level_code = rate.get("service_level_code")
		available_service.total_price = flt(rate.get("total_price") or rate.get("rate"))
		available_service.currency = rate.get("currency") or "ZAR"

		if rate.get("pickup_point_location_id"):
			available_service.pickup_point_location_id = rate.get("pickup_point_location_id")

		return available_service

	def get_address_dict(self, address):
		return {
			"company": address.address_title or "",
			"street_address": address.address_line1,
			"local_area": address.address_line2 or address.city,
			"city": address.city,
			"zone": getattr(address, "state", None) or address.city,
			"country": address.country_code,
			"code": address.pincode,
		}

	def get_parcel_list(self, parcels: list[dict]):
		parcel_list = []
		for parcel in parcels:
			parcel_count = cint(parcel.get("count")) or 1
			for _idx in range(parcel_count):
				parcel_list.append(
					{
						"description": parcel.get("description") or "Parcel",
						"submitted_length_cm": flt(parcel.get("length")),
						"submitted_width_cm": flt(parcel.get("width")),
						"submitted_height_cm": flt(parcel.get("height")),
						"submitted_weight_kg": flt(parcel.get("weight")),
						"custom_parcel_reference": parcel.get("name")
						or parcel.get("custom_parcel_reference"),
					}
				)
		return parcel_list

	def get_contact_full_name(self, contact):
		return " ".join(filter(None, [contact.first_name, contact.last_name]))

	def format_tracking_status(self, status: str | None) -> str:
		if not status:
			return ""
		return status.replace("-", " ").title()


def get_bobgo_utils() -> "BobGoUtils":
	return BobGoUtils()
