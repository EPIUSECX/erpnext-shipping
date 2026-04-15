# Copyright (c) 2026, Frappe and Contributors
# See license.txt

import unittest

from frappe import _dict

from erpnext_shipping.erpnext_shipping.doctype.bobgo.bobgo import BobGoUtils


class TestBobGo(unittest.TestCase):
	def setUp(self):
		self.utils = BobGoUtils.__new__(BobGoUtils)

	def test_extract_rates_from_list_response(self):
		rates = [{"provider_slug": "demo"}]
		self.assertEqual(self.utils.extract_rates(rates), rates)

	def test_extract_rates_from_wrapped_response(self):
		rates = [{"provider_slug": "demo"}]
		self.assertEqual(self.utils.extract_rates({"rates": rates}), rates)

	def test_get_parcel_list_expands_count(self):
		parcels = [{"count": 2, "length": 10, "width": 20, "height": 30, "weight": 1.5}]
		result = self.utils.get_parcel_list(parcels)

		self.assertEqual(len(result), 2)
		self.assertEqual(result[0]["submitted_length_cm"], 10)
		self.assertEqual(result[0]["submitted_weight_kg"], 1.5)

	def test_get_service_dict_maps_bobgo_rate(self):
		rate = {
			"provider_slug": "demo",
			"service_name": "Economy",
			"service_level_code": "ECO",
			"service_code": "demo-eco",
			"total_price": 49.99,
		}

		service = self.utils.get_service_dict(rate)

		self.assertEqual(service.service_provider, "BobGo")
		self.assertEqual(service.carrier, "demo")
		self.assertEqual(service.service_name, "Economy")
		self.assertEqual(service.provider_slug, "demo")
		self.assertEqual(service.service_level_code, "ECO")
		self.assertEqual(service.total_price, 49.99)

	def test_get_address_dict_uses_city_when_state_missing(self):
		address = _dict(
			address_title="Warehouse",
			address_line1="123 Main Road",
			address_line2="Suburb",
			city="Pretoria",
			country_code="ZA",
			pincode="0181",
		)

		address_dict = self.utils.get_address_dict(address)

		self.assertEqual(address_dict["zone"], "Pretoria")
