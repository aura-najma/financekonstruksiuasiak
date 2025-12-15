from flask import Flask, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from zoneinfo import ZoneInfo

import traceback
# ==========================
# IMPORT MODUL INTEGRASI
# ==========================
from scm_po_to_finance import sync_po_to_finance, sync_paid_back_to_scm #buat beli material (accounting)
from bayar_vendor_tukang import sync_po_tukang_to_finance, sync_paid_back_to_hrm #buat bayar vendor dan tukang (accounting)
from terima_employee import sync_employee_and_contract #buat terima employee dari HRM ke Finance (untuk payroll dan nanti struktur gaji ikut kode alden)
from shipping_costs import sync_internal_transfer_to_finance_expenses, sync_paid_expenses_note_back_to_scm #buat ongkir (expenses)
from sync_hrm_work_entry_to_finance import sync_hrm_work_entries_to_finance #nanti ini pake dari alden aja buat ngambil work entries dari HRM ke Finance (untuk payroll)
from notify_hrm import run_latest_paid
from update_task_hrm import run_update_task_to_hrm
# ==========================
# FLASK APP
# ==========================
app = Flask(__name__)


# ==========================
# RESPONSE HELPERS
# ==========================
def ok(result=None):
    if isinstance(result, dict):
        return jsonify({"ok": True, **result})
    return jsonify({"ok": True, "result": result})


def err(e):
    return jsonify({"ok": False, "error": str(e)}), 500
def to_bool(v, default=False):
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "y", "on")


# ==========================
# ROOT
# ==========================
@app.route("/", methods=["GET"])
def home():
    return "Odoo Integration Service is running"


# ==========================
# SCM → FINANCE (PO)
# ==========================
@app.route("/sync/po", methods=["GET", "POST"])
def route_sync_po():
    try:
        return ok(sync_po_to_finance())
    except Exception as e:
        return err(e)


# ==========================
# FINANCE/SCM: PAID → SCM
# ==========================
@app.route("/sync/paid", methods=["GET"])
def route_sync_paid():
    try:
        return ok(sync_paid_back_to_scm())
    except Exception as e:
        return err(e)


# ==========================
# HRM: EMPLOYEE → FINANCE
# ==========================
@app.route("/sync/employees", methods=["GET", "POST"])
def route_sync_employees():
    try:
        return ok(sync_employee_and_contract())
    except Exception as e:
        return err(e)


# ==========================
# HRM: PO → FINANCE (VENDOR/TUKANG)
# ==========================
@app.route("/sync/hrm/po", methods=["GET", "POST"])
def route_sync_hrm_po():
    try:
        return ok(sync_po_tukang_to_finance())
    except Exception as e:
        return err(e)


# ==========================
# HRM: PAID → HRM
# ==========================
@app.route("/sync/hrm/paid", methods=["GET"])
def route_sync_hrm_paid():
    try:
        return ok(sync_paid_back_to_hrm())
    except Exception as e:
        return err(e)
@app.route("/sync/shipping/create", methods=["GET"])
def route_shipping_create():
    try:
        limit = int(request.args.get("limit", 50))
        return jsonify({"ok": True, **sync_internal_transfer_to_finance_expenses(limit=limit)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/sync/shipping/paid-note", methods=["GET"])
def route_shipping_paid_note():
    try:
        limit = int(request.args.get("limit", 200))
        return jsonify({"ok": True, **sync_paid_expenses_note_back_to_scm(limit=limit)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
@app.route("/sync/hrm/work-entries", methods=["GET"])
def route_sync_hrm_work_entries():
    try:
        date_from = request.args.get("date_from", "2025-01-01")
        date_to   = request.args.get("date_to", "2027-01-01")
        batch_size = int(request.args.get("batch_size", 500))
        dry_run = request.args.get("dry_run", "0") in ("1", "true")

        return jsonify({
            "ok": True,
            **sync_hrm_work_entries_to_finance(
                date_from=date_from,
                date_to=date_to,
                batch_size=batch_size,
                dry_run=dry_run
            )
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    
@app.route("/notify/hrm/payrun-closed/latest", methods=["GET"])
def route_notify_latest_payrun_closed():
    try:
        res = notify_latest_payrun_closed()
        return jsonify({"ok": True, **res})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route("/notify/hrm/payrun-03-paid", methods=["GET"])
def route_notify_payrun_03_paid():
    try:
        result = notify_latest_payrun_03_paid()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/sync/hrm/payroll/latest-paid", methods=["GET"])
def route_sync_latest_paid():
    try:
        dry_run = request.args.get("dry_run", "0") in ("1", "true", "True", "yes")
        do_patch = request.args.get("patch", "1") in ("1", "true", "True", "yes")

        result = run_latest_paid(   # ⬅️ LANGSUNG
            dry_run=dry_run,
            do_patch=do_patch
        )
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }), 500

@app.route("/sync/hrm_tasks", methods=["GET"])
def route_sync_fin_to_hrm_tasks():
    dry_run = to_bool(request.args.get("dry_run"), False)
    sync_timesheets = to_bool(request.args.get("sync_timesheets"), True)
    sync_description = to_bool(request.args.get("sync_description"), True)
    sync_status = to_bool(request.args.get("sync_status"), True)

    project = request.args.get("project")  # optional
    only_project_names = [project] if project else ["Project Perumahan 1"]

    result = run_update_task_to_hrm(
        dry_run=dry_run,
        only_project_names=only_project_names,
        sync_timesheets=sync_timesheets,
        sync_description=sync_description,
        sync_status=sync_status,
    )
    return jsonify(result), (200 if result.get("ok") else 500)
# =====================================================
# AUTO SCHEDULER — JALAN SETIAP 3 MENIT
# =====================================================
def scheduled_sync_all():
    print("=== AUTO SYNC RUN (3 MINUTES) ===")

    try:
        print("[AUTO] SCM → Finance (PO)")
        print(sync_po_to_finance())
    except Exception as e:
        print("[ERROR] sync_po_to_finance:", e)

    try:
        print("[AUTO] Paid → SCM")
        print(sync_paid_back_to_scm())
    except Exception as e:
        print("[ERROR] sync_paid_back_to_scm:", e)

    try:
        print("[AUTO] HRM → Finance (Employee)")
        print(sync_employee_and_contract())
    except Exception as e:
        print("[ERROR] sync_employee_and_contract:", e)

    try:
        print("[AUTO] HRM PO → Finance (Vendor/Tukang)")
        print(sync_po_tukang_to_finance())
    except Exception as e:
        print("[ERROR] sync_po_tukang_to_finance:", e)

    try:
        print("[AUTO] Paid → HRM")
        print(sync_paid_back_to_hrm())
    except Exception as e:
        print("[ERROR] sync_paid_back_to_hrm:", e)

    try:
        print("[AUTO] SCM Internal Transfer DONE → Finance Expense (Shipping)")
        # limit boleh kamu atur
        print(sync_internal_transfer_to_finance_expenses(limit=50))
    except Exception as e:
        print("[ERROR] sync_internal_transfer_to_finance_expenses:", e)

    try:
        print("[AUTO] Finance Expense PAID → SCM Log Note (Shipping Paid)")
        print(sync_paid_expenses_note_back_to_scm(limit=200))
    except Exception as e:
        print("[ERROR] sync_paid_expenses_note_back_to_scm:", e)

    try:
        print("[AUTO] Finance Payroll (Latest Paid Payrun) → HRM Payslips")
        # scheduler biasanya non-dry agar benar2 jalan
        # patch=True biar link payslip-run + company_id kebeneran
        res = run_latest_paid(dry_run=False, do_patch=True)
        print(res)
    except Exception as e:
        print("[ERROR] run_latest_paid:", e)
        print(traceback.format_exc())

    try:
        print("[AUTO] FIN → HRM (Tasks/Stages/Projects/Timesheets)")
        result = run_update_task_to_hrm(
            dry_run=False,
            only_project_names=["Project Perumahan 1"],   # atau [] untuk semua project aktif
            sync_timesheets=True,
            sync_description=True,
            sync_status=True,
        )
        print("[AUTO] FIN → HRM result:", result.get("ok"), "| projects:", result.get("projects_processed"))
        if not result.get("ok"):
            print("[AUTO][ERROR] FIN → HRM trace:\n", result.get("trace"))
    except Exception as e:
        print("[ERROR] run_update_task_to_hrm:", e)
# ==========================
# START SCHEDULER
# ==========================
scheduler = BackgroundScheduler()
scheduler.add_job(
    func=scheduled_sync_all,
    trigger="interval",
    minutes=3,
    id="auto_sync_all",
    replace_existing=True
)
scheduler.start()


# ==========================
# MAIN
# ==========================
if __name__ == "__main__":
    try:
        # debug=False → supaya scheduler tidak jalan dua kali
        app.run(host="0.0.0.0", port=5000, debug=False)
    finally:
        scheduler.shutdown()
