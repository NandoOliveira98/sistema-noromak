"""V19.58 — Dashboard de vendas em tempo real (decisões do dono, 19/10).

A cópia das vendas (vendas_v1799) era atualizada a cada 10 min, mas o vigia do ML (20 s) e a
sincronização da Shopee (1 min) já descobrem cada venda para os avisos. Agora venda nova
dispara uma cópia rápida (só os últimos dias, sem chamada a mais para descobrir) e a tela
consulta uma impressão digital leve, recarregando só quando muda.
"""

from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from base import S, TesteNoromak

HOJE = datetime.now(S.BR_TZ).date()
APP = Path(__file__).resolve().parent.parent / "APP"


def aviso(venda_id: str) -> dict:
    return {"venda_id": venda_id, "venda_em": datetime.now(S.BR_TZ).isoformat(), "valor": 10.0,
            "titulo": "Produto", "qtd": 1, "itens": 1}


class DashboardTempoReal(TesteNoromak):

    def test_venda_nova_acorda_a_copia_das_vendas(self):
        S.avisos_venda_registrar("ml", [aviso("V0")])   # 1ª leitura: só marca
        S.VENDAS_SYNC_EVENT.clear()
        S.avisos_venda_registrar("ml", [aviso("V0")])   # repetida: não acorda
        self.assertFalse(S.VENDAS_SYNC_EVENT.is_set())
        S.avisos_venda_registrar("ml", [aviso("V1")])
        self.assertTrue(S.VENDAS_SYNC_EVENT.is_set(), "venda nova não acordou a cópia das vendas")

    def test_copia_rapida_so_le_os_ultimos_dias(self):
        chamadas = []
        with mock.patch.object(S, "vendas_marketplaces_conectados", return_value=["ml", "shopee"]), \
                mock.patch.object(S, "ml_casas_conectadas", return_value=[S.CASA_PRINCIPAL]), \
                mock.patch.object(S, "_vendas_sync_intervalo",
                                  side_effect=lambda m, i, f: chamadas.append((m, i, f)) or []):
            S.vendas_sync_recente()
        self.assertEqual(sorted(c[0] for c in chamadas), ["ml", "shopee"])
        for _, inicio, fim in chamadas:
            self.assertEqual(fim, HOJE)
            self.assertGreaterEqual(inicio, HOJE - timedelta(days=2), "cópia rápida leu histórico")

    def test_versao_muda_com_venda_nova(self):
        antes = S.vendas_versao()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,qty,amount,updated_at) "
                         "VALUES('ml','X1','1',?,?,1,50,?)", (S.now_iso(), HOJE.isoformat(), S.now_iso()))
            S.DB.commit()
        self.assertNotEqual(antes, S.vendas_versao())
        self.assertEqual(S.vendas_versao(), S.vendas_versao(), "versão instável sem mudança")

    def test_tela_consulta_versao_e_ranking_de_5(self):
        html = (APP / "admin.html").read_text(encoding="utf-8")
        self.assertIn("/expedicao/api/dashboard/vendas/versao", html)
        self.assertIn("V1957_RANK_LIMITE=5", html)
        self.assertIn("Mostrar mais", html)
