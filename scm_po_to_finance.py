import xmlrpc.client

# -----------------------------
# KONFIGURASI ODOO
# -----------------------------

FIN_URL = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB  = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"

SCM_URL = "https://scm-rumah.odoo.com/"
SCM_DB  = "scm-rumah"
SCM_USER = "najladhia259@gmail.com"
SCM_KEY  = "21aaf3b8c42329ea3277f4012958e83f61350b5e"

DEFAULT_EXPENSE_ACCOUNT_CODE = "51000020"
ANALYTIC_NAME_FIN = "Project Perumahan 1"
TAX_NAME_FIN = "12% (Non-Luxury Good)"
POST_IN_FIN = True


# -----------------------------
# FUNGSI BANTUAN
# -----------------------------

def connect(url, db, username, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, key.strip(), {})
    if not uid:
        raise Exception(f"Auth gagal ke {url}")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models

def find_one(models, db, uid, key, model, domain):
    ids = models.execute_kw(db, uid, key, model, "search", [domain], {"limit": 1})
    return ids[0] if ids else False

def upsert_vendor(fin_models, db, uid, key, vendor_name):
    vendor_id = find_one(fin_models, db, uid, key, "res.partner", [("name", "=", vendor_name)])
    if vendor_id:
        return vendor_id
    return fin_models.execute_kw(db, uid, key, "res.partner", "create", [{
        "name": vendor_name,
        "supplier_rank": 1,
    }])


# -----------------------------
# AMBIL PURCHASE ORDER DARI SCM
# -----------------------------

def get_pending_po(scm_models, db, uid, key):
    # AMBIL SEMUA PO YANG SUDAH CONFIRMED (state = purchase)
    po_ids = scm_models.execute_kw(
        db, uid, key, "purchase.order", "search",
        [[("state", "=", "purchase")]]
    )
    
    if not po_ids:
        return []

    pos = scm_models.execute_kw(
        db, uid, key, "purchase.order", "read",
        [po_ids], {"fields": ["id", "name", "partner_id", "date_order", "order_line"]}
    )

    line_ids = sum([po["order_line"] for po in pos], [])
    lines_map = {}

    if line_ids:
        lines = scm_models.execute_kw(
            db, uid, key, "purchase.order.line", "read",
            [line_ids], {"fields": ["order_id", "name", "product_qty", "price_unit"]}
        )
        for ln in lines:
            lines_map.setdefault(ln["order_id"][0], []).append(ln)

    return [(po, lines_map.get(po["id"], [])) for po in pos]



# -----------------------------
# SYNC PO → FINANCE (BUAT BILL)
# -----------------------------

def sync_po_to_finance():
    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    scm_uid, scm_models = connect(SCM_URL, SCM_DB, SCM_USER, SCM_KEY)

    pending = get_pending_po(scm_models, SCM_DB, scm_uid, SCM_KEY)
    if not pending:
        return {"message": "Tidak ada PO untuk disinkron."}

    expense_acc = find_one(fin_models, FIN_DB, fin_uid, FIN_KEY,
                            "account.account", [("code", "=", DEFAULT_EXPENSE_ACCOUNT_CODE)])
    analytic = find_one(fin_models, FIN_DB, fin_uid, FIN_KEY,
                        "account.analytic.account", [("name", "=", ANALYTIC_NAME_FIN)])

    created = 0
    skipped = 0

    for po, lines in pending:
        po_name = po["name"]
        vendor_name = po["partner_id"][1]

        # CEGAH DUPLIKASI: cek apakah bill dari PO ini sudah pernah ada
        ref_value = f"SCM PO {po_name}"
        existing_bill = find_one(
            fin_models, FIN_DB, fin_uid, FIN_KEY,
            "account.move", [
                ("move_type", "=", "in_invoice"),
                ("ref", "=", ref_value),
            ]
        )
        if existing_bill:
            skipped += 1
            continue


        # Create or find vendor
        vendor_id = upsert_vendor(fin_models, FIN_DB, fin_uid, FIN_KEY, vendor_name)

        invoice_lines = []
        for ln in lines:
            invoice_lines.append((0, 0, {
                "name": ln["name"],
                "quantity": ln["product_qty"],
                "price_unit": ln["price_unit"],
                "account_id": expense_acc,
                "analytic_distribution": {str(analytic): 100},
            }))

        bill_vals = {
            "move_type": "in_invoice",
            "partner_id": vendor_id,
            "ref": ref_value,
            "invoice_date": po["date_order"],
            "invoice_line_ids": invoice_lines,
        }

        bill_id = fin_models.execute_kw(
            FIN_DB, fin_uid, FIN_KEY, "account.move", "create", [bill_vals]
        )
        created += 1

        if POST_IN_FIN:
            fin_models.execute_kw(
                FIN_DB, fin_uid, FIN_KEY, "account.move", "action_post", [[bill_id]]
            )

    return {
        "created_bills": created,
        "skipped_already_existing": skipped,
        "message": "Sync selesai."
    }

# -----------------------------
# BAGIAN 2 — FINANCE → SCM
# KIRIM STATUS 'PAID'
# -----------------------------

def get_paid_bills(fin_models, db, uid, key):
    """Ambil vendor bill yang SUDAH dibayar (payment_state=paid)."""

    bill_ids = fin_models.execute_kw(
        db, uid, key,
        "account.move", "search",
        [[
            ("move_type", "=", "in_invoice"),
            ("payment_state", "=", "paid"),
            ("ref", "like", "SCM PO"),
        ]]
    )

    if not bill_ids:
        return []

    return fin_models.execute_kw(
        db, uid, key,
        "account.move", "read",
        [bill_ids],
        {"fields": ["id", "ref", "amount_total", "invoice_date", "payment_state"]}
    )


def sync_paid_back_to_scm():
    """Jika vendor bill di Finance sudah dibayar → update PO di SCM:
       - invoice_status = 'invoiced'
       - qty_invoiced di setiap line = product_qty
    """

    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    scm_uid, scm_models = connect(SCM_URL, SCM_DB, SCM_USER, SCM_KEY)

    bills = get_paid_bills(fin_models, FIN_DB, fin_uid, FIN_KEY)
    if not bills:
        return {"message": "Tidak ada bill paid untuk dikirim ke SCM."}

    updated = 0

    for bill in bills:
        # ref: "SCM PO P00012"
        ref = bill.get("ref") or ""
        if not ref.startswith("SCM PO "):
            continue

        po_number = ref.replace("SCM PO ", "").strip()

        # 1) cari PO di SCM
        po_id = find_one(
            scm_models, SCM_DB, scm_uid, SCM_KEY,
            "purchase.order", [("name", "=", po_number)]
        )
        if not po_id:
            continue

        # 2) set invoice_status header → Fully Billed
        #    (nilai internalnya: 'invoiced')
        scm_models.execute_kw(
            SCM_DB, scm_uid, SCM_KEY,
            "purchase.order", "write",
            [[po_id], {"invoice_status": "invoiced"}]
        )

        # 3) ambil semua line di PO itu
        po_data = scm_models.execute_kw(
            SCM_DB, scm_uid, SCM_KEY,
            "purchase.order", "read",
            [[po_id]], {"fields": ["order_line"]}
        )[0]
        line_ids = po_data.get("order_line", [])

        if line_ids:
            lines = scm_models.execute_kw(
                SCM_DB, scm_uid, SCM_KEY,
                "purchase.order.line", "read",
                [line_ids], {"fields": ["product_qty", "qty_invoiced"]}
            )

            # set qty_invoiced = product_qty per line
            for ln in lines:
                line_id = ln["id"]
                new_qty = ln["product_qty"] or 0.0

                scm_models.execute_kw(
                    SCM_DB, scm_uid, SCM_KEY,
                    "purchase.order.line", "write",
                    [[line_id], {"qty_invoiced": new_qty}]
                )

        updated += 1

    return {
        "updated_po_paid": updated,
        "message": "Sync Finance → SCM selesai (invoice_status & qty_invoiced terupdate)."
    }