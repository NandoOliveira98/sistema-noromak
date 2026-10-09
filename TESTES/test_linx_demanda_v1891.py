"""V18.91 — Demanda de Compra pela Linx Smart API (demanda de todos os canais e compra em aberto).

A Linx é simulada: nenhum teste aqui chama a API de verdade. O formato real nunca foi visto
(produção sem credenciais em 09/10): os testes prendem a leitura tolerante e, principalmente,
que dado que não veio NUNCA vira zero por cima da venda do e-commerce.
"""

import json
import unittest
from datetime import date, datetime

from base import S, TesteNoromak


def meses_fechados(n):
    hoje = datetime.now(S.BR_TZ).date().replace(day=1)
    ano, mes, out = hoje.year, hoje.month, []
    for _ in range(n):
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
        out.append((ano, mes))
    return out


class LeituraTolerante(unittest.TestCase):

    def test_mes_em_varios_formatos_e_total_pela_soma_dos_canais(self):
        resp = {"Dados": [
            {"Ano": 2026, "Mes": 9, "DemandaTotal": 10, "DemandaBalcao": 6, "DemandaOficina": 3, "DemandaTelemark": 1},
            {"MesAno": "08/2026", "DemandaBalcao": 3, "DemandaOficina": 2},
            {"periodo": "2026-07", "demandatotal": "4,5"},
            {"Competencia": "202606", "DemandaTotal": 7},
            {"Qualquer": "coisa"},
        ]}
        meses, ilegiveis = S._linx_demanda_meses(resp)
        self.assertEqual(meses["2026-09"]["total"], 10)
        self.assertEqual(meses["2026-08"]["total"], 5)
        self.assertEqual(meses["2026-07"]["total"], 4.5)
        self.assertEqual(meses["2026-06"]["total"], 7)
        self.assertEqual(ilegiveis, 1)

    def test_compra_em_aberto_e_o_que_falta_baixar(self):
        abertas = S._linx_compras_abertas([
            {"CodigoPedido": 77, "QuantidadePedida": 10, "QuantidadeBaixada": 4, "DataProvavelRecebimento": "2026-10-20T00:00:00"},
            {"CodigoPedido": 78, "QuantidadePedida": 5}])
        self.assertEqual([(a["pedido"], a["pedida"], a["baixada"], a["previsao"]) for a in abertas],
                         [("77", 10.0, 4.0, "2026-10-20"), ("78", 5.0, 0.0, "")])


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._orig = (S.linx_chamar, S.LINX_API_CHAVE, S.LINX_API_USUARIO, S.LINX_API_SENHA)
        S.LINX_API_CHAVE, S.LINX_API_USUARIO, S.LINX_API_SENHA = "chave", "usuario", "senha"
        self.chamadas = []
        self.historico = {}
        self.pedidos = {}
        self.erro = None

        def falsa(metodo, caminho, corpo=None):
            self.chamadas.append((metodo, caminho, corpo))
            if self.erro:
                raise self.erro
            if "/OrdemCompra/Historico/" in caminho:
                codigo = caminho.rsplit("/", 1)[1]
                if codigo not in self.historico:
                    raise S.LinxErro("Linx respondeu HTTP 412.", 412)
                return self.historico[codigo]
            if caminho.endswith("/DetalhesPedidos"):
                return self.pedidos.get(corpo["CodigoItem"], [])
            raise AssertionError(caminho)
        S.linx_chamar = falsa
        S.save_storage("precificador_online_v1", None, json.dumps({"products": [
            {"sku": "0893600022", "produto": "Arruela Würth", "custo": 2.5},
            {"sku": "0893764272", "produto": "Porca Würth", "custo": 1.0},
            {"sku": "1234", "produto": "Outra marca", "custo": 9.0}]}), False, actor="t", actor_role="admin")
        S.compras_salvar_fornecedor("WURTH", {"prefixos": ["0893"], "pedido_minimo": 0, "prazo": 7, "cobertura": 30}, "t")

    def tearDown(self):
        S.linx_chamar, S.LINX_API_CHAVE, S.LINX_API_USUARIO, S.LINX_API_SENHA = self._orig
        super().tearDown()

    def hist(self, codigo, por_mes):
        self.historico[codigo] = [{"Ano": a, "Mes": m, "DemandaTotal": q, "DemandaBalcao": q}
                                  for (a, m), q in zip(meses_fechados(len(por_mes)), por_mes)]

    def item(self, codigo, dias=60):
        return {x["codigo"]: x for x in S.compras_demanda_payload("WURTH", dias)["itens"]}[codigo]


class Rodada(Base):

    def test_sem_credencial_nao_chama_a_linx(self):
        S.LINX_API_USUARIO = ""
        r = S.linx_demanda_rodada(pausa=0)
        self.assertIn("pulado", r)
        self.assertEqual(self.chamadas, [])

    def test_busca_so_os_codigos_dos_fornecedores(self):
        S.linx_demanda_rodada(pausa=0)
        codigos = {c[1].rsplit("/", 1)[1] for c in self.chamadas if "Historico" in c[1]}
        self.assertEqual(codigos, {"0893600022", "0893764272"})
        corpo = [c[2] for c in self.chamadas if c[1].endswith("DetalhesPedidos")][0]
        self.assertTrue(corpo["ApenasPendentes"] and corpo["MostrarTodasFiliais"])

    def test_nao_repete_no_mesmo_dia_mas_o_botao_forca(self):
        S.linx_demanda_rodada(pausa=0)
        n = len(self.chamadas)
        S.linx_demanda_rodada(pausa=0)
        self.assertEqual(len(self.chamadas), n)
        S.linx_demanda_rodada(fornecedor="WURTH", forcar=True, pausa=0)
        self.assertGreater(len(self.chamadas), n)

    def test_guarda_a_primeira_resposta_real_de_cada_tipo(self):
        self.hist("0893600022", [5, 5])
        S.linx_demanda_rodada(pausa=0)
        with S.DB_LOCK:
            tipos = {r["tipo"] for r in S.DB.execute("SELECT tipo FROM linx_demanda_amostra_v1891").fetchall()}
        self.assertEqual(tipos, {"historico", "pedidos"})


class NaTela(Base):

    def test_demanda_dos_ultimos_meses_fechados_e_media_por_dia(self):
        self.hist("0893600022", [20, 40, 999])   # o 3º mês fica fora de um período de 60 dias
        S.linx_demanda_rodada(pausa=0)
        x = self.item("0893600022", dias=60)
        self.assertEqual(x["linx_total"], 60)
        self.assertAlmostEqual(x["linx_media_dia"], 60 / (2 * 30.4375), places=3)
        self.assertEqual(x["linx_canais"]["balcao"], 60)

    def test_compra_em_aberto_vira_a_caminho(self):
        self.hist("0893600022", [1])
        self.pedidos["0893600022"] = [{"CodigoPedido": 1, "QuantidadePedida": 12, "QuantidadeBaixada": 2,
                                       "DataProvavelRecebimento": "2026-10-30"}]
        S.linx_demanda_rodada(pausa=0)
        x = self.item("0893600022")
        self.assertEqual((x["linx_caminho"], x["linx_previsao"]), (10.0, "2026-10-30"))

    def test_codigo_que_a_linx_nao_acha_nao_vira_demanda_zero(self):
        # 412 no histórico: a tela continua com a venda do e-commerce
        S.linx_demanda_rodada(pausa=0)
        x = self.item("0893764272")
        self.assertIsNone(x["linx_total"])
        self.assertIsNone(x["linx_media_dia"])
        self.assertEqual(x["linx_caminho"], 0.0, "a consulta de pedidos funcionou: nada a caminho")

    def test_erro_da_linx_deixa_tudo_em_branco_e_visivel(self):
        self.erro = S.LinxErro("Linx respondeu HTTP 500.", 500)
        S.linx_demanda_rodada(pausa=0)
        d = S.compras_demanda_payload("WURTH", 60)
        x = {i["codigo"]: i for i in d["itens"]}["0893600022"]
        self.assertIsNone(x["linx_total"])
        self.assertIsNone(x["linx_caminho"])
        self.assertGreater(d["linx"]["itens_com_erro"], 0)
        self.assertIn("500", d["linx"]["erro_exemplo"])

    def test_formato_estranho_registra_e_segue(self):
        self.historico["0893600022"] = {"Lista": [{"campoNovo": 1}]}
        self.hist("0893764272", [3])
        r = S.linx_demanda_rodada(pausa=0)
        self.assertEqual(r["buscados"], 2)
        with S.DB_LOCK:
            erro = S.DB.execute("SELECT erro FROM linx_demanda_item_v1891 WHERE codigo='0893600022'").fetchone()[0]
        self.assertIn("formato não reconhecido", erro)
        self.assertEqual(self.item("0893764272")["linx_total"], 3)

    def test_rotas(self):
        status, corpo = self.cliente.pedir("POST", "/api/compras/linx/atualizar", {"fornecedor": "NAO-EXISTE"})
        self.assertEqual(status, 400, corpo)
        status, corpo = self.cliente.pedir("GET", "/api/compras/demanda?fornecedor=WURTH&dias=60")
        self.assertEqual(status, 200, corpo)
        self.assertTrue(corpo["linx"]["configurada"])
        status, corpo = self.cliente.pedir("GET", "/api/compras/linx/amostra")
        self.assertEqual(status, 200, corpo)

    def test_worker_novo_esta_no_painel_de_saude(self):
        self.assertIn("linx_demanda_loop", S.SAUDE_WORKERS_INFO)


if __name__ == "__main__":
    unittest.main()
