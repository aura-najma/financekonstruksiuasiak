import xmlrpc.client

# ==========================
# KONFIGURASI INSTANCE ODOO
# ==========================
FIN_URL = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB  = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"

SCM_URL = "https://scm-rumah.odoo.com"
SCM_DB  = "scm-rumah"
SCM_USER = "najladhia259@gmail.com"
SCM_KEY  = "21aaf3b8c42329ea3277f4012958e83f61350b5e"

# ==========================
# PARAMETER BISNIS
# ==========================
EXPENSE_ACCOUNT_CODE = "65110090"  # 65110090 Shipping Costs (Finance)
EXPENSE_PRODUCT_NAME = "Shipping Costs Proyek Perumahan 1"  # harus sama persis dg Expense Category/Product
ANALYTIC_NAME_FIN = "Project Perumahan 1"   # opsional (kalau ada)
DEFAULT_EMPLOYEE_NAME = "Rian Setiawan"     # expense dibuat atas nama employee ini
PAID_NOTE_MARKER = "[FINANCE ONGKIR PAID]"


# ==========================
# UTIL XMLRPC
# ==========================
def connect(url, db, username, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, key.strip(), {})
    if not uid:
        raise Exception(f"Auth gagal ke {url} (db={db}, user={username})")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models


def find_one(models, db, uid, key, model, domain):
    ids = models.execute_kw(db, uid, key, model, "search", [domain], {"limit": 1})
    return ids[0] if ids else False


def get_model_fields(models, db, uid, key, model):
    info = models.execute_kw(
        db, uid, key, model, "fields_get", [], {"attributes": ["string", "type"]}
    )
    return set(info.keys())


# ==========================
# SCM: LOG NOTE helper
# ==========================
def get_mail_mt_note_subtype_id(scm_models, db, uid, key):
    """
    Ambil subtype_id untuk 'Log note' (mail.mt_note) lewat ir.model.data
    """
    data_ids = scm_models.execute_kw(
        db, uid, key,
        "ir.model.data", "search",
        [[("module", "=", "mail"), ("name", "=", "mt_note")]],
        {"limit": 1}
    )
    if not data_ids:
        return False

    rec = scm_models.execute_kw(
        db, uid, key,
        "ir.model.data", "read",
        [data_ids],
        {"fields": ["res_id"]}
    )[0]
    return rec.get("res_id") or False


def post_log_note_scm(scm_models, db, uid, key, picking_id, body_html):
    """
    Post note ke chatter stock.picking sebagai 'Log note' (muncul di Activity).
    """
    mt_note_id = get_mail_mt_note_subtype_id(scm_models, db, uid, key)

    vals = {
        "model": "stock.picking",
        "res_id": picking_id,
        "body": body_html,
        "message_type": "comment",
    }
    if mt_note_id:
        vals["subtype_id"] = mt_note_id

    return scm_models.execute_kw(db, uid, key, "mail.message", "create", [vals])


def already_has_paid_note(scm_models, db, uid, key, picking_id):
    """
    Cegah dobel note: cek apakah marker sudah pernah ada di chatter transfer itu.
    """
    msg_ids = scm_models.execute_kw(
        db, uid, key,
        "mail.message", "search",
        [[
            ("model", "=", "stock.picking"),
            ("res_id", "=", picking_id),
            ("body", "ilike", PAID_NOTE_MARKER),
        ]],
        {"limit": 1}
    )
    return bool(msg_ids)


# ==========================
# SCM: AMBIL INTERNAL TRANSFER DONE
# ==========================
def get_done_internal_transfers(scm_models, db, uid, key, limit=50):
    ids = scm_models.execute_kw(
        db, uid, key,
        "stock.picking", "search",
        [[("state", "=", "done"), ("picking_type_id.code", "=", "internal")]],
        {"limit": limit, "order": "date_done desc"}
    )
    if not ids:
        return []

    return scm_models.execute_kw(
        db, uid, key,
        "stock.picking", "read",
        [ids],
        {"fields": ["id", "name", "date_done", "location_id", "location_dest_id"]}
    )


# ==========================
# FIN: cari product untuk Expense Category (product_id)
# ==========================
def find_expense_product(fin_models, db, uid, key, product_name):
    prod_ids = fin_models.execute_kw(
        db, uid, key,
        "product.product", "search",
        [[("name", "=", product_name)]],
        {"limit": 1}
    )
    return prod_ids[0] if prod_ids else False


def find_product_cost(fin_models, db, uid, key, product_id):
    """
    Ambil cost dari product untuk fallback.
    Banyak setup menaruh cost di standard_price, tapi kita fallback ke lst_price juga.
    """
    prod = fin_models.execute_kw(
        db, uid, key,
        "product.product", "read",
        [[product_id]],
        {"fields": ["standard_price", "lst_price"]}
    )[0]
    return float(prod.get("standard_price") or prod.get("lst_price") or 0.0)


# ==========================
# 1) SCM → FINANCE: CREATE EXPENSE ONGKIR (ambil cost dari product)
# ==========================
def sync_internal_transfer_to_finance_expenses(limit=50):
    fin_uid, fin = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    scm_uid, scm = connect(SCM_URL, SCM_DB, SCM_USER, SCM_KEY)

    transfers = get_done_internal_transfers(scm, SCM_DB, scm_uid, SCM_KEY, limit=limit)
    if not transfers:
        return {"message": "Tidak ada internal transfer DONE di SCM."}

    expense_fields = get_model_fields(fin, FIN_DB, fin_uid, FIN_KEY, "hr.expense")

    # employee di Finance
    employee_id = find_one(
        fin, FIN_DB, fin_uid, FIN_KEY,
        "hr.employee", [("name", "=", DEFAULT_EMPLOYEE_NAME)]
    )
    if not employee_id:
        raise Exception(f"Employee '{DEFAULT_EMPLOYEE_NAME}' tidak ditemukan di Finance.")

    # account Shipping Costs
    account_id = find_one(
        fin, FIN_DB, fin_uid, FIN_KEY,
        "account.account", [("code", "=", EXPENSE_ACCOUNT_CODE)]
    )
    if not account_id:
        raise Exception(f"Akun expense code {EXPENSE_ACCOUNT_CODE} tidak ditemukan di Finance.")

    # Expense Category/Product
    product_id = False
    if "product_id" in expense_fields:
        product_id = find_expense_product(fin, FIN_DB, fin_uid, FIN_KEY, EXPENSE_PRODUCT_NAME)
        if not product_id:
            raise Exception(
                f"Expense Category/Product '{EXPENSE_PRODUCT_NAME}' tidak ditemukan di Finance. "
                f"Buat dulu di Expenses → Configuration → Expense Categories."
            )

    # analytic opsional
    analytic_id = False
    if "analytic_distribution" in expense_fields and ANALYTIC_NAME_FIN:
        analytic_id = find_one(
            fin, FIN_DB, fin_uid, FIN_KEY,
            "account.analytic.account", [("name", "=", ANALYTIC_NAME_FIN)]
        )

    created = 0
    skipped = 0

    for tr in transfers:
        transfer_name = tr["name"]  # WH/INT/00001
        unique_name = f"SCM INT {transfer_name}"  # kunci anti duplikasi

        # anti duplikasi: cek expense dengan name sama
        exists = find_one(
            fin, FIN_DB, fin_uid, FIN_KEY,
            "hr.expense", [("name", "=", unique_name)]
        )
        if exists:
            skipped += 1
            continue

        from_loc = tr["location_id"][1] if tr.get("location_id") else "-"
        to_loc = tr["location_dest_id"][1] if tr.get("location_dest_id") else "-"

        expense_vals = {
            "name": unique_name,
            "employee_id": employee_id,
        }

        if "date" in expense_fields:
            expense_vals["date"] = tr.get("date_done") or False

        if "payment_mode" in expense_fields:
            expense_vals["payment_mode"] = "company_account"

        if "account_id" in expense_fields:
            expense_vals["account_id"] = account_id

        if product_id and "product_id" in expense_fields:
            expense_vals["product_id"] = product_id

        if "quantity" in expense_fields:
            expense_vals["quantity"] = 1.0

        if "description" in expense_fields:
            expense_vals["description"] = (
                f"Ongkir Internal Transfer {transfer_name} ({from_loc} → {to_loc})"
            )

        if analytic_id and "analytic_distribution" in expense_fields:
            expense_vals["analytic_distribution"] = {str(analytic_id): 100}

        # create expense
        exp_id = fin.execute_kw(FIN_DB, fin_uid, FIN_KEY, "hr.expense", "create", [expense_vals])
        created += 1

   
    return {
        "created_expenses": created,
        "skipped_existing": skipped,
        "message": "SCM Internal Transfer → Finance Expense selesai (pakai cost Expense Category).",
    }

# ==========================
# 2) FINANCE (PAID) → SCM: LOG NOTE "ONGKIR SUDAH DIBAYAR"
# ==========================
def get_paid_shipping_expenses(fin_models, db, uid, key, limit=200):
    """
    Ambil hr.expense yang state=paid dan name like 'SCM INT %'
    NOTE: jangan read unit_amount karena di instance kamu field itu gak ada.
    """
    exp_ids = fin_models.execute_kw(
        db, uid, key,
        "hr.expense", "search",
        [[("state", "=", "paid"), ("name", "like", "SCM INT ")]],
        {"limit": limit, "order": "id desc"}
    )
    if not exp_ids:
        return []

    # total_amount wajib dipakai buat nilai nominal
    fields = ["id", "name", "state", "total_amount"]
    return fin_models.execute_kw(db, uid, key, "hr.expense", "read", [exp_ids], {"fields": fields})


def expense_amount(exp):
    return exp.get("total_amount") or 0.0


def sync_paid_expenses_note_back_to_scm(limit=200):
    fin_uid, fin = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    scm_uid, scm = connect(SCM_URL, SCM_DB, SCM_USER, SCM_KEY)

    expenses = get_paid_shipping_expenses(fin, FIN_DB, fin_uid, FIN_KEY, limit=limit)
    if not expenses:
        return {"message": "Tidak ada shipping expense PAID di Finance untuk dikirim ke SCM."}

    noted = 0
    skipped = 0
    missing = 0

    for exp in expenses:
        name = (exp.get("name") or "").strip()
        if not name.startswith("SCM INT "):
            continue

        transfer_ref = name.replace("SCM INT ", "").strip()  # WH/INT/00001

        # cari picking di SCM
        picking_id = find_one(
            scm, SCM_DB, scm_uid, SCM_KEY,
            "stock.picking", [("name", "=", transfer_ref)]
        )
        if not picking_id:
            missing += 1
            continue

        # cegah dobel note
        if already_has_paid_note(scm, SCM_DB, scm_uid, SCM_KEY, picking_id):
            skipped += 1
            continue

        amount = expense_amount(exp)

        body = (
            f"{PAID_NOTE_MARKER}<br/>"
            f"Biaya ongkir untuk transfer <b>{transfer_ref}</b> telah dibayar oleh Finance.<br/>"
            f"Total: <b>{amount}</b>."
        )

        post_log_note_scm(scm, SCM_DB, scm_uid, SCM_KEY, picking_id, body)
        noted += 1

    return {
        "noted_transfers": noted,
        "skipped_already_noted": skipped,
        "missing_transfer_in_scm": missing,
        "message": "Finance PAID shipping expenses → SCM Log note selesai.",
    }