import os

import frappe
import json
import requests

import hmac
import hashlib
import base64

import traceback

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

@frappe.whitelist()
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

@frappe.whitelist()
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

@frappe.whitelist()
def get_api_token():
    # Ambil API Token & Signature dari Doctype "AOL API Settings"
    settings = frappe.get_doc("AOL API Settings")
    api_token = settings.api_token
    signature_secret = settings.signature_secret
    
    return api_token, signature_secret

def save_webhook_response(json_data):
    """
    Menyimpan data webhook ke dalam Doctype 'AOL Webhook Responses'.

    Args:
        json_data (list): Data webhook dalam bentuk JSON list yang berisi payload.

    Returns:
        None
    """
    try:
        # Pastikan JSON memiliki elemen
        if not json_data:
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
        # Format datetime sesuai "DD/MM/YYYY HH:MM:SS"
        from_time = start_time.strftime("%d/%m/%Y %H:%M:%S")
        to_time = end_time.strftime("%d/%m/%Y %H:%M:%S")

        try:
            # Ambil histori webhook dari API
            response = get_webhook_history(host, headers, from_time=from_time, to_time=to_time)

            if len(response["d"]) == 0:
                frappe.msgprint(f"Tidak ada data webhook dalam rentang {from_time} - {to_time}")
                continue

            # Loop setiap payload yang diterima
            for entry in response["d"]:
                for payload in entry.get("payload", []):
                    hash_code = ""
                    exists = ""
                    try:
                        # Buat hash unik untuk 
                        
                        timestamp_dt = datetime.strptime(payload["timestamp"], "%d/%m/%Y %H:%M:%S")
                        send_timestamp = timestamp_dt.strftime("%Y/%m/%d %H:%M:%S")
                        hash_code = generate_webhook_hash(payload["uuid"], send_timestamp)
                        try:
                            # Cek apakah hash sudah ada di Doctype
                            #exists = frappe.get_doc("AOL Webhook Responses", hash_code)
                            exists = frappe.db.get_values("AOL Webhook Responses", {"hash_code": hash_code})
                            
                            if len(exists) < 1:
                                # Simpan data jika hash belum ada
                                save_webhook_response(payload)
                                new_data_counter += 1
                        
                                frappe.log_error(f"Debug Exists: {exists} data count {new_data_counter}", f"Webhook Debugging \n hash {hash_code} type {type(exists)} len {len(exists)} \n {payload}")
                                
                        except Exception as e:
                            frappe.logger("webhook").info(f"Error Debugging Exists: {str(e)}", "Webhook Debugging Error")
                            
                    except Exception as e:
                        frappe.logger("webhook").error(f"Kesalahan saat memproses payload: {str(e)}")

            return new_data_counter
        except Exception as e:
            frappe.logger("webhook").critical(f"Gagal mengambil histori webhook dari {from_time} - {to_time}: {str(e)}")
            frappe.msgprint(f"Gagal mengambil histori webhook dari {from_time} - {to_time}: {str(e)}")
            
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
        
            webhook_last_sync_time(frappe.utils.now_datetime)
        
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