"""V17.83 — o que vendeu no período, agregado por SKU.

Base da Central de Pedidos do Dia para rentabilidade, curva ABC e solicitação
de compra. A rota é somente leitura: nenhum teste aqui pode ver dado mudar.
"""

import unittest

from base import S, TesteNoromak, gravar_como_integracao, linha, pedidos


def linha_datada(pedido, sku, quantidade, data, uid=None, **extra):
    """Linha de pedido com data explícita, no formato que o sistema usa."""
    return linha(pedido, sku, quantidade=quantidade, uid=uid,
                 **{"Data do Pedido": data, **extra})


class VendasAgregadas(TesteNoromak):

    def vendas(self, **filtros):
        consulta = "&".join(f"{k}={v}" for k, v in filtros.items())
        rota = "/api/vendas" + (f"?{consulta}" if consulta else "")
        status, corpo = self.cliente.pedir("GET", rota)
        self.assertEqual(status, 200, f"consulta de vendas falhou: {corpo}")
        return corpo

    def test_soma_unidades_do_mesmo_sku_em_pedidos_diferentes(self):
        gravar_como_integracao([
            linha_datada("PED-1", "SKU-A", 2, "2026-09-17", uid="P1L1"),
            linha_datada("PED-2", "SKU-A", 3, "2026-09-17", uid="P2L1"),
            linha_datada("PED-3", "SKU-B", 1, "2026-09-17", uid="P3L1"),
        ])
        corpo = self.vendas(data_inicio="2026-09-17", data_fim="2026-09-17")

        por_sku = {i["sku"]: i for i in corpo["itens"]}
        self.assertEqual(por_sku["SKU-A"]["unidades"], 5, "as unidades do SKU não somaram")
        self.assertEqual(por_sku["SKU-A"]["pedidos"], 2, "a contagem de pedidos do SKU está errada")
        self.assertEqual(por_sku["SKU-B"]["unidades"], 1)
        self.assertEqual(corpo["resumo"]["unidades"], 6)
        self.assertEqual(corpo["resumo"]["pedidos"], 3)
        self.assertEqual(corpo["resumo"]["skus"], 2)

    def test_ordena_do_mais_vendido_para_o_menos(self):
        gravar_como_integracao([
            linha_datada("PED-1", "SKU-POUCO", 1, "2026-09-17", uid="P1L1"),
            linha_datada("PED-2", "SKU-MUITO", 9, "2026-09-17", uid="P2L1"),
            linha_datada("PED-3", "SKU-MEDIO", 4, "2026-09-17", uid="P3L1"),
        ])
        corpo = self.vendas(data_inicio="2026-09-17", data_fim="2026-09-17")
        self.assertEqual(
            [i["sku"] for i in corpo["itens"]],
            ["SKU-MUITO", "SKU-MEDIO", "SKU-POUCO"],
            "a curva depende dessa ordem: mais vendido primeiro")

    def test_filtra_pelo_periodo_pedido(self):
        gravar_como_integracao([
            linha_datada("PED-ONTEM", "SKU-A", 5, "2026-09-16", uid="POL1"),
            linha_datada("PED-HOJE", "SKU-A", 2, "2026-09-17", uid="PHL1"),
        ])
        hoje = self.vendas(data_inicio="2026-09-17", data_fim="2026-09-17")
        self.assertEqual(hoje["resumo"]["unidades"], 2, "trouxe venda de fora do período")

        ambos = self.vendas(data_inicio="2026-09-16", data_fim="2026-09-17")
        self.assertEqual(ambos["resumo"]["unidades"], 7, "o período de dois dias não somou")

    def test_aceita_data_no_formato_brasileiro(self):
        """O Apollo e as telas entregam 17/09/2026; o servidor entrega ISO."""
        gravar_como_integracao([
            linha_datada("PED-BR", "SKU-A", 3, "17/09/2026 10:07:00", uid="PBL1"),
        ])
        corpo = self.vendas(data_inicio="2026-09-17", data_fim="2026-09-17")
        self.assertEqual(corpo["resumo"]["unidades"], 3,
                         "a data brasileira não foi reconhecida")

    def test_separa_por_marketplace(self):
        gravar_como_integracao([
            linha_datada("PED-1", "SKU-A", 2, "2026-09-17", uid="P1L1", Marketplace="Mercado Livre"),
            linha_datada("PED-2", "SKU-A", 1, "2026-09-17", uid="P2L1", Marketplace="SHOPEE"),
            linha_datada("PED-3", "SKU-B", 4, "2026-09-17", uid="P3L1", Marketplace="magalu"),
        ])
        corpo = self.vendas(data_inicio="2026-09-17", data_fim="2026-09-17")

        canais = {c["marketplace"]: c for c in corpo["marketplaces"]}
        self.assertEqual(canais["Mercado Livre"]["unidades"], 2)
        self.assertEqual(canais["Shopee"]["unidades"], 1, "SHOPEE não foi normalizado")
        self.assertEqual(canais["Magalu"]["unidades"], 4, "magalu não foi normalizado")

        sku_a = next(i for i in corpo["itens"] if i["sku"] == "SKU-A")
        self.assertEqual(sku_a["marketplaces"], {"Mercado Livre": 2, "Shopee": 1})

    def test_inclui_pedido_ja_conferido(self):
        """A tela mostra o dia inteiro, não só o que falta — inclusive o concluído."""
        gravar_como_integracao([
            linha_datada("PED-1", "SKU-A", 2, "2026-09-17", uid="P1L1"),
        ])
        self.cliente.pedir("POST", "/api/order-complete", {"pedido": "PED-1"})
        self.assertEqual(pedidos(), [], "o pedido devia ter saído da fila")

        corpo = self.vendas(data_inicio="2026-09-17", data_fim="2026-09-17")
        self.assertEqual(corpo["resumo"]["unidades"], 2,
                         "REGRESSÃO: a venda sumiu do relatório ao ser conferida")
        self.assertEqual(corpo["resumo"]["pedidos_concluidos"], 1)
        self.assertEqual(corpo["resumo"]["pedidos_pendentes"], 0)

    def test_nao_conta_o_mesmo_pedido_duas_vezes(self):
        gravar_como_integracao([
            linha_datada("PED-1", "SKU-A", 2, "2026-09-17", uid="P1L1"),
            linha_datada("PED-2", "SKU-A", 2, "2026-09-17", uid="P2L1"),
        ])
        self.cliente.pedir("POST", "/api/order-complete", {"pedido": "PED-1"})
        corpo = self.vendas(data_inicio="2026-09-17", data_fim="2026-09-17")
        self.assertEqual(corpo["resumo"]["pedidos"], 2, "pedido contado em dobro")
        self.assertEqual(corpo["resumo"]["unidades"], 4)

    def test_pedido_excluido_pelo_admin_nao_aparece(self):
        gravar_como_integracao([
            linha_datada("PED-1", "SKU-A", 2, "2026-09-17", uid="P1L1"),
            linha_datada("PED-2", "SKU-B", 3, "2026-09-17", uid="P2L1"),
        ])
        self.excluir_admin("PED-1")
        corpo = self.vendas(data_inicio="2026-09-17", data_fim="2026-09-17")
        skus = [i["sku"] for i in corpo["itens"]]
        self.assertNotIn("SKU-A", skus,
                         "REGRESSÃO: pedido excluído pelo ADMIN apareceu no relatório")
        self.assertEqual(corpo["resumo"]["unidades"], 3)

    def test_soma_as_linhas_de_um_kit(self):
        gravar_como_integracao([
            linha_datada("PED-KIT", "COMP-1", 1, "2026-09-17", uid="PKL1"),
            linha_datada("PED-KIT", "COMP-2", 2, "2026-09-17", uid="PKL2"),
        ])
        corpo = self.vendas(data_inicio="2026-09-17", data_fim="2026-09-17")
        self.assertEqual(corpo["resumo"]["pedidos"], 1, "o KIT virou dois pedidos")
        self.assertEqual(corpo["resumo"]["unidades"], 3)
        self.assertEqual(corpo["resumo"]["skus"], 2, "os componentes do KIT precisam aparecer separados")


class VendasValidacaoEAcesso(TesteNoromak):

    def test_recusa_data_em_formato_invalido(self):
        status, corpo = self.cliente.pedir("GET", "/api/vendas?data_inicio=17/09/2026")
        self.assertEqual(status, 400)
        self.assertIn("AAAA-MM-DD", corpo.get("error", ""))

    def test_recusa_periodo_invertido(self):
        status, corpo = self.cliente.pedir(
            "GET", "/api/vendas?data_inicio=2026-09-18&data_fim=2026-09-17")
        self.assertEqual(status, 400)

    def test_exige_sessao(self):
        from base import Cliente
        anonimo = Cliente(self.url_base)
        status, _ = anonimo.pedir("GET", "/api/vendas")
        self.assertIn(status, (401, 403), "a rota respondeu sem sessão autenticada")

    def test_periodo_vazio_responde_sem_erro(self):
        corpo = self.cliente.pedir("GET", "/api/vendas?data_inicio=2020-01-01&data_fim=2020-01-02")[1]
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["itens"], [])
        self.assertEqual(corpo["resumo"]["unidades"], 0)

    def test_consulta_nao_altera_nada(self):
        """Somente leitura: a versão do storage não pode avançar."""
        gravar_como_integracao([linha_datada("PED-1", "SKU-A", 2, "2026-09-17", uid="P1L1")])
        _, versao_antes, _ = S.get_storage("conf_pedidos")
        self.cliente.pedir("GET", "/api/vendas?data_inicio=2026-09-17&data_fim=2026-09-17")
        _, versao_depois, _ = S.get_storage("conf_pedidos")
        self.assertEqual(versao_antes, versao_depois,
                         "a consulta de vendas gravou alguma coisa")


if __name__ == "__main__":
    unittest.main()
