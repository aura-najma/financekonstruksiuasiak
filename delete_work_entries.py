import xmlrpc.client
import sys

# ==========================
# KONFIGURASI
# ==========================
URL = "https://uasiakfinancekonstruksi.odoo.com"
DB = "uasiakfinancekonstruksi"
USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
KEY = "39d2add86b05f11c07cd9c50755077d74820fc23"

TARGET_EMAIL = "sinta.palsu-2022@ftmm.unair.ac.id"

# Kalau mau test dulu tanpa benar-benar delete:
DRY_RUN = False


def connect(url, db, user, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, key.strip(), {})
    if not uid:
        raise Exception("Auth gagal (cek DB/USER/API KEY).")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models


def main():
    uid, models = connect(URL, DB, USER, KEY)

    # 1) Cari employee dari email
    emp_ids = models.execute_kw(
        DB, uid, KEY,
        "hr.employee", "search",
        [[("work_email", "=", TARGET_EMAIL)]],
        {"limit": 1}
    )

    if not emp_ids:
        raise Exception(f"Employee dengan email '{TARGET_EMAIL}' tidak ditemukan.")

    employee_id = emp_ids[0]
    print("Employee ID:", employee_id)

    # 2) Cari semua work entries employee tsb
    work_entry_ids = models.execute_kw(
        DB, uid, KEY,
        "hr.work.entry", "search",
        [[("employee_id", "=", employee_id)]]
    )

    total = len(work_entry_ids)
    print("Total work entries ditemukan:", total)

    if total == 0:
        print("Tidak ada work entries. Selesai.")
        return

    if DRY_RUN:
        print("DRY_RUN=True → Tidak melakukan perubahan apa pun.")
        print("Sample IDs:", work_entry_ids[:10])
        return

    # 3) Reset ke draft (kalau perlu)
    models.execute_kw(
        DB, uid, KEY,
        "hr.work.entry", "write",
        [work_entry_ids, {"state": "draft"}]
    )

    # 4) Delete
    models.execute_kw(
        DB, uid, KEY,
        "hr.work.entry", "unlink",
        [work_entry_ids]
    )

    print("✅ Semua work entries BERHASIL dihapus.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ ERROR:", str(e))
        sys.exit(1)
