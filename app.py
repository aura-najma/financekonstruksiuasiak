from flask import Flask, jsonify

# Import fungsi integrasi PO
from scm_po_to_finance import sync_po_to_finance, sync_paid_back_to_scm

# Import fungsi terima employee & kontrak dari HRM → Finance
from terima_employee import sync_employee_and_contract

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

if __name__ == "__main__":
    app.run(debug=True, port=5000)
