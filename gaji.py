
import xmlrpc.client

# ==========================
# KONFIGURASI INSTANCE ODOO
# ==========================

# FINANCE (TUJUAN)
FIN_URL = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB  = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"

# HRM (SUMBER DATA KARYAWAN)
HRM_URL  = "https://konstruksi-perumahan2.odoo.com"
HRM_DB   = "konstruksi-perumahan2"
HRM_USER = "fauziah.hamidah.al-2022@ftmm.unair.ac.id"
HRM_KEY  = "169979f5c07f649da80a58132ca9344cdc6d3ada"

# Nama Pay Run di HRM yang mau dioper ke Finance
HRM_PAYRUN_NAME = "Des 2025 - Gaji Asli"

# ==========================
# UTIL XML-RPC
# ==========================

def connect(url, db, username, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, key.strip(), {})
    if not uid:
        raise Exception(f"Gagal autentikasi ke {url} (user: {username})")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models

def search(models, db, uid, key, model, domain, limit=None):
    kwargs = {}
    if limit:
        kwargs["limit"] = limit
    return models.execute_kw(db, uid, key, model, "search", [domain], kwargs)

def read(models, db, uid, key, model, ids, fields):
    if not ids:
        return []
    return models.execute_kw(db, uid, key, model, "read", [ids], {"fields": fields})

def find_one(models, db, uid, key, model, domain):
    ids = search(models, db, uid, key, model, domain, limit=1)
    return ids[0] if ids else False

# ==========================
# MAPPING (HRM -> FINANCE)
# ==========================

_account_code_cache = {}
_fin_account_by_code_cache = {}
_journal_code_cache = {}
_fin_journal_by_code_cache = {}

def get_account_code(hrm_models, hrm_db, hrm_uid, hrm_key, account_id):
    """HRM account.account(id) -> code"""
    if not account_id:
        return None
    if account_id in _account_code_cache:
        return _account_code_cache[account_id]
    acc = read(hrm_models, hrm_db, hrm_uid, hrm_key, "account.account", [account_id], ["code", "name"])
    code = acc[0].get("code") if acc else None
    _account_code_cache[account_id] = code
    return code

def map_fin_account_id_by_code(fin_models, fin_db, fin_uid, fin_key, code):
    """Finance account.account by code -> id"""
    if not code:
        raise Exception("Account code kosong, tidak bisa mapping ke Finance.")
    if code in _fin_account_by_code_cache:
        return _fin_account_by_code_cache[code]
    fin_acc_id = find_one(fin_models, fin_db, fin_uid, fin_key, "account.account", [("code", "=", code)])
    if not fin_acc_id:
        raise Exception(f"Finance tidak punya account dengan code '{code}'. Samakan COA dulu.")
    _fin_account_by_code_cache[code] = fin_acc_id
    return fin_acc_id

def get_journal_code(hrm_models, hrm_db, hrm_uid, hrm_key, journal_id):
    """HRM account.journal(id) -> code"""
    if not journal_id:
        return None
    if journal_id in _journal_code_cache:
        return _journal_code_cache[journal_id]
    j = read(hrm_models, hrm_db, hrm_uid, hrm_key, "account.journal", [journal_id], ["code", "name", "type"])
    code = j[0].get("code") if j else None
    _journal_code_cache[journal_id] = code
    return code

def map_fin_journal_id_by_code(fin_models, fin_db, fin_uid, fin_key, code):
    """Finance account.journal by code -> id"""
    if not code:
        raise Exception("Journal code kosong, tidak bisa mapping ke Finance.")
    if code in _fin_journal_by_code_cache:
        return _fin_journal_by_code_cache[code]
    fin_j_id = find_one(fin_models, fin_db, fin_uid, fin_key, "account.journal", [("code", "=", code)])
    if not fin_j_id:
        raise Exception(f"Finance tidak punya journal dengan code '{code}'. Buat journal-nya dulu di Finance.")
    _fin_journal_by_code_cache[code] = fin_j_id
    return fin_j_id

def map_fin_partner_id(fin_models, fin_db, fin_uid, fin_key, partner_name=None):
    """
    Paling simple: semua payable payroll pakai partner company / generic partner.
    Biar gak ribet, kita cari partner bernama 'Payroll' kalau ada, kalau tidak bikin.
    """
    target_name = "Payroll"
    pid = find_one(fin_models, fin_db, fin_uid, fin_key, "res.partner", [("name", "=", target_name)])
    if pid:
        return pid
    return fin_models.execute_kw(fin_db, fin_uid, fin_key, "res.partner", "create", [{"name": target_name}])

# ==========================
# EXPORT HRM PAYRUN -> MOVES
# ==========================

def get_payrun_id(hrm_models, hrm_db, hrm_uid, hrm_key, payrun_name):
    run_id = find_one(hrm_models, hrm_db, hrm_uid, hrm_key, "hr.payslip.run", [("name", "=", payrun_name)])
    if not run_id:
        raise Exception(f"Pay Run '{payrun_name}' tidak ditemukan di HRM.")
    return run_id

def get_moves_from_payrun(hrm_models, hrm_db, hrm_uid, hrm_key, run_id):
    payslip_ids = search(hrm_models, hrm_db, hrm_uid, hrm_key, "hr.payslip", [("payslip_run_id", "=", run_id)])
    if not payslip_ids:
        raise Exception("Payrun ada, tapi tidak ada payslip di dalamnya.")

    slips = read(hrm_models, hrm_db, hrm_uid, hrm_key, "hr.payslip", payslip_ids, ["name", "move_id"])
    move_ids = sorted({s["move_id"][0] for s in slips if s.get("move_id")})
    missing = [s["name"] for s in slips if not s.get("move_id")]

    return move_ids, missing

def read_move_bundle(hrm_models, hrm_db, hrm_uid, hrm_key, move_id):
    move = read(hrm_models, hrm_db, hrm_uid, hrm_key, "account.move", [move_id],
                ["name", "ref", "date", "journal_id", "line_ids", "company_id"])[0]
    lines = read(hrm_models, hrm_db, hrm_uid, hrm_key, "account.move.line", move["line_ids"],
                 ["name", "account_id", "debit", "credit", "partner_id"])
    return move, lines

# ==========================
# IMPORT KE FINANCE
# ==========================

def finance_move_already_imported(fin_models, fin_db, fin_uid, fin_key, ref, date, fin_journal_id):
    """
    Anti dobel: cek jika sudah ada move di Finance dengan ref+date+journal sama.
    """
    if not ref:
        return False
    ids = search(fin_models, fin_db, fin_uid, fin_key, "account.move",
                 [("ref", "=", ref), ("date", "=", date), ("journal_id", "=", fin_journal_id)],
                 limit=1)
    return bool(ids)

def create_finance_move(fin_models, fin_db, fin_uid, fin_key, fin_journal_id, date, ref, line_vals):
    move_vals = {
        "move_type": "entry",
        "journal_id": fin_journal_id,
        "date": date,
        "ref": ref,
        "line_ids": [(0, 0, lv) for lv in line_vals],
    }
    move_id = fin_models.execute_kw(fin_db, fin_uid, fin_key, "account.move", "create", [move_vals])
    # Post
    fin_models.execute_kw(fin_db, fin_uid, fin_key, "account.move", "action_post", [[move_id]])
    return move_id

def build_finance_lines_from_hrm_lines(hrm_lines, hrm_models, hrm_db, hrm_uid, hrm_key,
                                      fin_models, fin_db, fin_uid, fin_key):
    """
    Convert HRM move lines -> Finance move lines:
    - account_id map by account.code
    - partner_id disederhanakan (pakai partner 'Payroll' kalau HRM partner kosong)
    """
    payroll_partner_id = map_fin_partner_id(fin_models, fin_db, fin_uid, fin_key)

    fin_lines = []
    for ln in hrm_lines:
        hrm_acc = ln.get("account_id")
        if not hrm_acc:
            continue

        hrm_account_id = hrm_acc[0]
        code = get_account_code(hrm_models, hrm_db, hrm_uid, hrm_key, hrm_account_id)
        fin_account_id = map_fin_account_id_by_code(fin_models, fin_db, fin_uid, fin_key, code)

        debit = float(ln.get("debit") or 0.0)
        credit = float(ln.get("credit") or 0.0)
        if debit == 0.0 and credit == 0.0:
            continue

        # partner: kalau HRM ada partner_id, boleh diabaikan (biar simple), atau set payroll_partner_id
        partner_id = payroll_partner_id

        fin_lines.append({
            "name": ln.get("name") or "/",
            "account_id": fin_account_id,
            "debit": debit,
            "credit": credit,
            "partner_id": partner_id,
        })

    if not fin_lines:
        raise Exception("Tidak ada move line yang kebentuk (semua debit/credit 0?).")
    return fin_lines

# ==========================
# MAIN
# ==========================

def push_payrun_accounting_to_finance(payrun_name: str):
    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    hrm_uid, hrm_models = connect(HRM_URL, HRM_DB, HRM_USER, HRM_KEY)

    run_id = get_payrun_id(hrm_models, HRM_DB, hrm_uid, HRM_KEY, payrun_name)
    move_ids, missing_slips = get_moves_from_payrun(hrm_models, HRM_DB, hrm_uid, HRM_KEY, run_id)

    if not move_ids:
        raise Exception(
            "Payslip di payrun belum punya Accounting Entry (move_id kosong). "
            "Artinya di HRM belum generate journal entry payroll."
        )

    imported = 0
    skipped = 0

    for mid in move_ids:
        hrm_move, hrm_lines = read_move_bundle(hrm_models, HRM_DB, hrm_uid, HRM_KEY, mid)

        hrm_journal_id = hrm_move["journal_id"][0] if hrm_move.get("journal_id") else None
        hrm_journal_code = get_journal_code(hrm_models, HRM_DB, hrm_uid, HRM_KEY, hrm_journal_id)
        fin_journal_id = map_fin_journal_id_by_code(fin_models, FIN_DB, fin_uid, FIN_KEY, hrm_journal_code)

        date = hrm_move.get("date")
        ref = hrm_move.get("ref") or f"Payroll Import - {payrun_name} - HRM Move {mid}"

        if finance_move_already_imported(fin_models, FIN_DB, fin_uid, FIN_KEY, ref, date, fin_journal_id):
            skipped += 1
            continue

        fin_line_vals = build_finance_lines_from_hrm_lines(
            hrm_lines, hrm_models, HRM_DB, hrm_uid, HRM_KEY,
            fin_models, FIN_DB, fin_uid, FIN_KEY
        )

        create_finance_move(fin_models, FIN_DB, fin_uid, FIN_KEY, fin_journal_id, date, ref, fin_line_vals)
        imported += 1

    return {
        "payrun": payrun_name,
        "hrm_run_id": run_id,
        "hrm_move_count": len(move_ids),
        "imported_moves": imported,
        "skipped_moves_already_exist": skipped,
        "payslips_missing_move_id": missing_slips,  # kalau ada, berarti belum generate accounting entry
    }
