import xmlrpc.client
# FINANCE (TUJUAN)
FIN_URL = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB  = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"
# =====================================================
common = xmlrpc.client.ServerProxy(f"{FIN_URL}/xmlrpc/2/common")
FIN_UID = common.authenticate(FIN_DB, FIN_USER, FIN_KEY, {})
if not FIN_UID:
    raise Exception("❌ Gagal login ke Odoo Finance")

FIN = xmlrpc.client.ServerProxy(f"{FIN_URL}/xmlrpc/2/object")

print("✅ Connected to Finance Odoo, UID =", FIN_UID)


def fin_wipe_work_entries_backend(batch_size=200):
    """
    Wipe semua hr.work.entry di FINANCE via backend.
    Urutan:
      1) Cancel + delete payslips (hr.payslip)
      2) Delete payslip runs (hr.payslip.run) jika ada
      3) Reset work entries -> draft (action-based)
      4) Unlink work entries
      5) Jika masih locked -> archive (active=False)

    Catatan:
    - Butuh hak akses Payroll/HR yang cukup.
    - Kalau ada lock date / journal posted yang mengunci payroll, unlink bisa gagal dan akan di-archive.
    """

    def _batched_search(model, domain, limit):
        return FIN.execute_kw(FIN_DB, FIN_UID, FIN_KEY, model, "search", [domain], {"limit": limit})

    def _safe_call(model, method, ids):
        try:
            FIN.execute_kw(FIN_DB, FIN_UID, FIN_KEY, model, method, [ids])
            return True, None
        except xmlrpc.client.Fault as e:
            return False, e

    # -----------------------------
    # 1) Cancel + delete payslips
    # -----------------------------
    deleted_slips = 0
    while True:
        slip_ids = _batched_search("hr.payslip", [], batch_size)
        if not slip_ids:
            break

        # cancel dulu (nama method beda antar versi)
        for m in ["action_payslip_cancel", "action_cancel", "cancel_sheet"]:
            ok, _ = _safe_call("hr.payslip", m, slip_ids)
            if ok:
                break

        ok, err = _safe_call("hr.payslip", "unlink", slip_ids)
        if not ok:
            raise Exception(f"Gagal delete payslip. Error: {err}")
        deleted_slips += len(slip_ids)
        print(f"[UNLOCK] deleted payslips: {deleted_slips}")

    # -----------------------------
    # 2) Delete payslip runs (opsional tapi bagus)
    # -----------------------------
    deleted_runs = 0
    while True:
        run_ids = _batched_search("hr.payslip.run", [], batch_size)
        if not run_ids:
            break
        ok, err = _safe_call("hr.payslip.run", "unlink", run_ids)
        if not ok:
            # kalau run nggak bisa dihapus, lanjut aja—tidak selalu kritikal
            print(f"[WARN] cannot delete payslip runs (will continue). Example error: {err}")
            break
        deleted_runs += len(run_ids)
        print(f"[UNLOCK] deleted payslip runs: {deleted_runs}")

    # -----------------------------
    # 3) Reset work entries -> draft
    # -----------------------------
    def _reset_work_entries(ids):
        # action-based reset (nama method beda antar versi)
        for m in ["action_draft", "action_reset_to_draft", "action_set_to_draft"]:
            ok, _ = _safe_call("hr.work.entry", m, ids)
            if ok:
                return True, None
        # kalau tidak ada action method, coba write langsung
        try:
            FIN.execute_kw(FIN_DB, FIN_UID, FIN_KEY, "hr.work.entry", "write", [ids, {"state": "draft"}])
            return True, None
        except xmlrpc.client.Fault as e:
            return False, e

    # -----------------------------
    # 4) Unlink work entries (kalau gagal -> archive)
    # -----------------------------
    deleted_we = 0
    archived_we = 0
    total_seen = 0

    while True:
        we_ids = _batched_search("hr.work.entry", [], batch_size)
        if not we_ids:
            break

        total_seen += len(we_ids)

        ok, err = _reset_work_entries(we_ids)
        if not ok:
            # kalau reset gagal, kita tetap coba unlink; kalau gagal juga -> archive
            print(f"[WARN] reset to draft failed (will try delete/arch). Example error: {err}")

        ok, err = _safe_call("hr.work.entry", "unlink", we_ids)
        if ok:
            deleted_we += len(we_ids)
            print(f"[DELETE] work entries deleted: {deleted_we}")
        else:
            # fallback: archive biar hilang dari UI
            ok2, err2 = _safe_call("hr.work.entry", "write", we_ids)
            if ok2:
                # ^ salah: write butuh vals; lakukan write langsung:
                pass
            try:
                FIN.execute_kw(FIN_DB, FIN_UID, FIN_KEY, "hr.work.entry", "write", [we_ids, {"active": False}])
                archived_we += len(we_ids)
                print(f"[ARCHIVE] locked entries archived: {archived_we} (delete error: {err})")
            except xmlrpc.client.Fault as e2:
                # kalau archive pun gagal, berarti permission/record rule berat
                raise Exception(f"Gagal delete dan gagal archive work entries. Delete error: {err} | Archive error: {e2}")

    return {
        "payslips_deleted": deleted_slips,
        "payslip_runs_deleted": deleted_runs,
        "work_entries_seen": total_seen,
        "work_entries_deleted": deleted_we,
        "work_entries_archived": archived_we,
    }
if __name__ == "__main__":
    result = fin_wipe_work_entries_backend(batch_size=200)
    print("=== WIPE RESULT ===")
    print(result)
