"""V19.71 — aviso de venda diz a filial (pedido do dono, 21/10: "Venda ML · R$ 39,40" era de Bauru e
não dava para saber). O vigia do ML roda dentro da casa da conta (ml_na_casa); a Shopee é só de Araçatuba."""

from datetime import datetime

from base import S, TesteNoromak


def venda(venda_id: str) -> dict:
    return {"venda_id": venda_id, "venda_em": datetime.now(S.BR_TZ).isoformat(), "valor": 39.4,
            "titulo": "Sintra Fast Spray", "qtd": 1, "itens": 1}


class AvisoComFilial(TesteNoromak):

    def titulos(self) -> dict:
        with S.DB_LOCK:
            rows = S.DB.execute("SELECT * FROM aviso_venda_v1893").fetchall()
        return {r["venda_id"]: S._avisos_formatar(r)["titulo"] for r in rows}

    def test_venda_de_cada_filial_no_titulo(self):
        for mp in ("ml", "shopee"):  # a 1ª leitura de cada marketplace só marca
            S.avisos_venda_registrar(mp, [venda(f"base-{mp}")])
        S.avisos_venda_registrar("ml", [venda("A1")])
        with S.ml_na_casa("bauru"):
            S.avisos_venda_registrar("ml", [venda("B1")])
        S.avisos_venda_registrar("shopee", [venda("S1")])
        t = self.titulos()
        self.assertEqual(t["A1"], "Venda ML Araçatuba · R$ 39,40")
        self.assertEqual(t["B1"], "Venda ML Bauru · R$ 39,40")
        self.assertEqual(t["S1"], "Venda Shopee Araçatuba · R$ 39,40")

    def test_aviso_antigo_sem_filial_continua_igual(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO aviso_venda_v1893(marketplace,venda_id,valor,titulo,criado_em) VALUES('ml','X',39.4,'t',?)",
                         (S.now_iso(),))
            S.DB.commit()
        self.assertEqual(self.titulos()["X"], "Venda ML · R$ 39,40")

    def test_schema_idempotente(self):
        S.ensure_avisos_venda_schema()
        S.ensure_avisos_venda_schema()


if __name__ == "__main__":
    import unittest
    unittest.main()
