import xmlrpc.client

FIN_URL = "https://uasiakfinancekonstruksi.odoo.com"
FIN_DB  = "uasiakfinancekonstruksi"
FIN_USER = "aura.najma.kustiananda-2022@ftmm.unair.ac.id"
FIN_KEY  = "39d2add86b05f11c07cd9c50755077d74820fc23"

def connect(url, db, user, key):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, key, {})
    if not uid:
        raise Exception("Login gagal")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models

def fin_debug_payslip_fields():
    fin_uid, fin_models = connect(FIN_URL, FIN_DB, FIN_USER, FIN_KEY)
    fields = fin_models.execute_kw(
        FIN_DB, fin_uid, FIN_KEY,
        "hr.payslip", "fields_get",
        [], {"attributes": ["string", "type", "required"]}
    )
    return list(fields.keys())

if __name__ == "__main__":
    fields = fin_debug_payslip_fields()
    print("FIELDS hr.payslip:")
    for f in fields:
        print("-", f)
