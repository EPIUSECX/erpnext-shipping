// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("BobGo", {
	refresh(frm) {
		bindCopyButton(
			frm,
			"copy_tracking_update_url",
			"erpnext_shipping.erpnext_shipping.doctype.bobgo.bobgo.copy_tracking_update_url",
			"Tracking webhook URL copied."
		);
		bindCopyButton(
			frm,
			"copy_shipment_submission_status_update_url",
			"erpnext_shipping.erpnext_shipping.doctype.bobgo.bobgo.copy_shipment_submission_status_update_url",
			"Submission webhook URL copied."
		);
		bindActionButton(
			frm,
			"subscribe_webhooks",
			"erpnext_shipping.erpnext_shipping.doctype.bobgo.bobgo.subscribe_webhooks",
			"Webhook subscriptions synced."
		);
		bindActionButton(
			frm,
			"refresh_webhook_status",
			"erpnext_shipping.erpnext_shipping.doctype.bobgo.bobgo.refresh_webhook_status",
			"Webhook status refreshed."
		);
	},
});

function bindCopyButton(frm, fieldname, method, successMessage) {
	const field = frm.fields_dict[fieldname];
	if (!field || !field.$input || field.$input.data("bobgo-copy-bound")) {
		return;
	}

	field.$input.data("bobgo-copy-bound", true);
	field.$input.on("click", () => {
		frappe.call({
			method,
			callback: async function (r) {
				if (!r.message) {
					return;
				}

				await copyText(r.message);
				frappe.show_alert({ message: __(successMessage), indicator: "green" });
			},
		});
	});
}

function bindActionButton(frm, fieldname, method, successMessage) {
	const field = frm.fields_dict[fieldname];
	if (!field || !field.$input || field.$input.data("bobgo-action-bound")) {
		return;
	}

	field.$input.data("bobgo-action-bound", true);
	field.$input.on("click", () => {
		frappe.call({
			method,
			freeze: true,
			freeze_message: __("Syncing Bob Go webhooks"),
			callback: function (r) {
				if (!r.message) {
					return;
				}

				frm.set_value(
					"tracking_webhook_subscribed",
					Number(r.message.tracking_webhook_subscribed || 0)
				);
				frm.set_value(
					"shipment_submission_status_webhook_subscribed",
					Number(r.message.shipment_submission_status_webhook_subscribed || 0)
				);
				frm.refresh_fields([
					"tracking_webhook_subscribed",
					"shipment_submission_status_webhook_subscribed",
				]);
				frappe.show_alert({ message: __(successMessage), indicator: "green" });
			},
		});
	});
}

async function copyText(text) {
	if (navigator.clipboard?.writeText) {
		await navigator.clipboard.writeText(text);
		return;
	}

	const textarea = document.createElement("textarea");
	textarea.value = text;
	document.body.appendChild(textarea);
	textarea.select();
	document.execCommand("copy");
	document.body.removeChild(textarea);
}
