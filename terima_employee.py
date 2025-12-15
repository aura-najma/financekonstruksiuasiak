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
      - hr.payroll.structure.type
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


def has_diff(old_vals: dict, new_vals: dict) -> bool:
    """
    Return True jika ada perbedaan nilai antara old dan new.
    - many2one di old biasanya [id, "Name"] → dibandingkan pakai id.
    """
    for k, v in new_vals.items():
        if k not in old_vals:
            continue

        old_v = old_vals.get(k)

        # many2one format: [id, name]
        if isinstance(old_v, list) and len(old_v) == 2:
            old_v = old_v[0]

        if old_v != v:
            return True
    return False


# ==========================
# BANK ACCOUNT HELPERS
# ==========================

def read_hrm_bank_accounts(hrm_models, db, uid, key, bank_ids):
    """Read res.partner.bank records dari HRM."""
    if not bank_ids:
        return []

    return hrm_models.execute_kw(
        db, uid, key,
        "res.partner.bank", "read",
        [bank_ids],
        {"fields": ["acc_number", "acc_holder_name", "bank_id"]}
    )


def get_fin_employee_partner_id(fin_models, db, uid, key, fin_emp_id):
    """
    Ambil partner yang dipakai untuk bank account domain di employee:
    - prioritas work_contact_id
    """
    rec = fin_models.execute_kw(
        db, uid, key,
        "hr.employee", "read",
        [[fin_emp_id]],
        {"fields": ["work_contact_id"]}
    )
    if not rec:
        return False

    work_contact = rec[0].get("work_contact_id")

    if work_contact:
        return work_contact[0]

    return False


def upsert_res_bank(fin_models, db, uid, key, bank_name):
    """Create/search res.bank di Finance by name."""
    if not bank_name:
        return False
    bank_id = find_one(fin_models, db, uid, key, "res.bank", [("name", "=", bank_name)])
    if bank_id:
        return bank_id
    return fin_models.execute_kw(
        db, uid, key,
        "res.bank", "create",
        [{"name": bank_name}]
    )


def upsert_partner_bank(fin_models, db, uid, key, partner_id, acc_number, acc_holder_name, bank_id):
    """
    Upsert res.partner.bank di Finance:
    unik by (partner_id, acc_number)
    """
    if not partner_id or not acc_number:
        return False

    existing = fin_models.execute_kw(
        db, uid, key,
        "res.partner.bank", "search",
        [[("partner_id", "=", partner_id), ("acc_number", "=", acc_number)]],
        {"limit": 1}
    )

    vals = {
        "acc_holder_name": acc_holder_name or False,
        "bank_id": bank_id or False,
    }

    if existing:
        fin_models.execute_kw(db, uid, key, "res.partner.bank", "write", [existing, vals])
        return existing[0]

    vals_create = {
        "partner_id": partner_id,
        "acc_number": acc_number,
        **vals
    }
    return fin_models.execute_kw(db, uid, key, "res.partner.bank", "create", [vals_create])


def sync_employee_bank_accounts(hrm_models, fin_models, hrm_uid, fin_uid, fin_emp_id, hrm_bank_ids):
    """
    Sinkron bank_account_ids: HRM res.partner.bank → Finance res.partner.bank,
    lalu link ke hr.employee.bank_account_ids.
    Return True jika ada perubahan.
    """
    if not hrm_bank_ids:
        # kalau HRM kosong, kita tidak auto hapus di Finance (biar aman)
        return False

    partner_id = get_fin_employee_partner_id(fin_models, FIN_DB, fin_uid, FIN_KEY, fin_emp_id)
    if not partner_id:
        # gak bisa link kalau employee belum punya partner
        return False

    hrm_banks = read_hrm_bank_accounts(hrm_models, HRM_DB, hrm_uid, HRM_KEY, hrm_bank_ids)
    fin_bank_account_ids = []

    for b in hrm_banks:
        acc_number = b.get("acc_number")
        acc_holder = b.get("acc_holder_name")

        fin_res_bank_id = False
        if b.get("bank_id"):
            bank_name = b["bank_id"][1]
            fin_res_bank_id = upsert_res_bank(fin_models, FIN_DB, fin_uid, FIN_KEY, bank_name)

        fin_partner_bank_id = upsert_partner_bank(
            fin_models, FIN_DB, fin_uid, FIN_KEY,
            partner_id=partner_id,
            acc_number=acc_number,
            acc_holder_name=acc_holder,
            bank_id=fin_res_bank_id
        )
        if fin_partner_bank_id:
            fin_bank_account_ids.append(fin_partner_bank_id)

    if not fin_bank_account_ids:
        return False

    # bandingin dulu bank_account_ids lama di employee Finance
    old_emp = fin_models.execute_kw(
        FIN_DB, fin_uid, FIN_KEY,
        "hr.employee", "read",
        [[fin_emp_id]],
        {"fields": ["bank_account_ids"]}
    )
    old_bank_ids = set(old_emp[0].get("bank_account_ids") or [])
    new_bank_ids = set(fin_bank_account_ids)

    if old_bank_ids == new_bank_ids:
        return False

    # set M2M = persis daftar baru
    fin_models.execute_kw(
        FIN_DB, fin_uid, FIN_KEY,
        "hr.employee", "write",
        [[fin_emp_id], {"bank_account_ids": [(6, 0, fin_bank_account_ids)]}]
    )
    return True


# ==========================
# AMBIL DATA DARI HRM
# ==========================

def get_hrm_employees(hrm_models, db, uid, key):
    """
    Ambil daftar karyawan aktif dari HRM.
    Sekalian bawa field payroll & 2 field tambahan:
    - structure_type_id
    - bank_account_ids

    PLUS: Informasi Pribadi
    - birthday
    - place_of_birth
    - sex
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
                "active",

                # ✅ Informasi Pribadi (ditambahkan)
                "birthday",
                "place_of_birth",
                "sex",

                "contract_date_start",
                "wage",
                "wage_type",
                "contract_type_id",
                "employee_type",

                "structure_type_id",
                "bank_account_ids",
            ]
        }
    )
    return employees


# ==========================
# FUNGSI UTAMA SYNC
# ==========================

def sync_employee_and_contract():
    """
    Sinkron karyawan dari HRM → Finance, termasuk:
    - structure_type_id (Salary Structure Type)
    - bank_account_ids (Employee bank accounts to pay salaries)
    - birthday, place_of_birth, sex (Informasi Pribadi)

    updated_employees hanya bertambah jika ada perubahan data nyata.
    """

    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    hrm_uid, hrm_models = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)

    employees = get_hrm_employees(hrm_models, HRM_DB, hrm_uid, HRM_KEY)
    if not employees:
        return {"message": "Tidak ada employee aktif di HRM untuk disinkron."}

    created_emp = 0
    updated_emp = 0
    still_synced = 0

    # Loop per employee
    for emp in employees:
        name = emp.get("name")
        identification_id = emp.get("identification_id")
        work_email = emp.get("work_email")

        # Tentukan domain pencarian employee di Finance
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

        # Mapping department & job (berdasarkan nama)
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

        # Mapping contract_type_id (hr.contract.type)
        contract_type_id = False
        if emp.get("contract_type_id"):
            ct_name = emp["contract_type_id"][1]
            contract_type_id = upsert_by_name(
                fin_models, FIN_DB, fin_uid, FIN_KEY,
                "hr.contract.type", "name", ct_name
            )

        # Mapping structure_type_id (hr.payroll.structure.type)
        structure_type_id = False
        if emp.get("structure_type_id"):
            st_name = emp["structure_type_id"][1]
            structure_type_id = upsert_by_name(
                fin_models, FIN_DB, fin_uid, FIN_KEY,
                "hr.payroll.structure.type", "name", st_name
            )

        # Data yang akan disimpan di Finance
        emp_vals = {
            "name": name,
            "identification_id": identification_id or False,
            "work_email": work_email or False,
            "work_phone": emp.get("work_phone") or False,
            "mobile_phone": emp.get("mobile_phone") or False,
            "department_id": department_id or False,
            "job_id": job_id or False,
            "active": emp.get("active", True),

            # ✅ Informasi Pribadi (ditambahkan)
            "birthday": emp.get("birthday") or False,
            "place_of_birth": emp.get("place_of_birth") or False,
            "sex": emp.get("sex") or False,

            "contract_date_start": emp.get("contract_date_start") or False,
            "wage": emp.get("wage") or 0.0,
            "wage_type": emp.get("wage_type") or False,
            "employee_type": emp.get("employee_type") or False,
            "contract_type_id": contract_type_id or False,

            "structure_type_id": structure_type_id or False,
        }
        if company_id:
            emp_vals["company_id"] = company_id

        hrm_bank_ids = emp.get("bank_account_ids") or []

        bank_changed = False
        employee_changed = False

        if fin_emp_ids:
            fin_emp_id = fin_emp_ids[0]

            # read old data untuk compare
            old_data = fin_models.execute_kw(
                FIN_DB, fin_uid, FIN_KEY,
                "hr.employee", "read",
                [[fin_emp_id]],
                {"fields": list(emp_vals.keys())}
            )

            if old_data and has_diff(old_data[0], emp_vals):
                fin_models.execute_kw(
                    FIN_DB, fin_uid, FIN_KEY,
                    "hr.employee", "write",
                    [[fin_emp_id], emp_vals]
                )
                employee_changed = True

            # sync bank accounts (smart compare inside)
            bank_changed = sync_employee_bank_accounts(
                hrm_models, fin_models, hrm_uid, fin_uid, fin_emp_id, hrm_bank_ids
            )

            if employee_changed or bank_changed:
                updated_emp += 1
            else:
                still_synced += 1

        else:
            # create
            fin_emp_id = fin_models.execute_kw(
                FIN_DB, fin_uid, FIN_KEY,
                "hr.employee", "create",
                [emp_vals]
            )
            created_emp += 1

            # setelah create, coba sync bank (kalau ada)
            sync_employee_bank_accounts(
                hrm_models, fin_models, hrm_uid, fin_uid, fin_emp_id, hrm_bank_ids
            )

    return {
        "created_employees": created_emp,
        "updated_employees": updated_emp,
        "still_synced": still_synced,
        "message": "Sync employee HRM → Finance selesai (smart update + bank + structure type + informasi pribadi).",
    }


# Kalau mau langsung run saat file dijalankan:
if __name__ == "__main__":
    print(sync_employee_and_contract())
