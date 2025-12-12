import xmlrpc.client

# FINANCE ODOO
FIN_URL  = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB   = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"   # api key finance

# HRM ODOO (DULUNYA SCM)
HRM_URL  = "https://konstruksi-perumahan2.odoo.com"
HRM_DB   = "konstruksi-perumahan2"
HRM_USER = "fauziah.hamidah.al-2022@ftmm.unair.ac.id"
HRM_KEY  = "169979f5c07f649da80a58132ca9344cdc6d3ada"   # api key hrm

def connect(url, db, username, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, key, {})
    if not uid:
        raise Exception(f"Auth failed: {url} db={db} user={username}")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models

def upsert_project_by_composite_key(models, db, uid, key, name, customer_name, date_start, vals):
    # Cari partner (customer) di TARGET (HRM) berdasarkan nama
    partner_ids = models.execute_kw(
        db, uid, key,
        "res.partner", "search",
        [[("name", "=", customer_name)]],
        {"limit": 1}
    )
    partner_id = partner_ids[0] if partner_ids else False

    # Domain pencarian project di TARGET (HRM)
    domain = [("name", "=", name)]
    if partner_id:
        domain.append(("partner_id", "=", partner_id))
    # date_start biasanya format 'YYYY-MM-DD'
    if date_start:
        domain.append(("date_start", "=", date_start))

    proj_ids = models.execute_kw(
        db, uid, key,
        "project.project", "search",
        [domain],
        {"limit": 1}
    )

    if proj_ids:
        models.execute_kw(db, uid, key, "project.project", "write", [proj_ids, vals])
        return proj_ids[0], "updated"

    new_id = models.execute_kw(db, uid, key, "project.project", "create", [vals])
    return new_id, "created"

# koneksi ke Finance (source) dan HRM (target)
fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
hrm_uid, hrm_models = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)

# Ambil project dari Finance
projects = fin_models.execute_kw(
    FIN_DB, fin_uid, FIN_KEY,
    "project.project", "search_read",
    [[]],
    {"fields": ["name", "partner_id", "date_start"]}
)

for p in projects:
    name = p["name"]
    customer_name = p["partner_id"][1] if p.get("partner_id") else ""
    date_start = p.get("date_start")  # bisa None/False

    # vals untuk create/update di HRM (isi minimum)
    vals = {"name": name}
    if customer_name:
        # cari partner di HRM lagi saat create (biar partner_id bener)
        partner_ids = hrm_models.execute_kw(
            HRM_DB, hrm_uid, HRM_KEY,
            "res.partner", "search",
            [[("name", "=", customer_name)]],
            {"limit": 1}
        )
        if partner_ids:
            vals["partner_id"] = partner_ids[0]
    if date_start:
        vals["date_start"] = date_start

    rec_id, action = upsert_project_by_composite_key(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        name, customer_name, date_start, vals
    )
    print(f"{name} | {customer_name} | {date_start} => {action} (id={rec_id})")

print("DONE")
