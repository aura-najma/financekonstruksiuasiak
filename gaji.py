import xmlrpc.client

# ==========================
# KONFIGURASI INSTANCE ODOO
# ==========================

FIN_URL = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB  = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"

HRM_URL  = "https://konstruksi-perumahan2.odoo.com"
HRM_DB   = "konstruksi-perumahan2"
HRM_USER = "fauziah.hamidah.al-2022@ftmm.unair.ac.id"
HRM_KEY  = "169979f5c07f649da80a58132ca9344cdc6d3ada"

REF_PREFIX = "HRM:"
SALARY_STRUCTURE_NAME = "Gaji Asli"  # <- sesuai yang kamu bilang

# ==========================
# XMLRPC CONNECT
# ==========================
def connect(url, db, user, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, key, {})
    if not uid:
        raise Exception(f"Gagal login: {db}")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models

def find_one(models, db, uid, key, model, domain, fields=None):
    ids = models.execute_kw(db, uid, key, model, "search", [domain], {"limit": 1})
    if not ids:
        return False
    if fields:
        rec = models.execute_kw(db, uid, key, model, "read", [ids], {"fields": fields})[0]
        return rec
    return ids[0]

# ==========================
# HRM HELPERS
# ==========================
def list_payruns_hrm(limit=20):
    uid, hrm = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)
    ids = hrm.execute_kw(
        HRM_DB, uid, HRM_KEY,
        "hr.payslip.run", "search",
        [[("state", "=", "02_close")]],
        {"order": "id desc", "limit": limit}
    )
    return hrm.execute_kw(
        HRM_DB, uid, HRM_KEY,
        "hr.payslip.run", "read",
        [ids],
        {"fields": ["id", "name", "date_start", "date_end", "state"]}
    )

def get_payslips_by_payrun_id(payrun_id):
    uid, hrm = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)

    slip_ids = hrm.execute_kw(
        HRM_DB, uid, HRM_KEY,
        "hr.payslip", "search",
        [[("payslip_run_id", "=", payrun_id)]]
    )
    if not slip_ids:
        return []

    return hrm.execute_kw(
        HRM_DB, uid, HRM_KEY,
        "hr.payslip", "read",
        [slip_ids],
        # tambahin contract_id biar bisa ambil contract_date_start
        {"fields": ["id", "name", "employee_id", "contract_id", "date_from", "date_to", "state"]}
    )

def get_contract_date_start_hrm(contract_id):
    """Ambil contract_date_start dari HRM."""
    if not contract_id:
        return None
    uid, hrm = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)
    rec = hrm.execute_kw(
        HRM_DB, uid, HRM_KEY,
        "hr.contract", "read",
        [[contract_id]],
        {"fields": ["contract_date_start", "date_start", "name"]}
    )[0]
    # prioritas contract_date_start, fallback date_start
    return rec.get("contract_date_start") or rec.get("date_start")

# ==========================
# FINANCE PAYROLL HELPERS
# ==========================
def fin_get_structure_id(fin_uid, fin_models):
    sid = find_one(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "hr.payroll.structure",
        [("name", "=", SALARY_STRUCTURE_NAME)]
    )
    if not sid:
        raise Exception(f"Payroll Structure '{SALARY_STRUCTURE_NAME}' tidak ditemukan di Finance.")
    return sid

def fin_upsert_employee(fin_uid, fin_models, employee_name):
    # minimal pakai name; kalau di dataset kamu punya work_email / identification_id, lebih bagus buat matching
    emp_id = find_one(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "hr.employee", [("name", "=", employee_name)]
    )
    if emp_id:
        return emp_id

    return fin_models.execute_kw(
        FIN_DB, fin_uid, FIN_KEY,
        "hr.employee", "create",
        [{"name": employee_name}]
    )

def fin_upsert_contract(fin_uid, fin_models, emp_id, contract_date_start):
    # cari contract aktif untuk employee
    contract_id = find_one(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "hr.contract", [("employee_id", "=", emp_id)],
    )
    if contract_id:
        return contract_id

    # buat contract baru (minimal fields)
    vals = {
        "name": "Auto Contract (from HRM)",
        "employee_id": emp_id,
        # gunakan contract_date_start -> date_start
        "date_start": contract_date_start,
        # state biasanya 'open' untuk aktif (tergantung versi Odoo)
        "state": "open",
    }
    return fin_models.execute_kw(
        FIN_DB, fin_uid, FIN_KEY,
        "hr.contract", "create",
        [vals]
    )

def fin_payslip_exists(fin_uid, fin_models, ref):
    ids = fin_models.execute_kw(
        FIN_DB, fin_uid, FIN_KEY,
        "hr.payslip", "search",
        [[("name", "=", ref)]],  # kita pakai name sebagai ref unik (biar gampang)
        {"limit": 1}
    )
    return bool(ids)

# ==========================
# CORE: PAYRUN → FINANCE (CREATE PAYSLIP)
# ==========================
def push_all_closed_payruns_to_finance_as_payslips(limit_payruns=20, compute=True, mark_done=False):
    runs = list_payruns_hrm(limit_payruns)

    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    structure_id = fin_get_structure_id(fin_uid, fin_models)

    summary = []

    for run in runs:
        slips = get_payslips_by_payrun_id(run["id"])
        created = skipped = 0

        for slip in slips:
            if slip.get("state") not in ("validated", "done", "paid"):
                skipped += 1
                continue

            emp_name = slip["employee_id"][1] if slip.get("employee_id") else "UNKNOWN"
            ref = f"{REF_PREFIX}|PAYRUN-{run['id']}|SLIP-{slip['id']}|{emp_name}"

            # cegah duplikasi
            if fin_payslip_exists(fin_uid, fin_models, ref):
                skipped += 1
                continue

            # employee
            fin_emp_id = fin_upsert_employee(fin_uid, fin_models, emp_name)

            # contract date start dari HRM contract
            hrm_contract_id = slip["contract_id"][0] if slip.get("contract_id") else None
            contract_date_start = get_contract_date_start_hrm(hrm_contract_id)
            if not contract_date_start:
                # fallback: pakai date_from payslip kalau contract_date_start kosong
                contract_date_start = slip.get("date_from")

            fin_contract_id = fin_upsert_contract(fin_uid, fin_models, fin_emp_id, contract_date_start)

            # create payslip di Finance
            payslip_vals = {
                "name": ref,  # dipakai sebagai identitas unik
                "employee_id": fin_emp_id,
                "contract_id": fin_contract_id,
                "struct_id": structure_id,
                "date_from": slip.get("date_from"),
                "date_to": slip.get("date_to"),
            }

            fin_payslip_id = fin_models.execute_kw(
                FIN_DB, fin_uid, FIN_KEY,
                "hr.payslip", "create",
                [payslip_vals]
            )

            # hitung sheet supaya payslip lines ke-generate
            if compute:
                fin_models.execute_kw(
                    FIN_DB, fin_uid, FIN_KEY,
                    "hr.payslip", "compute_sheet",
                    [[fin_payslip_id]]
                )

            # optional: set done (tergantung versi, methodnya bisa beda)
            if mark_done:
                # seringnya: action_payslip_done
                fin_models.execute_kw(
                    FIN_DB, fin_uid, FIN_KEY,
                    "hr.payslip", "action_payslip_done",
                    [[fin_payslip_id]]
                )

            created += 1

        summary.append({
            "payrun_id": run["id"],
            "payrun_name": run["name"],
            "total_slips": len(slips),
            "created_payslips": created,
            "skipped": skipped
        })

    return summary
