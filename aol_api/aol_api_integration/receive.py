import frappe
import json

@frappe.whitelist(allow_guest=True)  # allow_guest=True jika API dapat diakses tanpa login
def aol_auth_callback(code):
    """
    Menyimpan access_code ke Single Doctype AOL API Settings
    Args:
        access_code (str): Kode akses yang akan disimpan
    """
    if not code:
        frappe.throw("Parameter 'access_code' diperlukan")
    
    # Pastikan Single Doctype AOL API Settings ada
    if not frappe.db.exists("DocType", "AOL API Settings"):
        frappe.throw("Doctype 'AOL API Settings' tidak ditemukan")
    
    # Simpan access_code ke Single Doctype
    frappe.db.set_value("AOL API Settings", None, "access_code", code)
    frappe.db.commit()  # Pastikan perubahan disimpan ke database
    """
    return {
        "status": "success",
        "message": "Access code berhasil disimpan",
        "data": {
            "access_code": access_code
        }
    }
	"""
    # Respons HTML dengan JavaScript untuk menutup window
    success_message = f"Access code '{code}' berhasil diterima!"
    html_response = f"""
        <html>
        <head>
            <script>
                alert("{success_message}");
                window.close();  // Menutup jendela browser
            </script>
        </head>
        <body>
            <p>{success_message}</p>
        </body>
        </html>
    """
    frappe.respond_as_web_page(
        title="Access Code Berhasil",
        html=html_response,
        indicator_color="green"
    )

@frappe.whitelist(allow_guest=True)  # allow_guest=True agar dapat menerima data dari Accurate Online tanpa autentikasi
def webhook():
    """
    Menerima data webhook dari Accurate Online dan menyimpannya ke Doctype AOL Webhook Responses.
    """
    # Ambil data dari request (POST payload)
    try:
        data = frappe.request.get_data(as_text=True)  # Dapatkan data dalam format JSON
        if not data:
            frappe.throw("Data kosong, tidak ada yang dikirim oleh webhook")
        
        # Simpan data ke Doctype
        new_response = frappe.get_doc({
            "doctype": "AOL Webhook Responses",
            "responses": data  # Simpan data ke field "responses"
        })
        new_response.insert(ignore_permissions=True)  # Simpan tanpa memeriksa permission
        frappe.db.commit()  # Commit untuk memastikan data tersimpan
        
        return {
            "status": "success",
            "message": "Data berhasil disimpan",
            "data": json.loads(data)  # Kembalikan data asli sebagai respons
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Webhook Error")
        return {
            "status": "error",
            "message": str(e)
        }