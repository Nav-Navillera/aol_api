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
            self.generate_o2c()
        except Exception as e:
            self.log_error(str(e), "Error dalam before_save")
            
    def generate_o2c(self):
        so_data =  self.fetch_aol_data(self.payload)
        pre_sq_data = self.generate_sq_payload_from_data(so_data)
        sq_data = self.fetch_aol_data(pre_sq_data)
        
        self.data2 = so_data
        self.data_2_copy = sq_data
       
    def generate_o2c(self):
        try:
            doc_id = self.name
            so_data = self.fetch_aol_data(self.payload)
            pre_sq_data = self.generate_sq_payload_from_data(so_data)  # Fix error di sini
            if not pre_sq_data:
                frappe.throw("Gagal membuat payload SALES_QUOTATION.")

            sq_data = self.fetch_aol_data(pre_sq_data)

            self.data2 = so_data
            self.data_2_copy = sq_data

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), f"Error generate_o2c id {doc_id}")
            frappe.throw(f"Error pada generate_o2c: {str(e)}")

    def generate_sq_payload_from_data(self, data):
        try:
            # Pastikan data dalam bentuk dictionary
            if isinstance(data, str):
                data = json.loads(data)

            # Pastikan format data sesuai
            if not isinstance(data, dict) or "d" not in data:
                frappe.throw("Format data tidak valid dalam generate_sq_payload_from_data.")

            # Ambil salesQuotationId dari lokasi yang ditentukan
            sales_quotation_id = data["d"]["detailItem"][0]["salesQuotation"]["id"]

            # Struktur payload yang dihasilkan
            payload = {
                "type": "SALES_QUOTATION",
                "data": [{"salesQuotationId": sales_quotation_id}]
            }

            # self.log_error("test generate_sq_payload_from_data", f"Berhasil generate payload: {payload}")
            return payload  # Mengembalikan dictionary, bukan JSON string

        except (KeyError, IndexError, TypeError) as e:
            frappe.log_error(frappe.get_traceback(), "Error generate_sq_payload_from_data {}")
            return None  # Pastikan fungsi mengembalikan nilai saat error

    def fetch_aol_data(self, payload):
        """
        Mengambil data dari Accurate Online berdasarkan tipe dokumen yang diterima dari webhook.
        """
        try:
            
            doc_id = self.name
            
            # Pastikan payload dalam bentuk string sebelum diparsing
            if isinstance(payload, str):
                payload = json.loads(payload)

            # Jika payload adalah list, pastikan tidak kosong dan ambil elemen pertama
            if isinstance(payload, list):
                if not payload:
                    frappe.throw("Webhook data kosong, tidak ada elemen dalam array.")
                first_entry = payload[0]  # Ambil elemen pertama dari list
            elif isinstance(payload, dict):
                first_entry = payload  # Langsung gunakan jika sudah dictionary
            else:
                frappe.throw(f"Format webhook data tidak valid: {payload} {type(payload)}")

            # self.log_error("test", f"Format webhook data valid: {first_entry}")

            # Pastikan first_entry tetap dictionary sebelum dikembalikan
            if not isinstance(first_entry, dict):
                frappe.throw("Data yang diproses bukan dictionary yang valid.")

            # Mapping ID dan URL berdasarkan tipe dokumen
            data_id_map = {
                "SALES_ORDER": "salesOrderId",
                "SALES_QUOTATION": "salesQuotationId"
            }

            data_url_map = {
                "SALES_ORDER": "sales-order",
                "SALES_QUOTATION": "sales-quotation"
            }

            document_type = first_entry.get("type")

            if document_type not in data_id_map:
                frappe.throw(f"Tipe dokumen '{document_type}' tidak dikenali.")

            # Ambil informasi dari dictionary
            data_id = first_entry.get("data", [{}])[0].get(data_id_map[document_type])

            # Ambil API Token dan Signature Secret
            settings = frappe.get_doc("AOL API Settings")
            api_token = settings.api_token
            signature_secret = settings.signature_secret

            if not api_token or not signature_secret:
                frappe.throw("API Token atau Signature Secret belum diatur.")

            # Buat header dengan Signature
            headers = self.generate_headers(api_token, signature_secret)

            # Ambil host dari API token
            host = self.get_host_from_api_token(headers)

            # Ambil detail dokumen dari Accurate Online
            sales_quotation_data = self.get_data_details(host, headers, data_id, document_type, data_url_map)

            return sales_quotation_data  # Mengembalikan dictionary, bukan JSON string

        except json.JSONDecodeError:
            frappe.log_error("Gagal melakukan parsing JSON pada payload.", "JSON Parsing Error")
            frappe.throw("Payload tidak valid, gagal memparse JSON.")
        except requests.RequestException as e:
            frappe.log_error(str(e), "Request Error")
            frappe.throw(f"Terjadi kesalahan saat mengambil data dari Accurate API: {str(e)}")
        except Exception as e:
            frappe.log_error(frappe.get_traceback(),f"error fetch aol data, doc id {doc_id}")
            frappe.throw(f"Error handling webhook: {str(e)} ")

    def generate_headers(self, api_token, signature_secret):
        """
        Membuat header yang dibutuhkan untuk autentikasi API Accurate Online.
        """
        try:
            jakarta_timezone = timezone(timedelta(hours=7))
            timestamp = int(datetime.now(jakarta_timezone).timestamp())

            timestamp_text = datetime.now(jakarta_timezone).strftime("%d/%m/%Y %H:%M:%S")

            # Signature HMAC SHA-256
            signature = hmac.new(
                key=signature_secret.encode(),
                msg=str(timestamp_text).encode(),
                digestmod=hashlib.sha256
            ).digest()

            # Encode signature ke Base64
            signature_b64 = base64.b64encode(signature).decode()

            headers = {
                "Authorization": f"Bearer {api_token}",
                "X-Api-Timestamp": str(timestamp_text),
                "X-Api-Signature": signature_b64,
                "Content-Type": "application/json"
            }

            self.data = str(headers)

            return headers

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Error in generate_headers")
            frappe.throw(f"Gagal membuat headers API: {str(e)}")

    def get_host_from_api_token(self, headers):
        """
        Mendapatkan host dari API Token.
        """
        try:
            api_token_url = "https://account.accurate.id/api/api-token.do"

            response = requests.post(api_token_url, headers=headers)
            response.raise_for_status()

            try:
                response_data = response.json()
            except ValueError:
                frappe.throw("Gagal memparse JSON dari API response.")

            self.debug = str(response)

            # Pastikan key 'd' dan 'data usaha' ada
            host = "https://public.accurate.id/"

            return host

        except requests.RequestException as e:
            frappe.log_error(str(e), "Error in get_host_from_api_token")
            frappe.throw(f"Terjadi kesalahan saat mengambil host dari API Token: {str(e)}")
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Error in get_host_from_api_token")
            frappe.throw(f"Gagal mendapatkan host API: {str(e)}")

    def get_data_details(self, host, headers, data_id, data_type, data_url_map):
        """
        Mengambil detail dokumen dari Accurate Online.
        """
        try:
            data_endpoint = data_url_map[data_type]
            api_url = f"{host}/accurate/api/{data_endpoint}/detail.do"

            response = requests.get(api_url, headers=headers, params={"id": data_id})
            response.raise_for_status()

            return response.json()

        except requests.RequestException as e:
            frappe.log_error(str(e), "Error in get_data_details")
            frappe.throw(f"Gagal mengambil detail dokumen dari Accurate API: {str(e)}")
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Error in get_data_details")
            frappe.throw(f"Terjadi kesalahan saat mengambil detail data: {str(e)}")
