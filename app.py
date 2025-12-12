from flask import Flask, jsonify

# Import fungsi integrasi PO
from scm_po_to_finance import sync_po_to_finance, sync_paid_back_to_scm

# Import fungsi terima employee & kontrak dari HRM → Finance
from terima_employee import sync_employee_and_contract
from flask import Flask, jsonify

# Import fungsi integrasi PO
from scm_po_to_finance import sync_po_to_finance, sync_paid_back_to_scm

# Import fungsi terima employee & kontrak dari HRM → Finance (kalau mau dipakai)
from terima_employee import sync_employee_and_contract

from apscheduler.schedulers.background import BackgroundScheduler
app = Flask(__name__)

@app.route("/")
def home():
    return "Odoo Integration Service is running"

# -----------------------
# 1️⃣ SYNC PO → FINANCE
# -----------------------
@app.route("/sync/po", methods=["GET", "POST"])
def sync_po():
    try:
        result = sync_po_to_finance()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# -----------------------
# 2️⃣ SYNC PAID → SCM
# -----------------------
@app.route("/sync/paid", methods=["GET"])
def sync_paid():
    try:
        result = sync_paid_back_to_scm()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
# 4) SYNC PAYROLL (ACCOUNTING ENTRY) HRM → FINANCE
@app.route("/sync/payroll", methods=["GET", "POST"], strict_slashes=False)
def sync_payroll():
    if request.method == "GET":
        payrun_name = request.args.get("payrun_name")
    else:
        data = request.get_json(silent=True) or {}
        payrun_name = data.get("payrun_name")

    if not payrun_name:
        return jsonify({
            "ok": False,
            "error": "payrun_name wajib. Contoh: /sync/payroll?payrun_name=Des%202025%20-%20Gaji%20Asli"
        }), 400

    try:
        result = push_payrun_accounting_to_finance(payrun_name)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500



# -----------------------
# 3️⃣ SYNC EMPLOYEE + KONTRAK HRM → FINANCE
# -----------------------
@app.route("/sync/employees", methods=["GET", "POST"])
def sync_employees():
    try:
        result = sync_employee_and_contract()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def scheduled_job():
    """
    Fungsi yang akan dijalankan otomatis tiap 5 menit.
    Di sini kamu bisa atur mau panggil fungsi yang mana saja.
    """
    print("=== AUTO SYNC RUN ===")
    try:
        print("Sync PO → Finance:")
        print(sync_po_to_finance())
    except Exception as e:
        print("Error sync_po_to_finance:", e)

    try:
        print("Sync Paid → SCM:")
        print(sync_paid_back_to_scm())
    except Exception as e:
        print("Error sync_paid_back_to_scm:", e)

    # Kalau mau auto sync employee juga, buka komentar ini:
    # try:
    #     print("Sync Employee & Contract:")
    #     print(sync_employee_and_contract())
    # except Exception as e:
    #     print("Error sync_employee_and_contract:", e)


# Buat scheduler background
scheduler = BackgroundScheduler()
scheduler.add_job(func=scheduled_job, trigger="interval", minutes=5)
scheduler.start()

if __name__ == "__main__":
    try:
        # Jangan pakai debug=True supaya scheduler nggak jalan dua kali
        app.run(host="0.0.0.0", port=5000, debug=False)
    finally:
        scheduler.shutdown()