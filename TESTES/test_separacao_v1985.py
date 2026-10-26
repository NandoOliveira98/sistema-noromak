"""V19.85 — Lista de separação do dia (pedido do dono, 26/10).

Decisões: todos os pendentes da fila de conferência; tela própria (menu Expedição) e botão em
Pedidos pendentes; por filial (a do seletor do menu); ordem pela prateleira do Apollo da filial;
cada linha = prateleira, código, descrição, quantidade total. Kit abre em peças.
Apollo sem resposta nunca trava a lista: a peça fica sem prateleira, no fim.
"""
from __future__ import annotations

import unittest
from unittest import mock

from base import S, gravar_como_admin, linha, reiniciar_banco


def apollo(locais: dict[str, str], revenda: int = 1):
    def lookup(codigo, operador="", wait_seconds=0.0):
        if codigo not in locais:
            raise RuntimeError("sem resposta")
        return {"estoques": [{"revenda": revenda, "disponivel": 5, "localizacao": locais[codigo]}],
                "casas_sem_resposta": []}
    return lookup


class ListaDeSeparacao(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        S.SEPARACAO_LOCAIS.clear()
        S.SEPARACAO_SEM_RESPOSTA.clear()
        S.SEPARACAO_ULTIMA_LEITURA.clear()

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

    def test_todas_pede_para_escolher(self):
        with self.assertRaises(ValueError):
            S.separacao_lista("todas")

    def test_tela_menu_e_botao(self):
        self.assertTrue((S.APP_DIR / "separacao.html").exists())
        self.assertIn("/separacao", (S.APP_DIR / "nk-menu.js").read_text(encoding="utf-8"))
        self.assertIn("separacao", (S.APP_DIR / "admin.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
