"""V19.11 — Promoções automáticas: estoque na lista, filtro Sem estoque e aviso
"Voltou ao estoque sem promoção". Só lê o banco; nenhum teste chama o ML."""

import unittest
from datetime import timedelta
from pathlib import Path

from base import S
from test_promo_renovacao import HOJE, Base


class SemEstoque(Base):

    def _linha(self, mlb, fim=""):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO promo_renovacao_v1873(mlb, marcado, situacao, fim, checado_em) "
                         "VALUES(?,1,?,?,?)", (mlb, "ativa" if fim else "revisar", fim, "2026-10-01T10:00:00"))

    def _est(self, mlb, dias_atras, estoque, sem):
        dia = (HOJE - timedelta(days=dias_atras)).isoformat()
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO ml_estoque_dia_v1872(item_id, dia, checagens, sem_estoque, "
                         "estoque_min, estoque_ultimo, ultimo_sem_estoque) VALUES(?,?,1,?,?,?,?)",
                         (mlb, dia, 1 if sem else 0, estoque, estoque, 1 if sem else 0))

    def lista(self):
        return {i["mlb"]: i for i in S.promo_renovacao_payload(HOJE)["itens"]}

    def test_estoque_e_voltou(self):
        # zerado agora
        self._linha("MLB900"); self._est("MLB900", 0, 0, True)
        # zerou há 3 dias, voltou hoje, sem promoção -> avisa
        self._linha("MLB901"); self._est("MLB901", 3, 0, True); self._est("MLB901", 0, 5, False)
        # voltou, mas já tem promoção valendo -> não avisa
        fim = (HOJE + timedelta(days=10)).isoformat()
        self._linha("MLB902", fim); self._est("MLB902", 3, 0, True); self._est("MLB902", 0, 5, False)
        # sempre teve estoque -> não avisa
        self._linha("MLB903"); self._est("MLB903", 0, 7, False)
        # sem dado de estoque -> None, nunca zero
        self._linha("MLB904")
        it = self.lista()
        self.assertEqual(it["MLB900"]["estoque"], 0)
        self.assertFalse(it["MLB900"]["voltou_estoque"])
        self.assertEqual(it["MLB901"]["estoque"], 5)
        self.assertTrue(it["MLB901"]["voltou_estoque"])
        self.assertFalse(it["MLB902"]["voltou_estoque"])
        self.assertFalse(it["MLB903"]["voltou_estoque"])
        self.assertIsNone(it["MLB904"]["estoque"])

    def test_tela_tem_filtros(self):
        html = (Path(S.__file__).parent / "APP" / "promocoes-auto.html").read_text(encoding="utf-8")
        for t in ("sem_estoque:['Sem estoque'", "Esconder sem estoque", "Voltaram ao estoque sem promoção"):
            self.assertIn(t, html)


if __name__ == "__main__":
    unittest.main()
