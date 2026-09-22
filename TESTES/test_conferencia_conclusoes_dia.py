"""V18.08 — o contador de "conferidos" do dia tem de vir do SERVIDOR.

Bug real (21/09/2026, produção): o painel do dia da Conferência contava os
conferidos a partir do historicoConferencias, que fica no navegador de CADA
máquina. Com o dono olhando o painel numa máquina e o operador (matheus)
conferindo em outra, o contador do dono nunca via as conclusões dele — o pedido
saía da fila (concluído de verdade no servidor) mas não aparecia como conferido,
dando a impressão de que "o sistema apagou o pedido".

A rota GET /api/conferencia/conclusoes-dia devolve as conclusões de
completed_orders, que é a fonte COMPARTILHADA. Este teste garante que uma
conclusão feita por UMA sessão aparece para QUALQUER outra sessão, com o
marketplace certo, e que um pedido ainda pendente não entra na lista.
"""

import unittest

from base import Cliente, TesteNoromak, linha


class ConclusoesDoDiaCompartilhadas(TesteNoromak):
    def _concluir(self, cliente, pedido):
        status, corpo = cliente.pedir("POST", "/api/order-complete", {"pedido": pedido})
        self.assertEqual(status, 200, f"conclusão falhou: {corpo}")
        self.assertTrue(corpo.get("ok"), corpo)

    def test_conclusao_de_uma_maquina_aparece_para_outra(self):
        self.salvar_admin([
            linha("2000018553711772", "SKU-A", 1, uid="u1", Marketplace="SHOPEE"),
            linha("260920HSJ3VS25", "SKU-B", 1, uid="u2", Marketplace="MERCADO LIVRE"),
        ])

        # "Máquina do matheus" (a sessão do setUp) confere um pedido.
        self._concluir(self.cliente, "2000018553711772")

        # OUTRA máquina/sessão consulta o painel do dia — tem de enxergar a
        # conclusão feita na primeira.
        outra_maquina = Cliente(self.url_base)
        outra_maquina.entrar()
        status, corpo = outra_maquina.pedir("GET", "/api/conferencia/conclusoes-dia")
        self.assertEqual(status, 200, corpo)
        conclusoes = {c["pedido"]: c for c in (corpo.get("conclusoes") or [])}

        self.assertIn(
            "2000018553711772", conclusoes,
            "conclusão feita numa máquina TEM de aparecer em outra (fonte = servidor)",
        )
        self.assertEqual(
            conclusoes["2000018553711772"]["marketplace"], "SHOPEE",
            "o marketplace do conferido vem do payload salvo na conclusão",
        )
        self.assertNotIn(
            "260920HSJ3VS25", conclusoes,
            "pedido ainda pendente não pode aparecer como conferido",
        )

    def test_rota_exige_autenticacao(self):
        anonimo = Cliente(self.url_base)
        status, _ = anonimo.pedir("GET", "/api/conferencia/conclusoes-dia")
        self.assertEqual(status, 401, "sem login não pode ler as conclusões do dia")


if __name__ == "__main__":
    unittest.main()
