# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

BOBGO_PROVIDER = "BobGo"
PROD_BASE_URL = "https://api.bobgo.co.za/v2"
TEST_BASE_URL = "https://api.sandbox.bobgo.co.za/v2"

TRACKING_WEBHOOK_TOPIC = "tracking/updated"
SUBMISSION_STATUS_WEBHOOK_TOPIC = "shipment_submission_status/updated"

SUCCESSFUL_SUBMISSION_STATUSES = {"success"}
RETRYABLE_SUBMISSION_STATUSES = {"pending-rates", "pending-submission", "failed-will-retry"}
FAILED_SUBMISSION_STATUSES = {"no-rates", "failed-indefinitely"}
