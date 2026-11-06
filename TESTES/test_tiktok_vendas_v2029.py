"""V20.29 — vendas do TikTok Shop (Bauru) no Dashboard e acerto real de cada pedido.

Decisões do dono (06/11): a venda do TikTok entra nas vendas de Bauru (vendas_conta_v1898),
aparece no Dashboard com o filtro Bauru; o Financeiro continua só Araçatuba. A tarifa vem do
acerto real do TikTok; sem acerto = em branco, nunca estimada.
"""

import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from base import S, TesteNoromak

HOJE = datetime.now(S.BR_TZ).date()


def _conectar():
    agora = int(time.time())
    S._tiktok_gravar_token({"code": 0, "data": {"access_token": "AT", "refresh_token": "RT",
                                                "access_token_expire_in": agora + 86400,
                                                "refresh_token_expire_in": agora + 86400 * 30}}, conectando=True)
    with S.DB_LOCK:
        S.DB.execute("UPDATE tiktok_integration SET shop_cipher='CIPHER' WHERE id=1")
        S.DB.commit()


def _venda(numero, status="AWAITING_SHIPMENT", unidades=2, preco="19.90", dia=None):
    criado = datetime.combine(dia or HOJE, datetime.min.time()).replace(hour=12, tzinfo=S.BR_TZ)
    return {"id": numero, "status": status, "create_time": int(criado.timestamp()),
            "line_items": [{"id": f"{numero}-{i}", "sku_id": "SK1", "seller_sku": "0893600022", "product_id": "P1",
                            "product_name": "Desengripante", "sale_price": preco, "display_status": status}
                           for i in range(unidades)]}


class Base(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        for nome, valor in (("TIKTOK_APP_KEY", "K"), ("TIKTOK_APP_SECRET", "S"), ("TIKTOK_SERVICE_ID", "X")):
            p = mock.patch.object(S, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        _conectar()
        self.enviados = []

    def _api(self, vendas=(), financeiro=None):
        teste = self

        def falso(metodo, path, params=None, body=None, loja=True):
            teste.enviados.append((metodo, path, params, body))
            if path == "/order/202309/orders/search":
                return {"code": 0, "data": {"orders": list(vendas)}}
            if "/statement_transactions" in path and financeiro:
                return financeiro(path)
            return {"code": 0, "data": {}}
        return mock.patch.object(S, "tiktok_api", side_effect=falso)

    def _linhas(self, marketplace="tiktok"):
        with S.DB_LOCK:
            return [dict(r) for r in S.DB.execute(
                "SELECT * FROM vendas_conta_v1898 WHERE marketplace=?", (marketplace,)).fetchall()]


class Vendas(Base):

    def test_venda_entra_nas_vendas_de_bauru(self):
        with self._api([_venda("TT1")]):
            S.tiktok_vendas_sync()
        linhas = self._linhas()
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]["conta"], "bauru")
        self.assertEqual(linhas[0]["qty"], 2)
        self.assertAlmostEqual(linhas[0]["amount"], 39.8)
        self.assertEqual(linhas[0]["sale_day"], HOJE.isoformat())

    def test_nao_vai_para_vendas_v1799_nem_financeiro(self):
        with self._api([_venda("TT1")]):
            S.tiktok_vendas_sync()
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM vendas_v1799 WHERE marketplace='tiktok'").fetchone()[0]
        self.assertEqual(n, 0)
        fin = S.financeiro_payload(HOJE.isoformat(), HOJE.isoformat())
        self.assertNotIn("tiktok", fin.get("por_mp") or {})

    def test_cancelado_e_nao_pago_ficam_fora(self):
        with self._api([_venda("TT1", status="CANCELLED"), _venda("TT2", status="UNPAID")]):
            S.tiktok_vendas_sync()
        self.assertEqual(self._linhas(), [])

    def test_janela_incompleta_nao_apaga(self):
        with self._api([_venda("TT1")]):
            S.tiktok_vendas_sync()
        S._tiktok_vendas_gravar(HOJE.isoformat(), HOJE.isoformat(), [], complete=False)
        self.assertEqual(len(self._linhas()), 1)
        S._tiktok_vendas_gravar(HOJE.isoformat(), HOJE.isoformat(), [], complete=True)
        self.assertEqual(self._linhas(), [])

    def test_dashboard_bauru_mostra_tiktok(self):
        with self._api([_venda("TT1")]):
            S.tiktok_vendas_sync()
        d = S.vendas_dashboard_payload(HOJE.isoformat(), HOJE.isoformat(), conta="bauru")
        tiktok = next(m for m in d["por_marketplace"] if m["marketplace"] == "tiktok")
        self.assertEqual(tiktok["unidades"], 2)
        self.assertEqual(d["por_dia"][-1]["por_marketplace"]["tiktok"]["unidades"], 2)
        so = S.vendas_dashboard_payload(HOJE.isoformat(), HOJE.isoformat(), marketplace="tiktok", conta="todas")
        self.assertEqual(so["resumo"]["unidades"], 2)

    def test_sync_nao_mexe_nas_vendas_do_ml_de_bauru(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO vendas_conta_v1898(conta,marketplace,order_id,line_key,sale_at,sale_day,qty,amount,updated_at) "
                         "VALUES('bauru','ml','M1','L1',?,?,1,10,?)", (HOJE.isoformat(), HOJE.isoformat(), S.now_iso()))
            S.DB.commit()
        with self._api([_venda("TT1")]):
            S.tiktok_vendas_sync()
        S._tiktok_vendas_gravar(HOJE.isoformat(), HOJE.isoformat(), [], complete=True)
        self.assertEqual(len(self._linhas("ml")), 1)

    def test_desconectado_nao_chama(self):
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_integration SET access_token='', refresh_token='' WHERE id=1")
            S.DB.commit()
        with self._api([_venda("TT1")]):
            r = S.tiktok_vendas_rodada()
        self.assertFalse(r["sincronizou"])
        self.assertFalse(self.enviados)

    def test_worker_no_painel_de_saude(self):
        self.assertIn("tiktok_vendas_loop", S.SAUDE_WORKERS_INFO)

    def test_dashboard_tela_tem_o_canal(self):
        html = (Path(S.__file__).resolve().parent / "APP" / "admin.html").read_text(encoding="utf-8")
        inicio = html.index("const ordem=[")
        self.assertIn('"tiktok"', html[inicio:inicio + 80])
        legenda = html.index('id="v1799Legenda"')
        self.assertIn("TikTok", html[legenda:legenda + 600])
