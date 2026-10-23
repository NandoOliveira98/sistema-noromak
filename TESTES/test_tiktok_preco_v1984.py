"""V19.84 — Preço ideal do TikTok (Bauru) pela extensão.

Decisões do dono (23/10): afiliado fixo 5% em toda venda; loja inteira com frete grátis (programa 6%,
teto R$ 50 por produto); tarifa do TikTok BR (15/07/2026): abaixo de R$ 50 = 10% + R$ 4,00; R$ 50 ou
mais = 6% + R$ 6,00; imposto 12% (o do Financeiro); custo = Apollo de BAURU (revenda 3), a loja é de
Bauru. Margem = lucro ÷ preço. Conferido à mão com o dono: 2011004 (custo 30,80) → 65,71 (15%) e 72,16 (20%).
"""

from unittest import mock

from base import S, TesteNoromak


def _apollo(custo_bauru, custo_aracatuba=99.0):
    return {"ok": True, "codigo": "X", "custos": [
        {"revenda": 1, "custo_medio": custo_aracatuba, "tem_cadastro": True},
        {"revenda": 3, "custo_medio": custo_bauru, "tem_cadastro": True}]}


class Conta(TesteNoromak):

    def test_exemplos_conferidos_com_o_dono(self):
        cfg = S.tiktok_preco_config()
        self.assertAlmostEqual(S.tiktok_preco_ideal(30.80, 15, cfg)["preco"], 65.71, places=2)
        self.assertAlmostEqual(S.tiktok_preco_ideal(30.80, 20, cfg)["preco"], 72.16, places=2)
        self.assertAlmostEqual(S.tiktok_preco_ideal(6.17, 15, cfg)["preco"], 19.56, places=2)   # faixa de baixo
        self.assertAlmostEqual(S.tiktok_preco_ideal(20.96, 15, cfg)["preco"], 48.00, places=2)
        self.assertAlmostEqual(S.tiktok_preco_ideal(20.96, 20, cfg)["preco"], 52.86, places=2)  # pula de faixa

    def test_margem_do_preco_atual(self):
        cfg = S.tiktok_preco_config()
        r = S.tiktok_preco_detalhe(78.90, 30.80, cfg)
        self.assertAlmostEqual(r["margem"], 24.4, places=1)
        self.assertAlmostEqual(r["afiliado"], 3.94, places=2)

    def test_frete_tem_teto_de_50(self):
        cfg = S.tiktok_preco_config()
        r = S.tiktok_preco_detalhe(2000.0, 1000.0, cfg)
        self.assertEqual(r["frete"], 50.0)
        p = S.tiktok_preco_ideal(1000.0, 15, cfg)["preco"]
        self.assertAlmostEqual(S.tiktok_preco_detalhe(p, 1000.0, cfg)["margem"], 15.0, places=1)

    def test_preco_ideal_sempre_da_a_margem_pedida(self):
        cfg = S.tiktok_preco_config()
        for custo in (1.0, 5.0, 20.0, 31.0, 33.0, 80.0, 400.0):
            for m in (5, 15, 20, 35):
                p = S.tiktok_preco_ideal(custo, m, cfg)["preco"]
                self.assertAlmostEqual(S.tiktok_preco_detalhe(p, custo, cfg)["margem"], m, delta=0.05, msg=(custo, m))


class Custo(TesteNoromak):

    def test_custo_de_bauru_lido_agora(self):
        with mock.patch.object(S, "crm_cost_lookup", return_value=_apollo(30.80)):
            r = S.tiktok_preco_sku("2011004", [15, 20])
        self.assertEqual(r["custo"], 30.80)
        self.assertEqual([x["preco"] for x in r["precos"]], [65.71, 72.16])

    def test_kit_soma_as_pecas(self):
        custos = {"2011004": 30.80, "2050429": 10.0}
        with mock.patch.object(S, "crm_cost_lookup", side_effect=lambda c, **k: _apollo(custos[c])):
            r = S.tiktok_preco_sku("KIT-2011004/2050429", [15])
        self.assertAlmostEqual(r["custo"], 40.80)
        with mock.patch.object(S, "crm_cost_lookup", side_effect=lambda c, **k: _apollo(custos[c])):
            self.assertAlmostEqual(S.tiktok_preco_sku("KIT2-2050429", [15])["custo"], 20.0)

    def test_sem_custo_de_bauru_nao_usa_outra_filial(self):
        with mock.patch.object(S, "crm_cost_lookup", return_value=_apollo(0, custo_aracatuba=25.0)):
            r = S.tiktok_preco_sku("2011004", [15])
        self.assertIsNone(r["custo"])
        self.assertEqual(r["precos"], [])
        self.assertIn("2011004", r["faltando"])

    def test_apollo_fora_usa_a_ultima_leitura_de_bauru(self):
        S.custo_apollo_registrar("2011004", [{"revenda": 3, "custo_medio": 30.80}])
        with mock.patch.object(S, "crm_cost_lookup", side_effect=RuntimeError("Apollo indisponível")):
            r = S.tiktok_preco_sku("2011004", [15])
        self.assertEqual(r["custo"], 30.80)
        self.assertIn("última leitura", r["pecas"][0]["origem"])

    def test_custo_digitado_vence(self):
        with mock.patch.object(S, "crm_cost_lookup") as apollo:
            r = S.tiktok_preco_sku("2011004", [15], custo_digitado=30.80)
        apollo.assert_not_called()
        self.assertEqual(r["precos"][0]["preco"], 65.71)


class Rotas(TesteNoromak):

    def test_rota(self):
        with mock.patch.object(S, "crm_cost_lookup", return_value=_apollo(30.80)):
            st, corpo = self.cliente.pedir("GET", "/api/tiktok-estoque/preco?sku=2011004&margens=15,20&preco=78.90")
        self.assertEqual(st, 200, corpo)
        self.assertEqual([x["preco"] for x in corpo["precos"]], [65.71, 72.16])
        self.assertAlmostEqual(corpo["atual"]["margem"], 24.4, places=1)

    def test_sku_vazio_e_400(self):
        st, _ = self.cliente.pedir("GET", "/api/tiktok-estoque/preco?sku=")
        self.assertEqual(st, 400)


if __name__ == "__main__":
    import unittest
    unittest.main()
