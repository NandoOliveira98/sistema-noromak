"""V20.29 — TikTok Shop (Bauru): etiqueta automática (organizar envio + PDF), cancelamento.

Decisões do dono (06/11): o servidor organiza o envio sozinho e a Conferência de Bauru imprime a
etiqueta em PDF; a nota sai pelo Linx e o Noromak só espera (AGUARDANDO NF); a etiqueta
automática começa DESLIGADA (é a única ação que grava no TikTok); cancelado sai da fila com a
regra do ML (a linha do ADMIN fica, concluído não volta).
"""

import json
import time
from pathlib import Path
from unittest import mock

from base import S, TesteNoromak

DOC_URL = "https://p16-oec.tiktokcdn.com/etiqueta.pdf"


def _conectar():
    agora = int(time.time())
    S._tiktok_gravar_token({"code": 0, "data": {"access_token": "AT", "refresh_token": "RT",
                                                "access_token_expire_in": agora + 86400,
                                                "refresh_token_expire_in": agora + 86400 * 30}}, conectando=True)
    with S.DB_LOCK:
        S.DB.execute("UPDATE tiktok_integration SET shop_cipher='CIPHER' WHERE id=1")
        S.DB.commit()


def _pedido(numero, status="AWAITING_SHIPMENT", itens=(("SK1", "0893600022"),), **extra):
    p = {"id": numero, "status": status, "create_time": 1760000000, "rts_sla_time": 1760090000,
         "recipient_address": {"name": "Cliente Teste"}, "packages": [{"id": f"PK{numero}"}],
         "line_items": [{"id": f"{numero}-{i}", "sku_id": s, "seller_sku": c, "product_name": "Desengripante",
                         "display_status": status} for i, (s, c) in enumerate(itens)]}
    p.update(extra)
    return p


class Base(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        for nome, valor in (("TIKTOK_APP_KEY", "K"), ("TIKTOK_APP_SECRET", "S"), ("TIKTOK_SERVICE_ID", "X")):
            p = mock.patch.object(S, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        _conectar()
        S._TIKTOK_ETIQUETA_TENTATIVA.clear()
        self.enviados = []
        self.erro_ship = None
        self.doc_url = DOC_URL

    def _api(self, pedidos=None):
        teste = self

        def falso(metodo, path, params=None, body=None, loja=True):
            teste.enviados.append((metodo, path, params, body))
            if path == "/order/202309/orders/search":
                st = (body or {}).get("order_status")
                return {"code": 0, "data": {"orders": [o for o in (pedidos or []) if o.get("status") == st]}}
            if path.endswith("/ship"):
                if teste.erro_ship:
                    raise teste.erro_ship
                return {"code": 0, "data": {}}
            if path.endswith("/shipping_documents"):
                return {"code": 0, "data": {"doc_url": teste.doc_url}}
            return {"code": 0, "data": {}}
        return mock.patch.object(S, "tiktok_api", side_effect=falso)

    def _chamadas(self, fim):
        return [e for e in self.enviados if e[1].endswith(fim)]

    def _fila(self):
        bruto, _, _ = S.get_storage("conf_pedidos")
        return S._rows_from_storage_value(bruto)

    def _linha(self, pedido):
        return next(l for l in self._fila() if l.get("Pedido") == pedido)

    def _etiquetas_tiktok(self):
        bruto, _, _ = S.get_storage("conf_etiquetas")
        bank = json.loads(bruto) if bruto else {}
        return [r for r in (bank.get("registros") or {}).values() if r.get("marketplace") == "tiktok"]

    def _importar(self, pedidos):
        with self._api(pedidos):
            return S.tiktok_importar_pedidos()


class Etiqueta(Base):

    def test_comeca_desligada_e_desligada_nao_chama_o_ship(self):
        self.assertFalse(S.tiktok_etiqueta_ligada())
        with self._api([_pedido("TT1")]):
            S.tiktok_rodada()
        self.assertFalse(self._chamadas("/ship"))
        self.assertEqual(self._linha("TT1").get("Etiqueta API TikTok", ""), "")

    def test_ligada_organiza_e_vincula(self):
        S.tiktok_etiqueta_definir(True, "dono")
        with self._api([_pedido("TT1")]):
            S.tiktok_rodada()
        ship = self._chamadas("/ship")
        self.assertEqual(len(ship), 1)
        self.assertEqual(ship[0][1], "/fulfillment/202309/packages/PKTT1/ship")
        regs = self._etiquetas_tiktok()
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]["envio"], "TIKTOK_PDF:PKTT1")
        self.assertEqual(regs[0]["pedido"], "TT1")
        linha = self._linha("TT1")
        self.assertEqual(linha["Etiqueta API TikTok"], "VINCULADA")
        self.assertEqual(linha["PacoteTikTok"], "PKTT1")

    def test_ja_vinculado_nao_organiza_de_novo(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1")])
        self._importar([_pedido("TT1")])
        self.assertEqual(len(self._chamadas("/ship")), 1)
        self.assertEqual(len(self._etiquetas_tiktok()), 1)

    def test_aguardando_collection_nao_chama_ship_so_documento(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1", status="AWAITING_COLLECTION")])
        self.assertFalse(self._chamadas("/ship"))
        self.assertEqual(len(self._chamadas("/shipping_documents")), 1)
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "VINCULADA")

    def test_sem_nota_mostra_aguardando_nf_e_nao_repete_antes_de_600s(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self.erro_ship = RuntimeError("TikTok /ship: 21011001 invoice not uploaded")
        self._importar([_pedido("TT1")])
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "AGUARDANDO NF")
        self._importar([_pedido("TT1")])
        self.assertEqual(len(self._chamadas("/ship")), 1, "sem NF não pode virar uma chamada a cada volta")
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "AGUARDANDO NF")
        depois = time.time() + 601
        with mock.patch.object(S.time, "time", return_value=depois):
            self._importar([_pedido("TT1")])
        self.assertEqual(len(self._chamadas("/ship")), 2)

    def test_outro_erro_marca_erro_e_nao_trava_a_importacao(self):
        S.tiktok_etiqueta_definir(True, "dono")
        self.erro_ship = RuntimeError("TikTok HTTP 500: falhou")
        r = self._importar([_pedido("TT1")])
        self.assertEqual(r["etiquetas"]["erros"], 1)
        self.assertEqual(self._linha("TT1")["Etiqueta API TikTok"], "ERRO")

    def test_edicao_do_admin_fica_e_so_campos_operacionais_mudam(self):
        self._importar([_pedido("TT1")])
        linhas = self._fila()
        for l in linhas:
            if l.get("Pedido") == "TT1":
                l["Nome Conferência"] = "NOME DO ADMIN"
        self.salvar_admin(linhas)
        S.tiktok_etiqueta_definir(True, "dono")
        self._importar([_pedido("TT1")])
        linha = self._linha("TT1")
        self.assertEqual(linha.get("Nome Conferência"), "NOME DO ADMIN")
        self.assertEqual(linha["Etiqueta API TikTok"], "VINCULADA")
