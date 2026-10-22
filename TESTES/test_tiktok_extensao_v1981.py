"""V19.81 — Estoque do TikTok pela extensão do Seller Center (a Open API exige gerente de contas).

A extensão (Edge, sessão do vendedor) lê a lista de produtos do Seller Center e manda ao
Noromak; o Noromak calcula a quantidade como na V19.64 e devolve o que mudou; a extensão grava
no TikTok (diferença, `quantity_variation`) e devolve o resultado. Sem a API conectada, o
rodízio do Apollo roda mesmo assim quando há variações vindas da extensão.
"""

import json
from unittest import mock

from base import S, TesteNoromak


def _produto(product_id="P1", skus=None, nome="V-floc Shampoo"):
    return {"product_id": product_id, "product_name": nome, "product_status": 4,
            "skus": skus if skus is not None else [_sku("S1", "2011004", 3)]}


def _sku(sku_id, seller_sku, disponivel, armazem="W1", armazens=None):
    return {"id": sku_id, "seller_sku": seller_sku,
            "quantities": armazens if armazens is not None else
            [{"warehouse_id": armazem, "available_quantity": disponivel, "withholding_quantity": 0}]}


def _apollo(disp):
    return {"ok": True, "estoques": [{"revenda": 3, "casa": "Bauru", "disponivel": disp, "respondeu": True}]}


class Base(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        S._TIKTOK_APOLLO_CHAMADAS.clear()
        S.TIKTOK_RODADA.clear()

    def _receber(self, produtos, completo=True):
        return S.tiktok_produtos_da_extensao(produtos, completo=completo)

    def _ler_apollo(self, codigo, disp):
        with mock.patch.object(S, "crm_stock_lookup", return_value=_apollo(disp)):
            S.tiktok_apollo_ler(codigo)


class Produtos(Base):

    def test_produtos_do_seller_center_viram_variacoes(self):
        r = self._receber([_produto()])
        self.assertEqual(r["variacoes"], 1)
        linha = S._tiktok_linhas_sku()[0]
        self.assertEqual((linha["sku_id"], linha["product_id"], linha["seller_sku"], linha["warehouse_id"],
                          linha["armazens"], linha["qtd_tiktok"]), ("S1", "P1", "2011004", "W1", 1, 3))

    def test_leitura_parcial_nao_apaga_quem_nao_veio(self):
        self._receber([_produto("P1"), _produto("P2", [_sku("S2", "123456", 1)])])
        self._receber([_produto("P1")], completo=False)
        self.assertEqual({r["sku_id"] for r in S._tiktok_linhas_sku()}, {"S1", "S2"})
        self._receber([_produto("P1")], completo=True)
        self.assertEqual({r["sku_id"] for r in S._tiktok_linhas_sku()}, {"S1"})

    def test_lista_vazia_completa_nao_apaga_tudo(self):
        self._receber([_produto()])
        with self.assertRaises(ValueError):
            self._receber([], completo=True)
        self.assertEqual(len(S._tiktok_linhas_sku()), 1)

    def test_variacao_sem_id_e_ignorada(self):
        self._receber([_produto(skus=[{"seller_sku": "X"}, _sku("S1", "2011004", 2)])])
        self.assertEqual([r["sku_id"] for r in S._tiktok_linhas_sku()], ["S1"])


class Pendentes(Base):

    def test_devolve_so_o_que_mudou_com_o_desejado(self):
        self._receber([_produto(skus=[_sku("S1", "2011004", 3), _sku("S2", "2022", 5)])])
        self._ler_apollo("2011004", 7)
        self._ler_apollo("2022", 5)
        r = S.tiktok_extensao_pendentes()
        self.assertEqual(r["modo"], "simulacao")
        self.assertFalse(r["pode_enviar"])
        self.assertEqual([(i["sku_id"], i["tiktok"], i["desejado"], i["warehouse_id"], i["product_id"])
                          for i in r["itens"]], [("S1", 3, 7, "W1", "P1")])

    def test_lote_pode_enviar(self):
        S.tiktok_estoque_config_definir("modo_estoque", "lote", "teste")
        self.assertTrue(S.tiktok_extensao_pendentes()["pode_enviar"])

    def test_apollo_sem_leitura_nao_aparece(self):
        self._receber([_produto()])
        self.assertEqual(S.tiktok_extensao_pendentes()["itens"], [])


class Resultado(Base):

    def test_ok_grava_a_quantidade_final_e_o_historico(self):
        self._receber([_produto()])
        self._ler_apollo("2011004", 7)
        S.tiktok_estoque_config_definir("modo_estoque", "lote", "teste")
        r = S.tiktok_extensao_resultado([{"sku_id": "S1", "de": 3, "para": 7, "ok": True, "final": 7}], "dono")
        self.assertEqual((r["ok_count"], r["erros"]), (1, 0))
        self.assertEqual(S._tiktok_linhas_sku()[0]["qtd_tiktok"], 7)
        self.assertEqual(S.tiktok_extensao_pendentes()["itens"], [])
        log = S.tiktok_estoque_payload()["log"][0]
        self.assertEqual((log["resultado"], log["de"], log["para"], log["modo"]), ("OK", 3, 7, "lote"))
        self.assertIn("extensão", log["detalhe"])

    def test_erro_nao_marca_como_enviado(self):
        self._receber([_produto()])
        self._ler_apollo("2011004", 7)
        S.tiktok_extensao_resultado([{"sku_id": "S1", "de": 3, "para": 7, "ok": False, "erro": "code 1000"}], "dono")
        self.assertEqual(S._tiktok_linhas_sku()[0]["qtd_tiktok"], 3)
        self.assertEqual(S.tiktok_estoque_payload()["log"][0]["resultado"], "ERRO")

    def test_sku_desconhecido_e_recusado(self):
        r = S.tiktok_extensao_resultado([{"sku_id": "NAO", "ok": True, "final": 1}], "dono")
        self.assertEqual(r["ignorados"], 1)


class Worker(Base):

    def test_sem_api_le_o_apollo_quando_ha_variacoes_da_extensao(self):
        self._receber([_produto()])
        with mock.patch.object(S, "tiktok_status_payload", return_value={"connected": False}), \
                mock.patch.object(S, "crm_stock_lookup", return_value=_apollo(4)) as apollo, \
                mock.patch.object(S, "tiktok_api") as api:
            ev = mock.Mock()
            ev.is_set.side_effect = [False, True]
            S.tiktok_estoque_loop(ev)
        self.assertEqual(apollo.call_count, 1)
        api.assert_not_called()


class Revisao(Base):
    """Achados da revisão antes de subir (22/10)."""

    def _pronto(self, desejado=7):
        self._receber([_produto()])
        self._ler_apollo("2011004", desejado)
        S.tiktok_estoque_config_definir("modo_estoque", "lote", "teste")

    def test_uma_rodada_de_envio_por_vez(self):
        self._pronto()
        r = S.tiktok_extensao_iniciar("dono")
        self.assertEqual([i["sku_id"] for i in r["itens"]], ["S1"])
        with self.assertRaises(S.TikTokRodadaOcupada):
            S.tiktok_extensao_iniciar("outro")
        S.tiktok_extensao_resultado([{"sku_id": "S1", "de": 3, "para": 7, "ok": True, "final": 7}], "dono",
                                    rodada=r["rodada"], fim=True)
        self.assertIsNotNone(S.tiktok_extensao_iniciar("outro")["rodada"])

    def test_rodada_esquecida_vence(self):
        self._pronto()
        S.tiktok_extensao_iniciar("dono")
        with mock.patch.object(S.time, "time", return_value=S.time.time() + S.TIKTOK_RODADA_PRAZO + 1):
            self.assertTrue(S.tiktok_extensao_iniciar("outro")["rodada"])

    def test_resultado_parcial_renova_a_rodada(self):
        self._pronto()
        r = S.tiktok_extensao_iniciar("dono")
        depois = S.time.time() + S.TIKTOK_RODADA_PRAZO - 5
        with mock.patch.object(S.time, "time", return_value=depois):
            S.tiktok_extensao_resultado([{"sku_id": "S1", "de": 3, "para": 7, "ok": True, "final": 7}], "dono",
                                        rodada=r["rodada"])
        with mock.patch.object(S.time, "time", return_value=depois + 60), self.assertRaises(S.TikTokRodadaOcupada):
            S.tiktok_extensao_iniciar("outro")

    def test_quantidade_infinita_nao_quebra(self):
        self.assertIsNone(S._tiktok_quantidade(float("inf")))

    def test_simulacao_nao_inicia_rodada(self):
        self._receber([_produto()])
        with self.assertRaises(ValueError):
            S.tiktok_extensao_iniciar("dono")

    def test_leitura_bem_menor_que_a_lista_nao_apaga(self):
        self._receber([_produto(f"P{n}", [_sku(f"S{n}", f"10{n:04d}", 1)]) for n in range(20)])
        r = self._receber([_produto("P0", [_sku("S0", "100000", 1)])], completo=True)
        self.assertFalse(r["apagou"])
        self.assertEqual(len(S._tiktok_linhas_sku()), 20)

    def test_ok_sem_quantidade_final_vira_erro(self):
        self._pronto()
        r = S.tiktok_extensao_resultado([{"sku_id": "S1", "de": 3, "para": 7, "ok": True}], "dono")
        self.assertEqual((r["ok_count"], r["erros"]), (0, 1))
        self.assertEqual(S._tiktok_linhas_sku()[0]["qtd_tiktok"], 3)

    def test_final_diferente_do_pedido_vira_erro_e_guarda_o_real(self):
        self._pronto()
        r = S.tiktok_extensao_resultado([{"sku_id": "S1", "de": 3, "para": 7, "ok": True, "final": 9}], "dono")
        self.assertEqual(r["erros"], 1)
        self.assertEqual(S._tiktok_linhas_sku()[0]["qtd_tiktok"], 9)

    def test_entrada_estranha_nao_quebra(self):
        r = self._receber([{"product_id": "P1", "skus": 5}, {"product_id": "P2", "skus": [{"id": "S" * 500, "quantities": "x"}]}],
                          completo=False)
        self.assertEqual(r["variacoes"], 1)
        self.assertLessEqual(len(S._tiktok_linhas_sku()[0]["sku_id"]), 64)

    def test_quantidade_vazia_fica_nula(self):
        self._receber([_produto(skus=[_sku("S1", "2011004", None)])])
        self._ler_apollo("2011004", 7)
        self.assertIsNone(S._tiktok_linhas_sku()[0]["qtd_tiktok"])

    def test_sem_api_so_le_o_apollo_com_extensao_recente(self):
        self._receber([_produto()])
        S._tiktok_config_gravar("extensao_lida_epoch", str(int(S.time.time()) - S.TIKTOK_EXTENSAO_RECENTE - 10), "teste")
        with mock.patch.object(S, "tiktok_status_payload", return_value={"connected": False}), \
                mock.patch.object(S, "crm_stock_lookup", return_value=_apollo(4)) as apollo:
            ev = mock.Mock()
            ev.is_set.side_effect = [False, True]
            S.tiktok_estoque_loop(ev)
        apollo.assert_not_called()


class Rotas(Base):

    def test_rotas_da_extensao(self):
        st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/extensao/produtos",
                                       {"produtos": [_produto()], "completo": True})
        self.assertEqual(st, 200, corpo)
        self._ler_apollo("2011004", 9)
        st, corpo = self.cliente.pedir("GET", "/api/tiktok-estoque/extensao/pendentes")
        self.assertEqual(st, 200, corpo)
        self.assertEqual(corpo["itens"][0]["desejado"], 9)
        st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/extensao/iniciar", {})
        self.assertEqual(st, 400, corpo)  # Simulação
        S.tiktok_estoque_config_definir("modo_estoque", "lote", "teste")
        st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/extensao/iniciar", {})
        self.assertEqual(st, 200, corpo)
        rodada = corpo["rodada"]
        st, _ = self.cliente.pedir("POST", "/api/tiktok-estoque/extensao/iniciar", {})
        self.assertEqual(st, 409)
        st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/extensao/resultado",
                                       {"rodada": rodada, "fim": True,
                                        "resultados": [{"sku_id": "S1", "de": 3, "para": 9, "ok": True, "final": 9}]})
        self.assertEqual(st, 200, corpo)
        self.assertEqual(corpo["ok_count"], 1)

    def test_lista_vazia_pela_rota_e_400(self):
        st, _ = self.cliente.pedir("POST", "/api/tiktok-estoque/extensao/produtos", {"produtos": [], "completo": True})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    import unittest
    unittest.main()
