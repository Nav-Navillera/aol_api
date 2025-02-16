# Copyright (c) 2025, Navi and contributors
# For license information, please see license.txt


import frappe
import requests
import json
import hmac
import hashlib
import base64
import re
import time
from functools import lru_cache
from datetime import datetime, timezone, timedelta
from frappe.model.document import Document

class AOLWebhookResponses(Document):
	def before_save(self):
		try:
			if self.document_type == "SALES_ORDER":
				self.generate_o2c(self.row_id)
		except Exception as e:
			frappe.log_error(str(e), "Error dalam before_save")

	def generate_o2c(self, data_id):
		try:
			def safe_json_loads(data):
				return json.loads(data) if isinstance(data, str) else data

			# Ambil data dari Accurate Online
			so_data = safe_json_loads(self.fetch_aol_data(self.generate_so_payload_from_data(data_id)))
			pre_sq_data = self.generate_sq_payload_from_data(so_data)
			sq_data = safe_json_loads(self.fetch_aol_data(pre_sq_data))

			doc_id = so_data.get("d", {}).get("number", "")
			if not doc_id:
				frappe.throw("Sales Order Number tidak ditemukan, tidak dapat membuat Sales Pipeline Control.")

			existing_doc = frappe.get_doc("Sales Pipeline Control", {"doc_id": doc_id}) if frappe.db.exists("Sales Pipeline Control", {"doc_id": doc_id}) else None

			cbd_items, sq_items = [], []
			total_cbd_amount, total_sq_price, total_discount, total_sq_amount = 0, 0, 0, 0

			for item in so_data.get("d", {}).get("detailItem", []):
				quantity, cost_per_uom = item.get("quantity", 0), item.get("numericField6", 0)
				item_price_per_uom, discount = item.get("unitPrice", 0), item.get("itemCashDiscount", 0)
				sq_item_amount = quantity * item_price_per_uom
				total_price = quantity * cost_per_uom

				cbd_items.append({
					"job_name": item.get("detailName", ""),
					"quantity": quantity,
					"uom": item.get("itemUnit", {}).get("name", ""),
					"cost_per_uom": cost_per_uom,
					"amount": total_price
				})

				sq_items.append({
					"job_name": item.get("detailName", ""),
					"quantity": quantity,
					"uom": item.get("itemUnit", {}).get("name", ""),
					"item_price_per_uom": item_price_per_uom,
					"_discount": discount / sq_item_amount if sq_item_amount else 0,
					"discount": discount,
					"sq_item_amount": sq_item_amount
				})

				total_cbd_amount += total_price
				total_sq_price += sq_item_amount
				total_discount += discount
				total_sq_amount += sq_item_amount

			discount_percentage = (total_discount / total_sq_price) * 100 if total_sq_price else 0
			profit_amount = total_sq_price - total_cbd_amount
			sq_final_amount = total_sq_price - total_discount
			gross_margin = sq_final_amount - total_cbd_amount
			p_gross_margin = (gross_margin / total_cbd_amount) * 100 if total_cbd_amount else 100
			fee_customer = so_data["d"].get("numericField4", 0)
			p_fee_customer = (fee_customer / gross_margin) * 100 if gross_margin else 0
			p_nett_margin = ((gross_margin - fee_customer) / total_cbd_amount) * 100 if total_cbd_amount else 100
			nett_margin = gross_margin - fee_customer

			doc = existing_doc or frappe.get_doc({"doctype": "Sales Pipeline Control"})

			fields = {
				"data_id": data_id,
				"doc_id": doc_id,
				"status": so_data.get("d", {}).get("statusName", ""),
				"term_of_payment": so_data.get("d", {}).get("paymentTerm", {}).get("netDays", ""),
				"customer_name": so_data.get("d", {}).get("customer", {}).get("name", ""),
				"sales_name": so_data.get("d", {}).get("charField9", ""),
				"cbd_id": sq_data.get("d", {}).get("charField8", ""),
				"description": so_data.get("d", {}).get("description", ""),
				"quantity": "1",
				"uom": "Set",
				"unit_price": total_cbd_amount,
				"cbd_amount": total_cbd_amount,
				"sq_number": sq_data.get("d", {}).get("number", ""),
				"sq_release_date": self.convert_date_format(sq_data.get("d", {}).get("transDate", "")),
				"sq_price": total_sq_price,
				"discount_amount": total_discount,
				"discount": discount_percentage,
				"profit_amount": profit_amount,
				"_profit": (profit_amount / total_cbd_amount) * 100 if total_cbd_amount else 0,
				"sq_final_amount": sq_final_amount,
				"gross_margin": gross_margin,
				"p_gross_margin": p_gross_margin,
				"so_number": doc_id,
				"sales_order_id": so_data.get("d", {}).get("poNumber", ""),
				"so_release_date": self.convert_date_format(so_data.get("d", {}).get("transDate", "")),
				"fee_customer": fee_customer,
				"p_fee_customer": p_fee_customer,
				"nett_margin": nett_margin
			}

			for key, value in fields.items():
				setattr(doc, key, value)

			if existing_doc:
				doc.set("cbd_items", [])
				doc.set("sq_items", [])

			for item in cbd_items:
				doc.append("cbd_items", item)
			for item in sq_items:
				doc.append("sq_items", item)

			doc.save()
			frappe.msgprint(f"Sales Pipeline Control {doc_id} {'diperbarui' if existing_doc else 'dibuat'}.")

		except Exception as e:
			frappe.log_error(f"Error dalam generate_o2c: {str(e)}", "generate_o2c")
			frappe.throw(f"Terjadi kesalahan dalam proses generate_o2c: {str(e)}")

			self.status = "Error"
			self.status_details = f"Detail: {str(e)}"
			
	def generate_so_payload_from_data(self, data_id):
		try:
			
			return {
				"type": "SALES_ORDER",
				"data": [{"salesOrderId": data_id}]
			}

		except (KeyError, IndexError, TypeError):
			frappe.log_error(frappe.get_traceback() + f"data: {debug_data}" , "Error generate_sq_payload_from_data")
			return None

	def generate_sq_payload_from_data(self, data):
		"""
		Menghasilkan payload sales quotation dari data webhook.
		"""
		try:
			if isinstance(data, str):
				data = json.loads(data)

			if not isinstance(data, dict) or "d" not in data:
				frappe.throw("Format data tidak valid dalam generate_sq_payload_from_data.")

			debug_data = data["d"]
			
			sales_quotation_id = data["d"]["detailItem"][0]["salesQuotation"]["id"]
			

			return {
				"type": "SALES_QUOTATION",
				"data": [{"salesQuotationId": sales_quotation_id}]
			}

		except (KeyError, IndexError, TypeError):
			frappe.log_error(frappe.get_traceback() + f"data: {debug_data}" , "Error generate_sq_payload_from_data")
			return None  

	def fetch_aol_data(self, payload):
		"""
		Mengambil data dari Accurate Online berdasarkan payload webhook.
		"""
		try:
			# Pastikan payload dalam bentuk dictionary atau list
			if isinstance(payload, str):
				try:
					payload = json.loads(payload)  # Parsing hanya sekali
				except json.JSONDecodeError:
					frappe.throw("Format webhook tidak valid: payload bukan JSON yang benar.")

			if isinstance(payload, list):  
				if not payload:  
					frappe.throw("Format webhook tidak valid: list kosong.")  
				first_entry = payload[0]  # Ambil elemen pertama jika list
			elif isinstance(payload, dict):  
				first_entry = payload  # Langsung gunakan jika dictionary
			else:  
				frappe.throw(f"Format webhook tidak valid: tipe data {type(payload)} tidak didukung.")

			# Pastikan first_entry adalah dictionary
			if not isinstance(first_entry, dict):
				frappe.throw(f"Format webhook tidak valid: data harus berupa dictionary. Tipe: {type(first_entry)}")

			database_id = first_entry.get("databaseId", None) 

			# Ambil nilai "type"
			document_type = first_entry.get("type", None)

			# Ambil data pertama dari "data" jika ada
			data_list = first_entry.get("data")
			if isinstance(data_list, list) and data_list:
				data_id = data_list[0].get(self.get_data_id_key(document_type), None)
			elif isinstance(data_list, dict):  # Jika data adalah dictionary langsung
				data_id = data_list.get(self.get_data_id_key(document_type), None)
			else:
				data_id = None
			
			frappe.log_error("fetch_aol_data", f"data: {data_id} type: {document_type}")

			if not data_id:
				frappe.throw(f"ID tidak ditemukan untuk dokumen '{document_type}'.")
			
			headers = self.get_headers_with_cache()
			host = "https://public.accurate.id"  # Tidak perlu mengambil dari cache karena sudah pasti
			
			# self.access_aol_db(host="https://account.accurate.id", headers=headers, database_id=database_id)
		
			return self.get_data_details(host, headers, data_id, document_type)

		except json.JSONDecodeError:
			frappe.throw("Payload tidak valid, gagal memparse JSON.")
		except requests.RequestException as e:
			frappe.throw(f"Terjadi kesalahan saat mengambil data dari Accurate API: {str(e)}")
		except Exception as e:
			frappe.throw(f"Error handling webhook: {str(e)}")

	@staticmethod
	def get_data_id_key(document_type):
		"""
		Mendapatkan key ID berdasarkan tipe dokumen.
		"""
		return {
			"SALES_ORDER": "salesOrderId",
			"SALES_QUOTATION": "salesQuotationId"
		}.get(document_type, "")
		
	def get_headers_with_cache(self):
		"""
		Mengambil header dari cache jika masih berlaku (kurang dari 5 menit),
		atau membuat header baru jika sudah expired.
		"""
		cache_key = "aol_api_headers"
		cached_data = frappe.cache().get_value(cache_key)

		if cached_data:
			cached_data = json.loads(cached_data)  # Konversi dari string ke dict
			cached_time = cached_data.get("timestamp", 0)
			if time.time() - cached_time < 300:  # 5 menit
				return cached_data["headers"]

		# Generate header baru karena cache kadaluarsa
		new_headers = self.get_new_headers()
		frappe.cache().set_value(cache_key, json.dumps({"headers": new_headers, "timestamp": time.time()}))
		return new_headers

	def get_new_headers(self):
		"""
		Menghasilkan header autentikasi API Accurate Online (cache untuk optimasi).
		"""
		try:
			settings = frappe.get_doc("AOL API Settings")
			api_token = settings.api_token
			signature_secret = settings.signature_secret

			if not api_token or not signature_secret:
				frappe.throw("API Token atau Signature Secret belum diatur.")

			jakarta_timezone = timezone(timedelta(hours=7))
			timestamp_text = datetime.now(jakarta_timezone).strftime("%d/%m/%Y %H:%M:%S")

			signature = hmac.new(
				key=signature_secret.encode(),
				msg=str(timestamp_text).encode(),
				digestmod=hashlib.sha256
			).digest()

			return {
				"Authorization": f"Bearer {api_token}",
				"X-Api-Timestamp": timestamp_text,
				"X-Api-Signature": base64.b64encode(signature).decode(),
				"Content-Type": "application/json"
			}

		except Exception:
			frappe.throw("Gagal membuat headers API.")

	def get_data_details(self, host, headers, data_id, data_type):
		"""
		Mengambil detail dokumen dari Accurate Online.
		"""
		try:
			api_url = f"{host}/accurate/api/{self.get_data_url(data_type)}/detail.do"
			response = requests.get(api_url, headers=headers, params={"id": data_id})
			response.raise_for_status()

			return response.json()

		except requests.RequestException as e:
			frappe.throw(f"Gagal mengambil detail dokumen dari Accurate API: {str(e)}")
			
	def access_aol_db(self, host, headers, database_id):
		"""
		Mengambil detail dokumen dari Accurate Online.
		"""
		try:
			api_url = f"{host}/api/open-db.do"
			response = requests.get(api_url, headers=headers, params={"id": database_id})
			response.raise_for_status()

		except requests.RequestException as e:
			frappe.throw(f"Gagal mengakses db {database_id} dari Accurate API: {str(e)}")

	@staticmethod
	def get_data_url(document_type):
		"""
		Mendapatkan endpoint URL berdasarkan tipe dokumen.
		"""
		return {
			"SALES_ORDER": "sales-order",
			"SALES_QUOTATION": "sales-quotation"
		}.get(document_type, "")
	
	@staticmethod
	def convert_date_format(date_str):
		"""Mengonversi tanggal dari format dd/MM/yyyy ke format yyyy-MM-dd yang diterima oleh Frappe."""
		try:
			return datetime.strptime(date_str, "%d/%m/%Y").strftime("%Y-%m-%d")
		except ValueError:
			frappe.throw(f"Format tanggal tidak valid: {date_str}")
