"""V19.64 — TikTok Shop (Bauru), fase 1: estoque do Apollo de Bauru → TikTok e pedidos na fila.

Decisões do dono (20/10): grava estoque SÓ no TikTok (Simulação → Lote → Automático com OWNER);
manda TODO o disponível de Bauru menos o que já foi vendido e ainda está na fila de Bauru;
Apollo sem resposta = não mexe (nunca zero); vínculo pelo seller_sku = código Apollo, o vínculo
à mão vence; pedidos do TikTok entram na fila de Bauru com a importação ligada.
"""

import json
import time
from unittest import mock

from base import S, TesteNoromak


def _conectar():
    agora = int(time.time())
    S._tiktok_gravar_token({"code": 0, "data": {"access_token": "AT", "refresh_token": "RT",
                                                "access_token_expire_in": agora + 86400,
                                                "refresh_token_expire_in": agora + 86400 * 30}}, conectando=True)
    with S.DB_LOCK:
        S.DB.execute("UPDATE tiktok_integration SET shop_cipher='CIPHER' WHERE id=1")
        S.DB.commit()


def _sku(sku_id, seller_sku, qtd, produto="P1", armazens=None):
    return {"id": sku_id, "seller_sku": seller_sku,
            "inventory": armazens if armazens is not None else [{"warehouse_id": "W1", "quantity": qtd}]}


def _produtos(*skus, produto="P1"):
    return {"code": 0, "data": {"products": [{"id": produto, "title": "Produto teste", "status": "ACTIVATE",
                                              "skus": list(skus)}]}}


def _apollo(disponivel_bauru, respondeu=True):
    return {"ok": True, "estoques": [
        {"revenda": 1, "casa": "Araçatuba", "disponivel": 50.0, "respondeu": True},
        {"revenda": 3, "casa": "Bauru", "disponivel": disponivel_bauru if respondeu else None, "respondeu": respondeu}]}


class Base(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        for nome, valor in (("TIKTOK_APP_KEY", "K"), ("TIKTOK_APP_SECRET", "S"), ("TIKTOK_SERVICE_ID", "X")):
            p = mock.patch.object(S, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        _conectar()
        S._tiktok_config_gravar("fonte", "api", "teste")  # V20.40: estes testes são da API
        self.enviados = []
        self.relidos = {}
        S._TIKTOK_APOLLO_CHAMADAS.clear()

    def _api(self, respostas_produtos=None, pedidos=None):
        enviados = self.enviados

        def falso(metodo, path, params=None, body=None, loja=True):
            enviados.append((metodo, path, body))
            if path == "/product/202502/products/search":
                return respostas_produtos or _produtos()
            if path == "/order/202309/orders/search":
                st = (body or {}).get("order_status")
                return {"code": 0, "data": {"orders": [o for o in (pedidos or []) if o.get("status") == st]}}
            if path.endswith("/inventory/update"):
                return {"code": 0, "data": {"errors": []}}
            if path == "/order/202309/orders":  # V20.40: releitura do que sumiu da lista
                return {"code": 0, "data": {"orders": [{"id": n, "status": st} for n, st in self.relidos.items()]}}
            return {"code": 0, "data": {}}
        return mock.patch.object(S, "tiktok_api", side_effect=falso)

    def _ler_apollo(self, codigo, disp, respondeu=True):
        with mock.patch.object(S, "crm_stock_lookup", return_value=_apollo(disp, respondeu)):
            S.tiktok_apollo_ler(codigo)

    def _fila(self, linhas):
        S.save_storage("conf_pedidos", None, json.dumps(linhas), True, actor="teste", actor_role="admin")

    def _item(self, sku_id):
        return next(i for i in S.tiktok_estoque_payload()["itens"] if i["sku_id"] == sku_id)


class Pecas(Base):

    def test_seller_sku_vira_pecas_do_apollo(self):
        self.assertEqual(S._tiktok_pecas("0893600022"), [("0893600022", 1)])
        self.assertEqual(S._tiktok_pecas("0893600022/0893764272"), [("0893600022", 1), ("0893764272", 1)])
        self.assertEqual(S._tiktok_pecas("KIT2-PA18"), [("PA18", 2)])
        self.assertEqual(S._tiktok_pecas("151490-0893270212"), [("0893270212", 1)])
        self.assertEqual(S._tiktok_pecas(""), [])


class Estoque(Base):

    def test_manda_o_disponivel_de_bauru_menos_o_vendido_na_fila_de_bauru(self):
        with self._api(_produtos(_sku("S1", "0893600022", 9))):
            S.tiktok_produtos_sincronizar()
        self._ler_apollo("0893600022", 5)
        self._fila([
            {"Pedido": "B1", "SKU": "0893600022", "Quantidade": "2", "Casa": "bauru", "__uidLinha": "b1"},
            {"Pedido": "A1", "SKU": "0893600022", "Quantidade": "4", "__uidLinha": "a1"},  # Araçatuba não conta
        ])
        it = self._item("S1")
        self.assertEqual(it["apollo"], 5)
        self.assertEqual(it["pendente"], 2)
        self.assertEqual(it["desejado"], 3)
        self.assertEqual(it["situacao"], "enviar")

    def test_apollo_sem_resposta_nunca_vira_zero(self):
        with self._api(_produtos(_sku("S1", "0893600022", 9))):
            S.tiktok_produtos_sincronizar()
        self._ler_apollo("0893600022", None, respondeu=False)
        it = self._item("S1")
        self.assertIsNone(it["desejado"])
        self.assertEqual(it["situacao"], "desconhecido")
        with self._api():
            S.tiktok_estoque_config_definir("modo_estoque", "automatico", "dono")
            r = S.tiktok_estoque_enviar(ator="sistema", automatico=True)
        self.assertEqual(r["enviados"], 0)
        self.assertFalse([e for e in self.enviados if e[1].endswith("/inventory/update")])

    def test_nao_lido_ainda_nao_e_enviado(self):
        with self._api(_produtos(_sku("S1", "0893600022", 9))):
            S.tiktok_produtos_sincronizar()
        self.assertEqual(self._item("S1")["situacao"], "desconhecido")

    def test_kit_fica_com_a_peca_que_tem_menos(self):
        with self._api(_produtos(_sku("S1", "AAA1/BBB2", 0), _sku("S2", "KIT2-CCC3", 0))):
            S.tiktok_produtos_sincronizar()
        self._ler_apollo("AAA1", 5)
        self._ler_apollo("BBB2", 2)
        self._ler_apollo("CCC3", 7)
        self.assertEqual(self._item("S1")["desejado"], 2)
        self.assertEqual(self._item("S2")["desejado"], 3)

    def test_sem_vinculo_e_vinculo_manual_vence(self):
        with self._api(_produtos(_sku("S1", "", 4), _sku("S2", "ERRADO", 4))):
            S.tiktok_produtos_sincronizar()
        self.assertEqual(self._item("S1")["situacao"], "sem_vinculo")
        S.tiktok_vinculo_definir("S2", "0893600022", "dono")
        self._ler_apollo("0893600022", 6)
        it = self._item("S2")
        self.assertEqual(it["pecas"], [["0893600022", 1]])
        self.assertEqual(it["desejado"], 6)
        S.tiktok_vinculo_definir("S2", "", "dono")  # apagar volta para o seller_sku
        self.assertEqual(self._item("S2")["pecas"], [["ERRADO", 1]])

    def test_igual_ao_tiktok_nao_envia(self):
        with self._api(_produtos(_sku("S1", "0893600022", 5))):
            S.tiktok_produtos_sincronizar()
        self._ler_apollo("0893600022", 5)
        self.assertEqual(self._item("S1")["situacao"], "igual")

    def test_varios_depositos_nao_envia(self):
        armazens = [{"warehouse_id": "W1", "quantity": 1}, {"warehouse_id": "W2", "quantity": 1}]
        with self._api(_produtos(_sku("S1", "0893600022", 0, armazens=armazens))):
            S.tiktok_produtos_sincronizar()
        self._ler_apollo("0893600022", 5)
        self.assertEqual(self._item("S1")["situacao"], "varios_depositos")


class Modos(Base):

    def _preparar(self):
        with self._api(_produtos(_sku("S1", "0893600022", 9), _sku("S2", "0893764272", 4))):
            S.tiktok_produtos_sincronizar()
        self._ler_apollo("0893600022", 5)
        self._ler_apollo("0893764272", 4)

    def test_comeca_em_simulacao_e_simulacao_nao_chama_o_tiktok(self):
        self.assertEqual(S.tiktok_estoque_config()["modo_estoque"], "simulacao")
        self._preparar()
        with self._api():
            with self.assertRaises(ValueError):
                S.tiktok_estoque_enviar(ator="dono")
            r = S.tiktok_estoque_enviar(ator="sistema", automatico=True)
        self.assertEqual(r["enviados"], 0)
        self.assertFalse([e for e in self.enviados if e[1].endswith("/inventory/update")])

    def test_lote_envia_so_o_que_mudou_no_formato_da_api(self):
        self._preparar()
        S.tiktok_estoque_config_definir("modo_estoque", "lote", "dono")
        with self._api():
            r = S.tiktok_estoque_enviar(ator="dono")
            auto = S.tiktok_estoque_enviar(ator="sistema", automatico=True)
        self.assertEqual(r["enviados"], 1)
        self.assertEqual(auto["enviados"], 0, "no Lote o worker não envia sozinho")
        upd = [e for e in self.enviados if e[1].endswith("/inventory/update")]
        self.assertEqual(len(upd), 1)
        self.assertEqual(upd[0][1], "/product/202309/products/P1/inventory/update")
        self.assertEqual(upd[0][2], {"skus": [{"id": "S1", "inventory": [{"warehouse_id": "W1", "quantity": 5}]}]})
        self.assertEqual(self._item("S1")["tiktok"], 5)
        self.assertEqual(self._item("S1")["situacao"], "igual")

    def test_automatico_envia_quando_a_fila_de_bauru_muda(self):
        self._preparar()
        S.tiktok_estoque_config_definir("modo_estoque", "automatico", "dono")
        with self._api():
            S.tiktok_estoque_enviar(ator="sistema", automatico=True)
            self._fila([{"Pedido": "ML1", "SKU": "0893764272", "Quantidade": "1", "Casa": "bauru", "__uidLinha": "m1"}])
            r = S.tiktok_estoque_enviar(ator="sistema", automatico=True)
        self.assertEqual(r["enviados"], 1)
        self.assertEqual(self._item("S2")["tiktok"], 3, "venda do ML de Bauru baixa no TikTok sem ler o Apollo")

    def test_erro_do_tiktok_nao_marca_como_enviado(self):
        self._preparar()
        S.tiktok_estoque_config_definir("modo_estoque", "lote", "dono")

        def falha(metodo, path, params=None, body=None, loja=True):
            return {"code": 0, "data": {"errors": [{"code": 12019024, "message": "stock count is invalid",
                                                    "detail": {"sku_id": "S1"}}]}}
        with mock.patch.object(S, "tiktok_api", side_effect=falha):
            r = S.tiktok_estoque_enviar(ator="dono")
        self.assertEqual(r["enviados"], 0)
        self.assertEqual(r["erros"], 1)
        self.assertEqual(self._item("S1")["tiktok"], 9)


class LeituraApollo(Base):

    def test_le_bauru_e_respeita_o_limite_proprio(self):
        with self._api(_produtos(_sku("S1", "AAA1", 0), _sku("S2", "BBB2", 0), _sku("S3", "CCC3", 0),
                                 _sku("S4", "DDD4", 0))):
            S.tiktok_produtos_sincronizar()
        chamadas = []
        with mock.patch.object(S, "crm_stock_lookup", side_effect=lambda c, **kw: chamadas.append(c) or _apollo(2)):
            for _ in range(6):
                S.tiktok_apollo_ler_proximo()
        self.assertEqual(len(chamadas), S.TIKTOK_APOLLO_POR_MINUTO, "passou de 3 consultas por minuto ao Apollo")
        self.assertEqual(self._item("S1")["apollo"], 2)

    def test_limite_do_apollo_ocupado_nao_grava_nada(self):
        with self._api(_produtos(_sku("S1", "AAA1", 0))):
            S.tiktok_produtos_sincronizar()
        with mock.patch.object(S, "crm_stock_lookup", side_effect=S.CrmLimiteAtingido(30)):
            S.tiktok_apollo_ler_proximo()
        self.assertEqual(self._item("S1")["situacao"], "desconhecido")


def _pedido(numero, status="AWAITING_SHIPMENT", itens=(("SK1", "0893600022"), ("SK1", "0893600022"))):
    return {"id": numero, "status": status, "create_time": 1760000000, "rts_sla_time": 1760090000,
            "recipient_address": {"name": "Cliente Teste"},
            "line_items": [{"id": f"{numero}-{i}", "sku_id": s, "seller_sku": c, "product_name": "Desengripante",
                            "display_status": status} for i, (s, c) in enumerate(itens)]}


class Pedidos(Base):

    def _fila_atual(self):
        bruto, _, _ = S.get_storage("conf_pedidos")
        return S._rows_from_storage_value(bruto)

    def test_importacao_comeca_ligada_e_pedido_entra_na_fila_de_bauru(self):
        self.assertTrue(S.tiktok_importacao_ligada())
        with self._api(pedidos=[_pedido("TT1")]):
            r = S.tiktok_rodada()
        self.assertTrue(r["importou"])
        linhas = [l for l in self._fila_atual() if l.get("Pedido") == "TT1"]
        self.assertEqual(len(linhas), 1, "cada unidade vem como um line_item; tem de virar uma linha com a quantidade")
        l = linhas[0]
        self.assertEqual(l["Quantidade"], "2")
        self.assertEqual(l["Casa"], "bauru")
        self.assertEqual(l["Marketplace"], "TIKTOK")
        self.assertEqual(l["OrigemCadastro"], "API_TIKTOK")

    def test_reimportar_nao_duplica_e_enviado_sai(self):
        with self._api(pedidos=[_pedido("TT1"), _pedido("TT2")]):
            S.tiktok_importar_pedidos()
            S.tiktok_importar_pedidos()
        self.assertEqual(len([l for l in self._fila_atual() if l.get("Pedido") == "TT1"]), 1)
        self.relidos = {"TT1": "IN_TRANSIT"}  # V20.40: só sai com o TikTok confirmando
        with self._api(pedidos=[_pedido("TT2")]):
            S.tiktok_importar_pedidos()
        pedidos = {l.get("Pedido") for l in self._fila_atual()}
        self.assertNotIn("TT1", pedidos)
        self.assertIn("TT2", pedidos)

    def test_desligada_nao_importa(self):
        S.tiktok_importacao_definir(False, "dono")
        with self._api(pedidos=[_pedido("TT1")]):
            r = S.tiktok_rodada()
        self.assertFalse(r["importou"])
        self.assertFalse([l for l in self._fila_atual() if l.get("Pedido") == "TT1"])

    def test_falha_da_api_nao_esvazia_a_fila(self):
        with self._api(pedidos=[_pedido("TT1")]):
            S.tiktok_importar_pedidos()
        with mock.patch.object(S, "tiktok_api", side_effect=RuntimeError("fora do ar")):
            with self.assertRaises(RuntimeError):
                S.tiktok_importar_pedidos()
        self.assertTrue([l for l in self._fila_atual() if l.get("Pedido") == "TT1"])

    def test_pedido_do_tiktok_desconta_do_estoque(self):
        with self._api(_produtos(_sku("S1", "0893600022", 9)), pedidos=[_pedido("TT1")]):
            S.tiktok_produtos_sincronizar()
            S.tiktok_importar_pedidos()
        self._ler_apollo("0893600022", 5)
        self.assertEqual(self._item("S1")["desejado"], 3)


class Rotas(Base):

    def test_automatico_exige_owner(self):
        st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/config", {"modo_estoque": "automatico"})
        self.assertIn(st, (403, 428), corpo)
        self.assertEqual(S.tiktok_estoque_config()["modo_estoque"], "simulacao")
        st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/config", {"modo_estoque": "lote"})
        self.assertEqual(st, 200, corpo)
        self.assertEqual(S.tiktok_estoque_config()["modo_estoque"], "lote")

    def test_payload_e_tela(self):
        st, corpo = self.cliente.pedir("GET", "/api/tiktok-estoque")
        self.assertEqual(st, 200)
        self.assertIn("resumo", corpo)
        texto = (S.APP_DIR / "tiktok-estoque.html").read_text(encoding="utf-8")
        self.assertIn("nk-menu.js?v=", texto)
        menu = (S.APP_DIR / "nk-menu.js").read_text(encoding="utf-8")
        self.assertIn('href: "/tiktok-estoque"', menu)

    def test_workers_no_painel_de_saude(self):
        for nome in ("tiktok_pedidos_loop", "tiktok_estoque_loop"):
            self.assertIn(nome, S.SAUDE_WORKERS_INFO)


if __name__ == "__main__":
    import unittest
    unittest.main()
