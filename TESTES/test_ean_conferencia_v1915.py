"""V19.15 — EAN gravado na Conferência sumia (13/10, ZK403, ZK130, CH28, 816.0002-0...).

Em 30/09 um salvamento do Admin registrou o campo "Código de Barras" VAZIO como override
ADMIN em 319 produtos (delta contra o banco, o erro que a V19.00 corrigiu). A leitura
reaplica o vazio, e o "+ EAN" da Conferência lê o banco já com os vazios e grava tudo de
volta: cada EAN novo apagava os anteriores dos produtos presos."""
import json
import unittest

from base import S, TesteNoromak


def ean_de(sku):
    return {p["SKU"]: p.get("Código de Barras") for p in json.loads(S.get_storage("conf_produtos")[0])}[sku]


class EanConferencia(TesteNoromak):
    def setUp(self):
        super().setUp()
        prods = [{"__uidProduto": f"P{i}", "SKU": s, "Código de Barras": "", "Descrição": s}
                 for i, s in enumerate(["ZK403", "ZK130", "CH28"])]
        S.save_storage("conf_produtos", None, json.dumps(prods), False, actor="admin", actor_role="admin")

    def override_vazio(self, sku):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO admin_manual_overrides(storage_key,entity_id,field_name,value_json,is_deleted,updated_at,updated_by) "
                         "VALUES('conf_produtos',?,'Código de Barras','\"\"',0,'2026-09-30T10:06:11',?)", (f"SKU:{sku}", "luis"))
            S.DB.commit()

    def ean(self, sku, ean):
        st, c = self.cliente.pedir("POST", "/api/product-ean", {"sku": sku, "ean": ean})
        self.assertEqual(st, 200, c)

    def test_override_vazio_nao_apaga_o_ean(self):
        for s in ("ZK403", "ZK130"):
            self.override_vazio(s)
        self.ean("ZK403", "111")
        self.ean("ZK130", "222")
        self.ean("CH28", "333")
        self.assertEqual((ean_de("ZK403"), ean_de("ZK130"), ean_de("CH28")), ("111", "222", "333"))

    def test_conferencia_troca_ean_definido_pelo_admin(self):
        # V19.17 (decisão do dono, 14/10): a Conferência é quem mais cadastra EAN; o EAN
        # bipado lá substitui o do Admin (antes a V19.15 recusava com 400).
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO admin_manual_overrides(storage_key,entity_id,field_name,value_json,is_deleted,updated_at,updated_by) "
                         "VALUES('conf_produtos','SKU:CH28','Código de Barras','\"999\"',0,'2026-10-01',?)", ("luis",))
            S.DB.commit()
        self.ean("CH28", "333")
        self.assertEqual(ean_de("CH28"), "333")

    def test_migracao_limpa_vazios_e_devolve_os_eans_do_registro(self):
        for s in ("ZK403", "ZK130"):
            self.override_vazio(s)
        S.audit("matheus", "product.ean.quick_update", "ZK403|(vazio)->040141283511|produtos=1|linhas_pendentes=1")
        S.audit("matheus", "product.ean.quick_update", "ZK130|(vazio)->ZK130|produtos=1")
        S.audit("matheus", "product.ean.quick_update", "ZK130|ZK130->7891234|produtos=1")
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM admin_manual_overrides WHERE storage_key='__migracao__'")
            S.DB.commit()
        S.ean_overrides_vazios_migrar_v1915(forcar=True)
        self.assertEqual((ean_de("ZK403"), ean_de("ZK130")), ("040141283511", "7891234"))   # o último vale
        with S.DB_LOCK:
            n = S.DB.execute("SELECT count(*) FROM admin_manual_overrides WHERE storage_key='conf_produtos' "
                             "AND field_name='Código de Barras'").fetchone()[0]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
