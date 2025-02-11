# Copyright (c) 2025, Navi and contributors
# For license information, please see license.txt


import frappe
import requests
import json
import hmac
import hashlib
import base64
from datetime import datetime, timezone, timedelta
from frappe.model.document import Document

class AOLWebhookResponses(Document):
    def before_save(self):
        try:
            # Ambil data Sales Quotation
            # qs_data = self.fetch_aol_data("SALES_QUOTATION")

            # Ambil data Sales Order
            # so_data = self.fetch_aol_data("SALES_ORDER")

            # Proses data Order-to-Cash (O2C)
            # self.o2c_generator(qs_data, so_data)
            pass
        except Exception as e:
            self.log_error(str(e), "Error dalam before_save")

    def fetch_aol_data(self, data_type):
        """
        Mengambil dan memproses data dari Accurate Online berdasarkan webhook.
        Args:
            data_type (str): Jenis data ("SALES_ORDER" atau "SALES_QUOTATION").
        Returns:
            dict: Data hasil request ke API Accurate.
        """
        try:
            # Parse payload webhook
            webhook_data = json.loads(self.payload)

            if len(webhook_data) == 0:
                self.log_error("Webhook data kosong, tidak ada elemen dalam field payload.", "Tidak ada data di field payload")
                return {}

            first_entry = webhook_data[0]

            # Mapping ID dan URL API
            data_id = {
                "SALES_ORDER": "salesOrderId",
                "SALES_QUOTATION": "salesQuotationId"
            }

            data_url = {
                "SALES_ORDER": "sales-order",
                "SALES_QUOTATION": "sales-quotation"
            }

            database_id = first_entry.get("databaseId")
            data_row_id = first_entry.get("data", [{}])[0].get(data_id.get(data_type))

            if not database_id or not data_row_id:
                self.log_error("ID Database atau ID baris data tidak valid.", "Error target")
                return {}

            # Ambil API Token & Signature dari Doctype "AOL API Settings"
            settings = frappe.get_doc("AOL API Settings")
            api_token = settings.api_token
            signature_secret = settings.client_secret

            if not api_token or not signature_secret:
                frappe.throw("API Token atau Signature Secret belum diatur.")

            # Generate Headers
            headers = self.generate_headers(api_token, signature_secret)

            # Dapatkan Host API
            host = self.get_host_from_api_token(headers)

            # Ambil Data dari API
            url = f"{host}/api/{data_url[data_type]}/{data_row_id}"
            response = frappe.safe_decode(frappe.make_get_request(url, headers=headers).text)

            return json.loads(response)

        except Exception as e:
            self.log_error(str(e), f"Error saat mengambil {data_type} dari API")
            return {}

    def o2c_generator(self, qs_data, so_data):
        """
        Mengisi data pada Doctype berdasarkan mapping JSON.
        Args:
            qs_data (dict): Data Sales Quotation dari Accurate Online.
            so_data (dict): Data Sales Order dari Accurate Online.
        """
        try:
            # Mapping JSON ke Field Doctype
            field_mapping = {
                "doc_id": "so_data.d.number",
                "status": "so_data.d.statusName",
                "term_of_payment": "so_data.d.paymentTerm.netDays",
                "customer_name": "so_data.d.customer.name",
                "type": "",  # Diisi dari parsing doc_id
                "sales_name": "so_data.d.charField9",
                "cbd_id": "",
                "cbd_release_date": "",
                "description": "so_data.d.description",
                "quantity": "1",  # Default
                "uom": "Set",  # Default
                "unit_price": "",  # Sum of cbd_items.amount
                "cbd_amount": "",  # unit_price * quantity
                "sq_number": "qs_data.d.number",
                "sq_release_date": "",
                "so_release_date": "so_data.d.dateField1"
            }

            # Ambil nilai dari JSON menggunakan mapping
            for field, json_path in field_mapping.items():
                if json_path:
                    self.set(field, self.get_json_value(so_data, qs_data, json_path))

            # Parsing `doc_id` untuk mendapatkan `type`
            self.set("type", self.parse_doc_id(self.get("doc_id")))

            # Mengisi child table `cbd_items`
            self.set("cbd_items", self.extract_cbd_items(so_data))

            # Hitung total harga (sum dari semua `amount` dalam `cbd_items`)
            total_price = sum(item.get("amount", 0) for item in self.get("cbd_items", []))
            self.set("unit_price", total_price)
            self.set("cbd_amount", total_price * int(self.get("quantity", 1)))

        except Exception as e:
            self.log_error(str(e), "Error dalam o2c_generator")

    def get_json_value(self, so_data, qs_data, json_path):
        """
        Mengambil nilai dari JSON berdasarkan dot notation.
        Args:
            so_data (dict): JSON dari Sales Order.
            qs_data (dict): JSON dari Sales Quotation.
            json_path (str): Path dalam JSON (misal: "so_data.d.customer.name").
        Returns:
            Any: Nilai yang ditemukan atau None jika tidak ditemukan.
        """
        try:
            # Tentukan sumber data (Sales Order atau Sales Quotation)
            source_data = so_data if json_path.startswith("so_data") else qs_data
            path = json_path.replace("so_data.", "").replace("qs_data.", "").split(".")

            # Ambil nilai secara rekursif
            value = source_data
            for key in path:
                if isinstance(value, dict):
                    value = value.get(key, None)
                else:
                    return None  # Jika bukan dictionary, berhenti

            return value
        except Exception:
            return None

    def extract_cbd_items(self, so_data):
        """
        Mengambil data `cbd_items` dari Sales Order.
        Args:
            so_data (dict): JSON dari Sales Order.
        Returns:
            list: Data untuk child table `cbd_items`.
        """
        try:
            cbd_items = []
            items = so_data.get("d", {}).get("detailItem", [])

            for item in items:
                cbd_items.append({
                    "job_name": item.get("detailName"),
                    "quantity": item.get("quantity"),
                    "uom": item.get("itemUnit", {}).get("name"),
                    "cost_per_uom": item.get("unitPrice"),
                    "amount": item.get("totalPrice")
                })

            return cbd_items
        except Exception:
            return []

    def parse_doc_id(self, doc_id):
        """
        Parsing `doc_id` untuk mendapatkan `type`.
        Args:
            doc_id (str): Nomor dokumen dari Sales Order.
        Returns:
            str: Jenis dokumen yang diproses.
        """
        try:
            if not doc_id:
                return ""
            if doc_id.startswith("SQ-"):
                return "Sales Quotation"
            elif doc_id.startswith("SO-"):
                return "Sales Order"
            return "Unknown"
        except Exception:
            return ""


    def generate_headers(self, api_token, signature_secret):
        """
        Membuat header yang dibutuhkan untuk autentikasi API Accurate Online.
        """
        # Timestamp dalam zona waktu Asia/Jakarta (GMT+7)
        jakarta_timezone = timezone(timedelta(hours=7))  # Zona waktu Jakarta
        timestamp = int(datetime.now(jakarta_timezone).timestamp())  # Unix timestamp dalam GMT+7

        # Format timestamp ke bentuk "dd/MM/yyyy HH:mm:ss"
        timestamp_text = datetime.now(jakarta_timezone).strftime("%d/%m/%Y %H:%M:%S")

        # Signature HMAC SHA-256
        signature = hmac.new(
            key=signature_secret.encode(),
            msg=str(timestamp_text).encode(),
            digestmod=hashlib.sha256
        ).digest()

        # Encode signature ke Base64
        signature_b64 = base64.b64encode(signature).decode()

        self.action = timestamp_text
        self.status = str(signature_b64)

        # Buat header
        headers = {
            "Authorization": f"Bearer {api_token}",
            "X-Api-Timestamp": str(timestamp_text),
            "X-Api-Signature": signature_b64,
            "Content-Type": "application/json"
        }

        self.data = str(headers)

        return headers

    def get_host_from_api_token(self, headers):
        """
        Mendapatkan host dari API Token.
        """
        api_token_url = "https://account.accurate.id/api/api-token.do"

        response = requests.post(api_token_url, headers=headers)
        if response.status_code != 200:
            frappe.throw(f"Failed to fetch host: {response.text}")

        # Parse respons JSON
        try:
            response_data = response.json()
        except ValueError:
            frappe.throw("Gagal memparse JSON dari API response.")

        # Log untuk debugging (hapus setelah selesai)
        self.debug =  str(response)

        # Pastikan key 'd' dan 'data usaha' ada
        # data_usaha = response_data.get("d", {}).get("data usaha", {})
        # host = data_usaha.get("host")

        #if not host:
        #    frappe.throw("Host tidak ditemukan dalam response API Token.")
        host = "https://public.accurate.id/"
        return host

    def get_data_details(self, host, headers, data_id, data_type, data_url):
        """
        Mengambil detail dokumen sales-quotation dari Accurate Online.
        """
        data_endpoint = data_url[data_type]

        api_url = f"{host}/accurate/api/{data_endpoint}/detail.do"

        response = requests.get(api_url, headers=headers, params={"id": data_id})
        if response.status_code != 200:
            frappe.throw(f"Failed to fetch sales quotation details: {response.text}")

        return response.json()

    def log_error(error, title="Application Error"):
        """
        Mencatat error ke dalam Doctype Error Log di Frappe.
        Args:
            error (Exception): Exception yang ingin dicatat.
            title (str, optional): Judul error. Default: "Application Error".
        """
        try:
            error_message = f"{title}\n\n{str(error)}\n\nTraceback:\n{traceback.format_exc()}"

            # Simpan error ke dalam Doctype Error Log
            frappe.get_doc({
                "doctype": "Error Log",
                "error": error_message
            }).insert(ignore_permissions=True)
            
            frappe.db.commit()  # Pastikan data tersimpan
            
        except Exception as e:
            # Jika gagal mencatat error, cetak ke log sistem
            frappe.logger().error(f"Failed to log error: {str(e)}")
