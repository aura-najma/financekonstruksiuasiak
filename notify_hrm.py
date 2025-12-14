import xmlrpc.client
from datetime import datetime

# ==========================
# FINANCE (SUMBER PAYRUN)
# ==========================
FIN_URL = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB  = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"

# ==========================
# HRM (TARGET NOTIF)
# ==========================
HRM_URL  = "https://konstruksi-perumahan2.odoo.com"
HRM_DB   = "konstruksi-perumahan2"
HRM_USER = "fauziah.hamidah.al-2022@ftmm.unair.ac.id"
HRM_KEY  = "169979f5c07f649da80a58132ca9344cdc6d3ada"

TARGET_USER_EMAIL = "fauziah.hamidah.al-2022@ftmm.unair.ac.id"

PAYRUN_NOTE_MARKER = "[AUTO NOTIF PAYRUN 03_PAID]"

# ==========================
# CONNECT + UTIL
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

def get_partner_id_by_user_email(models, db, uid, key, email):
    user_id = find_one(
        models, db, uid, key,
        "res.users",
        ["|", ("login", "=", email), ("email", "=", email)]
    )
    if not user_id:
        return False

    user = models.execute_kw(
        db, uid, key, "res.users", "read",
        [[user_id]], {"fields": ["partner_id", "name", "login", "email"]}
    )[0]
    partner = user.get("partner_id")
    return partner[0] if partner else False

def already_notified(models, db, uid, key, model, res_id, marker):
    msg_ids = models.execute_kw(
        db, uid, key,
        "mail.message", "search",
        [[
            ("model", "=", model),
            ("res_id", "=", res_id),
            ("body", "ilike", marker),
        ]],
        {"limit": 1}
    )
    return bool(msg_ids)

def post_hrm_notification(models, db, uid, key, model, res_id, target_partner_id, subject, body_html, marker=None):
    if marker:
        body_html = f"{marker}<br/>{body_html}"

    vals = {
        "model": model,
        "res_id": res_id,
        "subject": subject,
        "body": body_html,

        # penting: bikin notif ke inbox (Discuss)
        "message_type": "notification",
        "subtype_id": False,

        # target penerima
        "partner_ids": [(4, target_partner_id)],
    }
    return models.execute_kw(db, uid, key, "mail.message", "create", [vals])

# ==========================
# FINANCE: AUTO DETECT PAYRUN STATE 03_PAID
# ==========================
def find_latest_paid_payrun_in_finance(fin_models, db, uid, key):
    """
    Cari payrun terakhir yang state = '03_paid' di FINANCE.
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
# MAIN: NOTIFY HRM USER (FAUZIAH) WHEN PAYRUN 03_PAID
# ==========================
def notify_latest_payrun_03_paid():
    # 1) ambil payrun 03_paid dari FINANCE
    fin_uid, fin = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)

    run = find_latest_paid_payrun_in_finance(fin, FIN_DB, fin_uid, FIN_KEY)
    if not run:
        return {"message": "Tidak ada payrun dengan state '03_paid' di Finance."}

    payrun_id = run["id"]
    payrun_name = run.get("name")
    payrun_state = run.get("state")

    # 2) connect HRM untuk kirim notif ke Fauziah
    hrm_uid, hrm = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)

    target_partner_id = get_partner_id_by_user_email(hrm, HRM_DB, hrm_uid, HRM_KEY, TARGET_USER_EMAIL)
    if not target_partner_id:
        raise Exception(f"User HRM target tidak ditemukan: {TARGET_USER_EMAIL}")

    # 3) anti duplikasi: tempel marker di partner (biar ga spam)
    marker = f"{PAYRUN_NOTE_MARKER} FIN_PAYRUN_ID={payrun_id}"

    if already_notified(hrm, HRM_DB, hrm_uid, HRM_KEY, "res.partner", target_partner_id, marker):
        return {"message": "Notif untuk payrun ini sudah pernah dikirim ke HRM.", "payrun_id": payrun_id, "payrun_name": payrun_name}

    subject = "Pay Run sudah PAID (03_paid) — Finance"
    body = f"""
    <p><b>Pay Run sudah PAID (03_paid)</b></p>
    <p>Pay Run: <b>{payrun_name}</b> (ID Finance: {payrun_id})</p>
    <p>Status: <b>{payrun_state}</b></p>
    <p>Waktu notif: <b>{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</b></p>
    """

    # kirim notif ke partner record (res.partner)
    post_hrm_notification(
        hrm, HRM_DB, hrm_uid, HRM_KEY,
        model="res.partner",
        res_id=target_partner_id,
        target_partner_id=target_partner_id,
        subject=subject,
        body_html=body,
        marker=marker
    )

    return {"message": "✅ Notif '03_paid' terkirim ke HRM (Fauziah).", "payrun_id": payrun_id, "payrun_name": payrun_name, "state": payrun_state}
