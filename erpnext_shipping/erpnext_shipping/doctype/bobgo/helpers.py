# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

from __future__ import annotations

import json
from typing import Any

import frappe


def format_tracking_status(status: str | None) -> str:
	if not status:
		return ""
	return status.replace("-", " ").title()


def normalize_erpnext_tracking_status(tracking_statuses: list[str]) -> str:
	normalized_statuses = [status.lower() for status in tracking_statuses if status]
	if not normalized_statuses:
		return ""

	if any("deliver" in status for status in normalized_statuses):
		return "Delivered"
	if any("return" in status for status in normalized_statuses):
		return "Returned"
	if any("lost" in status for status in normalized_statuses):
		return "Lost"

	# Bob Go statuses such as pending-collection, collected, in-transit, and
	# out-for-delivery all map cleanly onto ERPNext's generic in-progress state.
	return "In Progress"


def normalize_tracking_response(response_data: Any, tracking_reference: str) -> dict:
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


def get_bobgo_tracking_status_info(payload: dict) -> str:
	checkpoints = payload.get("checkpoints") or []
	latest_checkpoint = checkpoints[0] if checkpoints else {}
	return (
		payload.get("status_friendly")
		or latest_checkpoint.get("status_friendly")
		or latest_checkpoint.get("message")
		or payload.get("status")
		or ""
	)


def log_bobgo_webhook(title: str, payload: dict):
	frappe.log_error(title=title, message=json.dumps(payload, indent=2, default=str))
