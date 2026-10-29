"""V20.00 — Demanda de Compra pela PORTA do NoromakWeb (GET /api/v1/expedicao/demanda).

A porta foi ligada pelo Guilherme em 28/10 e devolve uma linha por (peça, filial, mês fechado),
com `sem_historico`, `com_erro` e `pendentes` (ainda na fila de leitura: perguntar de novo).
Decisão do dono (29/10): a filial é escolhida na tela (Araçatuba padrão, Ribeirão, Bauru, Todas).
A porta é simulada: nenhum teste chama o NoromakWeb de verdade. O que importa prender:
dado que não veio NUNCA vira zero por cima da venda do e-commerce.
"""

import json
import unittest
from datetime import datetime

from base import S, TesteNoromak


def meses_fechados(n):
    hoje = datetime.now(S.BR_TZ).date().replace(day=1)
    ano, mes, out = hoje.year, hoje.month, []
    for _ in range(n):
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
        out.append(f"{ano:04d}-{mes:02d}")
    return out


class Base(TesteNoromak):

    def setUp(self):
        super().setUp()
        self._orig = (S._exp_porta_get, S.CHAVE_CRM, S.linx_chamar, S.LINX_API_USUARIO, S.LINX_API_CHAVE, S.LINX_API_SENHA)
        S.CHAVE_CRM = "chave-teste"
        S.LINX_API_USUARIO = ""            # Smart API desligada (como em produção)
        self.chamadas = []
        self.linhas = {}                   # codigo -> [linhas da porta]
        self.sem = set()
        self.erros = {}
        self.pendente_vezes = {}           # codigo -> quantas perguntas fica em pendentes
        self.falha = None

        def porta(rota, params):
            self.chamadas.append((rota, dict(params)))
            if self.falha:
                raise self.falha
            self.assertEqual(rota, "demanda")
            pedidos = params["codigos"].split(",")
            r = {"ok": True, "itens": [], "sem_historico": [], "com_erro": [], "pendentes": []}
            for c in pedidos:
                if self.pendente_vezes.get(c, 0) > 0:
                    self.pendente_vezes[c] -= 1
                    r["pendentes"].append(c)
                elif c in self.erros:
                    r["com_erro"].append({"codigo": c, "erro": self.erros[c]})
                elif c in self.linhas:
                    r["itens"].extend(self.linhas[c])
                else:
                    r["sem_historico"].append(c)
            return r
        S._exp_porta_get = porta
        S.save_storage("precificador_online_v1", None, json.dumps({"products": [
            {"sku": "0893600022", "produto": "Arruela Würth", "custo": 2.5},
            {"sku": "0893764272", "produto": "Porca Würth", "custo": 1.0},
            {"sku": "1234", "produto": "Outra marca", "custo": 9.0}]}), False, actor="t", actor_role="admin")
        S.compras_salvar_fornecedor("WURTH", {"prefixos": ["0893"], "pedido_minimo": 0, "prazo": 7, "cobertura": 30}, "t")

    def tearDown(self):
        S._exp_porta_get, S.CHAVE_CRM, S.linx_chamar, S.LINX_API_USUARIO, S.LINX_API_CHAVE, S.LINX_API_SENHA = self._orig
        super().tearDown()

    def hist(self, codigo, por_filial):
        """por_filial = {revenda: [qtd do mês fechado mais recente, do anterior, ...]}"""
        linhas = []
        for revenda, qtds in por_filial.items():
            for mes, q in zip(meses_fechados(len(qtds)), qtds):
                linhas.append({"codigo": codigo, "revenda": revenda, "mes": mes, "DemandaBalcao": q,
                               "DemandaOficina": 0, "DemandaTelemark": 0, "DemandaTotal": q, "OcorrenciaVendas": 1})
        self.linhas[codigo] = linhas

    def item(self, codigo, dias=60, filial="1"):
        return {x["codigo"]: x for x in S.compras_demanda_payload("WURTH", dias, filial)["itens"]}[codigo]


class Rodada(Base):

    def test_sem_chave_nao_chama_a_porta(self):
        S.CHAVE_CRM = ""
        r = S.linx_demanda_rodada(pausa=0)
        self.assertIn("pulado", r)
        self.assertEqual(self.chamadas, [])

    def test_uma_chamada_em_lote_so_com_os_codigos_dos_fornecedores(self):
        S.linx_demanda_rodada(pausa=0)
        self.assertEqual(len(self.chamadas), 1)
        rota, params = self.chamadas[0]
        self.assertEqual(set(params["codigos"].split(",")), {"0893600022", "0893764272"})
        self.assertEqual(int(params["meses"]), 12)

    def test_pendentes_sao_perguntados_de_novo(self):
        self.hist("0893600022", {1: [5, 5]})
        self.pendente_vezes["0893600022"] = 1
        r = S.linx_demanda_rodada(pausa=0)
        self.assertEqual(len(self.chamadas), 2)
        self.assertEqual(self.chamadas[1][1]["codigos"], "0893600022", "só o pendente volta a ser pedido")
        self.assertEqual(r["com_erro"], 0)
        self.assertEqual(self.item("0893600022")["linx_total"], 10)

    def test_pendente_que_nao_sai_fica_para_a_proxima_rodada(self):
        self.hist("0893600022", {1: [5]})
        self.pendente_vezes["0893600022"] = 99
        r = S.linx_demanda_rodada(pausa=0)
        self.assertEqual(r["pendentes"], 1)
        self.assertIsNone(self.item("0893600022")["linx_total"])
        self.pendente_vezes["0893600022"] = 0
        S.linx_demanda_rodada(pausa=0)      # não foi "feito" hoje: pergunta de novo
        self.assertEqual(self.item("0893600022", dias=30)["linx_total"], 5)

    def test_nao_repete_no_mesmo_dia_mas_o_botao_forca(self):
        S.linx_demanda_rodada(pausa=0)
        n = len(self.chamadas)
        S.linx_demanda_rodada(pausa=0)
        self.assertEqual(len(self.chamadas), n)
        S.linx_demanda_rodada(fornecedor="WURTH", forcar=True, pausa=0)
        self.assertGreater(len(self.chamadas), n)

    def test_porta_fora_do_ar_nao_grava_nada_e_mostra_o_erro(self):
        self.falha = S.PortaErro("A porta do Apollo para esta etapa ainda não está liberada (404).")
        r = S.linx_demanda_rodada(pausa=0)
        self.assertFalse(r["ok"])
        d = S.compras_demanda_payload("WURTH", 60, "1")
        x = {i["codigo"]: i for i in d["itens"]}["0893600022"]
        self.assertIsNone(x["linx_total"])
        self.assertIn("404", d["linx"]["erro_exemplo"])

    def test_guarda_a_primeira_resposta_real(self):
        self.hist("0893600022", {1: [1]})
        S.linx_demanda_rodada(pausa=0)
        with S.DB_LOCK:
            tipos = {r["tipo"] for r in S.DB.execute("SELECT tipo FROM linx_demanda_amostra_v1891").fetchall()}
        self.assertIn("porta_demanda", tipos)

    def test_amostra_nao_e_a_resposta_so_com_pendentes(self):
        # revisão 29/10: a 1ª resposta real vem toda em pendentes; guardar ela não ajudava a conferir
        self.hist("0893600022", {1: [1]})
        self.pendente_vezes["0893600022"] = 1
        self.pendente_vezes["0893764272"] = 1
        S.linx_demanda_rodada(pausa=0)
        with S.DB_LOCK:
            js = S.DB.execute("SELECT json FROM linx_demanda_amostra_v1891 WHERE tipo='porta_demanda'").fetchone()[0]
        self.assertTrue(json.loads(js)["itens"])


class PorFilial(Base):

    def setUp(self):
        super().setUp()
        self.hist("0893600022", {1: [20, 40, 999], 2: [3, 1], 3: [0, 7]})
        S.linx_demanda_rodada(pausa=0)

    def test_aracatuba_e_o_padrao(self):
        x = self.item("0893600022", dias=60)
        self.assertEqual(x["linx_total"], 60)      # o 3º mês fica fora de 60 dias
        # sem e-commerce no mês, a média é a dos meses fechados (o mês atual nunca a derruba)
        self.assertAlmostEqual(x["linx_media_dia"], 60 / (2 * 30.4375), places=3)
        self.assertEqual(x["linx_canais"]["balcao"], 60)
        self.assertEqual(x["linx_mes_atual_ecom"], 0)

    def test_cada_filial_e_todas(self):
        self.assertEqual(self.item("0893600022", filial="2")["linx_total"], 4)
        self.assertEqual(self.item("0893600022", filial="3")["linx_total"], 7)
        self.assertEqual(self.item("0893600022", filial="todas")["linx_total"], 71)

    def test_filial_sem_linha_de_uma_peca_lida_e_zero(self):
        # a Linx não manda todos os meses de todas as casas: a peça foi lida, a filial vendeu 0
        self.hist("0893764272", {1: [4]})
        S.linx_demanda_rodada(forcar=True, fornecedor="WURTH", pausa=0)
        self.assertEqual(self.item("0893764272", filial="3")["linx_total"], 0)

    def test_resumo_diz_a_filial(self):
        d = S.compras_demanda_payload("WURTH", 60, "3")
        self.assertEqual(d["linx"]["filial"], "3")
        self.assertTrue(d["linx"]["configurada"])

    def test_filial_invalida_e_recusada(self):
        with self.assertRaises(ValueError):
            S.compras_demanda_payload("WURTH", 60, "9")


class MesAtual(Base):
    """Decisão do dono (29/10): a Linx só tem meses fechados; o mês corrente entra pelo e-commerce
    (ML + Shopee até hoje), somado no total e nos dias da média."""

    def venda(self, sku, qtd, dia, conta=None, mp="ml", pedido=None):
        pedido = pedido or f"{sku}-{dia}-{qtd}-{conta}"
        with S.DB_LOCK:
            if conta:
                S.DB.execute("INSERT INTO vendas_conta_v1898(conta,marketplace,order_id,line_key,sale_at,sale_day,sku,qty,updated_at)"
                             " VALUES(?,?,?,?,?,?,?,?,?)", (conta, mp, pedido, "1", dia, dia, sku, qtd, dia))
            else:
                S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,sku,qty,updated_at)"
                             " VALUES(?,?,?,?,?,?,?,?)", (mp, pedido, "1", dia, dia, sku, qtd, dia))
            S.DB.commit()

    def setUp(self):
        super().setUp()
        self.hoje = datetime.now(S.BR_TZ).date()
        self.hist("0893600022", {1: [10, 10], 3: [2, 2]})
        S.linx_demanda_rodada(pausa=0)

    def test_ecommerce_do_mes_entra_no_total_e_nos_dias(self):
        self.venda("0893600022", 4, self.hoje.isoformat(), mp="ml")
        self.venda("0893600022", 1, self.hoje.replace(day=1).isoformat(), mp="shopee")
        # venda de um mês fechado já está dentro da Linx: não soma de novo
        self.venda("0893600022", 50, meses_fechados(1)[0] + "-15")
        x = self.item("0893600022", dias=60)
        self.assertEqual(x["linx_mes_atual_ecom"], 5)
        self.assertEqual(x["linx_total"], 25)
        self.assertAlmostEqual(x["linx_media_dia"], max(20 / (2 * 30.4375), 25 / (2 * 30.4375 + self.hoje.day)), places=3)

    def test_media_nunca_fica_abaixo_dos_meses_fechados(self):
        # revisão 29/10: o mês atual só tem o e-commerce; somar os dias sem o balcão derrubava
        # pela metade a peça de balcão (88/mês → ~1,5/dia em vez de ~2,9)
        x = self.item("0893600022", dias=30)
        self.assertAlmostEqual(x["linx_media_dia"], 10 / 30.4375, places=3)
        self.assertEqual(self.item("0893600022", dias=30, filial="2")["linx_media_dia"], 0)

    def test_mes_que_acelerou_sobe_a_media(self):
        self.venda("0893600022", 200, self.hoje.isoformat())
        x = self.item("0893600022", dias=30)
        self.assertAlmostEqual(x["linx_media_dia"], 210 / (30.4375 + self.hoje.day), places=3)
        self.assertGreater(x["linx_media_dia"], 10 / 30.4375)

    def test_virada_do_mes_usa_os_meses_ja_lidos(self):
        # dia 1º: a tela já pede o mês que acabou de fechar, mas a leitura é da noite anterior
        # (ainda sem ele). Antes: todas as peças com demanda ~0 até a rodada da noite.
        anterior = meses_fechados(1)[0]                      # mês que "acabou de fechar"
        lidos = meses_fechados(3)[1:]                        # o que a leitura antiga tinha
        self.linhas["0893600022"] = [{"codigo": "0893600022", "revenda": 1, "mes": m, "DemandaBalcao": 30,
                                      "DemandaOficina": 0, "DemandaTelemark": 0, "DemandaTotal": 30,
                                      "OcorrenciaVendas": 1} for m in lidos]
        S.linx_demanda_rodada(forcar=True, fornecedor="WURTH", pausa=0)
        lido_em = anterior + "-28"
        with S.DB_LOCK:
            S.DB.execute("UPDATE linx_demanda_item_v1891 SET lido_dia=?, buscado_dia=? WHERE codigo='0893600022'",
                         (lido_em, lido_em))
            S.DB.commit()
        self.venda("0893600022", 6, anterior + "-10")       # e-commerce do mês que fechou
        self.venda("0893600022", 2, self.hoje.isoformat())  # e-commerce de hoje
        x = self.item("0893600022", dias=30)
        self.assertEqual(x["linx_total"], 30 + 8)
        corridos = (self.hoje - datetime.strptime(anterior + "-01", "%Y-%m-%d").date()).days + 1
        self.assertAlmostEqual(x["linx_media_dia"], max(30 / 30.4375, 38 / (30.4375 + corridos)), places=3)

    def test_reserva_ecommerce_e_da_conta_da_filial(self):
        # peça sem histórico na Linx: a tela usa a venda do e-commerce, que tem de ser a da filial
        self.venda("0893764272", 7, self.hoje.isoformat())                 # Araçatuba
        self.venda("0893764272", 3, self.hoje.isoformat(), conta="bauru")
        self.assertIsNone(self.item("0893764272", filial="3")["linx_total"])
        self.assertEqual(self.item("0893764272", filial="1")["vendido"], 7)
        self.assertEqual(self.item("0893764272", filial="3")["vendido"], 3)
        self.assertEqual(self.item("0893764272", filial="2")["vendido"], 0)
        self.assertEqual(self.item("0893764272", filial="todas")["vendido"], 10)

    def test_bauru_usa_a_venda_da_conta_de_bauru(self):
        self.venda("0893600022", 3, self.hoje.isoformat(), conta="bauru")
        self.venda("0893600022", 7, self.hoje.isoformat())            # Araçatuba
        self.assertEqual(self.item("0893600022", filial="3")["linx_total"], 4 + 3)
        self.assertEqual(self.item("0893600022", filial="1")["linx_total"], 20 + 7)
        self.assertEqual(self.item("0893600022", filial="2")["linx_total"], 0)
        self.assertEqual(self.item("0893600022", filial="todas")["linx_total"], 24 + 10)

    def test_kit_do_mes_vira_as_pecas(self):
        self.venda("0893600022/0893764272", 2, self.hoje.isoformat())
        self.assertEqual(self.item("0893600022")["linx_mes_atual_ecom"], 2)

    def test_resumo_diz_o_mes_atual(self):
        d = S.compras_demanda_payload("WURTH", 60, "1")
        self.assertEqual(d["linx"]["mes_atual"], self.hoje.strftime("%Y-%m"))
        self.assertEqual(d["linx"]["mes_atual_dias"], self.hoje.day)


class NuncaZero(Base):

    def test_sem_historico_segue_com_o_ecommerce(self):
        S.linx_demanda_rodada(pausa=0)
        x = self.item("0893764272")
        self.assertIsNone(x["linx_total"])
        self.assertIsNone(x["linx_media_dia"])

    def test_com_erro_fica_em_branco_e_visivel(self):
        self.erros["0893600022"] = "não encontrada no Apollo"
        S.linx_demanda_rodada(pausa=0)
        d = S.compras_demanda_payload("WURTH", 60, "1")
        x = {i["codigo"]: i for i in d["itens"]}["0893600022"]
        self.assertIsNone(x["linx_total"])
        self.assertEqual(d["linx"]["itens_com_erro"], 1)
        self.assertIn("não encontrada", d["linx"]["erro_exemplo"])

    def test_erro_depois_de_uma_leitura_boa_mantem_o_que_ja_veio(self):
        self.hist("0893600022", {1: [6]})
        S.linx_demanda_rodada(pausa=0)
        self.erros["0893600022"] = "Apollo não respondeu"
        S.linx_demanda_rodada(forcar=True, fornecedor="WURTH", pausa=0)
        self.assertEqual(self.item("0893600022", dias=30)["linx_total"], 6)

    def test_sem_smart_api_a_caminho_fica_digitado_nunca_zero(self):
        self.hist("0893600022", {1: [6]})
        S.linx_demanda_rodada(pausa=0)
        self.assertIsNone(self.item("0893600022")["linx_caminho"])

    def test_com_smart_api_a_compra_em_aberto_continua_vindo_do_linx(self):
        S.LINX_API_CHAVE, S.LINX_API_USUARIO, S.LINX_API_SENHA = "chave", "usuario", "senha"

        def falsa(metodo, caminho, corpo=None):
            self.assertTrue(caminho.endswith("/DetalhesPedidos"), caminho)
            if corpo["CodigoItem"] == "0893600022":
                return [{"CodigoPedido": 1, "QuantidadePedida": 12, "QuantidadeBaixada": 2,
                         "DataProvavelRecebimento": "2026-11-30"}]
            return []
        S.linx_chamar = falsa
        self.hist("0893600022", {1: [6]})
        S.linx_demanda_rodada(pausa=0)
        x = self.item("0893600022")
        self.assertEqual((x["linx_caminho"], x["linx_previsao"]), (10.0, "2026-11-30"))
        self.assertEqual(self.item("0893764272")["linx_caminho"], 0.0)


class Rotas(Base):

    def test_rotas(self):
        status, corpo = self.cliente.pedir("POST", "/api/compras/linx/atualizar", {"fornecedor": "NAO-EXISTE"})
        self.assertEqual(status, 400, corpo)
        status, corpo = self.cliente.pedir("GET", "/api/compras/demanda?fornecedor=WURTH&dias=60&filial=3")
        self.assertEqual(status, 200, corpo)
        self.assertEqual(corpo["linx"]["filial"], "3")
        status, corpo = self.cliente.pedir("GET", "/api/compras/demanda?fornecedor=WURTH&dias=60&filial=x")
        self.assertEqual(status, 400, corpo)
        status, corpo = self.cliente.pedir("GET", "/api/compras/linx/amostra")
        self.assertEqual(status, 200, corpo)

    def test_tela_tem_o_seletor_de_filial(self):
        with open(S.APP_DIR / "compras.html", encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="filialLinx"', html)
        self.assertIn("&filial=", html)

    def test_worker_esta_no_painel_de_saude(self):
        self.assertIn("linx_demanda_loop", S.SAUDE_WORKERS_INFO)


if __name__ == "__main__":
    unittest.main()
