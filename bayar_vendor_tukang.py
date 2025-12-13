import xmlrpc.client

# -----------------------------
# KONFIGURASI ODOO
# -----------------------------

# FINANCE (TUJUAN)
FIN_URL = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB  = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"

# HRM (SUMBER) — dulu SCM
HRM_URL  = "https://konstruksi-perumahan2.odoo.com"
HRM_DB   = "konstruksi-perumahan2"
HRM_USER = "fauziah.hamidah.al-2022@ftmm.unair.ac.id"
HRM_KEY  = "169979f5c07f649da80a58132ca9344cdc6d3ada"

# DEFAULT akun biaya untuk "tukang borongan" (jasa/vendor)
DEFAULT_EXPENSE_ACCOUNT_CODE = "65110030"  # Consultant Fees (Expenses)

# Optional: analytic untuk proyek di Finance
ANALYTIC_NAME_FIN = "Project Perumahan 1"

# Posting otomatis bill di Finance?
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
# AMBIL PURCHASE ORDER DARI HRM
# (ASUMSI HRM ADA purchase.order)
# -----------------------------

def get_pending_po(hrm_models, db, uid, key):
    """
    Ambil semua PO yang sudah confirmed (state = 'purchase') dari HRM.
    """
    po_ids = hrm_models.execute_kw(
        db, uid, key, "purchase.order", "search",
        [[("state", "=", "purchase")]]
    )

    if not po_ids:
        return []

    pos = hrm_models.execute_kw(
        db, uid, key, "purchase.order", "read",
        [po_ids], {"fields": ["id", "name", "partner_id", "date_order", "order_line"]}
    )

    line_ids = sum([po.get("order_line", []) for po in pos], [])
    lines_map = {}

    if line_ids:
        lines = hrm_models.execute_kw(
            db, uid, key, "purchase.order.line", "read",
            [line_ids], {"fields": ["id", "order_id", "name", "product_qty", "price_unit"]}
        )
        for ln in lines:
            lines_map.setdefault(ln["order_id"][0], []).append(ln)

    return [(po, lines_map.get(po["id"], [])) for po in pos]


# -----------------------------
# SYNC HRM PO → FINANCE (BUAT BILL)
# -----------------------------

def sync_po_tukang_to_finance():
    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    hrm_uid, hrm_models = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)

    pending = get_pending_po(hrm_models, HRM_DB, hrm_uid, HRM_KEY)
    if not pending:
        return {"message": "Tidak ada PO (state=purchase) di HRM untuk disinkron."}

    expense_acc = find_one(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "account.account", [("code", "=", DEFAULT_EXPENSE_ACCOUNT_CODE)]
    )
    if not expense_acc:
        raise Exception(f"Akun expense code {DEFAULT_EXPENSE_ACCOUNT_CODE} tidak ditemukan di Finance.")

    analytic_id = find_one(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "account.analytic.account", [("name", "=", ANALYTIC_NAME_FIN)]
    )
    # analytic boleh kosong (kalau gak ketemu), jadi jangan di-hard fail

    created = 0
    skipped = 0

    for po, lines in pending:
        po_name = po.get("name")
        partner = po.get("partner_id") or [False, "Unknown Vendor"]
        vendor_name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "Unknown Vendor"

        # CEGAH DUPLIKASI: cek apakah bill dari PO ini sudah pernah ada
        ref_value = f"HRM PO {po_name}"
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

        vendor_id = upsert_vendor(fin_models, FIN_DB, fin_uid, FIN_KEY, vendor_name)

        invoice_lines = []
        for ln in lines:
            line_vals = {
                "name": ln.get("name") or "",
                "quantity": ln.get("product_qty") or 0.0,
                "price_unit": ln.get("price_unit") or 0.0,
                "account_id": expense_acc,
            }
            if analytic_id:
                line_vals["analytic_distribution"] = {str(analytic_id): 100}

            invoice_lines.append((0, 0, line_vals))

        bill_vals = {
            "move_type": "in_invoice",
            "partner_id": vendor_id,
            "ref": ref_value,
            "invoice_date": po.get("date_order"),
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
        "message": "Sync HRM PO → Finance selesai."
    }


# -----------------------------
# BAGIAN 2 — FINANCE → HRM
# KIRIM STATUS 'PAID' BALIK KE PO
# -----------------------------

def get_paid_bills(fin_models, db, uid, key):
    """
    Ambil vendor bill yang SUDAH dibayar (payment_state=paid)
    dan ref-nya berasal dari HRM PO.
    """
    bill_ids = fin_models.execute_kw(
        db, uid, key,
        "account.move", "search",
        [[
            ("move_type", "=", "in_invoice"),
            ("payment_state", "=", "paid"),
            ("ref", "like", "HRM PO "),
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


def sync_paid_back_to_hrm():
    """
    Jika vendor bill di Finance sudah dibayar → update PO di HRM:
    - invoice_status = 'invoiced'
    - qty_invoiced di setiap line = product_qty
    """
    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    hrm_uid, hrm_models = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)

    bills = get_paid_bills(fin_models, FIN_DB, fin_uid, FIN_KEY)
    if not bills:
        return {"message": "Tidak ada bill paid untuk dikirim ke HRM."}

    updated = 0

    for bill in bills:
        ref = bill.get("ref") or ""
        if not ref.startswith("HRM PO "):
            continue

        po_number = ref.replace("HRM PO ", "").strip()

        # 1) cari PO di HRM
        po_id = find_one(
            hrm_models, HRM_DB, hrm_uid, HRM_KEY,
            "purchase.order", [("name", "=", po_number)]
        )
        if not po_id:
            continue

        # 2) set invoice_status header
        hrm_models.execute_kw(
            HRM_DB, hrm_uid, HRM_KEY,
            "purchase.order", "write",
            [[po_id], {"invoice_status": "invoiced"}]
        )

        # 3) ambil semua line di PO
        po_data = hrm_models.execute_kw(
            HRM_DB, hrm_uid, HRM_KEY,
            "purchase.order", "read",
            [[po_id]], {"fields": ["order_line"]}
        )[0]
        line_ids = po_data.get("order_line", [])

        if line_ids:
            lines = hrm_models.execute_kw(
                HRM_DB, hrm_uid, HRM_KEY,
                "purchase.order.line", "read",
                [line_ids], {"fields": ["id", "product_qty", "qty_invoiced"]}
            )

            for ln in lines:
                line_id = ln["id"]
                new_qty = ln.get("product_qty") or 0.0

                hrm_models.execute_kw(
                    HRM_DB, hrm_uid, HRM_KEY,
                    "purchase.order.line", "write",
                    [[line_id], {"qty_invoiced": new_qty}]
                )

        updated += 1

    return {
        "updated_po_paid": updated,
        "message": "Sync Finance → HRM selesai (invoice_status & qty_invoiced terupdate)."
    }
