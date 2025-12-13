import xmlrpc.client
from datetime import datetime, timedelta

# ==========================
# KONFIGURASI DEFAULT
# ==========================
FIN_URL  = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB   = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"

HRM_URL  = "https://konstruksi-perumahan2.odoo.com"
HRM_DB   = "konstruksi-perumahan2"
HRM_USER = "fauziah.hamidah.al-2022@ftmm.unair.ac.id"
HRM_KEY  = "169979f5c07f649da80a58132ca9344cdc6d3ada"


# ==========================
# XMLRPC HELPERS
# ==========================
def connect(url, db, user, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, key, {})
    if not uid:
        raise RuntimeError(f"Gagal login ke {url} db={db}")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models

def fields_get(models, db, uid, key, model):
    return models.execute_kw(db, uid, key, model, "fields_get", [], {"attributes": ["type", "required"]})

def has_field(fg, field_name):
    return field_name in fg

def search(models, db, uid, key, model, domain, offset=0, limit=0, order=None):
    kwargs = {"offset": offset}
    if limit:
        kwargs["limit"] = limit
    if order:
        kwargs["order"] = order
    return models.execute_kw(db, uid, key, model, "search", [domain], kwargs)

def read(models, db, uid, key, model, ids, fields):
    return models.execute_kw(db, uid, key, model, "read", [ids], {"fields": fields})

def search_read(models, db, uid, key, model, domain, fields, offset=0, limit=0, order=None):
    kwargs = {"fields": fields, "offset": offset}
    if limit:
        kwargs["limit"] = limit
    if order:
        kwargs["order"] = order
    return models.execute_kw(db, uid, key, model, "search_read", [domain], kwargs)

def create(models, db, uid, key, model, vals, dry_run=False):
    if dry_run:
        return 0
    return models.execute_kw(db, uid, key, model, "create", [vals])

def pick_first_existing(fg, candidates):
    for c in candidates:
        if c in fg:
            return c
    return None

def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def dt_from_date_and_duration(date_str, duration_hours):
    start = datetime.strptime(date_str, "%Y-%m-%d")
    dur = float(duration_hours or 0.0)
    stop = start + timedelta(hours=dur)
    return start.strftime("%Y-%m-%d %H:%M:%S"), stop.strftime("%Y-%m-%d %H:%M:%S")


# ======================================================
# CORE LOGIC (TIDAK AUTO-RUN)
# ======================================================
def sync_hrm_work_entries_to_finance(
    date_from="2025-01-01",
    date_to="2027-01-01",
    batch_size=500,
    dry_run=False,
):
    """
    Dipanggil dari app.py atau CLI
    WAJIB return dict (buat jsonify)
    """

    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    hrm_uid, hrm_models = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)

    fin_we_fg = fields_get(fin_models, FIN_DB, fin_uid, FIN_KEY, "hr.work.entry")
    hrm_we_fg = fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.work.entry")

    HRM_DATE_FIELD = pick_first_existing(hrm_we_fg, ["date"])
    FIN_DATE_FIELD = pick_first_existing(fin_we_fg, ["date"])
    FIN_START_FIELD = pick_first_existing(fin_we_fg, ["date_start", "date_from"])
    FIN_STOP_FIELD  = pick_first_existing(fin_we_fg, ["date_stop", "date_to"])

    if not HRM_DATE_FIELD:
        raise RuntimeError("HRM hr.work.entry tidak punya field date")
    if not FIN_DATE_FIELD and (not FIN_START_FIELD or not FIN_STOP_FIELD):
        raise RuntimeError("FIN hr.work.entry tidak punya field tanggal yang dikenali")

    # ==========================
    # PRELOAD HRM DATA
    # ==========================
    hrm_domain = [(HRM_DATE_FIELD, ">=", date_from), (HRM_DATE_FIELD, "<", date_to)]
    hrm_ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY,
                     "hr.work.entry", hrm_domain, order=f"{HRM_DATE_FIELD} asc")

    created = skipped = already = 0

    # ==========================
    # LOAD EXISTING FIN KEYS
    # ==========================
    fin_existing_keys = set()
    if FIN_DATE_FIELD:
        fin_domain = [(FIN_DATE_FIELD, ">=", date_from), (FIN_DATE_FIELD, "<", date_to)]
        fin_fields = ["employee_id", "work_entry_type_id", FIN_DATE_FIELD, "duration"]
    else:
        fin_domain = [(FIN_START_FIELD, ">=", f"{date_from} 00:00:00"),
                      (FIN_START_FIELD, "<", f"{date_to} 00:00:00")]
        fin_fields = ["employee_id", "work_entry_type_id", FIN_START_FIELD, FIN_STOP_FIELD, "duration"]

    offset = 0
    while True:
        page = search_read(fin_models, FIN_DB, fin_uid, FIN_KEY,
                           "hr.work.entry", fin_domain, fin_fields,
                           offset=offset, limit=1000)
        if not page:
            break
        for we in page:
            emp = we["employee_id"][0] if we.get("employee_id") else 0
            wty = we["work_entry_type_id"][0] if we.get("work_entry_type_id") else 0
            if FIN_DATE_FIELD:
                fin_existing_keys.add((emp, wty, we.get(FIN_DATE_FIELD)))
            else:
                fin_existing_keys.add((emp, wty, we.get(FIN_START_FIELD), we.get(FIN_STOP_FIELD)))
        offset += 1000

    # ==========================
    # CREATE WORK ENTRIES
    # ==========================
    hrm_fields = ["name", "employee_id", "work_entry_type_id", HRM_DATE_FIELD, "duration"]

    for batch_ids in chunked(hrm_ids, batch_size):
        batch = read(hrm_models, HRM_DB, hrm_uid, HRM_KEY,
                     "hr.work.entry", batch_ids, hrm_fields)

        for we in batch:
            emp = we["employee_id"][0] if we.get("employee_id") else None
            wty = we["work_entry_type_id"][0] if we.get("work_entry_type_id") else None
            d = we.get(HRM_DATE_FIELD)
            dur = float(we.get("duration") or 0.0)

            if FIN_DATE_FIELD:
                key = (emp, wty, d)
                if key in fin_existing_keys:
                    already += 1
                    continue
                vals = {
                    "name": we.get("name") or "Work Entry",
                    "employee_id": emp,
                    "work_entry_type_id": wty,
                    FIN_DATE_FIELD: d,
                    "duration": dur,
                }
            else:
                ds, de = dt_from_date_and_duration(d, dur)
                key = (emp, wty, ds, de)
                if key in fin_existing_keys:
                    already += 1
                    continue
                vals = {
                    "name": we.get("name") or "Work Entry",
                    "employee_id": emp,
                    "work_entry_type_id": wty,
                    FIN_START_FIELD: ds,
                    FIN_STOP_FIELD: de,
                    "duration": dur,
                }

            create(fin_models, FIN_DB, fin_uid, FIN_KEY,
                   "hr.work.entry", vals, dry_run=dry_run)
            fin_existing_keys.add(key)
            created += 1

    return {
        "range": {"from": date_from, "to": date_to},
        "dry_run": dry_run,
        "created": created,
        "already_exists": already,
        "skipped": skipped,
    }

