
import xmlrpc.client

# ==========================
# KONFIGURASI INSTANCE ODOO
# ==========================

# FINANCE (TUJUAN)
FIN_URL = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB  = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"

# HRM (SUMBER DATA KARYAWAN)
HRM_URL  = "https://konstruksi-perumahan2.odoo.com"
HRM_DB   = "konstruksi-perumahan2"
HRM_USER = "fauziah.hamidah.al-2022@ftmm.unair.ac.id"
HRM_KEY  = "169979f5c07f649da80a58132ca9344cdc6d3ada"


# ==========================
# FUNGSI BANTUAN
# ==========================

def connect(url, db, username, key):
    """Konek ke Odoo via XML-RPC dan return (uid, models)."""
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, key.strip(), {})
    if not uid:
        raise Exception(f"Gagal autentikasi ke {url} (user: {username})")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models


def find_one(models, db, uid, key, model, domain):
    """Cari 1 record berdasarkan domain, return id atau False."""
    ids = models.execute_kw(
        db, uid, key,
        model, "search",
        [domain],
        {"limit": 1}
    )
    return ids[0] if ids else False


def upsert_by_name(models, db, uid, key, model, name_field, name_value):
    """
    Cari record berdasarkan name_field = name_value.
    Kalau belum ada → create baru. Return ID (atau False kalau name kosong).
    Dipakai untuk:
      - hr.department
      - hr.job
      - hr.contract.type
    """
    if not name_value:
        return False

    rec_id = find_one(models, db, uid, key, model, [(name_field, "=", name_value)])
    if rec_id:
        return rec_id

    return models.execute_kw(
        db, uid, key,
        model, "create",
        [{name_field: name_value}]
    )


# ==========================
# AMBIL DATA DARI HRM
# ==========================

def get_hrm_employees(hrm_models, db, uid, key):
    """
    Ambil daftar karyawan aktif dari HRM.
    Sekalian bawa field payroll yang tertanam di hr.employee:
        contract_date_start, wage, wage_type, contract_type_id, employee_type
    """
    emp_ids = hrm_models.execute_kw(
        db, uid, key,
        "hr.employee", "search",
        [[("active", "=", True)]]
    )
    if not emp_ids:
        return []

    employees = hrm_models.execute_kw(
        db, uid, key,
        "hr.employee", "read",
        [emp_ids],
        {
            "fields": [
                "name",
                "identification_id",
                "work_email",
                "work_phone",
                "mobile_phone",
                "department_id",
                "job_id",
                "company_id",
                "birthday",
                "active",
                # --- FIELD PAYROLL DI HR.EMPLOYEE ---
                "contract_date_start",
                "wage",
                "wage_type",
                "contract_type_id",
                "employee_type",
            ]
        }
    )
    return employees


# ==========================
# FUNGSI UTAMA SYNC
# ==========================

def sync_employee_and_contract():
    """
    Sinkron karyawan dari HRM → Finance, termasuk field payroll yang ada di hr.employee:

    - Identity / kerja:
        name, identification_id, work_email, work_phone, mobile_phone,
        department_id (by name), job_id (by name), company_id (by name), work_location, active.
    - Payroll di hr.employee:
        contract_date_start, wage, wage_type, contract_type_id (by name), employee_type.

    Matching employee:
      1) identification_id
      2) work_email
      3) name
    """

    # 1) Connect ke kedua instance
    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    hrm_uid, hrm_models = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)

    # 2) Ambil employees dari HRM
    employees = get_hrm_employees(hrm_models, HRM_DB, hrm_uid, HRM_KEY)
    if not employees:
        return {"message": "Tidak ada employee aktif di HRM untuk disinkron."}

    created_emp = 0
    updated_emp = 0

    # 3) Loop per employee
    for emp in employees:
        name = emp.get("name")
        identification_id = emp.get("identification_id")
        work_email = emp.get("work_email")

        # --- Tentukan domain pencarian employee di Finance ---
        if identification_id:
            domain = [("identification_id", "=", identification_id)]
        elif work_email:
            domain = [("work_email", "=", work_email)]
        else:
            domain = [("name", "=", name)]

        fin_emp_ids = fin_models.execute_kw(
            FIN_DB, fin_uid, FIN_KEY,
            "hr.employee", "search",
            [domain],
            {"limit": 1}
        )

        # --- Mapping department & job (berdasarkan nama) ---
        department_id = False
        if emp.get("department_id"):
            dept_name = emp["department_id"][1]
            department_id = upsert_by_name(
                fin_models, FIN_DB, fin_uid, FIN_KEY,
                "hr.department", "name", dept_name
            )

        job_id = False
        if emp.get("job_id"):
            job_name = emp["job_id"][1]
            job_id = upsert_by_name(
                fin_models, FIN_DB, fin_uid, FIN_KEY,
                "hr.job", "name", job_name
            )

        # company_id tidak dibuat otomatis, hanya dicari berdasarkan nama
        company_id = False
        if emp.get("company_id"):
            comp_name = emp["company_id"][1]
            company_id = find_one(
                fin_models, FIN_DB, fin_uid, FIN_KEY,
                "res.company", [("name", "=", comp_name)]
            )

        # --- Mapping contract_type_id di employee (many2one -> hr.contract.type) ---
        contract_type_id = False
        if emp.get("contract_type_id"):
            ct_name = emp["contract_type_id"][1]
            contract_type_id = upsert_by_name(
                fin_models, FIN_DB, fin_uid, FIN_KEY,
                "hr.contract.type", "name", ct_name
            )

        # --- Nilai-nilai hr.employee yang akan disimpan di Finance ---
        emp_vals = {
            "name": name,
            "identification_id": identification_id or False,
            "work_email": work_email or False,
            "work_phone": emp.get("work_phone") or False,
            "mobile_phone": emp.get("mobile_phone") or False,
            "department_id": department_id or False,
            "job_id": job_id or False,
            "active": emp.get("active", True),
            # --- FIELD PAYROLL DI HR.EMPLOYEE ---
            "contract_date_start": emp.get("contract_date_start") or False,
            "wage": emp.get("wage") or 0.0,
            "wage_type": emp.get("wage_type") or False,
            "employee_type": emp.get("employee_type") or False,
            "contract_type_id": contract_type_id or False,
        }
        if company_id:
            emp_vals["company_id"] = company_id

        # --- Create / Update employee di Finance ---
        if fin_emp_ids:
            fin_emp_id = fin_emp_ids[0]
            fin_models.execute_kw(
                FIN_DB, fin_uid, FIN_KEY,
                "hr.employee", "write",
                [[fin_emp_id], emp_vals]
            )
            updated_emp += 1
        else:
            fin_emp_id = fin_models.execute_kw(
                FIN_DB, fin_uid, FIN_KEY,
                "hr.employee", "create",
                [emp_vals]
            )
            created_emp += 1

    # Return ringkasan
    return {
        "created_employees": created_emp,
        "updated_employees": updated_emp,
        "message": "Sync employee HRM → Finance (termasuk field payroll di hr.employee) selesai.",
    }