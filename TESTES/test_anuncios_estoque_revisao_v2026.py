"""V20.26 (revisao final) - estoque dos anuncios do ML: chave do atendimento no Apollo, mesma abertura de SKU no
pendente, pedido concluido nao sobe antes de o Apollo reler, leitura da fila por versao, anuncio parado/com variacoes,
erros seguidos, "Enviar agora" em segundo plano, Automatico volta a Manual, trava das fotos."""

import base64
import json
import struct
import time
import zlib
from unittest import mock

from base import S, linha
from test_anuncios_estoque_v2026 import EstoqueBase, inserir, canal, fila, kits, SENHA, Cliente


def ler(teste, codigo, ara=None, bau=None, filial="aracatuba"):
    teste.apollo[codigo] = {1: ara, 3: bau}
    S.anuncios_apollo_ler(codigo, filial)


def item(mlb="MLB1", conta="aracatuba"):
    return next(i for i in S.anuncios_estoque_calcular(conta) if i["anuncio_id"] == mlb)


def faturamento(pedido, atendimento="790955", situacao="pronto"):
    with S.DB_LOCK:
        S.DB.execute("INSERT OR REPLACE INTO faturamento_v1943(order_key,pedido,marketplace,situacao,atendimento) "
                     "VALUES(?,?,?,?,?)", (S.normalize(pedido), pedido, "Mercado Livre", situacao, atendimento))
        S.DB.commit()


class ChaveAtendimentoTeste(EstoqueBase):

    def preparar(self):
        inserir(ficha="P1", modo="automatico", estoque=2)
        ler(self, "P1", ara=10)
        fila([linha("PED1", "P1", 3, Marketplace="MERCADO LIVRE"), linha("PED2", "P1", 2, Marketplace="MERCADO LIVRE"),
              linha("PED3", "P1", 1, Marketplace="MERCADO LIVRE")])
        faturamento("PED1")                                   # atendimento achado no Apollo
        faturamento("PED2", atendimento="", situacao="aguardando_cadastro")  # ainda sem atendimento

    def test_padrao_desliga_desconta_tudo(self):
        self.preparar()
        cfg = S.anuncio_estoque_config("aracatuba")
        self.assertFalse(cfg["apollo_desconta_atendimento"])
        it = item()
        self.assertEqual(it["calculado"], 4)                     # 10 - 3 - 2 - 1
        self.assertEqual(it["calculado_tudo"], 4)
        self.assertEqual(it["calculado_sem_atendimento"], 7)     # so PED2 e PED3 (PED3 nao esta no Faturamento)
        p = it["pecas"][0]
        self.assertEqual((p["a_baixar"], p["a_baixar_tudo"], p["a_baixar_sem_atendimento"]), (6, 6, 3))

    def test_ligada_desconta_so_sem_atendimento(self):
        self.preparar()
        S.anuncio_estoque_desconta_definir("aracatuba", True, "luis")
        self.assertTrue(S.anuncio_estoque_config("aracatuba")["apollo_desconta_atendimento"])
        self.assertFalse(S.anuncio_estoque_config("bauru")["apollo_desconta_atendimento"])  # por conta
        it = item()
        self.assertEqual((it["calculado"], it["pecas"][0]["a_baixar"]), (7, 3))
        faturamento("PED3", situacao="feito", atendimento="")    # ja foi ao caixa: tambem nao desconta
        it = item()  # o pendente sem atendimento caiu: segura ate o Apollo reler a peca
        self.assertEqual((it["calculado"], it["calculado_sem_atendimento"]), (2, 2))
        self.assertTrue(it["segurado"])
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_apollo_v2026 SET lido_epoch=?", (int(time.time()) + 5,))
            S.DB.commit()
        self.assertEqual(item()["calculado"], 8)
        S.anuncio_estoque_desconta_definir("aracatuba", False, "luis")
        self.assertEqual(item()["calculado"], 4)
        log = S.anuncio_estoque_log("ml", "aracatuba")
        self.assertIn("só sem atendimento", log[1]["motivo"])
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM audit_log WHERE action='anuncios.estoque_desconta_atendimento'").fetchone()[0]
        self.assertEqual(n, 2)

    def test_simulacao_registra_os_dois_numeros(self):
        self.preparar()
        S.anuncios_estoque_rodada("aracatuba", ator="sistema")
        log = S.anuncio_estoque_log("ml", "aracatuba", "MLB1")[0]
        self.assertEqual(log["resultado"], "simulado")
        self.assertIn("descontando tudo: 4", log["motivo"])
        self.assertIn("só sem atendimento: 7", log["motivo"])

    def test_rota_perfil_admin_sem_owner(self):
        st, c = self.cliente.pedir("POST", "/api/anuncios/estoque/config",
                                   {"conta": "aracatuba", "apollo_desconta_atendimento": True})
        self.assertEqual(st, 200, c)
        self.assertTrue(c["config"]["apollo_desconta_atendimento"])
        self.assertEqual(c["config"]["modo"], "simulacao")  # o modo da conta nao muda
        st, c = self.cliente.pedir("POST", "/api/anuncios/estoque/config",
                                   {"conta": "aracatuba", "apollo_desconta_atendimento": "sim"})
        self.assertEqual(st, 400, c)
        with S.DB_LOCK:
            S.upsert_app_user("sup_rev", "sup_rev", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("sup_rev", SENHA, area="admin")
        st, _ = sup.pedir("POST", "/api/anuncios/estoque/config", {"conta": "aracatuba", "apollo_desconta_atendimento": False})
        self.assertEqual(st, 403)
        self.assertTrue(S.anuncio_estoque_config("aracatuba")["apollo_desconta_atendimento"])


class AberturaDoSkuTeste(EstoqueBase):

    def test_pendente_usa_a_mesma_abertura_do_estoque(self):
        with mock.patch.object(S, "_separacao_codigos_conhecidos", return_value={"PSH367/80"}):
            inserir(anuncio_id="MLB1", ficha="WAP103/S", modo="automatico")
            inserir(anuncio_id="MLB2", ficha="PSH367/80", modo="automatico")
            inserir(anuncio_id="MLB3", ficha="KIT-1/10405594-1/10405569", modo="automatico")
            for cod in ("WAP103/S", "PSH367/80", "10405594", "10405569"):
                ler(self, cod, ara=10)
            fila([linha("PED1", "WAP103/S", 2), linha("PED2", "PSH367/80", 3),
                  linha("PED3", "KIT-1/10405594-1/10405569", 4)])
            self.assertEqual(item("MLB1")["calculado"], 8)
            self.assertEqual(item("MLB2")["calculado"], 7)
            self.assertEqual(item("MLB3")["calculado"], 6)
            pend = S.pendentes_por_filial(("aracatuba",))["aracatuba"]
        self.assertEqual(pend.get(S._exp_norm_codigo("WAP103/S")), 2)
        self.assertNotIn(S._exp_norm_codigo("S"), pend)

    def test_kit_do_cadastro_na_fila_abre_em_pecas(self):
        kits([{"id": "KIT_a", "sku_kit": "KITAB", "nome": "Kit", "ativo": True,
               "componentes": [{"sku": "A1", "quantidade": 2}, {"sku": "B1", "quantidade": 1}]}])
        fila([linha("PED1", "KITAB", 2)])
        pend = S.pendentes_por_filial(("aracatuba",))["aracatuba"]
        self.assertEqual((pend.get("A1"), pend.get("B1")), (4, 2))

    def test_tiktok_continua_igual(self):
        fila([linha("PED1", "WAP103/S", 2, Casa="bauru"), linha("PED2", "KIT2-X1", 1, Casa="bauru")])
        esperado = {}
        for sku, q in (("WAP103/S", 2), ("KIT2-X1", 1)):
            for c, u in S._tiktok_pecas(sku):
                esperado[S._exp_norm_codigo(c)] = esperado.get(S._exp_norm_codigo(c), 0) + q * u
        self.assertEqual(S._tiktok_pendentes_bauru(), esperado)


class QuedaDoPendenteTeste(EstoqueBase):

    def test_teto_usa_o_menor_entre_enviado_e_lido_no_ml(self):
        # V20.26 (revisao): enviado 6, o ML ja mostra 5 (venda que ainda nao esta na fila); concluir outro pedido
        # nao pode devolver 6 ao ML enquanto o Apollo nao reler
        inserir(ficha="P1", modo="automatico", estoque=3)
        self.ml.item("MLB1", 3)
        ler(self, "P1", ara=10)
        fila([linha("PED1", "P1", 4, Marketplace="MERCADO LIVRE")])
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        S.anuncios_estoque_rodada("aracatuba", ator="sistema")
        self.assertEqual(canal()["estoque_enviado"], 6)
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_apollo_v2026 SET lido_epoch=lido_epoch-30")
            S.DB.execute("UPDATE anuncio_canal_v2023 SET estoque=5 WHERE anuncio_id='MLB1'")
            S.DB.commit()
        fila([])
        self.assertEqual(item()["calculado"], 5)

    def test_pedido_concluido_nao_sobe_antes_do_apollo_reler(self):
        inserir(ficha="P1", modo="automatico", estoque=3)
        self.ml.item("MLB1", 3)
        ler(self, "P1", ara=10)
        fila([linha("PED1", "P1", 4, Marketplace="MERCADO LIVRE")])
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        S.anuncios_estoque_rodada("aracatuba", ator="sistema")
        self.assertEqual(canal()["estoque_enviado"], 6)
        with S.DB_LOCK:  # a leitura do Apollo e de antes da conclusao
            S.DB.execute("UPDATE anuncio_apollo_v2026 SET lido_epoch=lido_epoch-30")
            S.DB.commit()
        fila([])  # a Conferencia concluiu: o pendente cai, o Apollo ainda nao baixou
        it = item()
        self.assertEqual(it["calculado"], 6)  # nao sobe para 10
        self.assertIn("reler P1", it["segurado"])
        self.ml.chamadas.clear()
        S.anuncios_estoque_rodada("aracatuba", ator="sistema")
        self.assertEqual(self.ml.escritas(), [])
        # a peca vai para o comeco do rodizio (antes de uma nunca lida)
        inserir(anuncio_id="MLB2", ficha="PN", modo="automatico")
        self.apollo["PN"] = {1: 5}
        self.apollo["P1"] = {1: 6}  # o Apollo baixou
        self.assertEqual(S.anuncios_apollo_ler_proximo("aracatuba"), "P1")
        it = item()
        self.assertEqual((it["calculado"], it["segurado"]), (6, ""))
        self.apollo["P1"] = {1: 9}  # entrou mercadoria depois: sobe normalmente com a leitura nova
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_apollo_v2026 SET lido_epoch=lido_epoch+5")
            S.DB.commit()
        S.anuncios_apollo_ler("P1", "aracatuba")
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_apollo_v2026 SET lido_epoch=? WHERE codigo='P1'", (int(time.time()) + 5,))
            S.DB.commit()
        self.assertEqual(item()["calculado"], 9)

    def test_sem_envio_anterior_fica_no_ml(self):
        inserir(ficha="P1", modo="automatico", estoque=5)
        ler(self, "P1", ara=10)
        fila([linha("PED1", "P1", 4)])
        item()
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_apollo_v2026 SET lido_epoch=lido_epoch-30")
            S.DB.commit()
        fila([])
        self.assertEqual(item()["calculado"], 5)  # teto = o numero no ML


class LeituraDaFilaTeste(EstoqueBase):

    def test_rodada_le_a_fila_uma_vez(self):
        for i in range(4):
            inserir(anuncio_id=f"MLB{i}", ficha=f"P{i}", modo="automatico", estoque=1)
            self.ml.item(f"MLB{i}", 1)
            ler(self, f"P{i}", ara=5 + i)
        fila([linha("PED1", "P1", 1)])
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        S._ml_cache_leitura_limpar()
        original = S.get_storage
        lidas = []

        def contar(chave, *a, **kw):
            if chave == "conf_pedidos":
                lidas.append(chave)
            return original(chave, *a, **kw)

        with mock.patch.object(S, "get_storage", side_effect=contar):
            r = S.anuncios_estoque_rodada("aracatuba", ator="sistema")
            self.assertEqual(r["enviados"], 4, r)
            self.assertEqual(len(lidas), 1)
            S.anuncios_estoque_rodada("aracatuba", ator="sistema")
            S.anuncios_estoque_payload("aracatuba")
            self.assertEqual(len(lidas), 1)  # a versao nao mudou: nenhuma leitura a mais
        fila([linha("PED1", "P1", 2)])
        self.assertEqual(item("MLB1")["calculado"], 4)  # versao nova: le de novo


class AnuncioParadoTeste(EstoqueBase):

    def test_encerrado_e_com_variacoes_nao_entram(self):
        inserir(anuncio_id="MLB1", ficha="P1", modo="automatico")
        inserir(anuncio_id="MLB2", ficha="P1", modo="automatico")
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_canal_v2023 SET status='closed' WHERE anuncio_id='MLB1'")
            S.DB.execute("UPDATE anuncio_canal_v2023 SET variacoes=1 WHERE anuncio_id='MLB2'")
            S.DB.commit()
        ler(self, "P1", ara=5)
        self.assertIn("encerrado", item("MLB1")["motivo"])
        self.assertIsNone(item("MLB1")["calculado"])
        self.assertIn("variações", item("MLB2")["motivo"])

    def test_ml_mostra_variacoes_marca_e_nao_envia(self):
        inserir(ficha="P1", modo="automatico", estoque=1)
        self.ml.item("MLB1", 1)
        ler(self, "P1", ara=5)
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        original = self.ml.get
        self.ml.get = lambda caminho, **kw: {**original(caminho, **kw), "variations": [{"id": 1}]}
        with mock.patch.object(S, "ml_api_get", side_effect=lambda c, **kw: self.ml.get(c, **kw)):
            S.anuncios_estoque_rodada("aracatuba", ator="sistema")
        self.assertEqual(self.ml.escritas(), [])
        self.assertEqual(canal()["variacoes"], 1)
        self.assertIn("variações", item()["motivo"])

    def test_tres_erros_param_e_aparecem_no_painel(self):
        inserir(ficha="P1", modo="automatico", estoque=3)
        self.ml.item("MLB1", 3)
        self.ml.nao_muda.add("MLB1")
        ler(self, "P1", ara=10)
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        with mock.patch.object(S, "ANUNCIO_ESTOQUE_ERRO_ESPERA_S", 0):
            for _ in range(5):
                S.anuncios_estoque_rodada("aracatuba", ator="sistema")
        self.assertEqual(len(self.ml.escritas()), 3)
        rep = S.anuncios_estoque_erros_repetidos()
        self.assertEqual([(e["anuncio_id"], e["erros"]) for e in rep], [("MLB1", 3)])
        saude = S._saude_anuncios_estoque()
        self.assertEqual(saude["nivel"], "atencao")
        self.assertIn("MLB1", saude["detalhe"])
        self.assertIn("anuncios_estoque", {c["id"] for c in S.saude_checks()})
        # o numero mudou: tenta de novo
        ler(self, "P1", ara=11)
        with mock.patch.object(S, "ANUNCIO_ESTOQUE_ERRO_ESPERA_S", 0):
            S.anuncios_estoque_rodada("aracatuba", ator="sistema")
        self.assertEqual(len(self.ml.escritas()), 4)
        self.assertEqual(S.anuncios_estoque_erros_repetidos(), [])
        self.assertEqual(S._saude_anuncios_estoque()["nivel"], "ok")


class EnvioEmFundoTeste(EstoqueBase):

    def esperar(self, conta="aracatuba"):
        for _ in range(200):
            if not S.anuncios_estoque_envio_situacao(conta).get("rodando"):
                return
            time.sleep(0.05)
        self.fail("o envio nao terminou")

    def test_enviar_agora_em_segundo_plano(self):
        inserir(ficha="P1", modo="automatico", estoque=3)
        self.ml.item("MLB1", 3)
        ler(self, "P1", ara=10)
        S.anuncio_estoque_config_definir("aracatuba", "lote", "luis")
        st, c = self.cliente.pedir("POST", "/api/anuncios/estoque/enviar", {"conta": "aracatuba"})
        self.assertEqual(st, 200, c)
        self.assertTrue(c["iniciado"])
        self.esperar()
        st, c = self.cliente.pedir("GET", "/api/anuncios/estoque?conta=aracatuba")
        self.assertEqual(st, 200, c)
        self.assertFalse(c["envio"]["rodando"])
        self.assertEqual(c["envio"]["resultado"]["enviados"], 1)
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 10)
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM audit_log WHERE action='anuncios.estoque_enviar'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_um_por_conta(self):
        S.anuncio_estoque_config_definir("aracatuba", "lote", "luis")
        trava = S._ANUNCIOS_ESTOQUE_ENVIO_TRAVAS["aracatuba"]
        trava.acquire()
        try:
            st, _ = self.cliente.pedir("POST", "/api/anuncios/estoque/enviar", {"conta": "aracatuba"})
            self.assertEqual(st, 409)
        finally:
            trava.release()


class VoltaParaManualTeste(EstoqueBase):

    def test_puxada_ve_full(self):
        inserir(ficha="P1", modo="automatico")
        res = {"lidos": 0, "novos": 0, "atualizados": 0, "sem_ficha": 0}
        S._anuncio_gravar_itens("aracatuba", {"MLB1": {"titulo": "T", "sku_ml": "P1", "preco": 10, "estoque": 3,
                                                       "status": "active", "logistica": "fulfillment"}}, res)
        self.assertEqual(canal()["estoque_modo"], "manual")
        self.assertIn("Full", S.anuncio_estoque_log("ml", "aracatuba", "MLB1")[0]["detalhe"])

    def test_remover_e_reincluir(self):
        inserir(ficha="P1", modo="automatico")
        S.anuncio_remover("ml", "aracatuba", "MLB1", "luis")
        self.assertEqual(canal()["estoque_modo"], "manual")
        S.anuncio_reincluir("ml", "aracatuba", "MLB1", "luis")
        self.assertEqual(canal()["estoque_modo"], "manual")
        self.assertEqual(S.anuncio_estoque_log("ml", "aracatuba", "MLB1")[0]["motivo"], "modo automatico -> manual")

    def test_trocar_controle_registra(self):
        inserir(ficha="P1", modo="automatico")
        S.anuncio_controle_definir("ml", "aracatuba", "MLB1", "alephee", "luis", False)
        self.assertEqual(canal()["estoque_modo"], "manual")
        self.assertIn("controle noromak -> alephee", S.anuncio_estoque_log("ml", "aracatuba", "MLB1")[0]["detalhe"])

    def test_ml_mostra_full_no_envio(self):
        inserir(ficha="P1", modo="automatico", estoque=3)
        self.ml.item("MLB1", 3, logistica="fulfillment")
        ler(self, "P1", ara=10)
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        S.anuncios_estoque_rodada("aracatuba", ator="sistema")
        self.assertEqual((canal()["estoque_modo"], canal()["logistica"]), ("manual", "fulfillment"))


def png(lado=600):
    linha_px = b"\x00" + b"\xff\xff\xff" * lado
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 2, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(linha_px * lado)) + ch(b"IEND", b""))


class TravaDasFotosTeste(EstoqueBase):

    def test_foto_durante_as_fotos_do_ml_e_409(self):
        S.ficha_importar("P1", {"nome": "Produto 1"}, "apollo")
        corpo = {"codigo": "P1", "base64": base64.b64encode(png()).decode()}
        S.ANUNCIOS_FOTOS_ML_LOCK.acquire()
        try:
            st, c = self.cliente.pedir("POST", "/api/anuncios/foto", corpo)
            self.assertEqual(st, 409, c)
            self.assertIn("Aguarde", c["error"])
        finally:
            S.ANUNCIOS_FOTOS_ML_LOCK.release()
        self.assertEqual(S.anuncio_fotos("P1"), [])
        st, c = self.cliente.pedir("POST", "/api/anuncios/foto", corpo)
        self.assertEqual(st, 200, c)


class TelaRevisaoTeste(EstoqueBase):

    def test_tela(self):
        from pathlib import Path
        html = (Path(S.__file__).parent / "APP" / "anuncios.html").read_text(encoding="utf-8")
        for trecho in ("apollo_desconta_atendimento", "confirmar com o Guilherme", "descontando tudo:",
                       "só sem atendimento:", "acompanharEnvioConta", "e.status === 401 || e.status === 403"):
            self.assertIn(trecho, html, trecho)


if __name__ == "__main__":
    import unittest
    unittest.main()
