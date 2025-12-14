import xmlrpc.client

FIN_URL = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB  = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"   # pake key kamu (tanpa spasi)

SCM_URL = "https://scm-rumah.odoo.com/"
SCM_DB  = "scm-rumah"
SCM_USER = "najladhia259@gmail.com"
SCM_KEY  = "b19ed34964b8588ef12924a697eeeee08c92363b"
def connect(url, db, username, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, key, {})
    if not uid:
        raise Exception(f"Auth failed: {url} db={db} user={username}")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models

def upsert_project_by_composite_key(models, db, uid, key, name, customer_name, date_start, vals):
    # Cari partner (customer) di SCM berdasarkan nama
    partner_ids = models.execute_kw(
        db, uid, key,
        "res.partner", "search",
        [[("name", "=", customer_name)]],
        {"limit": 1}
    )
    partner_id = partner_ids[0] if partner_ids else False

    # Domain pencarian project di SCM
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

fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
scm_uid, scm_models = connect(SCM_URL, SCM_DB, SCM_USER, SCM_KEY)

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

    # vals untuk create/update di SCM (isi minimum)
    vals = {"name": name}
    if customer_name:
        # cari partner di SCM lagi saat create (biar partner_id bener)
        partner_ids = scm_models.execute_kw(
            SCM_DB, scm_uid, SCM_KEY,
            "res.partner", "search",
            [[("name", "=", customer_name)]],
            {"limit": 1}
        )
        if partner_ids:
            vals["partner_id"] = partner_ids[0]
    if date_start:
        vals["date_start"] = date_start

    rec_id, action = upsert_project_by_composite_key(
        scm_models, SCM_DB, scm_uid, SCM_KEY,
        name, customer_name, date_start, vals
    )
    print(f"{name} | {customer_name} | {date_start} => {action} (id={rec_id})")

print("DONE")