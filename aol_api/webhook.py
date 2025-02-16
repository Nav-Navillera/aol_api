import os

import frappe
import json
import requests

import hmac
import hashlib
import base64

import traceback
import re
import time
from functools import lru_cache
from datetime import datetime, timezone, timedelta
from frappe.utils import get_site_path, now_datetime, add_to_date


frappe.init(site="navi")
frappe.connect()

def get_webhook_sync_ranges():
    """Membuat rentang waktu untuk sinkronisasi histori webhook dari jam 00:00:00 ke 23:59:59"""
    site_config_path = get_site_path("site_config.json")

    # Ambil waktu terakhir sinkronisasi dari site_config.json
    if os.path.exists(site_config_path):
        with open(site_config_path, "r") as f:
            site_config = json.load(f)
        last_sync_str = site_config.get("last_webhook_sync_time")
    else:
        last_sync_str = None

    # Konversi waktu terakhir sinkronisasi ke datetime
    if last_sync_str:
        last_sync = frappe.utils.get_datetime(last_sync_str)
    else:
        # Jika belum ada, default ke 1 bulan ke belakang
        last_sync = add_to_date(now_datetime(), months=-1)

    now = now_datetime()

    # Pastikan tidak lebih dari 1 bulan ke belakang
    one_month_ago = add_to_date(now, months=-1)
    if last_sync < one_month_ago:
        last_sync = one_month_ago

    # Set start_time ke pukul 00:00:00 di hari terakhir sinkronisasi
    start_time = last_sync.replace(hour=0, minute=0, second=0)

    sync_ranges = []

    while start_time < now:
        # Set end_time ke 23:59:59 di hari yang sama
        end_time = start_time.replace(hour=23, minute=59, second=59)

        # Jangan biarkan end_time melebihi waktu sekarang
        if end_time > now:
            end_time = now

        sync_ranges.append((start_time, end_time))

        # Geser ke hari berikutnya
        start_time = add_to_date(start_time, days=1).replace(hour=0, minute=0, second=0)

    return sync_ranges

def generate_webhook_hash(uuid, timestamp):
    """Buat hash unik 16 karakter dari SHA-1."""
    return hashlib.sha1(f"{uuid}|{timestamp}".encode()).hexdigest()[:21]

def generate_headers(api_token, signature_secret):
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

    # Buat header
    headers = {
        "Authorization": f"Bearer {api_token}",
        "X-Api-Timestamp": str(timestamp_text),
        "X-Api-Signature": signature_b64,
        "Content-Type": "application/json"
    }

    return headers

def get_host_from_api_token(headers):
    """
    Mendapatkan host dari API Token.
    """
    
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
    """
    
    host = "https://public.accurate.id/"
    return host

def get_api_token():
    # Ambil API Token & Signature dari Doctype "AOL API Settings"
    settings = frappe.get_doc("AOL API Settings")
    api_token = settings.api_token
    signature_secret = settings.signature_secret
    
    return api_token, signature_secret

def save_webhook_response(json_data):
    """
    Menyimpan data webhook ke dalam Doctype 'AOL Webhook Responses'. 
    dengan meminta terlebih dahulu details data yang diberikan webhook accurate pada
    fungsi receiver

    Args:
        json_data (list): Data webhook dalam bentuk JSON list yang berisi payload.

    Returns:
        None
    """
    try:
        
        # Pastikan payload dalam bentuk string sebelum diparsing
        if isinstance(json_data, str):
            payload = json.loads(json_data)

        # Jika payload adalah list, pastikan tidak kosong dan ambil elemen pertama
        if isinstance(json_data, list):
            if not json_data:
                frappe.throw("Webhook data kosong, tidak ada elemen dalam array.")
            parsed_data = json_data[0]  # Ambil elemen pertama dari list
        elif isinstance(json_data, dict):
            parsed_data = json_data  # Langsung gunakan jika sudah dictionary
        else:
            frappe.throw(f"Format webhook data tidak valid: {json_data} {type(json_data)}")
        
        if not parsed_data:
            frappe.logger("webhook").error("JSON data kosong atau tidak valid")
            return

        # Ambil nilai dari elemen pertama JSON
        first_entry = json_data
        database_id = first_entry.get("databaseId")
        document_type = first_entry.get("type")
        
        timestamp_dt = datetime.strptime(first_entry["timestamp"], "%d/%m/%Y %H:%M:%S")
        send_timestamp = timestamp_dt.strftime("%Y/%m/%d %H:%M:%S")
        
        uuid = first_entry.get("uuid")

        # Pastikan key 'data' ada dan tidak kosong
        if "data" not in first_entry or not first_entry["data"]:
            frappe.logger("webhook").error("Data sales order tidak ditemukan")
            return

        # Ambil elemen pertama dari 'data'
        sales_data = first_entry["data"][0]
        row_id = sales_data.get("salesOrderId")
        action = sales_data.get("action")
        
        hash_code = generate_webhook_hash(uuid, send_timestamp)

        # Cek apakah data dengan hash_code sudah ada
        if frappe.db.exists("AOL Webhook Responses", {"hash_code": hash_code}):
            frappe.logger("webhook").error(f"Dokumen duplikat terdeteksi untuk hash_code: {hash_code}")
            return

        # Simpan ke Doctype 'AOL Webhook Responses'
        new_response = frappe.get_doc({
            "doctype": "AOL Webhook Responses",
            "payload": json_data,  # Simpan JSON mentah sebagai log
            "uuid": uuid,
            "document_type": document_type,
            "database_id": database_id,
            "row_id": row_id,
            "action": action,
            "timestamp": send_timestamp,
            "hash_code": hash_code
        })

        # Masukkan ke database
        new_response.insert(ignore_permissions=True)
        frappe.db.commit()

        # Perbarui waktu terakhir sinkronisasi webhook
        webhook_last_sync_time(send_timestamp)
        
        #frappe.log_error(f"Debug Exists: {json_data} data count {parsed_data}", "Webhook Debugging ")
                            
    
    except frappe.DuplicateEntryError:
        pass
        #frappe.logger("webhook").error(f"Duplicate entry error untuk hash_code: {hash_code}")
    except Exception as e:
        frappe.logger("webhook").error(f"Kesalahan saat menyimpan webhook response: {str(e)}")

@frappe.whitelist(allow_guest=True)
def receiver():
    """
    Menerima data webhook dari Accurate Online dan menyimpannya ke Doctype AOL Webhook Responses.
    """
    try:
        # Ambil data dari request dalam format JSON string
        data = frappe.request.get_data(as_text=True)

        if not data:
            log_error("Data kosong, tidak ada yang dikirim oleh webhook", "Data Kosong")
            return

        # Parse JSON string ke dictionary
        json_data = json.loads(data)

        # Pastikan data memiliki elemen pertama
        if not isinstance(json_data, list) or len(json_data) == 0:
            log_error("Format JSON tidak valid", "Format Error")
            return

        save_webhook_response(json_data)
        
        # Pastikan response dikembalikan dalam JSON
        """
        frappe.local.response = {
            "type": "json",
            "status": "success",
            "message": "Data berhasil disimpan"
        }
        """

    except Exception as e:
        frappe.db.rollback()
        log_error(e, "Webhook error")
        frappe.local.response = {
            "type": "json",
            "status": "error",
            "message": str(e)
        }

@frappe.whitelist()
def log_error(error, title="Application Error"):
    """
    Mencatat error ke dalam Doctype Error Log di Frappe.

    Args:
        error (Exception): Exception yang ingin dicatat.
        title (str, optional): Judul error. Default: "Application Error".
    """
    try:
        error_message = f"{str(error)}\n\nTraceback:\n{traceback.format_exc()}"

        # Simpan error ke dalam Doctype Error Log
        frappe.get_doc({
            "doctype": "Error Log",
            "method": title,
            "error": error_message
        }).insert(ignore_permissions=True)

        frappe.db.commit()  # Pastikan data tersimpan

    except Exception as e:
        # Jika gagal mencatat error, cetak ke log sistem
        frappe.logger().error(f"Failed to log error: {str(e)}")
   
@frappe.whitelist() 
def process_webhook_sync(host, headers):
    """
    Loop setiap rentang waktu dan panggil get_webhook_history.
    Setelah mendapatkan data, periksa hash code, dan simpan jika belum ada.
    """
    sync_ranges = get_webhook_sync_ranges()
    new_data_counter = 0

    for start_time, end_time in sync_ranges:
        from_time = start_time.strftime("%d/%m/%Y %H:%M:%S")
        to_time = end_time.strftime("%d/%m/%Y %H:%M:%S")

        try:
            # Ambil histori webhook dari API
            response = get_webhook_history(host, headers, from_time=from_time, to_time=to_time)

            # Debugging: Log isi response
            frappe.logger("webhook").info(f"Webhook Response Debug: {response}")

            # Pastikan response valid dan memiliki kunci 's'
            if not isinstance(response, dict) or "s" not in response:
                frappe.logger("webhook").error(f"Format respons tidak valid: {response}")
                frappe.msgprint(f"Format respons tidak valid, cek log untuk detail.")
                continue

            # Jika 's' False, log error dan tampilkan pesan
            if not response["s"]:
                frappe.logger("webhook").warning(f"Webhook request gagal: {response.get('d', 'Tidak ada detail')}")
                frappe.msgprint(f"Webhook gagal: {response.get('d', 'Tidak ada detail')}")
                continue

            # Pastikan "d" adalah list sebelum diproses
            if not isinstance(response.get("d"), list) or len(response["d"]) == 0:
                frappe.msgprint(f"Tidak ada pembaruan data dalam rentang {from_time} - {to_time}")
                continue

            # Loop setiap payload yang diterima
            for entry in response["d"]:
                if not isinstance(entry, dict) or "payload" not in entry:
                    frappe.logger("webhook").warning(f"Format entry tidak valid: {entry}")
                    continue

                for payload in entry.get("payload", []):
                    try:
                        timestamp_dt = datetime.strptime(payload["timestamp"], "%d/%m/%Y %H:%M:%S")
                        send_timestamp = timestamp_dt.strftime("%Y/%m/%d %H:%M:%S")
                        hash_code = generate_webhook_hash(payload["uuid"], send_timestamp)

                        payload_data = payload.get("data", {})

                        try:
                            exists = frappe.db.exists("AOL Webhook Responses", {"hash_code": hash_code})

                            if not exists:
                                save_webhook_response(payload)
                                new_data_counter += 1

                            frappe.logger("webhook").info(f"Processed webhook: {hash_code}, Exists: {exists}")

                        except Exception as e:
                            frappe.logger("webhook").error(f"Error saat memeriksa hash: {str(e)}")

                    except Exception as e:
                        frappe.logger("webhook").error(f"Kesalahan saat memproses payload: {str(e)}")

        except Exception as e:
            frappe.logger("webhook").critical(f"Gagal mengambil histori webhook dari {from_time} - {to_time}: {str(e)}")
            frappe.msgprint(f"Gagal mengambil histori webhook dari {from_time} - {to_time}: {str(e)}")

    return new_data_counter

@frappe.whitelist()
def sync_webhook():
    """
    Fungsi ini digunakan sebagai trigger untuk melakukan sinkronisasi webhook.
    Mengambil API token, signature secret, dan menjalankan proses webhook sync.
    """
    try:
        # Ambil API token & signature secret
        api_token, signature_secret = get_api_token()

        if not api_token or not signature_secret:
            frappe.throw("API Token atau Signature Secret belum diatur.")

        try:
            # Generate Headers
            headers = generate_headers(api_token, signature_secret)

            # Dapatkan Host API
            host = "https://account.accurate.id/"

            # Proses sinkronisasi webhook
            data_counter = process_webhook_sync(host, headers)
            
            # frappe.throw(f"""Selesai, didapat {data_counter} baris history""")
        
        except Exception as e:
            frappe.logger("webhook").error(f"Gagal memproses webhook: {str(e)}")
            # frappe.throw("Terjadi kesalahan saat memproses webhook. Lihat log untuk detail.")

    except Exception as e:
        frappe.logger("webhook").critical(f"Kesalahan umum pada sync_webhook: {str(e)}")
        frappe.throw("Gagal menjalankan sinkronisasi webhook. Silakan periksa konfigurasi API Token dan Signature Secret.")


def get_webhook_history(host, headers, from_time, to_time):
    """
    Mengambil history webhook
    """
    
    api_url = f"{host}api/webhook-history.do"

    response = requests.get(api_url, headers=headers, params={"from": from_time,
                                                              "to": to_time})
    
    #log_error( from_time + " " + to_time + "" + str(headers) + "" + str(response.text), "test")
    if response.status_code != 200:
        frappe.throw(f"Failed to fetch webhook history: {response.text}")

    return response.json()

def webhook_last_sync_time(new_timestamp: str):
    """
    Menyimpan waktu terakhir sinkronisasi webhook ke site_config.json.
    Hanya memperbarui jika timestamp baru lebih awal dari yang sudah tersimpan.

    :param new_timestamp: Timestamp baru dalam format "YYYY-MM-DD HH:MM:SS"
    """
    try:
        site_config_path = frappe.get_site_path("site_config.json")

        # Baca konfigurasi yang ada
        with open(site_config_path, "r") as f:
            config = json.load(f)

        # Ambil nilai lama jika ada
        old_timestamp = config.get("last_webhook_sync_time")

        if old_timestamp:
            # Konversi string timestamp ke objek datetime
            old_time = datetime.strptime(old_timestamp, "%Y/%m/%d %H:%M:%S")
            new_time = datetime.strptime(new_timestamp, "%Y/%m/%d %H:%M:%S")

            # Jika timestamp baru lebih lama dari yang lama, tidak perlu update
            if new_time <= old_time:
                frappe.logger().info(f"Sinkronisasi diabaikan: {new_timestamp} <= {old_timestamp}")
                return False  # Tidak diperbarui
       
        # Simpan nilai baru ke site_config.json
        config["last_webhook_sync_time"] = new_timestamp
        with open(site_config_path, "w") as f:
            json.dump(config, f, indent=4)

        frappe.logger().info(f"Sinkronisasi diperbarui: {new_timestamp}")
        return True  # Berhasil diperbarui

    except Exception as e:
        frappe.logger().error(f"Error update last sync time: {str(e)}")
        return False

@frappe.whitelist()
def renew_webhook_subcription():
    """
    Mengambil detail dokumen dari Accurate Online.
    """
    try:
        
        headers = get_headers_with_cache()
        host = "https://account.accurate.id"
        
        api_url = f"{host}/api/webhook-renew.do"
        response = requests.get(api_url, headers=headers, params={})
        response.raise_for_status()

    except requests.RequestException as e:
        frappe.throw(f"Gagal mengakses db {database_id} dari Accurate API: {str(e)}")
        
@frappe.whitelist()
def generate_o2c(data_id):
    try:
        def safe_json_loads(data):
            return json.loads(data) if isinstance(data, str) else data

        # Ambil data dari Accurate Online
        so_data = safe_json_loads(fetch_aol_data(generate_so_payload_from_data(data_id)))
        pre_sq_data = generate_sq_payload_from_data(so_data)
        sq_data = safe_json_loads(fetch_aol_data(pre_sq_data))

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
            "sq_release_date": convert_date_format(sq_data.get("d", {}).get("transDate", "")),
            "sq_price": total_sq_price,
            "discount_amount": total_discount,
            "discount": discount_percentage,
            "profit_amount": profit_amount,
            "_profit": (profit_amount / total_cbd_amount) * 100 if total_cbd_amount else 0,
            "sq_final_amount": sq_final_amount,
            "gross_margin": gross_margin,
            "p_gross_margin": p_gross_margin,
            "sales_order_id": doc_id,
            "po_number": so_data.get("d", {}).get("poNumber", ""),
            "so_release_date": convert_date_format(so_data.get("d", {}).get("transDate", "")),
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
        
def generate_so_payload_from_data(data_id):
    try:
        
        return {
            "type": "SALES_ORDER",
            "data": [{"salesOrderId": data_id}]
        }

    except (KeyError, IndexError, TypeError):
        frappe.log_error(frappe.get_traceback() + f"data: {debug_data}" , "Error generate_sq_payload_from_data")
        return None

def generate_sq_payload_from_data(data):
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

def fetch_aol_data(payload):
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
            data_id = data_list[0].get(get_data_id_key(document_type), None)
        elif isinstance(data_list, dict):  # Jika data adalah dictionary langsung
            data_id = data_list.get(get_data_id_key(document_type), None)
        else:
            data_id = None
        
        frappe.log_error("fetch_aol_data", f"data: {data_id} type: {document_type}")

        if not data_id:
            frappe.throw(f"ID tidak ditemukan untuk dokumen '{document_type}'.")
        
        headers = get_headers_with_cache()
        host = "https://public.accurate.id"  # Tidak perlu mengambil dari cache karena sudah pasti
        
        # access_aol_db(host="https://account.accurate.id", headers=headers, database_id=database_id)
    
        return get_data_details(host, headers, data_id, document_type)

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
    
def get_headers_with_cache():
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
    new_headers = get_new_headers()
    frappe.cache().set_value(cache_key, json.dumps({"headers": new_headers, "timestamp": time.time()}))
    return new_headers

def get_new_headers():
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

def get_data_details(host, headers, data_id, data_type):
    """
    Mengambil detail dokumen dari Accurate Online.
    """
    try:
        api_url = f"{host}/accurate/api/{get_data_url(data_type)}/detail.do"
        response = requests.get(api_url, headers=headers, params={"id": data_id})
        response.raise_for_status()

        return response.json()

    except requests.RequestException as e:
        frappe.throw(f"Gagal mengambil detail dokumen dari Accurate API: {str(e)}")
        
def access_aol_db(host, headers, database_id):
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