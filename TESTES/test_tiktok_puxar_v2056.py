"""V20.56 — revisar-antes-de-subir do "Puxar anúncios do TikTok" (11/11, "corrija todos").

- O status do TikTok diz se o atendimento automático está ligado (a tela avisa antes de passar para a API).
- A leitura do catálogo pelo botão tem andamento consultável (GET /api/tiktok-estoque/catalogo).
- Se os ids das variações lidas pela API não batem com NENHUM dos lidos pela extensão, nada é gravado: senão os 91
  anúncios entrariam de novo, duplicados nas fichas.
"""
import time
from unittest import mock

from base import S, TesteNoromak


def _prod(ids):
    return {"code": 0, "data": {"products": [{"id": "P" + i, "status": "ACTIVATE", "title": "T", "skus": [
        {"id": i, "seller_sku": "S" + i, "inventory": [{"warehouse_id": "W", "quantity": 1}]}]} for i in ids]}}


def _extensao(n=20):
    S._tiktok_sku_gravar({f"V{i}": {"product_id": "P", "titulo": "T", "status": "ACTIVATE", "seller_sku": f"S{i}",
                                    "warehouse_id": "W", "armazens": 1, "qtd": 1} for i in range(n)},
                         apagar_ausentes=False)


class Status(TesteNoromak):
    def test_status_diz_o_atendimento(self):
        st = S.tiktok_status_payload()
        self.assertIn("atendimento_ligado", st)
        self.assertIn("etiqueta_ligada", st)


class IdsDiferentes(TesteNoromak):
    def test_nenhum_id_em_comum_nao_grava(self):
        _extensao()
        with mock.patch.object(S, "tiktok_api", return_value=_prod([f"X{i}" for i in range(20)])):
            r = S.tiktok_produtos_sincronizar()
        self.assertFalse(r["ok"])
        self.assertIn("ids", r["erro"])
        ids = {x["sku_id"] for x in S._tiktok_linhas_sku()}
        self.assertEqual(ids, {f"V{i}" for i in range(20)}, "nada novo nem apagado")
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM anuncio_canal_v2023 WHERE canal='tiktok' AND anuncio_id LIKE 'X%'"
                             ).fetchone()[0]
        self.assertEqual(n, 0, "sem anúncios duplicados na plataforma")

    def test_com_ids_em_comum_grava(self):
        _extensao()
        ids = [f"V{i}" for i in range(20)] + ["NOVO1"]
        with mock.patch.object(S, "tiktok_api", return_value=_prod(ids)):
            r = S.tiktok_produtos_sincronizar()
        self.assertTrue(r["ok"])
        self.assertIn("NOVO1", {x["sku_id"] for x in S._tiktok_linhas_sku()})

    def test_loja_sem_nada_lido_antes_grava(self):
        with mock.patch.object(S, "tiktok_api", return_value=_prod(["A1", "A2"])):
            r = S.tiktok_produtos_sincronizar()
        self.assertTrue(r["ok"])


class Andamento(TesteNoromak):
    def _esperar(self):
        for _ in range(100):
            if not S.tiktok_catalogo_situacao()["rodando"]:
                return S.tiktok_catalogo_situacao()
            time.sleep(0.02)
        self.fail("leitura não terminou")

    def test_resultado_e_erro_ficam_na_situacao(self):
        with mock.patch.object(S, "tiktok_produtos_sincronizar", return_value={"ok": True, "variacoes": 7}):
            self.assertTrue(S.tiktok_catalogo_iniciar())
            s = self._esperar()
        self.assertEqual((s["variacoes"], s["erro"]), (7, ""))
        self.assertTrue(s["fim_em"])
        with mock.patch.object(S, "tiktok_produtos_sincronizar", side_effect=RuntimeError("TikTok fora")):
            S.tiktok_catalogo_iniciar()
            s = self._esperar()
        self.assertIn("TikTok fora", s["erro"])
        with mock.patch.object(S, "tiktok_produtos_sincronizar", return_value={"ok": False, "erro": "ids diferentes"}):
            S.tiktok_catalogo_iniciar()
            s = self._esperar()
        self.assertIn("ids diferentes", s["erro"])


if __name__ == "__main__":
    import unittest
    unittest.main()
