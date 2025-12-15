# update_task_to_hrm.py
import re
import traceback
import xmlrpc.client
from datetime import datetime

# ==========================
# KONFIGURASI (EDIT SESUAI KAMU)
# ==========================
FIN_URL  = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB   = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"

HRM_URL  = "https://konstruksi-perumahan2.odoo.com"
HRM_DB   = "konstruksi-perumahan2"
HRM_USER = "fauziah.hamidah.al-2022@ftmm.unair.ac.id"
HRM_KEY  = "169979f5c07f649da80a58132ca9344cdc6d3ada"

# default flags (bisa dioverride dari app route)
DRY_RUN = False
ONLY_PROJECT_NAMES = ["Project Perumahan 1"]
SYNC_TIMESHEETS   = True
SYNC_DESCRIPTION  = True
SYNC_STATUS       = True


# ==========================
# XMLRPC HELPERS
# ==========================
def connect(url, db, user, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, key, {})
    if not uid:
        raise RuntimeError(f"Gagal login: {db} ({user}). Cek DB/USER/API KEY.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models

def fields_get(models, db, uid, key, model, attrs=None):
    attrs = attrs or ["type", "string", "selection", "required", "relation"]
    return models.execute_kw(db, uid, key, model, "fields_get", [], {"attributes": attrs})

def pick_fields(field_map, wanted):
    return [f for f in wanted if f and (f in field_map)]

def search_read(models, db, uid, key, model, domain, fields, limit=0, order=None, ctx=None):
    kwargs = {"fields": fields, "limit": limit}
    if order:
        kwargs["order"] = order
    if ctx:
        kwargs["context"] = ctx
    return models.execute_kw(db, uid, key, model, "search_read", [domain], kwargs)

def search(models, db, uid, key, model, domain, limit=0, order=None, ctx=None):
    kwargs = {"limit": limit}
    if order:
        kwargs["order"] = order
    if ctx:
        kwargs["context"] = ctx
    return models.execute_kw(db, uid, key, model, "search", [domain], kwargs)

def read(models, db, uid, key, model, ids, fields, ctx=None):
    kwargs = {"fields": fields}
    if ctx:
        kwargs["context"] = ctx
    return models.execute_kw(db, uid, key, model, "read", [ids], kwargs)

def create(models, db, uid, key, model, vals, ctx=None):
    kwargs = {}
    if ctx:
        kwargs["context"] = ctx
    return models.execute_kw(db, uid, key, model, "create", [vals], kwargs)

def write(models, db, uid, key, model, ids, vals, ctx=None):
    kwargs = {}
    if ctx:
        kwargs["context"] = ctx
    return models.execute_kw(db, uid, key, model, "write", [ids, vals], kwargs)

def safe_m2o_id(val):
    if isinstance(val, list) and val:
        return val[0]
    if isinstance(val, int):
        return val
    return None

def safe_m2o_name(val):
    if isinstance(val, list) and len(val) >= 2:
        return val[1]
    return None

def find_first_existing(field_map, candidates):
    for c in candidates:
        if c in field_map:
            return c
    return None

def build_selection_mapper(src_field_def, dst_field_def):
    src_sel = src_field_def.get("selection") or []
    dst_sel = dst_field_def.get("selection") or []

    src_key_to_label = {k: (lbl or "").strip() for k, lbl in src_sel}
    dst_key_to_label = {k: (lbl or "").strip() for k, lbl in dst_sel}

    # if keys overlap, we can use direct
    if set(src_key_to_label.keys()) & set(dst_key_to_label.keys()):
        return lambda v: v

    # else map by label
    dst_label_to_key = {lbl.lower(): k for k, lbl in dst_key_to_label.items()}
    src_key_to_dst_key = {}
    for sk, slbl in src_key_to_label.items():
        dk = dst_label_to_key.get((slbl or "").lower())
        if dk:
            src_key_to_dst_key[sk] = dk

    def mapper(v):
        if v is None:
            return None
        return src_key_to_dst_key.get(v, None)

    return mapper


# ==========================
# TEXT CLEANERS (TIMESHEETS)
# ==========================
def clean_timesheet_desc(desc: str) -> str:
    if not desc:
        return ""
    s = desc.strip()
    s = re.sub(r"\[(?:HRM_TS|FIN_TS|SCM_TS):\d+\]\s*", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s).strip()

    lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
    uniq = []
    for ln in lines:
        if not uniq or uniq[-1] != ln:
            uniq.append(ln)
    return "\n".join(uniq).strip()


# ==========================
# HRM (DEST) ENSURE HELPERS
# ==========================
def ensure_project_in_hrm(hrm_models, hrm_uid, project_name, stats=None):
    ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "project.project",
                 [("name", "=", project_name)], limit=1)
    if ids:
        return ids[0]

    if DRY_RUN:
        return None

    pid = create(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "project.project",
                 {"name": project_name, "active": True})
    if stats is not None:
        stats["projects_created"] += 1
    return pid

def ensure_stage_in_hrm_for_project(hrm_models, hrm_uid, hrm_project_id, stage_name, sequence=10, stats=None):
    existing = search(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        "project.task.type",
        [("name", "=", stage_name), ("project_ids", "in", [hrm_project_id])],
        limit=1
    )
    if existing:
        return existing[0]

    if DRY_RUN:
        return None

    sid = create(
        hrm_models, HRM_DB, hrm_uid, HRM_KEY,
        "project.task.type",
        {"name": stage_name, "sequence": sequence, "project_ids": [(4, hrm_project_id)]}
    )
    if stats is not None:
        stats["stages_created"] += 1
    return sid


# ==========================
# EMPLOYEE / USER MAPPING (TO HRM)
# ==========================
def map_employee_to_hrm(hrm_models, hrm_uid, employee_name):
    if not employee_name:
        return None
    ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.employee", [("name", "=", employee_name)], limit=1)
    if ids:
        return ids[0]
    ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.employee", [("name", "ilike", employee_name)], limit=1)
    return ids[0] if ids else None

def map_user_to_hrm(hrm_models, hrm_uid, user_name):
    if not user_name:
        return None
    ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "res.users", [("name", "=", user_name)], limit=1)
    if ids:
        return ids[0]
    ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "res.users", [("name", "ilike", user_name)], limit=1)
    return ids[0] if ids else None

def get_employee_company_id_hrm(hrm_models, hrm_uid, employee_id):
    if not employee_id:
        return None, None
    emp_fields = fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.employee")
    f_active  = "active" if "active" in emp_fields else None
    f_company = "company_id" if "company_id" in emp_fields else None

    want = []
    if f_active: want.append(f_active)
    if f_company: want.append(f_company)
    if not want:
        return True, None

    recs = read(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.employee", [employee_id], want)
    if not recs:
        return None, None
    active = recs[0].get("active") if f_active else True
    company_id = safe_m2o_id(recs[0].get("company_id")) if f_company else None
    return active, company_id

def get_user_company_id_hrm(hrm_models, hrm_uid, user_id):
    try:
        user_fields = fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "res.users")
    except Exception:
        return None
    f_company = "company_id" if "company_id" in user_fields else None
    if not f_company:
        return None
    recs = read(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "res.users", [user_id], [f_company])
    if not recs:
        return None
    return safe_m2o_id(recs[0].get(f_company))

def get_default_employee_for_user_hrm(hrm_models, hrm_uid):
    emp_fields = fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.employee")
    has_active = "active" in emp_fields
    domain = [("user_id", "=", hrm_uid)]
    if has_active:
        domain.append(("active", "=", True))
    emp_ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.employee", domain, limit=1)
    if emp_ids:
        return emp_ids[0]
    emp_ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "hr.employee", [("user_id", "=", hrm_uid)], limit=1)
    return emp_ids[0] if emp_ids else None

def resolve_employee_for_timesheet_hrm(hrm_models, hrm_uid, preferred_emp_id):
    if preferred_emp_id:
        active, company_id = get_employee_company_id_hrm(hrm_models, hrm_uid, preferred_emp_id)
        if active:
            if company_id:
                return preferred_emp_id, company_id, [company_id]
            user_company = get_user_company_id_hrm(hrm_models, hrm_uid, hrm_uid)
            if user_company:
                return preferred_emp_id, user_company, [user_company]

    fallback_emp = get_default_employee_for_user_hrm(hrm_models, hrm_uid)
    if fallback_emp:
        active, company_id = get_employee_company_id_hrm(hrm_models, hrm_uid, fallback_emp)
        if active:
            if company_id:
                return fallback_emp, company_id, [company_id]
            user_company = get_user_company_id_hrm(hrm_models, hrm_uid, hrm_uid)
            if user_company:
                return fallback_emp, user_company, [user_company]

    user_company = get_user_company_id_hrm(hrm_models, hrm_uid, hrm_uid)
    if user_company:
        return None, user_company, [user_company]

    return None, None, None

def get_project_analytic_account_id_hrm(hrm_models, hrm_uid, hrm_project_id):
    proj_fields = fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "project.project")
    f = None
    for cand in ["analytic_account_id", "account_id"]:
        if cand in proj_fields:
            f = cand
            break
    if not f:
        return None
    recs = read(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "project.project", [hrm_project_id], [f])
    if not recs:
        return None
    return safe_m2o_id(recs[0].get(f))


# ==========================
# TASK UPSERT -> HRM
# ==========================
def upsert_task_hrm(
    hrm_models, hrm_uid, hrm_task_fields_map,
    hrm_project_id,
    task_name,
    stage_id=None,
    description=None,
    deadline=None,
    kanban_state=None,
    parent_id=None,
    status_field_name=None,
    status_value=None,
    stats=None,
):
    domain = [("name", "=", task_name), ("project_id", "=", hrm_project_id)]
    if "parent_id" in hrm_task_fields_map:
        domain.append(("parent_id", "=", parent_id or False))

    ids = search(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "project.task", domain, limit=1)
    existing_id = ids[0] if ids else None

    vals = {}
    if "name" in hrm_task_fields_map:
        vals["name"] = task_name
    if "project_id" in hrm_task_fields_map:
        vals["project_id"] = hrm_project_id
    if stage_id and "stage_id" in hrm_task_fields_map:
        vals["stage_id"] = stage_id
    if deadline and "date_deadline" in hrm_task_fields_map:
        vals["date_deadline"] = deadline
    if kanban_state and "kanban_state" in hrm_task_fields_map:
        vals["kanban_state"] = kanban_state
    if "parent_id" in hrm_task_fields_map:
        vals["parent_id"] = parent_id or False

    if description and ("description" in hrm_task_fields_map):
        vals["description"] = description

    if status_field_name and (status_field_name in hrm_task_fields_map) and (status_value is not None):
        vals[status_field_name] = status_value

    if existing_id:
        if DRY_RUN:
            return existing_id
        if vals:
            write(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "project.task", [existing_id], vals)
        if stats is not None:
            stats["tasks_updated"] += 1
        return existing_id

    if DRY_RUN:
        return None

    new_id = create(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "project.task", vals)
    if stats is not None:
        stats["tasks_created"] += 1
    return new_id


# ==========================
# TIMESHEET SYNC: FIN (SRC) -> HRM (DST)
# ==========================
def sync_timesheets_for_task_fin_to_hrm(
    fin_models, fin_uid,
    hrm_models, hrm_uid,
    fin_task_id, hrm_task_id,
    fin_project_id, hrm_project_id,
    stats=None,
):
    try:
        fin_aal_fields = fields_get(fin_models, FIN_DB, fin_uid, FIN_KEY, "account.analytic.line")
        hrm_aal_fields = fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "account.analytic.line")
    except Exception:
        return

    wanted_fin = ["id", "date", "name", "unit_amount", "employee_id", "user_id", "task_id", "project_id"]
    fin_fields = pick_fields(fin_aal_fields, wanted_fin)

    domain = [("task_id", "=", fin_task_id)]
    if "project_id" in fin_aal_fields:
        domain.append(("project_id", "=", fin_project_id))

    fin_lines = search_read(
        fin_models, FIN_DB, fin_uid, FIN_KEY,
        "account.analytic.line",
        domain,
        fields=fin_fields,
        limit=0,
        order="date asc,id asc"
    )
    if not fin_lines:
        return

    # HRM field presence
    name_field = "name" if "name" in hrm_aal_fields else None
    date_field = "date" if "date" in hrm_aal_fields else None
    unit_field = "unit_amount" if "unit_amount" in hrm_aal_fields else None
    task_field = "task_id" if "task_id" in hrm_aal_fields else None
    proj_field = "project_id" if "project_id" in hrm_aal_fields else None
    acct_field = "account_id" if "account_id" in hrm_aal_fields else None
    emp_field  = "employee_id" if "employee_id" in hrm_aal_fields else None
    user_field = "user_id" if "user_id" in hrm_aal_fields else None
    company_field = "company_id" if "company_id" in hrm_aal_fields else None

    if not (name_field and date_field and unit_field and task_field):
        return

    hrm_account_required = bool((hrm_aal_fields.get("account_id") or {}).get("required"))
    hrm_analytic_account_id = get_project_analytic_account_id_hrm(hrm_models, hrm_uid, hrm_project_id)
    if hrm_account_required and (not hrm_analytic_account_id):
        return

    hrm_employee_required = bool((hrm_aal_fields.get("employee_id") or {}).get("required"))

    ext_candidates = ["x_fin_ts_id", "x_studio_fin_ts_id", "x_fin_timesheet_id", "x_external_fin_ts_id"]
    EXT_ID_FIELD = find_first_existing(hrm_aal_fields, ext_candidates)

    for ln in fin_lines:
        fin_ts_id = ln.get("id")
        fin_date = ln.get("date")
        fin_desc_raw = (ln.get("name") or "").strip()
        fin_hours = ln.get("unit_amount")

        hrm_name = clean_timesheet_desc(fin_desc_raw)

        preferred_emp_id = None
        if emp_field and ln.get("employee_id"):
            emp_name = safe_m2o_name(ln.get("employee_id"))
            preferred_emp_id = map_employee_to_hrm(hrm_models, hrm_uid, emp_name)

        emp_id, emp_company_id, allowed_company_ids = resolve_employee_for_timesheet_hrm(
            hrm_models, hrm_uid, preferred_emp_id
        )

        if hrm_employee_required and not emp_id:
            if stats is not None:
                stats["timesheets_skipped"] += 1
            continue

        ctx = {}
        if allowed_company_ids:
            ctx["allowed_company_ids"] = allowed_company_ids

        # dedup
        existing_id = None
        if EXT_ID_FIELD:
            ids = search(
                hrm_models, HRM_DB, hrm_uid, HRM_KEY,
                "account.analytic.line",
                [("task_id", "=", hrm_task_id), (EXT_ID_FIELD, "=", fin_ts_id)],
                limit=1
            )
            existing_id = ids[0] if ids else None
        else:
            fallback_domain = [("task_id", "=", hrm_task_id)]
            if fin_date:
                fallback_domain.append((date_field, "=", fin_date))
            if fin_hours is not None:
                fallback_domain.append((unit_field, "=", fin_hours))
            if hrm_name:
                fallback_domain.append((name_field, "=", hrm_name))
            if emp_field and emp_id:
                fallback_domain.append((emp_field, "=", emp_id))

            ids = search(
                hrm_models, HRM_DB, hrm_uid, HRM_KEY,
                "account.analytic.line",
                fallback_domain,
                limit=1
            )
            existing_id = ids[0] if ids else None

        vals = {
            name_field: hrm_name,
            date_field: fin_date,
            unit_field: fin_hours,
            task_field: hrm_task_id,
        }
        if EXT_ID_FIELD:
            vals[EXT_ID_FIELD] = fin_ts_id
        if proj_field:
            vals[proj_field] = hrm_project_id
        if acct_field and hrm_analytic_account_id:
            vals[acct_field] = hrm_analytic_account_id
        if emp_field and emp_id:
            vals[emp_field] = emp_id
        if company_field and emp_company_id:
            vals[company_field] = emp_company_id

        if user_field and ln.get("user_id"):
            user_name = safe_m2o_name(ln.get("user_id"))
            hrm_user_id = map_user_to_hrm(hrm_models, hrm_uid, user_name)
            if hrm_user_id:
                vals[user_field] = hrm_user_id

        try:
            if existing_id:
                if DRY_RUN:
                    continue
                write(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "account.analytic.line", [existing_id], vals, ctx=ctx)
                if stats is not None:
                    stats["timesheets_updated"] += 1
            else:
                if DRY_RUN:
                    continue
                create(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "account.analytic.line", vals, ctx=ctx)
                if stats is not None:
                    stats["timesheets_created"] += 1
        except Exception:
            if stats is not None:
                stats["timesheets_failed"] += 1


# ==========================
# PUBLIC ENTRYPOINT FOR FLASK ROUTE
# ==========================
def run_update_task_to_hrm(
    dry_run=False,
    only_project_names=None,
    sync_timesheets=True,
    sync_description=True,
    sync_status=True,
):
    """
    FIN -> HRM sync (projects/stages/tasks/subtasks + optional timesheets)
    Return dict suitable for jsonify().
    """
    global DRY_RUN, ONLY_PROJECT_NAMES, SYNC_TIMESHEETS, SYNC_DESCRIPTION, SYNC_STATUS
    DRY_RUN = bool(dry_run)
    ONLY_PROJECT_NAMES = only_project_names if only_project_names is not None else []
    SYNC_TIMESHEETS = bool(sync_timesheets)
    SYNC_DESCRIPTION = bool(sync_description)
    SYNC_STATUS = bool(sync_status)

    stats = {
        "started_at": datetime.utcnow().isoformat() + "Z",
        "dry_run": DRY_RUN,
        "only_project_names": ONLY_PROJECT_NAMES,
        "flags": {
            "sync_timesheets": SYNC_TIMESHEETS,
            "sync_description": SYNC_DESCRIPTION,
            "sync_status": SYNC_STATUS,
        },

        "projects_total": 0,
        "projects_created": 0,
        "projects_processed": 0,

        "stages_created": 0,

        "tasks_total": 0,
        "tasks_created": 0,
        "tasks_updated": 0,
        "subtasks_skipped_parent_unmapped": 0,

        "timesheets_created": 0,
        "timesheets_updated": 0,
        "timesheets_skipped": 0,
        "timesheets_failed": 0,
        "errors": [],
    }

    try:
        # connect
        fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
        hrm_uid, hrm_models = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)

        fin_task_fields_map = fields_get(fin_models, FIN_DB, fin_uid, FIN_KEY, "project.task")
        hrm_task_fields_map = fields_get(hrm_models, HRM_DB, hrm_uid, HRM_KEY, "project.task")

        # status mapping
        status_candidates = ["state", "status", "task_state", "x_state", "x_status", "x_task_state",
                             "x_studio_state", "x_studio_status"]
        FIN_STATUS_FIELD = find_first_existing(fin_task_fields_map, status_candidates)
        HRM_STATUS_FIELD = find_first_existing(hrm_task_fields_map, status_candidates)

        status_mapper = None
        if SYNC_STATUS and FIN_STATUS_FIELD and HRM_STATUS_FIELD:
            if (fin_task_fields_map.get(FIN_STATUS_FIELD, {}).get("type") == "selection") and \
               (hrm_task_fields_map.get(HRM_STATUS_FIELD, {}).get("type") == "selection"):
                status_mapper = build_selection_mapper(
                    fin_task_fields_map[FIN_STATUS_FIELD],
                    hrm_task_fields_map[HRM_STATUS_FIELD],
                )

        fin_desc_field = "description" if "description" in fin_task_fields_map else None
        hrm_desc_field = "description" if "description" in hrm_task_fields_map else None

        wanted_task_fields = [
            "id", "name", "stage_id", "date_deadline", "parent_id", "child_ids", "kanban_state",
            FIN_STATUS_FIELD if (SYNC_STATUS and FIN_STATUS_FIELD) else None,
            fin_desc_field if (SYNC_DESCRIPTION and fin_desc_field) else None,
        ]
        wanted_task_fields = [x for x in wanted_task_fields if x]
        fin_task_fields = pick_fields(fin_task_fields_map, wanted_task_fields)

        # projects from FIN
        proj_domain = [("active", "=", True)]
        if ONLY_PROJECT_NAMES:
            proj_domain.append(("name", "in", ONLY_PROJECT_NAMES))

        fin_projects = search_read(
            fin_models, FIN_DB, fin_uid, FIN_KEY,
            "project.project", proj_domain,
            fields=["id", "name"],
            limit=0
        )
        stats["projects_total"] = len(fin_projects)

        for p in fin_projects:
            fin_project_id = p["id"]
            project_name = (p.get("name") or "").strip()
            if not project_name:
                continue

            # ensure project in HRM
            hrm_project_id = ensure_project_in_hrm(hrm_models, hrm_uid, project_name, stats=stats)
            if hrm_project_id is None and DRY_RUN:
                stats["projects_processed"] += 1
                continue

            # stages from FIN -> ensure in HRM
            fin_stages = search_read(
                fin_models, FIN_DB, fin_uid, FIN_KEY,
                "project.task.type",
                [("project_ids", "in", [fin_project_id])],
                fields=["id", "name", "sequence"],
                limit=0
            )

            stage_map = {}
            if fin_stages:
                for s in sorted(fin_stages, key=lambda x: x.get("sequence", 10)):
                    sname = (s.get("name") or "").strip()
                    if not sname:
                        continue
                    hrm_stage_id = ensure_stage_in_hrm_for_project(
                        hrm_models, hrm_uid, hrm_project_id, sname, sequence=s.get("sequence", 10), stats=stats
                    )
                    if hrm_stage_id:
                        stage_map[sname] = hrm_stage_id

            # tasks from FIN
            fin_tasks_all = search_read(
                fin_models, FIN_DB, fin_uid, FIN_KEY,
                "project.task",
                [("project_id", "=", fin_project_id)],
                fields=fin_task_fields,
                limit=0,
                order="id asc"
            )
            stats["tasks_total"] += len(fin_tasks_all)

            # derive stages if needed
            if not stage_map:
                derived = {}
                for t in fin_tasks_all:
                    stname = safe_m2o_name(t.get("stage_id"))
                    if stname:
                        derived[stname] = True
                if derived:
                    seq = 10
                    for sname in derived.keys():
                        hrm_stage_id = ensure_stage_in_hrm_for_project(
                            hrm_models, hrm_uid, hrm_project_id, sname, sequence=seq, stats=stats
                        )
                        if hrm_stage_id:
                            stage_map[sname] = hrm_stage_id
                        seq += 10

            # split parents/children
            parents, childs = [], []
            for t in fin_tasks_all:
                pid = safe_m2o_id(t.get("parent_id")) if "parent_id" in t else None
                (childs if pid else parents).append(t)

            def map_stage_id(fin_task):
                stage_name = safe_m2o_name(fin_task.get("stage_id"))
                return stage_map.get(stage_name) if stage_name else None

            def map_status_value(fin_task):
                if not (SYNC_STATUS and FIN_STATUS_FIELD and HRM_STATUS_FIELD):
                    return None
                v = fin_task.get(FIN_STATUS_FIELD)
                if status_mapper:
                    mv = status_mapper(v)
                    return mv if mv is not None else v
                return v

            def take_description(fin_task):
                if not (SYNC_DESCRIPTION and fin_desc_field and hrm_desc_field):
                    return None
                return fin_task.get(fin_desc_field)

            fin_to_hrm_task = {}

            # upsert parents
            for t in parents:
                fin_tid = t.get("id")
                tname = (t.get("name") or "").strip()
                if not tname:
                    continue

                hrm_tid = upsert_task_hrm(
                    hrm_models, hrm_uid, hrm_task_fields_map,
                    hrm_project_id,
                    task_name=tname,
                    stage_id=map_stage_id(t),
                    description=take_description(t),
                    deadline=t.get("date_deadline"),
                    kanban_state=t.get("kanban_state") if "kanban_state" in t else None,
                    parent_id=None,
                    status_field_name=HRM_STATUS_FIELD if SYNC_STATUS else None,
                    status_value=map_status_value(t) if SYNC_STATUS else None,
                    stats=stats,
                )
                if hrm_tid and fin_tid:
                    fin_to_hrm_task[fin_tid] = hrm_tid

                if SYNC_TIMESHEETS and hrm_tid and fin_tid:
                    sync_timesheets_for_task_fin_to_hrm(
                        fin_models, fin_uid,
                        hrm_models, hrm_uid,
                        fin_task_id=fin_tid,
                        hrm_task_id=hrm_tid,
                        fin_project_id=fin_project_id,
                        hrm_project_id=hrm_project_id,
                        stats=stats,
                    )

            # upsert childs
            for t in childs:
                fin_tid = t.get("id")
                tname = (t.get("name") or "").strip()
                if not tname:
                    continue

                fin_parent_id = safe_m2o_id(t.get("parent_id"))
                hrm_parent_id = fin_to_hrm_task.get(fin_parent_id)
                if not hrm_parent_id:
                    stats["subtasks_skipped_parent_unmapped"] += 1
                    continue

                hrm_tid = upsert_task_hrm(
                    hrm_models, hrm_uid, hrm_task_fields_map,
                    hrm_project_id,
                    task_name=tname,
                    stage_id=map_stage_id(t),
                    description=take_description(t),
                    deadline=t.get("date_deadline"),
                    kanban_state=t.get("kanban_state") if "kanban_state" in t else None,
                    parent_id=hrm_parent_id,
                    status_field_name=HRM_STATUS_FIELD if SYNC_STATUS else None,
                    status_value=map_status_value(t) if SYNC_STATUS else None,
                    stats=stats,
                )
                if hrm_tid and fin_tid:
                    fin_to_hrm_task[fin_tid] = hrm_tid

                if SYNC_TIMESHEETS and hrm_tid and fin_tid:
                    sync_timesheets_for_task_fin_to_hrm(
                        fin_models, fin_uid,
                        hrm_models, hrm_uid,
                        fin_task_id=fin_tid,
                        hrm_task_id=hrm_tid,
                        fin_project_id=fin_project_id,
                        hrm_project_id=hrm_project_id,
                        stats=stats,
                    )

            stats["projects_processed"] += 1

        stats["finished_at"] = datetime.utcnow().isoformat() + "Z"
        return {"ok": True, **stats}

    except Exception as e:
        stats["finished_at"] = datetime.utcnow().isoformat() + "Z"
        stats["errors"].append(str(e))
        return {"ok": False, **stats, "trace": traceback.format_exc()}


# ==========================
# CLI RUN (optional)
# ==========================
if __name__ == "__main__":
    # contoh jalan manual:
    res = run_update_task_to_hrm(
        dry_run=False,
        only_project_names=["Project Perumahan 1"],
        sync_timesheets=True,
        sync_description=True,
        sync_status=True,
    )
    print(res)
