"""V19.85 — Lista de separação do dia (pedido do dono, 26/10).

Decisões: todos os pendentes da fila de conferência; tela própria (menu Expedição) e botão em
Pedidos pendentes; por filial (a do seletor do menu); ordem pela prateleira do Apollo da filial;
cada linha = prateleira, código, descrição, quantidade total. Kit abre em peças.
Apollo sem resposta nunca trava a lista: a peça fica sem prateleira, no fim.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from base import S, gravar_como_admin, linha, reiniciar_banco


def apollo(locais: dict[str, str], revenda: int = 1):
    def lookup(codigo, operador="", wait_seconds=0.0):
        if codigo not in locais:
            raise RuntimeError("sem resposta")
        return {"estoques": [{"revenda": revenda, "disponivel": 5, "respondeu": True,
                              "localizacao": locais[codigo]}],
                "casas_sem_resposta": []}
    return lookup


class ListaDeSeparacao(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        S.SEPARACAO_LOCAIS.clear()
        S.SEPARACAO_SEM_RESPOSTA.clear()
        S.SEPARACAO_ULTIMA_LEITURA.clear()
        S.SEPARACAO_LENDO.clear()
        S.SEPARACAO_PAUSA_ATE[0] = 0.0

    def montar(self, filial="aracatuba", locais=None, revenda=1):
        with mock.patch.object(S, "crm_stock_lookup", side_effect=apollo(locais or {}, revenda)), \
                mock.patch.object(S, "SEPARACAO_INTERVALO", 0):
            S.separacao_ler_apollo(filial)
            return S.separacao_lista(filial)

    def test_soma_por_codigo_e_ordena_pela_prateleira(self):
        gravar_como_admin([
            linha("P1", "AAA", 2, Descrição="Parafuso"),
            linha("P2", "AAA", 1, Descrição="Parafuso"),
            linha("P3", "BBB", 1, Descrição="Porca"),
            linha("P4", "CCC", 3, Descrição="Arruela")])
        r = self.montar(locais={"AAA": "ZZ.9.14", "BBB": "ZZ.9.9"})
        self.assertEqual([(i["codigo"], i["qtd"], i["localizacao"]) for i in r["itens"]],
                         [("BBB", 1, "ZZ.9.9"), ("AAA", 3, "ZZ.9.14"), ("CCC", 3, "")])
        self.assertEqual(r["pedidos"], 4)
        self.assertEqual(r["itens"][1]["descricao"], "Parafuso")

    def test_kit_abre_em_pecas(self):
        gravar_como_admin([linha("P1", "AAA/BBB", 2), linha("P2", "KIT3-CCC", 1),
                           linha("P3", "151490-DDD", 1)])
        r = self.montar()
        self.assertEqual({i["codigo"]: i["qtd"] for i in r["itens"]},
                         {"AAA": 2, "BBB": 2, "CCC": 3, "DDD": 1})

    def test_so_a_filial_escolhida_com_a_prateleira_dela(self):
        gravar_como_admin([linha("P1", "AAA", 1), linha("P2", "BBB", 1, Casa="bauru")])
        r = self.montar("bauru", {"BBB": "B.1"}, revenda=3)
        self.assertEqual([(i["codigo"], i["localizacao"]) for i in r["itens"]], [("BBB", "B.1")])

    def test_concluido_nao_entra(self):
        gravar_como_admin([linha("P1", "AAA", 1), linha("P2", "BBB", 1)])
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO completed_orders(order_key,order_id,completed_at,completed_by,payload) "
                         "VALUES(?,?,?,?,?)", (S.normalize("P2"), "P2", "x", "x", "{}"))
            S.DB.commit()
        self.assertEqual([i["codigo"] for i in self.montar()["itens"]], ["AAA"])

    def test_prateleira_guardada_nao_chama_o_apollo_de_novo(self):
        gravar_como_admin([linha("P1", "AAA", 1)])
        self.montar(locais={"AAA": "A.1"})
        with mock.patch.object(S, "crm_stock_lookup", side_effect=AssertionError("chamou")):
            S.separacao_ler_apollo("aracatuba")
            self.assertEqual(S.separacao_lista("aracatuba")["itens"][0]["localizacao"], "A.1")

    def test_lista_sai_na_hora_e_diz_o_que_falta_ler(self):
        gravar_como_admin([linha("P1", "AAA", 1)])
        with mock.patch.object(S, "separacao_iniciar_leitura"):
            r = S.separacao_lista("aracatuba")
        self.assertEqual(r["faltam"], 1)
        self.assertEqual(r["itens"][0]["localizacao"], "")

    def test_sem_resposta_tenta_de_novo_depois(self):
        gravar_como_admin([linha("P1", "AAA", 1)])
        self.montar()   # Apollo não respondeu
        with mock.patch.object(S, "separacao_iniciar_leitura") as ler:
            S.separacao_lista("aracatuba")
            ler.assert_not_called()
            S.SEPARACAO_ULTIMA_LEITURA["aracatuba"] -= S.SEPARACAO_REPETIR_S + 1
            S.separacao_lista("aracatuba")
            ler.assert_called_once()

    # --- Revisão antes de subir (26/10) ---

    def test_codigo_inteiro_com_barra_nao_vira_duas_pecas(self):
        # V19.48: "PSH367/80" é UM produto (está no Precificador como código inteiro).
        atual, _, _ = S.get_storage("precificador_online_v1")
        S.save_storage("precificador_online_v1", atual, json.dumps({"products": [{"sku": "PSH367/80"}]}),
                       False, actor="admin_teste", actor_role="admin", source_screen="Teste",
                       change_method="Teste automatizado", client_ip="127.0.0.1")
        gravar_como_admin([linha("P1", "PSH367/80", 2), linha("P2", "AAA/BBB", 1)])
        r = self.montar()
        self.assertEqual({i["codigo"]: i["qtd"] for i in r["itens"]},
                         {"PSH367/80": 2, "AAA": 1, "BBB": 1})

    def test_kit_cadastrado_como_produto_abre_em_pecas(self):
        # 151 kits estão no Precificador como produto comum (V18.80): se as peças existem, é kit.
        atual, _, _ = S.get_storage("precificador_online_v1")
        S.save_storage("precificador_online_v1", atual, json.dumps({"products": [
            {"sku": "AAA/BBB"}, {"sku": "AAA"}, {"sku": "BBB"}, {"sku": "KIT2-PSH367/80"}]}),
            False, actor="admin_teste", actor_role="admin", source_screen="Teste",
            change_method="Teste automatizado", client_ip="127.0.0.1")
        gravar_como_admin([linha("P1", "AAA/BBB", 1), linha("P2", "KIT2-PSH367/80", 1)])
        self.assertEqual({i["codigo"]: i["qtd"] for i in self.montar()["itens"]},
                         {"AAA": 1, "BBB": 1, "PSH367/80": 2})

    def test_thread_que_nao_sobe_nao_trava_a_leitura(self):
        gravar_como_admin([linha("P1", "AAA", 1)])
        with mock.patch.object(S.threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            S.separacao_iniciar_leitura("aracatuba")
        self.assertFalse(S.SEPARACAO_LENDO)

    # --- Lista real de 26/10 ---

    def test_kit_com_quantidade_de_cada_peca(self):
        # "KIT-1/10405594-1/10405569" = 1 de cada lanterna (pedido 2000019075079456).
        gravar_como_admin([linha("P1", "KIT-1/10405594-1/10405569", 1), linha("P2", "KIT-2/AAA-1/BBB", 1)])
        self.assertEqual({i["codigo"]: i["qtd"] for i in self.montar()["itens"]},
                         {"10405594": 1, "10405569": 1, "AAA": 2, "BBB": 1})

    def test_variacao_curta_depois_da_barra_e_uma_peca(self):
        # "WAP103/S" (filtro) não é "WAP103" + "S", mesmo fora do Precificador.
        gravar_como_admin([linha("P1", "WAP103/S", 1)])
        self.assertEqual([i["codigo"] for i in self.montar()["itens"]], ["WAP103/S"])

    def test_separada_por_marketplace(self):
        gravar_como_admin([
            linha("P1", "AAA", 2, Marketplace="MERCADO LIVRE"),
            linha("P2", "AAA", 1, Marketplace="SHOPEE"),
            linha("P3", "BBB", 1, Marketplace="SHOPEE")])
        r = self.montar(locais={"AAA": "A.1", "BBB": "A.0"})
        grupos = {g["nome"]: g for g in r["grupos"]}
        self.assertEqual([g["nome"] for g in r["grupos"]], ["Mercado Livre", "Shopee"])
        self.assertEqual([(i["codigo"], i["qtd"]) for i in grupos["Mercado Livre"]["itens"]], [("AAA", 2)])
        self.assertEqual([(i["codigo"], i["qtd"]) for i in grupos["Shopee"]["itens"]], [("BBB", 1), ("AAA", 1)])
        self.assertEqual((grupos["Shopee"]["pedidos"], grupos["Shopee"]["unidades"]), (2, 2))

    def test_filial_que_nao_respondeu_nao_vira_sem_prateleira(self):
        gravar_como_admin([linha("P1", "AAA", 1)])
        resposta = {"estoques": [{"revenda": 1, "disponivel": None, "respondeu": False, "localizacao": ""}],
                    "casas_sem_resposta": [1]}
        with mock.patch.object(S, "crm_stock_lookup", return_value=resposta),                 mock.patch.object(S, "SEPARACAO_INTERVALO", 0):
            S.separacao_ler_apollo("aracatuba")
        self.assertNotIn((1, "AAA"), S.SEPARACAO_LOCAIS)
        self.assertEqual(S.SEPARACAO_SEM_RESPOSTA["aracatuba"], {"AAA"})

    def test_quantidade_com_virgula(self):
        gravar_como_admin([linha("P1", "AAA", "3,00")])
        self.assertEqual(self.montar()["itens"][0]["qtd"], 3)

    def test_nao_espera_vaga_e_para_no_limite_do_apollo(self):
        gravar_como_admin([linha("P1", "AAA", 1), linha("P2", "BBB", 1)])
        chamadas = []

        def cheio(codigo, operador="", wait_seconds=0.0):
            chamadas.append(wait_seconds)
            raise S.CrmLimiteAtingido(30)
        with mock.patch.object(S, "crm_stock_lookup", side_effect=cheio),                 mock.patch.object(S, "SEPARACAO_INTERVALO", 0):
            S.separacao_ler_apollo("aracatuba")
        self.assertEqual(chamadas, [0])                      # uma tentativa, sem esperar vaga
        self.assertFalse(S.SEPARACAO_SEM_RESPOSTA.get("aracatuba"))   # não é "sem resposta": falta ler
        with mock.patch.object(S, "separacao_iniciar_leitura") as ler:
            self.assertEqual(S.separacao_lista("aracatuba")["faltam"], 2)
            ler.assert_not_called()                          # pausa: deixa a vez para a operação

    def test_uma_leitura_por_vez_para_as_duas_filiais(self):
        gravar_como_admin([linha("P1", "AAA", 1), linha("P2", "BBB", 1, Casa="bauru")])
        S.SEPARACAO_LENDO.add("aracatuba")
        with mock.patch.object(S.threading, "Thread") as thread:
            S.separacao_iniciar_leitura("bauru")
            thread.assert_not_called()
        self.assertGreaterEqual(S.SEPARACAO_INTERVALO, 10)

    def test_todas_pede_para_escolher(self):
        with self.assertRaises(ValueError):
            S.separacao_lista("todas")

    def test_tela_menu_e_botao(self):
        self.assertTrue((S.APP_DIR / "separacao.html").exists())
        self.assertIn("/separacao", (S.APP_DIR / "nk-menu.js").read_text(encoding="utf-8"))
        self.assertIn("separacao", (S.APP_DIR / "admin.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
