# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

from __future__ import annotations

import json
from base64 import b64decode
from datetime import datetime, time, timedelta, timezone
from typing import Any

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime
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
		return_response: bool = False,
	):
		# Keep all Bob Go HTTP concerns in one place so the carrier methods stay focused
		# on payload mapping and response normalization.
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

		if return_response:
			return response

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

		# Bob Go expects shipment-style parcel/contact data for rate lookups, so we map
		# the ERPNext Shipment inputs into their payload here.
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
			if not rates:
				frappe.log_error(
					title="Bob Go Rates Debug",
					message=json.dumps(
						{
							"payload": payload,
							"response_data": response_data,
							"extracted_rates": rates,
						},
						indent=2,
						default=str,
					),
				)
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
		# Bob Go requires the selected provider/service level from a prior rates call.
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
			payload["collection_min_date"] = self.get_collection_min_date(pickup_date)

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
		# Waybills are fetched by tracking reference, not Bob Go shipment ID.
		tracking_references = [item.strip() for item in tracking_reference.split(",") if item.strip()]
		response = self.request(
			"GET",
			"shipments/waybill",
			params={"tracking_references": json.dumps(tracking_references)},
			expect_json=False,
			return_response=True,
		)
		content = self.extract_label_content(response, tracking_references)
		if not content.startswith(b"%PDF"):
			frappe.log_error(
				title="Bob Go Label Debug",
				message=json.dumps(
					{
						"tracking_references": tracking_references,
						"status_code": response.status_code,
						"headers": dict(response.headers),
						"content_preview": response.text[:500],
					},
					indent=2,
					default=str,
				),
			)
			frappe.throw(_("Bob Go did not return a valid PDF label."), title=_("Bob Go"))

		return content

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

			response_data = self.normalize_tracking_response(response_data, current_reference)

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
			# Bob Go returns the latest event first in webhook examples, and the polling
			# endpoint mirrors that structure, so we use the first checkpoint as the most
			# helpful status detail when one is present.
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

		provider_rate_requests = response_data.get("provider_rate_requests") or []
		rates = []
		for provider_rate_request in provider_rate_requests:
			if provider_rate_request.get("status") != "success":
				continue

			for response in provider_rate_request.get("responses") or []:
				if response.get("status") and response.get("status") != "success":
					continue

				rate = frappe._dict(response)
				rate.provider_slug = provider_rate_request.get("provider_slug")
				rate.provider_name = provider_rate_request.get("provider_name")
				rates.append(rate)

		if rates:
			return rates

		return []

	def get_service_dict(self, rate: dict):
		# Normalize Bob Go's rate response into the same shape the existing service
		# selector dialog already understands.
		available_service = frappe._dict()
		available_service.service_provider = BOBGO_PROVIDER
		available_service.carrier = rate.get("provider_name") or rate.get("courier_name") or rate.get(
			"provider_slug"
		)
		available_service.carrier_name = available_service.carrier
		service_level = rate.get("service_level") or {}
		available_service.service_name = (
			rate.get("service_name") or service_level.get("name") or rate.get("service_level_code")
		)
		available_service.service_id = rate.get("service_code") or rate.get("id")
		available_service.provider_slug = rate.get("provider_slug")
		available_service.service_level_code = rate.get("service_level_code")
		available_service.total_price = flt(
			rate.get("total_price") or rate.get("rate") or rate.get("rate_amount")
		)
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

	def normalize_tracking_response(self, response_data: Any, tracking_reference: str) -> dict:
		if isinstance(response_data, dict):
			return response_data

		if isinstance(response_data, list):
			checkpoints = [item for item in response_data if isinstance(item, dict)]
			latest_checkpoint = checkpoints[0] if checkpoints else {}
			return {
				"shipment_tracking_reference": tracking_reference,
				"tracking_reference": tracking_reference,
				"status": latest_checkpoint.get("status"),
				"status_friendly": latest_checkpoint.get("status_friendly"),
				"checkpoints": checkpoints,
			}

		frappe.log_error(
			title="Bob Go Tracking Debug",
			message=json.dumps(
				{
					"tracking_reference": tracking_reference,
					"response_data": response_data,
				},
				indent=2,
				default=str,
			),
		)
		return {
			"shipment_tracking_reference": tracking_reference,
			"tracking_reference": tracking_reference,
			"status": "",
			"status_friendly": "",
			"checkpoints": [],
		}

	def extract_label_content(self, response: requests.Response, tracking_references: list[str]) -> bytes:
		content = response.content or b""
		if content.startswith(b"%PDF"):
			return content

		content_type = (response.headers.get("Content-Type") or "").lower()
		if "application/json" in content_type:
			payload = response.json()
			for key in ("data", "content", "pdf", "file_content", "waybill"):
				value = payload.get(key)
				if isinstance(value, str):
					decoded = self.decode_possible_base64(value)
					if decoded:
						return decoded

		decoded = self.decode_possible_base64(response.text.strip())
		if decoded:
			return decoded

		frappe.log_error(
			title="Bob Go Label Debug",
			message=json.dumps(
				{
					"tracking_references": tracking_references,
					"status_code": response.status_code,
					"headers": dict(response.headers),
					"content_preview": response.text[:500],
				},
				indent=2,
				default=str,
			),
		)
		return content

	def decode_possible_base64(self, value: str | None) -> bytes | None:
		if not value:
			return None

		if value.startswith("data:application/pdf;base64,"):
			value = value.split(",", 1)[1]

		try:
			decoded = b64decode(value, validate=True)
		except Exception:
			return None

		return decoded if decoded.startswith(b"%PDF") else None

	def get_collection_min_date(self, pickup_date: str) -> str:
		# ERPNext gives us a plain date on the Shipment form, while Bob Go expects a
		# full ISO datetime. Use 08:00 South Africa time as a sensible collection default.
		parsed_datetime = get_datetime(pickup_date)
		if parsed_datetime.tzinfo:
			return parsed_datetime.isoformat()

		sast = timezone(timedelta(hours=2))
		collection_datetime = datetime.combine(parsed_datetime.date(), time(hour=8), tzinfo=sast)
		return collection_datetime.isoformat()


def get_bobgo_utils() -> "BobGoUtils":
	return BobGoUtils()
