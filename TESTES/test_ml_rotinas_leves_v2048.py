"""V20.48 — rotinas do ML sem trabalho repetido (10/11: banco ocupado 45% do tempo, telas travando).

Medido em produção (Painel de saúde, 18 h): o vigia de etiquetas segurava o banco 2 h 56 min e a reconciliação de
pedidos manuais 2 h 05 min. Causas:
1. A "recuperação de pedidos presos" rodava em TODA volta do vigia (a conta `round(120 / intervalo)` dava 0 com o
   intervalo de 300 s da V20.15) e reimportava os pedidos que só esperam etiqueta — que já têm venda e envio e que o
   próprio vigia acompanha. ~60 importações completas a cada 5 min.
2. A reconciliação reimportava a cada 60 s o pedido que já tem a venda e ainda não tem envio.
3. Abrir o Precificador relia todos os anúncios no ML (174 s): a leitura do ML fica guardada 15 min.
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

from base import S, TesteNoromak, gravar_como_integracao, linha


def linha_ml(venda: str, sku: str, **extra) -> dict:
    return linha(venda, sku, uid=f"{venda}-{sku}", Marketplace="MERCADO LIVRE",
                 VendaMercadoLivre=venda, OrigemCadastro="API Mercado Livre", **extra)


class RecuperacaoSoDosPresos(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        gravar_como_integracao([
            # esperando etiqueta: já tem venda e envio — é do vigia de etiquetas
            linha_ml("2000000000001", "SKU-A", ShipmentIdMercadoLivre="4400000000001"),
            # preso de verdade: veio da extensão sem venda nem envio
            linha("2000000000002", "SKU-B", uid="m2", Marketplace="MERCADO LIVRE"),
        ], origem="API Mercado Livre")

    def rodar(self, **kw):
        por_envio, por_numero = [], []
        with mock.patch.object(S, "_ml_orders_for_shipment", side_effect=lambda sid: por_envio.append(sid) or []), \
                mock.patch.object(S, "_ml_recover_one_identifier",
                                  side_effect=lambda ident, source: por_numero.append(ident) or {"orders": []}), \
                mock.patch.object(S, "ml_log_event"):
            S._ml_recover_local_pending_identifiers_once(**kw)
        return por_envio, por_numero

    def test_automatica_pula_quem_ja_tem_venda_e_envio(self):
        por_envio, por_numero = self.rodar(max_identifiers=30, automatica=True)
        self.assertNotIn("4400000000001", por_envio,
                         "REGRESSÃO: pedido que só espera etiqueta foi reimportado pela recuperação")
        self.assertNotIn("2000000000001", por_numero)
        self.assertIn("2000000000002", por_numero, "o pedido preso de verdade saiu da recuperação")

    def test_botao_manual_continua_completo(self):
        por_envio, por_numero = self.rodar()
        self.assertIn("4400000000001", por_envio)
        self.assertIn("2000000000001", por_numero)


class DescobertaNoRitmoCerto(unittest.TestCase):

    def setUp(self) -> None:
        S._ML_DESCOBERTA_ULTIMA.clear()

    def test_descoberta_a_cada_15_min_por_casa(self):
        t0 = 1_000_000.0
        self.assertTrue(S._ml_descoberta_devida("aracatuba", t0))
        self.assertFalse(S._ml_descoberta_devida("aracatuba", t0 + 300),
                         "REGRESSÃO: a recuperação voltou a rodar em toda volta do vigia")
        self.assertTrue(S._ml_descoberta_devida("bauru", t0 + 300), "cada casa tem o seu relógio")
        self.assertTrue(S._ml_descoberta_devida("aracatuba", t0 + S.ML_DESCOBERTA_S))

    def test_ciclo_so_descobre_quando_devido(self):
        with mock.patch.object(S, "ml_pending_label_watch_once"), \
                mock.patch.object(S, "_ml_recover_local_pending_identifiers_once", return_value={}) as rec, \
                mock.patch.object(S, "_ml_discover_untracked_ready_labels_once", return_value={}) as desc:
            S._ml_pending_label_watch_ciclo(True)
            S._ml_pending_label_watch_ciclo(True)
        self.assertEqual(rec.call_count, 1)
        self.assertEqual(desc.call_count, 1)
        self.assertTrue(rec.call_args.kwargs.get("automatica"))


class ReconciliacaoEspacada(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        S._ML_RECONC_COM_VENDA.clear()
        gravar_como_integracao([
            # a API já reconheceu a venda, mas o ML ainda não criou o envio
            linha_ml("2000000000005", "SKU-C"),
            # print/extensão: só o número
            linha("2000000000006", "SKU-D", uid="m6", Marketplace="MERCADO LIVRE"),
        ], origem="API Mercado Livre")

    def test_venda_sem_envio_e_revista_a_cada_15_min(self):
        t0 = time.time()
        self.assertIn("2000000000005", S._ml_manual_pending_numbers(agora=t0))
        self.assertNotIn("2000000000005", S._ml_manual_pending_numbers(agora=t0 + 60),
                         "REGRESSÃO: venda sem envio reimportada a cada 60 s")
        self.assertIn("2000000000005", S._ml_manual_pending_numbers(agora=t0 + S.ML_RECONC_COM_VENDA_S + 1))

    def test_pedido_de_print_continua_a_cada_minuto(self):
        t0 = time.time()
        self.assertIn("2000000000006", S._ml_manual_pending_numbers(agora=t0))
        self.assertIn("2000000000006", S._ml_manual_pending_numbers(agora=t0 + 60))


class PrecificadorGuardaALeituraDoMl(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        S._ML_PRECIFICADOR_LIVE.clear()
        self.leituras = 0

    def todos(self, forcar=False):
        self.leituras += 1
        return ["MLB1"]

    def payload(self, links, agora=None, todos=None):
        with mock.patch.object(S, "ml_access_token", return_value=("tok", "123")),                 mock.patch.object(S, "ml_todos_os_anuncios", side_effect=todos or self.todos),                 mock.patch.object(S, "ml_items_meta", return_value={"1": {"sku": "SKU-X", "title": "Peça", "price": 10}}),                 mock.patch.object(S, "_ml_precificador_persisted_links", return_value=links),                 mock.patch.object(S, "_promo_precifier_index", return_value={}),                 mock.patch.object(S.time, "time", return_value=agora or 2_000_000.0):
            return S.ml_precificador_links_payload(5000)

    def test_reabrir_nao_vai_ao_ml_de_novo(self):
        p1 = self.payload({})
        p2 = self.payload({"SKU-Y": [{"mlb": "MLB9", "sku": "SKU-Y"}]})
        self.assertEqual(self.leituras, 1, "REGRESSÃO: o Precificador releu o ML a cada abertura")
        self.assertEqual(p1["total_items"], p2["total_items"])
        self.assertIn("SKU-Y", p2["links"], "o vínculo gravado no banco tem de valer na hora (só o ML fica guardado)")

    def test_depois_de_15_min_le_de_novo(self):
        self.payload({}, agora=2_000_000.0)
        self.payload({}, agora=2_000_000.0 + S.ML_PRECIFICADOR_LIVE_S + 1)
        self.assertEqual(self.leituras, 2)

    def test_leitura_sem_nenhum_anuncio_nao_fica_guardada(self):
        def falha(forcar=False):
            self.leituras += 1
            raise RuntimeError("ML fora")
        p = self.payload({}, todos=falha)
        self.assertTrue(p["errors"])
        self.payload({})
        self.assertEqual(self.leituras, 2)

    def test_leitura_parcial_fica_guardada_com_o_erro(self):
        with mock.patch.object(S, "ml_items_meta", side_effect=RuntimeError("429")):
            with mock.patch.object(S, "ml_access_token", return_value=("tok", "123")),                     mock.patch.object(S, "ml_todos_os_anuncios", side_effect=self.todos),                     mock.patch.object(S, "_ml_precificador_persisted_links", return_value={}),                     mock.patch.object(S, "_promo_precifier_index", return_value={}):
                p = S.ml_precificador_links_payload(5000)
        self.assertTrue(p["errors"])
        self.payload({})
        self.assertEqual(self.leituras, 1, "leitura parcial não ficou guardada: cada abertura relia tudo")

    def test_quem_chega_com_leitura_em_andamento_nao_espera(self):
        self.payload({}, agora=2_000_000.0)   # há uma leitura guardada (vencida daqui a pouco)
        S._ML_PRECIFICADOR_LIVE_LOCK.acquire()
        try:
            with mock.patch.object(S, "ML_PRECIFICADOR_LIVE_ESPERA_S", 0.05):
                t = time.monotonic()
                p = self.payload({}, agora=2_000_000.0 + S.ML_PRECIFICADOR_LIVE_S + 1)
                self.assertLess(time.monotonic() - t, 2)
        finally:
            S._ML_PRECIFICADOR_LIVE_LOCK.release()
        self.assertEqual(self.leituras, 1, "devia devolver a última leitura em vez de esperar")
        self.assertEqual(p["total_items"], 1)


class RevisaoV2048(TesteNoromak):
    """revisar-antes-de-subir da V20.48."""

    def test_linha_com_etiqueta_removida_pelo_admin_continua_na_recuperacao(self):
        # texto VINCULADA mas sem etiqueta válida no banco: o vigia pula, então a recuperação tem de rever
        gravar_como_integracao([
            linha_ml("2000000000007", "SKU-E", ShipmentIdMercadoLivre="4400000000007",
                     **{"Etiqueta API Mercado Livre": "VINCULADA"}),
        ], origem="API Mercado Livre")
        self.assertNotIn(("2000000000007", "4400000000007"), S._ml_all_pending_label_candidates())
        por_envio = []
        with mock.patch.object(S, "_ml_orders_for_shipment", side_effect=lambda sid: por_envio.append(sid) or []),                 mock.patch.object(S, "_ml_recover_one_identifier", return_value={"orders": []}),                 mock.patch.object(S, "ml_log_event"):
            S._ml_recover_local_pending_identifiers_once(max_identifiers=30, automatica=True)
        self.assertIn("4400000000007", por_envio, "REGRESSÃO: pedido com etiqueta removida ficou sem ninguém")

    def test_reconciliacao_que_falha_nao_gasta_a_vez(self):
        S._ML_RECONC_COM_VENDA.clear()
        gravar_como_integracao([linha_ml("2000000000008", "SKU-F")], origem="API Mercado Livre")
        with mock.patch.object(S, "ml_access_token", side_effect=RuntimeError("token renovando")):
            with self.assertRaises(RuntimeError):
                S.ml_reconcile_manual_orders_once()
        self.assertIn("2000000000008", S._ml_manual_pending_numbers(vezes=[]),
                      "a falha da volta deixou a venda 15 min sem revisão")

    def test_descoberta_que_falha_tenta_na_volta_seguinte(self):
        S._ML_DESCOBERTA_ULTIMA.clear()
        with mock.patch.object(S, "ml_pending_label_watch_once"),                 mock.patch.object(S, "_ml_recover_local_pending_identifiers_once", side_effect=RuntimeError("ML fora")),                 mock.patch.object(S, "_ml_discover_untracked_ready_labels_once", return_value={}),                 mock.patch.object(S, "ml_log_event"):
            S._ml_pending_label_watch_ciclo(True)
        self.assertTrue(S._ml_descoberta_devida(S.ml_casa_atual()))


if __name__ == "__main__":
    unittest.main()
