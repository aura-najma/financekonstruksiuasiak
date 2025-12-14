# -*- coding: utf-8 -*-
import xmlrpc.client

FIN_URL  = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB   = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"


# ==========================
# CONNECT
# ==========================
def connect(url, db, user, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, key, {})
    if not uid:
        raise Exception(f"Login gagal: db={db} user={user}")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models


# ==========================
# HELPERS
# ==========================
def fields_get(models, db, uid, key, model, field_names=None, attributes=None):
    """
    field_names: list[str] or None (None = all fields)
    attributes: list[str] e.g. ["string","type","required","selection","relation"]
    """
    if attributes is None:
        attributes = ["string", "type", "required", "selection", "relation"]

    args = []
    if field_names is None:
        # all fields
        args = []
    else:
        # specific field(s)
        args = [field_names]

    return models.execute_kw(
        db, uid, key,
        model, "fields_get",
        args,
        {"attributes": attributes}
    )


def print_selection(fg, field_name):
    if field_name not in fg:
        print(f"[WARN] field '{field_name}' tidak ditemukan")
        return

    meta = fg[field_name]
    print("Field name :", field_name)
    print("Label      :", meta.get("string"))
    print("Type       :", meta.get("type"))

    sel = meta.get("selection") or []
    if not sel:
        print("Selections : (kosong / bukan selection)")
        return

    print("Selections :")
    for val, label in sel:
        print(f" - {val} => {label}")


# ==========================
# DEBUG
# ==========================
def debug_hr_payslip_fields(fin_models, fin_uid, show_all_fields=True):
    if not show_all_fields:
        return

    fg = fields_get(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "hr.payslip",
        field_names=None,
        attributes=["string", "type", "required"]
    )

    print("\n=== FIELDS hr.payslip ===")
    for f in sorted(list(fg.keys())):
        print("-", f)


def debug_hr_payslip_state(fin_models, fin_uid):
    fg = fields_get(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "hr.payslip",
        field_names=["state"],
        attributes=["string", "type", "selection"]
    )

    print("\n=== STATE hr.payslip ===")
    print_selection(fg, "state")


def debug_hr_payslip_run_state(fin_models, fin_uid):
    fg = fields_get(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "hr.payslip.run",
        field_names=["state"],
        attributes=["string", "type", "selection"]
    )

    print("\n=== STATE hr.payslip.run ===")
    print_selection(fg, "state")


# ==========================
# MAIN
# ==========================
if __name__ == "__main__":
    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)

    # 1) Kalau mau lihat semua field payslip (banyak banget), set True
    debug_hr_payslip_fields(fin_models, fin_uid, show_all_fields=False)

    # 2) Ini yang kamu butuhin: state payslip apa aja
    debug_hr_payslip_state(fin_models, fin_uid)

    # 3) Bonus: state payrun (biar ngerti "03_paid" itu apa aja opsinya)
    debug_hr_payslip_run_state(fin_models, fin_uid)
