# -*- coding: utf-8 -*-
"""
notify_hrm.py

ONE FILE:
(1) Auto-pick latest PAID payrun in FINANCE (state='03_paid')
(2) Sync payslips from FIN -> HRM into SAME HRM Pay Run
(3) Fix/patch HRM payslips link fields (+ company_id) to avoid UI showing "0"

Run standalone:
  python notify_hrm.py

Call from app.py:
  import notify_hrm
  notify_hrm.run_latest_paid(dry_run=True/False, do_patch=True/False, hrm_run_id_force=12)
"""

import traceback
import xmlrpc.client
from datetime import datetime, timezone

# ==========================
# CONFIG
# ==========================
# FINANCE (SOURCE)
FIN_URL  = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB   = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"  # <-- replace if needed

# HRM (TARGET)
HRM_URL  = "https://konstruksi-perumahan2.odoo.com"
HRM_DB   = "konstruksi-perumahan2"
HRM_USER = "fauziah.hamidah.al-2022@ftmm.unair.ac.id"
HRM_KEY  = "169979f5c07f649da80a58132ca9344cdc6d3ada"  # <-- replace if needed

# If you already know the HRM run id (e.g. 12), set it here to FORCE using it.
# If None, script will search/create by (name, date_start, date_end).
HRM_RUN_ID_FORCE = None  # e.g. 12

# Salary structure name in HRM (target)
HRM_STRUCTURE_NAME = "Gaji Asli"

# DRY RUN
DRY_RUN = False

# Actions
DO_SYNC  = True
DO_PATCH = True

# Patch target:
# - if DO_SYNC True: it will patch the hrm_run_id produced/used by sync
# - else: it will patch HRM_RUN_ID_PATCH
HRM_RUN_ID_PATCH = 12  # used only if DO_SYNC is False


# ==========================
# ID NORMALIZATION
# ==========================
def norm_id(x):
    """Return int id from possible forms: 12, [12, 'Name'], [12], (12, 'Name'), [[12]], [[12,'x']], etc."""
    if x is False or x is None:
        return None
    if isinstance(x, int):
        return int(x)
    if isinstance(x, (list, tuple)):
        if not x:
            return None
        if isinstance(x[0], int):
            return int(x[0])
        if isinstance(x[0], (list, tuple)) and x[0] and isinstance(x[0][0], int):
            return int(x[0][0])
        return None
    try:
        return int(x)
    except Exception:
        return None

def norm_ids(ids):
    """Return flat list[int] from: 12, [12], [[12]], [[12,'x']], [ [12,'x'], [13,'y'] ], etc."""
    if ids is False or ids is None:
        return []
    if isinstance(ids, int):
        return [ids]
    if isinstance(ids, (list, tuple)):
        if len(ids) == 1 and isinstance(ids[0], (list, tuple)):
            return norm_ids(ids[0])
        out = []
        for it in ids:
            nid = norm_id(it)
            if nid is not None:
                out.append(nid)
        return out
    nid = norm_id(ids)
    return [nid] if nid is not None else []


# ==========================
# XMLRPC HELPERS
# ==========================
def connect(url):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return common, models

def login(common, db, user, key):
    uid = common.authenticate(db, user, key, {})
    if not uid:
        raise RuntimeError(f"Login failed for {db} user={user}")
    return uid

def call(models, db, uid, key, model, method, args=None, kwargs=None):
    args = args or []
    kwargs = kwargs or {}
    return models.execute_kw(db, uid, key, model, method, args, kwargs)

def fields_get(models, db, uid, key, model):
    return call(models, db, uid, key, model, "fields_get", args=[[]], kwargs={"attributes": ["type", "string", "relation"]})

def read(models, db, uid, key, model, ids, fields=None):
    fields = fields or []
    ids = norm_ids(ids)
    return call(models, db, uid, key, model, "read", args=[ids], kwargs={"fields": fields})

def search_read(models, db, uid, key, model, domain, fields=None, limit=0, order=None):
    fields = fields or []
    kw = {"fields": fields}
    if limit:
        kw["limit"] = limit
    if order:
        kw["order"] = order
    return call(models, db, uid, key, model, "search_read", args=[domain], kwargs=kw)

def search(models, db, uid, key, model, domain, limit=0, order=None):
    kw = {}
    if limit:
        kw["limit"] = limit
    if order:
        kw["order"] = order
    return call(models, db, uid, key, model, "search", args=[domain], kwargs=kw)

def create(models, db, uid, key, model, vals):
    return call(models, db, uid, key, model, "create", args=[[vals]])

def write(models, db, uid, key, model, ids, vals):
    ids = norm_ids(ids)
    return call(models, db, uid, key, model, "write", args=[ids, vals])

def try_call(models, db, uid, key, model, method, args=None, kwargs=None):
    try:
        return call(models, db, uid, key, model, method, args=args, kwargs=kwargs)
    except Exception:
        return None


# ==========================
# AUTO PICK LATEST PAID RUN (FIN)
# ==========================
def find_latest_paid_payrun_in_finance(fin_models, db, uid, key):
    """
    Cari payrun terakhir yang state = '03_paid' di FINANCE.
    Order by write_date desc, id desc.
    """
    run_ids = fin_models.execute_kw(
        db, uid, key,
        "hr.payslip.run", "search",
        [[("state", "=", "03_paid")]],
        {"limit": 1, "order": "write_date desc, id desc"}
    )
    if not run_ids:
        return False

    run = fin_models.execute_kw(
        db, uid, key,
        "hr.payslip.run", "read",
        [run_ids],
        {"fields": ["id", "name", "state", "write_date"]}
    )[0]
    return run


# ==========================
# MODEL FIELD DETECTION
# ==========================
def pick_first(existing_fields, *candidates):
    for c in candidates:
        if c in existing_fields:
            return c
    return None

def must_field(existing_fields, name):
    if name not in existing_fields:
        raise RuntimeError(f"Required field '{name}' not found. Sample fields: {sorted(list(existing_fields))[:30]}")
    return name

def detect_payslip_fields(models, db, uid, key):
    fg = fields_get(models, db, uid, key, "hr.payslip")
    fset = set(fg.keys())

    employee_field = must_field(fset, "employee_id")
    date_from_field = pick_first(fset, "date_from", "date_start")
    date_to_field = pick_first(fset, "date_to", "date_end")
    struct_field = pick_first(fset, "struct_id", "structure_id")
    run_link_field = pick_first(fset, "payslip_run_id", "run_id", "batch_id")

    if not date_from_field or not date_to_field:
        raise RuntimeError(f"Cannot detect date_from/date_to fields on hr.payslip. Found: {sorted(list(fset))}")

    if not struct_field:
        raise RuntimeError(f"Cannot detect structure field on hr.payslip. Found: {sorted(list(fset))}")

    if not run_link_field:
        raise RuntimeError(f"Cannot detect payrun linkage field on hr.payslip. Found: {sorted(list(fset))}")

    return {
        "employee": employee_field,
        "date_from": date_from_field,
        "date_to": date_to_field,
        "struct": struct_field,
        "run_link": run_link_field,
        "state": "state" if "state" in fset else None,
        "name": "name" if "name" in fset else None,
        "company_id": "company_id" if "company_id" in fset else None,
        "_all_fields": fset,
    }

def detect_run_fields(models, db, uid, key):
    fg = fields_get(models, db, uid, key, "hr.payslip.run")
    fset = set(fg.keys())

    name_field = pick_first(fset, "name", "display_name")
    date_start_field = pick_first(fset, "date_start", "date_from", "date_begin")
    date_end_field = pick_first(fset, "date_end", "date_to", "date_finish")
    state_field = "state" if "state" in fset else None
    slip_ids_field = pick_first(fset, "slip_ids", "payslip_ids")

    if not name_field:
        raise RuntimeError("Cannot detect hr.payslip.run name field.")
    if not date_start_field or not date_end_field:
        raise RuntimeError("Cannot detect hr.payslip.run date_start/date_end fields.")

    return {
        "name": name_field,
        "date_start": date_start_field,
        "date_end": date_end_field,
        "state": state_field,
        "slip_ids": slip_ids_field,
        "company_id": "company_id" if "company_id" in fset else None,
        "_all_fields": fset,
    }


# ==========================
# BUSINESS LOGIC (SYNC)
# ==========================
def find_structure_id(hrm_models, hrm_uid):
    recs = search_read(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        "hr.payroll.structure",
        domain=[("name", "=", HRM_STRUCTURE_NAME)],
        fields=["id", "name"],
        limit=1
    )
    if recs:
        return recs[0]["id"]

    recs = search_read(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        "hr.payroll.structure",
        domain=[("name", "ilike", HRM_STRUCTURE_NAME)],
        fields=["id", "name"],
        limit=1
    )
    if recs:
        return recs[0]["id"]

    raise RuntimeError(f"HRM salary structure not found: {HRM_STRUCTURE_NAME}")

def get_fin_run_and_slips(fin_models, fin_uid, fin_run_id):
    run_fields = detect_run_fields(fin_models, FIN_DB, fin_uid, FIN_KEY)
    ps_fields = detect_payslip_fields(fin_models, FIN_DB, fin_uid, FIN_KEY)

    fin_run = read(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "hr.payslip.run",
        [fin_run_id],
        fields=[
            "id",
            run_fields["name"],
            run_fields["date_start"],
            run_fields["date_end"],
            run_fields["state"] if run_fields["state"] else "id",
        ],
    )[0]

    fin_run_name = fin_run[run_fields["name"]]
    fin_date_start = fin_run[run_fields["date_start"]]
    fin_date_end = fin_run[run_fields["date_end"]]
    fin_state = fin_run[run_fields["state"]] if run_fields["state"] else None

    fin_run_link = ps_fields["run_link"]

    fin_slips = search_read(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "hr.payslip",
        domain=[(fin_run_link, "=", fin_run_id)],
        fields=[
            "id",
            ps_fields["name"] if ps_fields["name"] else "id",
            ps_fields["employee"],
            ps_fields["date_from"],
            ps_fields["date_to"],
            "line_ids",
        ],
        limit=0
    )

    return {
        "id": fin_run_id,
        "name": fin_run_name,
        "date_start": fin_date_start,
        "date_end": fin_date_end,
        "state": fin_state,
    }, fin_slips, ps_fields

def fin_get_amounts(fin_models, fin_uid, fin_slip_id):
    slip = read(fin_models, FIN_DB, fin_uid, FIN_KEY, "hr.payslip", [fin_slip_id], fields=["line_ids"])[0]
    line_ids = norm_ids(slip.get("line_ids"))
    if not line_ids:
        return None

    lines = search_read(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "hr.payslip.line",
        domain=[("id", "in", line_ids)],
        fields=["id", "code", "total"],
        limit=0
    )
    m = {l["code"]: float(l["total"] or 0.0) for l in lines if l.get("code")}
    return {"BASIC": m.get("BASIC", 0.0), "GROSS": m.get("GROSS", 0.0), "NET": m.get("NET", 0.0)}

def hrm_find_or_create_run(hrm_models, hrm_uid, fin_run):
    run_fields = detect_run_fields(hrm_models, HRM_DB, hrm_uid, HRM_KEY)

    if HRM_RUN_ID_FORCE:
        rec = read(
            hrm_models, HRM_DB, hrm_uid, HRM_KEY,
            "hr.payslip.run",
            [HRM_RUN_ID_FORCE],
            fields=["id", run_fields["name"], run_fields["date_start"], run_fields["date_end"]],
        )[0]
        return rec["id"]

    dom = [
        (run_fields["name"], "=", fin_run["name"]),
        (run_fields["date_start"], "=", fin_run["date_start"]),
        (run_fields["date_end"], "=", fin_run["date_end"]),
    ]
    recs = search_read(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        "hr.payslip.run",
        domain=dom,
        fields=["id", run_fields["name"], run_fields["date_start"], run_fields["date_end"]],
        limit=1,
        order="id desc"
    )
    if recs:
        return recs[0]["id"]

    vals = {
        run_fields["name"]: fin_run["name"],
        run_fields["date_start"]: fin_run["date_start"],
        run_fields["date_end"]: fin_run["date_end"],
    }
    if DRY_RUN:
        print(f"[DRY] would create HRM payrun: {vals}")
        return None

    return create(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip.run", vals)

def hrm_get_run_company_id(hrm_models, hrm_uid, hrm_run_id):
    run_fields = detect_run_fields(hrm_models, HRM_DB, hrm_uid, HRM_KEY)
    if not run_fields.get("company_id"):
        return None
    rec = read(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip.run", [hrm_run_id], fields=["company_id"])[0]
    comp = rec.get("company_id")
    return comp[0] if isinstance(comp, list) and comp else None

def hrm_find_employee(hrm_models, hrm_uid, fin_employee_m2o):
    name = fin_employee_m2o[1] if isinstance(fin_employee_m2o, list) and len(fin_employee_m2o) >= 2 else None
    if not name:
        return None

    recs = search_read(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        "hr.employee",
        domain=[("name", "=", name)],
        fields=["id", "name"],
        limit=1
    )
    if recs:
        return recs[0]["id"]

    recs = search_read(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        "hr.employee",
        domain=[("name", "ilike", name)],
        fields=["id", "name"],
        limit=1
    )
    if recs:
        return recs[0]["id"]

    return None

def hrm_existing_slip(hrm_models, hrm_uid, hrm_slip_fields, hrm_run_id, hrm_emp_id, date_from, date_to):
    dom = [
        (hrm_slip_fields["run_link"], "=", hrm_run_id),
        (hrm_slip_fields["employee"], "=", hrm_emp_id),
        (hrm_slip_fields["date_from"], "=", date_from),
        (hrm_slip_fields["date_to"], "=", date_to),
    ]
    recs = search_read(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        "hr.payslip",
        domain=dom,
        fields=["id"],
        limit=1,
        order="id desc"
    )
    return recs[0]["id"] if recs else None

def hrm_set_inputs_from_fin(hrm_models, hrm_uid, hrm_payslip_id, fin_amounts):
    try:
        fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip.input")
    except Exception:
        return

    slip = read(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip", [hrm_payslip_id], fields=["input_line_ids"])[0]
    input_ids = norm_ids(slip.get("input_line_ids"))
    inputs = []
    if input_ids:
        inputs = search_read(
            hrm_models, HRM_DB, hrm_uid, HRM_KEY,
            "hr.payslip.input",
            domain=[("id", "in", input_ids)],
            fields=["id", "code", "amount"],
            limit=0
        )

    by_code = {i.get("code"): i for i in inputs if i.get("code")}
    desired = {
        "BASIC": fin_amounts.get("BASIC", 0.0),
        "GROSS": fin_amounts.get("GROSS", 0.0),
        "NET": fin_amounts.get("NET", 0.0),
    }

    for code, amount in desired.items():
        rec = by_code.get(code)
        if rec:
            if DRY_RUN:
                print(f"[DRY] would write input {rec['id']} code={code} amount={amount}")
            else:
                write(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip.input", [rec["id"]], {"amount": amount})

def hrm_compute_sheet(hrm_models, hrm_uid, hrm_payslip_id):
    ids = norm_ids([hrm_payslip_id])
    ok = try_call(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip", "compute_sheet", args=[ids])
    if ok is not None:
        return True

    ok = try_call(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip", "action_refresh_from_work_entries", args=[ids])
    if ok is not None:
        try_call(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip", "compute_sheet", args=[ids])
        return True

    return False
def hrm_validate_and_paid(hrm_models, hrm_uid, hrm_payslip_id):
    """
    Force workflow:
    draft -> validated -> paid
    Safe: try-call beberapa kandidat method.
    """
    ids = [norm_id(hrm_payslip_id)]

    # 1️⃣ VALIDATE
    validate_methods = [
        "action_payslip_done",   # paling umum
        "action_validate",
        "action_confirm",
    ]

    validated = False
    for m in validate_methods:
        ok = try_call(
            hrm_models, HRM_DB, hrm_uid, HRM_KEY,
            "hr.payslip", m, args=[ids]
        )
        if ok is not None:
            print(f"[WF] validated via {m}")
            validated = True
            break

    # 2️⃣ PAID
    paid_methods = [
        "action_payslip_paid",
        "action_mark_as_paid",
    ]

    paid = False
    for m in paid_methods:
        ok = try_call(
            hrm_models, HRM_DB, hrm_uid, HRM_KEY,
            "hr.payslip", m, args=[ids]
        )
        if ok is not None:
            print(f"[WF] paid via {m}")
            paid = True
            break

    # 3️⃣ PRINT FINAL STATE
    st = read(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        "hr.payslip", ids, fields=["state"]
    )[0]["state"]

    print(f"[STATE] HRM payslip {hrm_payslip_id} final state = {st}")

    return {
        "validated": validated,
        "paid": paid,
        "final_state": st,
    }

def hrm_extract_totals(hrm_models, hrm_uid, hrm_payslip_id):
    slip = read(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip", [hrm_payslip_id], fields=["line_ids"])[0]
    line_ids = norm_ids(slip.get("line_ids"))
    if not line_ids:
        return {"BASIC": 0.0, "GROSS": 0.0, "NET": 0.0}

    lines = search_read(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        "hr.payslip.line",
        domain=[("id", "in", line_ids)],
        fields=["code", "total"],
        limit=0
    )
    m = {l["code"]: float(l["total"] or 0.0) for l in lines if l.get("code")}
    return {"BASIC": m.get("BASIC", 0.0), "GROSS": m.get("GROSS", 0.0), "NET": m.get("NET", 0.0)}

def sync_fin_to_hrm(fin_models, fin_uid, hrm_models, hrm_uid, fin_run_id):
    fin_run, fin_slips, fin_ps = get_fin_run_and_slips(fin_models, fin_uid, fin_run_id)
    print(f"[FIN] run id={fin_run['id']} name={fin_run['name']} state={fin_run.get('state')}")
    print(f"[FIN] slips in run: {len(fin_slips)}")

    hrm_run_id_raw = hrm_find_or_create_run(hrm_models, hrm_uid, fin_run)
    hrm_run_id = norm_id(hrm_run_id_raw)

    if not hrm_run_id:
        print("[DRY] HRM run id unknown (dry). Exiting sync.")
        return None
    print(f"[HRM] payrun id={hrm_run_id} (target)")
    print(f"[HRM] Open PayRun URL: {HRM_URL}/web#id={hrm_run_id}&model=hr.payslip.run&view_type=form")


    hrm_slip_fields = detect_payslip_fields(hrm_models, HRM_DB, hrm_uid, HRM_KEY)
    struct_id = find_structure_id(hrm_models, hrm_uid)
    print(f"[HRM] structure picked: {HRM_STRUCTURE_NAME} => id={struct_id}")

    run_company_id = hrm_get_run_company_id(hrm_models, hrm_uid, hrm_run_id)
    if run_company_id:
        print(f"[HRM] payrun company_id={run_company_id} (will force on payslips)")

    ps_fields_cache = fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip")
    has_name = "name" in ps_fields_cache
    has_company = "company_id" in ps_fields_cache

    ok = 0
    create_n = 0
    update_n = 0
    fail = 0

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for s in fin_slips:
        fin_slip_id = s["id"]
        fin_emp = s.get(fin_ps["employee"])
        fin_emp_name = fin_emp[1] if isinstance(fin_emp, list) and len(fin_emp) > 1 else str(fin_emp)

        try:
            hrm_emp_id = hrm_find_employee(hrm_models, hrm_uid, fin_emp)
            if not hrm_emp_id:
                print(f"[SKIP] employee not found in HRM: {fin_emp_name}")
                continue

            date_from = s.get(fin_ps["date_from"])
            date_to = s.get(fin_ps["date_to"])
            if not date_from or not date_to:
                print(f"[SKIP] missing dates in FIN slip={fin_slip_id} emp={fin_emp_name} date_from={date_from} date_to={date_to}")
                continue

            existing_id = hrm_existing_slip(
                hrm_models, hrm_uid, hrm_slip_fields,
                hrm_run_id, hrm_emp_id, date_from, date_to
            )

            if existing_id:
                hrm_slip_id = existing_id
                is_create = False
                patch = {
                    hrm_slip_fields["run_link"]: hrm_run_id,
                    hrm_slip_fields["struct"]: struct_id,
                }
                if run_company_id and has_company:
                    patch["company_id"] = run_company_id
                if not DRY_RUN:
                    write(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip", [hrm_slip_id], patch)
            else:
                vals = {
                    hrm_slip_fields["employee"]: hrm_emp_id,
                    hrm_slip_fields["date_from"]: date_from,
                    hrm_slip_fields["date_to"]: date_to,
                    hrm_slip_fields["struct"]: struct_id,
                    hrm_slip_fields["run_link"]: hrm_run_id,
                }
                if run_company_id and has_company:
                    vals["company_id"] = run_company_id
                if has_name:
                    vals["name"] = f"Salary Slip - {fin_emp_name} - {fin_run['name']} (SYNC {now_str})"

                if DRY_RUN:
                    print(f"[DRY] would create HRM payslip for {fin_emp_name}: {vals}")
                    continue

                hrm_slip_id = create(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip", vals)
                is_create = True

            fin_amounts = fin_get_amounts(fin_models, fin_uid, fin_slip_id) or {"BASIC": 0.0, "GROSS": 0.0, "NET": 0.0}

            if not DRY_RUN:
                hrm_set_inputs_from_fin(hrm_models, hrm_uid, hrm_slip_id, fin_amounts)
                hrm_compute_sheet(hrm_models, hrm_uid, hrm_slip_id)
                wf = hrm_validate_and_paid(hrm_models, hrm_uid, hrm_slip_id)
            totals = hrm_extract_totals(hrm_models, hrm_uid, hrm_slip_id) if not DRY_RUN else fin_amounts

            tag = "create" if is_create else "update"
            print(f"[OK] {fin_emp_name} -> HRM slip={hrm_slip_id} "
                  f"BASIC={totals['BASIC']:.1f} GROSS={totals['GROSS']:.1f} NET={totals['NET']:.1f} ({tag})")

            ok += 1
            if is_create:
                create_n += 1
            else:
                update_n += 1

        except xmlrpc.client.Fault as e:
            fail += 1
            print(f"[ERR] FIN slip={fin_slip_id} emp={fin_emp_name} => {e}")
        except Exception as e:
            fail += 1
            print(f"[ERR] FIN slip={fin_slip_id} emp={fin_emp_name} => {e}")
            traceback.print_exc()

    print(f"[DONE][SYNC] ok={ok} create={create_n} update={update_n} fail={fail} DRY_RUN={DRY_RUN}")
    print(f"[NEXT][SYNC] {HRM_URL}/web#id={hrm_run_id}&model=hr.payslip.run&view_type=form")
    return hrm_run_id


# ==========================
# BUSINESS LOGIC (PATCH)
# ==========================
def patch_hrm_payrun_links(hrm_models, hrm_uid, hrm_run_id):
    ps_fg = fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip")
    run_fg = fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip.run")

    link_fields = [f for f in ("payslip_run_id", "run_id", "batch_id") if f in ps_fg]
    if not link_fields:
        raise RuntimeError("No run link field found on hr.payslip (need payslip_run_id/run_id/batch_id)")

    print("[PATCH] Detected link fields on hr.payslip:", link_fields)

    run_company_id = None
    if "company_id" in run_fg:
        run_rec = read(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip.run", [hrm_run_id], ["name", "company_id"])[0]
        if run_rec.get("company_id"):
            run_company_id = run_rec["company_id"][0]
        print("[PATCH] Payrun:", run_rec.get("name"), "company_id:", run_company_id)
    else:
        run_rec = read(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip.run", [hrm_run_id], ["name"])[0]
        print("[PATCH] Payrun:", run_rec.get("name"), "(no company_id field)")

    base_ids = []
    for lf in link_fields:
        ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip", [(lf, "=", hrm_run_id)])
        print(f"[PATCH] Count by {lf} = {len(ids)}")
        if ids:
            base_ids = ids
            break

    if not base_ids:
        print("[PATCH] No payslips found by link fields. Trying fallback search by date range of payrun...")
        run_det = read(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip.run", [hrm_run_id], ["date_start", "date_end"])[0]
        ds = run_det.get("date_start")
        de = run_det.get("date_end")

        date_from = "date_from" if "date_from" in ps_fg else ("date_start" if "date_start" in ps_fg else None)
        date_to   = "date_to"   if "date_to"   in ps_fg else ("date_end"   if "date_end"   in ps_fg else None)
        if not date_from or not date_to:
            raise RuntimeError("Cannot detect payslip date fields for fallback search.")

        base_ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip", [(date_from, "=", ds), (date_to, "=", de)])
        print("[PATCH] Fallback payslips by date:", len(base_ids))

    if not base_ids:
        print("[PATCH] Still no payslips found. Stop.")
        return False

    patch = {lf: hrm_run_id for lf in link_fields}

    if run_company_id and "company_id" in ps_fg:
        patch["company_id"] = run_company_id

    if DRY_RUN:
        print("[DRY][PATCH] would write:", patch, "to payslips:", base_ids[:10], "...")
        return True

    ok = write(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.payslip", base_ids, patch)
    print("[PATCH] WRITE result:", ok)
    print("[PATCH] Patched payslip ids sample:", base_ids[:10])
    print("[PATCH] Open Payrun:")
    print(f"{HRM_URL}/web#id={hrm_run_id}&model=hr.payslip.run&view_type=form")
    return True


# ==========================
# WRAPPER: callable from app.py
# ==========================
def run_latest_paid(dry_run=None, do_patch=None, hrm_run_id_force=None):
    """
    Dipanggil dari app.py route.
    - dry_run/do_patch/hrm_run_id_force OPTIONAL; kalau None pakai CONFIG global.
    Return dict biar bisa jsonify.
    """
    global DRY_RUN, DO_PATCH, HRM_RUN_ID_FORCE

    if dry_run is not None:
        DRY_RUN = bool(dry_run)
    if do_patch is not None:
        DO_PATCH = bool(do_patch)
    if hrm_run_id_force is not None:
        HRM_RUN_ID_FORCE = hrm_run_id_force

    fin_common, fin_models = connect(FIN_URL)
    hrm_common, hrm_models = connect(HRM_URL)

    fin_uid = login(fin_common, FIN_DB, FIN_USER, FIN_KEY) if DO_SYNC else None
    hrm_uid = login(hrm_common, HRM_DB, HRM_USER, HRM_KEY)

    latest = None
    fin_run_id = None
    hrm_run_id = None

    if DO_SYNC:
        latest = find_latest_paid_payrun_in_finance(fin_models, FIN_DB, fin_uid, FIN_KEY)
        if not latest:
            raise RuntimeError("No FINANCE payrun found with state='03_paid'.")

        fin_run_id = latest["id"]
        hrm_run_id = sync_fin_to_hrm(fin_models, fin_uid, hrm_models, hrm_uid, fin_run_id)
        if not hrm_run_id:
            raise RuntimeError("Sync didn't produce HRM payrun id.")

    patched = None
    if DO_PATCH:
        target_run_id = hrm_run_id if hrm_run_id else HRM_RUN_ID_PATCH
        patched = patch_hrm_payrun_links(hrm_models, hrm_uid, target_run_id)

    return {
        "latest_fin_payrun": latest,
        "fin_run_id": fin_run_id,
        "hrm_run_id": hrm_run_id,
        "patched": patched,
        "dry_run": DRY_RUN,
        "do_patch": DO_PATCH,
        "fin_url": f"{FIN_URL}/web#id={fin_run_id}&model=hr.payslip.run&view_type=form" if fin_run_id else None,
        "hrm_url": f"{HRM_URL}/web#id={hrm_run_id}&model=hr.payslip.run&view_type=form" if hrm_run_id else None,
    }


# ==========================
# MAIN (standalone)
# ==========================
def main():
    try:
        res = run_latest_paid(dry_run=None, do_patch=None, hrm_run_id_force=None)
        print("[MAIN] OK:", res)
    except Exception as e:
        print("[MAIN] ERROR:", e)
        traceback.print_exc()


if __name__ == "__main__":
    main()
