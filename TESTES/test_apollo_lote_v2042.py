"""V20.42 - Estoque dos anuncios relido em LOTE pela porta estoque/lote (Guilherme, 09/11).

Uma chamada por minuto com todos os codigos em Automatico (ate 500 por chamada, limite proprio de 6/min, separado
dos 20/min). O rodizio de 3/min fica so de reserva: roda quando o lote falha ou nunca deu certo ha 3 min.
Formato real lido em 09/11: {"ok": true, "itens": [{"codigo", "revenda", "disponivel", "tem_cadastro", "erro"}]}.
"""

import time
from unittest import mock

from base import S, linha
from test_anuncios_estoque_v2026 import EstoqueBase, inserir, fila


class FalsaPortaLote:
    def __init__(self):
        self.estoque = {}       # (codigo, revenda) -> disponivel | None | "sem_cadastro"
        self.chamadas = []      # (codigos, revendas)
        self.erro = None        # excecao levantada na chamada
        self.ok = True
        self.inexistentes = set()  # codigos sem cadastro em nenhuma casa
        self.recusados = set()     # codigos que a porta recusa com 422

    def http(self, codigos, revendas):
        self.chamadas.append((list(codigos), list(revendas)))
        if self.erro is not None:
            raise self.erro
        if self.recusados & set(codigos):
            e = S.PortaErro("A porta do Apollo respondeu HTTP 422: codigo invalido")
            e.status = 422
            raise e
        if not self.ok:
            return {"ok": False, "erro": "Apollo fora"}
        itens = []
        for c in codigos:
            for r in revendas:
                v = "sem_cadastro" if c in self.inexistentes else self.estoque.get((c, r), 0)
                if v == "sem_cadastro":
                    itens.append({"codigo": c, "revenda": r, "disponivel": 0, "tem_cadastro": False, "erro": None})
                elif v is None:
                    itens.append({"codigo": c, "revenda": r, "disponivel": None, "tem_cadastro": None,
                                  "erro": "casa nao respondeu"})
                else:
                    itens.append({"codigo": c, "revenda": r, "disponivel": float(v), "tem_cadastro": True,
                                  "erro": None})
        return {"ok": True, "lido_em": "2026-11-09T10:13:44-03:00", "revendas": list(revendas), "itens": itens}


def apollo(codigo, filial="aracatuba"):
    with S.DB_LOCK:
        r = S.DB.execute("SELECT * FROM anuncio_apollo_v2026 WHERE codigo=? AND filial=?", (codigo, filial)).fetchone()
    return dict(r) if r else None


class LoteBase(EstoqueBase):

    def setUp(self):
        super().setUp()
        self.porta = FalsaPortaLote()
        p = mock.patch.object(S, "_apollo_estoque_lote_http", side_effect=self.porta.http)
        p.start()
        self.addCleanup(p.stop)
        S._apollo_lote_zerar()


class LeituraEmLoteTeste(LoteBase):

    def test_uma_chamada_para_todos_os_codigos_das_duas_filiais(self):
        inserir(anuncio_id="MLB1", ficha="P1", modo="automatico")
        inserir(anuncio_id="MLB2", ficha="P2", modo="automatico")
        inserir(conta="bauru", anuncio_id="MLB9", ficha="P1", modo="automatico")
        self.porta.estoque.update({("P1", 1): 10, ("P2", 1): 4, ("P1", 3): 7})
        mud = set()
        self.assertTrue(S.anuncios_apollo_lote(mud))
        self.assertEqual(len(self.porta.chamadas), 1)
        codigos, revendas = self.porta.chamadas[0]
        self.assertEqual(sorted(codigos), ["P1", "P2"])
        self.assertEqual(sorted(revendas), [1, 2, 3])
        self.assertEqual(apollo("P1")["disponivel"], 10)
        self.assertEqual(apollo("P2")["disponivel"], 4)
        self.assertEqual(apollo("P1", "bauru")["disponivel"], 7)
        self.assertIsNone(apollo("P2", "bauru"))  # Bauru nao tem anuncio de P2: nao grava
        self.assertEqual(mud, {"aracatuba", "bauru"})
        self.assertEqual(self.apollo_chamadas, [])  # o rodizio (porta estoque, 20/min) nao foi usado

    def test_sempre_as_tres_filiais(self):
        """revisao 2: "nao existe no Apollo" nao pode depender de qual filial tem Automatico (Ribeirao inclusa)."""
        inserir(anuncio_id="MLB1", ficha="P1", modo="automatico")
        S.anuncios_apollo_lote(set())
        self.assertEqual(self.porta.chamadas[0][1], [1, 2, 3])

    def test_sem_automatico_nao_chama(self):
        inserir(ficha="P1", modo="manual")
        self.assertFalse(S.anuncios_apollo_lote(set()))
        self.assertEqual(self.porta.chamadas, [])

    def test_uma_vez_por_minuto(self):
        inserir(ficha="P1", modo="automatico")
        self.assertTrue(S.anuncios_apollo_lote(set()))
        self.assertFalse(S.anuncios_apollo_lote(set()))  # 20 s depois: espera o minuto
        self.assertEqual(len(self.porta.chamadas), 1)
        S._APOLLO_LOTE_ESTADO["tentado_mono"] -= S.ANUNCIO_APOLLO_LOTE_S + 1
        self.assertTrue(S.anuncios_apollo_lote(set()))
        self.assertEqual(len(self.porta.chamadas), 2)

    def test_sem_mudanca_nao_marca_a_filial(self):
        inserir(ficha="P1", modo="automatico")
        self.porta.estoque[("P1", 1)] = 5
        S.anuncios_apollo_lote(set())
        S._APOLLO_LOTE_ESTADO["tentado_mono"] = 0
        mud = set()
        S.anuncios_apollo_lote(mud)
        self.assertEqual(mud, set())
        self.porta.estoque[("P1", 1)] = 4
        S._APOLLO_LOTE_ESTADO["tentado_mono"] = 0
        S.anuncios_apollo_lote(mud)
        self.assertEqual(mud, {"aracatuba"})

    def test_casa_sem_resposta_e_nulo_nunca_zero(self):
        inserir(ficha="P1", modo="automatico")
        self.porta.estoque[("P1", 1)] = None
        S.anuncios_apollo_lote(set())
        r = apollo("P1")
        self.assertIsNone(r["disponivel"])
        self.assertEqual(r["respondeu"], 0)

    def test_sem_cadastro_so_nesta_casa_e_zero(self):
        """A peca existe no Apollo (cadastrada em Bauru) mas nao em Araçatuba: Araçatuba = 0, como na porta estoque."""
        inserir(ficha="P1", modo="automatico")
        inserir(conta="bauru", anuncio_id="MLB9", ficha="P1", modo="automatico")
        self.porta.estoque[("P1", 1)] = "sem_cadastro"
        self.porta.estoque[("P1", 3)] = 5
        S.anuncios_apollo_lote(set())
        self.assertEqual(apollo("P1")["disponivel"], 0)
        self.assertEqual(apollo("P1")["respondeu"], 1)
        self.assertEqual(apollo("P1", "bauru")["disponivel"], 5)

    def test_codigo_que_nao_voltou_e_nulo(self):
        inserir(anuncio_id="MLB1", ficha="P1", modo="automatico")
        inserir(anuncio_id="MLB2", ficha="P2", modo="automatico")
        original = self.porta.http

        def sem_p2(codigos, revendas):
            d = original(codigos, revendas)
            d["itens"] = [i for i in d["itens"] if i["codigo"] != "P2"]
            return d
        self.porta.http = sem_p2
        with mock.patch.object(S, "_apollo_estoque_lote_http", side_effect=sem_p2):
            S.anuncios_apollo_lote(set())
        self.assertIsNone(apollo("P2")["disponivel"])

    def test_ok_false_nao_grava_nada(self):
        inserir(ficha="P1", modo="automatico")
        self.porta.ok = False
        self.assertFalse(S.anuncios_apollo_lote(set()))
        self.assertIsNone(apollo("P1"))
        self.assertIn("Apollo fora", S._APOLLO_LOTE_ESTADO["erro"])

    def test_erro_de_rede_nao_grava_nada(self):
        inserir(ficha="P1", modo="automatico")
        self.porta.erro = S.PortaErro("O Apollo não respondeu: timeout", passageiro=True)
        self.assertFalse(S.anuncios_apollo_lote(set()))
        self.assertIsNone(apollo("P1"))

    def test_mais_de_500_codigos_em_mais_de_uma_chamada(self):
        with S.DB_LOCK:
            for i in range(620):
                S.DB.execute(
                    "INSERT INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,sku_anuncio,controle,titulo,"
                    "estoque_modo) VALUES('ml','aracatuba',?,?,?,'noromak','T','automatico')",
                    (f"MLB{i}", f"Q{i:04d}", f"Q{i:04d}"))
            S.DB.commit()
        self.assertTrue(S.anuncios_apollo_lote(set()))
        self.assertEqual([len(c) for c, _ in self.porta.chamadas], [500, 120])

    def test_limite_proprio_por_minuto(self):
        with S.DB_LOCK:
            for i in range(S.ANUNCIO_APOLLO_LOTE_POR_MINUTO * 500 + 10):
                S.DB.execute(
                    "INSERT INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,sku_anuncio,controle,titulo,"
                    "estoque_modo) VALUES('ml','aracatuba',?,?,?,'noromak','T','automatico')",
                    (f"MLB{i}", f"Q{i:05d}", f"Q{i:05d}"))
            S.DB.commit()
        self.assertFalse(S.anuncios_apollo_lote(set()))  # nao coube tudo no minuto: nao e "lote completo"
        self.assertEqual(len(self.porta.chamadas), S.ANUNCIO_APOLLO_LOTE_POR_MINUTO)

    def test_porta_nao_liberada_espera_10_min(self):
        inserir(ficha="P1", modo="automatico")
        self.porta.erro = S.PortaErro("A porta do Apollo para esta etapa ainda não está liberada (404).")
        self.porta.erro.status = 404
        S.anuncios_apollo_lote(set())
        S._APOLLO_LOTE_ESTADO["tentado_mono"] -= S.ANUNCIO_APOLLO_LOTE_S + 1
        S.anuncios_apollo_lote(set())
        self.assertEqual(len(self.porta.chamadas), 1)


def tiktok_sku(seller_sku="TK1"):
    with S.DB_LOCK:
        S.DB.execute("INSERT OR REPLACE INTO tiktok_sku_v1964(sku_id,product_id,titulo,seller_sku,status) "
                     "VALUES('S1','PR1','Prod',?,'ACTIVATE')", (seller_sku,))
        S.DB.commit()
    S._tiktok_config_gravar("extensao_lida_epoch", str(int(time.time())), "teste")


class RevisaoTeste(LoteBase):
    """revisar-antes-de-subir da V20.42 (09/11): os 5 achados corrigidos."""

    def test_1_peca_que_nao_existe_no_apollo_nao_vira_zero(self):
        """A porta estoque responde ok:false (peca inexistente em todas as casas) e o rodizio gravava None.
        No lote ela volta tem_cadastro:false em todas as casas pedidas: tem de ficar sem leitura, nunca 0."""
        inserir(ficha="ERRADO1", modo="automatico")
        self.porta.inexistentes.add("ERRADO1")
        S.anuncios_apollo_lote(set())
        r = apollo("ERRADO1")
        self.assertIsNone(r["disponivel"])
        self.assertEqual(r["respondeu"], 0)

    def test_1_peca_inexistente_nao_manda_zero_ao_ml(self):
        inserir(ficha="ERRADO1", modo="automatico")
        self.porta.inexistentes.add("ERRADO1")
        self.apollo["ERRADO1"] = RuntimeError("Peça não encontrada no Apollo.")
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        self.ml.item("MLB1", 7)
        S.anuncios_estoque_volta({})
        self.assertEqual(self.ml.escritas(), [])
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 7)

    def test_1_tiktok_peca_inexistente_sem_leitura(self):
        tiktok_sku()
        self.porta.inexistentes.add("TK1")
        S.anuncios_apollo_lote(set())
        with S.DB_LOCK:
            r = dict(S.DB.execute("SELECT * FROM tiktok_apollo_v1964 WHERE codigo='TK1'").fetchone())
        self.assertIsNone(r["disponivel"])
        self.assertEqual(r["respondeu"], 0)

    def test_2_codigo_sem_numero_vai_para_o_rodizio(self):
        inserir(anuncio_id="MLB1", ficha="P1", modo="automatico")
        inserir(anuncio_id="MLB2", ficha="P2", modo="automatico")
        self.porta.estoque.update({("P1", 1): 10, ("P2", 1): None})
        self.apollo["P2"] = {1: 6}
        self.assertTrue(S.anuncios_apollo_lote(set()))
        self.assertTrue(S.apollo_lote_em_dia())
        self.assertEqual(S.apollo_lote_fora("aracatuba"), {"P2"})
        S._APOLLO_LOTE_ESTADO["tentado_mono"] = time.monotonic()  # a volta nao chama o lote de novo agora
        S.anuncios_estoque_volta({})
        self.assertEqual(self.apollo_chamadas, ["P2"])  # o rodizio leu so o P2
        self.assertEqual(apollo("P2")["disponivel"], 6)

    def test_2_lote_sem_numero_nao_apaga_a_leitura_do_rodizio(self):
        inserir(ficha="P2", modo="automatico")
        self.apollo["P2"] = {1: 6}
        S.anuncios_apollo_ler("P2", "aracatuba")  # o rodizio acabou de ler
        self.porta.estoque[("P2", 1)] = None
        S.anuncios_apollo_lote(set())
        self.assertEqual(apollo("P2")["disponivel"], 6)

    def test_2_sem_numero_e_leitura_velha_fica_nulo(self):
        inserir(ficha="P2", modo="automatico")
        self.apollo["P2"] = {1: 6}
        S.anuncios_apollo_ler("P2", "aracatuba")
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_apollo_v2026 SET lido_epoch=lido_epoch-?",
                         (S.ANUNCIO_APOLLO_LOTE_RESERVA_S + 5,))
            S.DB.commit()
        self.porta.estoque[("P2", 1)] = None
        S.anuncios_apollo_lote(set())
        self.assertIsNone(apollo("P2")["disponivel"])

    def test_2_codigo_com_pontos_volta_sem_os_pontos(self):
        inserir(ficha="6007.003.315.00.8", modo="automatico")
        self.porta.estoque[("6007.003.315.00.8", 1)] = 9
        original = self.porta.http

        def sem_pontos(codigos, revendas):
            d = original(codigos, revendas)
            for i in d["itens"]:
                i["codigo"] = i["codigo"].replace(".", "")
            return d
        with mock.patch.object(S, "_apollo_estoque_lote_http", side_effect=sem_pontos):
            S.anuncios_apollo_lote(set())
        self.assertEqual(apollo("6007.003.315.00.8")["disponivel"], 9)
        self.assertEqual(S.apollo_lote_fora("aracatuba"), set())

    def test_2_tiktok_sem_numero_nao_esta_em_dia(self):
        tiktok_sku()
        self.porta.estoque[("TK1", 3)] = None
        S.anuncios_apollo_lote(set())
        self.assertFalse(S.apollo_lote_em_dia(tiktok=True))  # o rodizio do TikTok continua

    def test_3_codigo_recusado_nao_derruba_os_outros(self):
        inserir(anuncio_id="MLB1", ficha="P1", modo="automatico")
        inserir(anuncio_id="MLB2", ficha="RUIM", modo="automatico")
        self.porta.estoque[("P1", 1)] = 10
        original = self.porta.http

        def recusa(codigos, revendas):
            if "RUIM" in codigos:
                self.porta.chamadas.append((list(codigos), list(revendas)))
                e = S.PortaErro("A porta do Apollo respondeu HTTP 422: codigo invalido")
                e.status = 422
                raise e
            return original(codigos, revendas)
        with mock.patch.object(S, "_apollo_estoque_lote_http", side_effect=recusa):
            self.assertTrue(S.anuncios_apollo_lote(set()))
            self.assertEqual(apollo("P1")["disponivel"], 10)
            self.assertEqual(S.apollo_lote_fora("aracatuba"), {"RUIM"})
            n = len(self.porta.chamadas)
            S._APOLLO_LOTE_ESTADO["tentado_mono"] = 0
            S._APOLLO_LOTE_CHAMADAS.clear()
            self.assertTrue(S.anuncios_apollo_lote(set()))
            self.assertNotIn("RUIM", self.porta.chamadas[-1][0])  # o recusado nao vai de novo
            self.assertEqual(len(self.porta.chamadas), n + 1)
            self.assertEqual(S.apollo_lote_fora("aracatuba"), {"RUIM"})  # o rodizio continua cobrindo

    def test_3_tiktok_vai_com_o_codigo_limpo(self):
        tiktok_sku("tk 1")
        S.anuncios_apollo_lote(set())
        self.assertEqual(self.porta.chamadas[0][0], ["TK1"])

    def test_3_404_so_pelo_codigo_http(self):
        inserir(ficha="P1", modo="automatico")
        self.porta.erro = S.PortaErro("A porta do Apollo respondeu HTTP 500: peca 4045 falhou")
        S.anuncios_apollo_lote(set())
        self.assertEqual(S._APOLLO_LOTE_ESTADO["fechada_ate"], 0.0)

    def test_3_porta_marca_o_codigo_http_no_erro(self):
        import urllib.error
        import io
        erro = urllib.error.HTTPError("http://x", 422, "x", {}, io.BytesIO(b"{}"))
        with mock.patch.object(S.urllib.request, "urlopen", side_effect=erro):
            with self.assertRaises(S.PortaErro) as cm:
                S._exp_porta_resposta(S.urllib.request.Request("http://x"), False)
        self.assertEqual(cm.exception.status, 422)

    def test_4_hora_da_leitura_e_a_de_antes_da_chamada(self):
        inserir(ficha="P1", modo="automatico")
        original = self.porta.http
        antes = {}

        def lenta(codigos, revendas):
            antes["t"] = time.time()
            time.sleep(1.2)
            return original(codigos, revendas)
        with mock.patch.object(S, "_apollo_estoque_lote_http", side_effect=lenta):
            S.anuncios_apollo_lote(set())
        self.assertLessEqual(apollo("P1")["lido_epoch"], int(antes["t"]))

    def test_4_nao_grava_por_cima_de_leitura_mais_nova(self):
        inserir(ficha="P1", modo="automatico")
        original = self.porta.http

        def rodizio_no_meio(codigos, revendas):
            d = original(codigos, revendas)
            time.sleep(1.1)
            self.apollo["P1"] = {1: 4}
            S.anuncios_apollo_ler("P1", "aracatuba")  # o rodizio leu depois que a porta leu
            return d
        self.porta.estoque[("P1", 1)] = 5
        with mock.patch.object(S, "_apollo_estoque_lote_http", side_effect=rodizio_no_meio):
            S.anuncios_apollo_lote(set())
        self.assertEqual(apollo("P1")["disponivel"], 4)

    def test_5_porta_lenta_nao_prende_a_volta(self):
        inserir(ficha="P1", modo="automatico")
        self.porta.estoque[("P1", 1)] = 10
        original = self.porta.http

        def lenta(codigos, revendas):
            time.sleep(1.5)
            return original(codigos, revendas)
        self.apollo["P1"] = S.CrmLimiteAtingido(10)  # o rodizio nao le nada nesta volta
        with mock.patch.object(S, "_apollo_estoque_lote_http", side_effect=lenta), \
                mock.patch.object(S, "ANUNCIO_APOLLO_LOTE_ESPERA_S", 0.2):
            t0 = time.monotonic()
            S.anuncios_estoque_volta({})
            self.assertLess(time.monotonic() - t0, 1.2)
            S._apollo_lote_esperar(5)
        self.assertEqual(apollo("P1")["disponivel"], 10)
        self.assertEqual(S._apollo_lote_mudancas_tirar(), {"aracatuba"})

    def test_5_mudanca_do_lote_atrasado_chega_na_volta_seguinte(self):
        inserir(ficha="P1", modo="automatico")
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        self.ml.item("MLB1", 3)
        self.porta.estoque[("P1", 1)] = 10
        original = self.porta.http

        def lenta(codigos, revendas):
            time.sleep(1.0)
            return original(codigos, revendas)
        self.apollo["P1"] = S.CrmLimiteAtingido(10)  # o rodizio nao le nada nesta volta
        estado = {"versao": None, "recalculo": time.monotonic()}
        with mock.patch.object(S, "_apollo_estoque_lote_http", side_effect=lenta), \
                mock.patch.object(S, "ANUNCIO_APOLLO_LOTE_ESPERA_S", 0.1):
            S.anuncios_estoque_volta(estado)
            S._apollo_lote_esperar(5)
            self.assertEqual(S.anuncios_estoque_volta(estado), ["aracatuba"])
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 10)

    def test_5_um_lote_por_vez(self):
        inserir(ficha="P1", modo="automatico")
        original = self.porta.http

        def lenta(codigos, revendas):
            time.sleep(1.0)
            return original(codigos, revendas)
        with mock.patch.object(S, "_apollo_estoque_lote_http", side_effect=lenta), \
                mock.patch.object(S, "ANUNCIO_APOLLO_LOTE_ESPERA_S", 0.1):
            S.anuncios_apollo_lote_fundo()
            S._APOLLO_LOTE_ESTADO["tentado_mono"] = 0
            S.anuncios_apollo_lote_fundo()
            S._apollo_lote_esperar(5)
        self.assertEqual(len(self.porta.chamadas), 1)


def muitos(n, prefixo="Q"):
    with S.DB_LOCK:
        for i in range(n):
            S.DB.execute(
                "INSERT INTO anuncio_canal_v2023(canal,conta,anuncio_id,ficha_codigo,sku_anuncio,controle,titulo,"
                "estoque_modo) VALUES('ml','aracatuba',?,?,?,'noromak','T','automatico')",
                (f"MLB{prefixo}{i}", f"{prefixo}{i:04d}", f"{prefixo}{i:04d}"))
        S.DB.commit()
    return [f"{prefixo}{i:04d}" for i in range(n)]


def proximo_minuto():
    S._APOLLO_LOTE_ESTADO["tentado_mono"] = 0
    S._APOLLO_LOTE_CHAMADAS.clear()


class Revisao2Teste(LoteBase):
    """2a revisao da V20.42 (09/11, "corrigir os 3")."""

    def test_1_recusado_entre_muitos_e_achado_em_poucos_minutos(self):
        """Com 120 codigos as 5 chamadas do minuto nao bastam para isolar o recusado: a busca continua no minuto
        seguinte (antes recomecava do zero e o lote nunca ficava em dia)."""
        cods = muitos(120)
        ruim = cods[0]
        self.porta.recusados.add(ruim)
        for minuto in range(6):
            S.anuncios_apollo_lote(set())
            if ruim in S._APOLLO_LOTE_ESTADO["recusados"]:
                break
            proximo_minuto()
        self.assertIn(ruim, S._APOLLO_LOTE_ESTADO["recusados"])
        self.assertLessEqual(minuto, 3)
        for c in cods[1:]:  # todos os outros foram lidos
            self.assertEqual(apollo(c)["respondeu"], 1, c)
        proximo_minuto()
        self.assertTrue(S.anuncios_apollo_lote(set()))
        self.assertTrue(S.apollo_lote_em_dia())
        self.assertNotIn(ruim, self.porta.chamadas[-1][0])
        self.assertIn(ruim, S.apollo_lote_fora("aracatuba"))

    def test_1_com_a_busca_pendente_o_lote_esta_em_dia(self):
        """O que ja foi lido conta; o pedaco suspeito vai pelo rodizio ate a busca terminar."""
        cods = muitos(120)
        self.porta.recusados.add(cods[0])
        self.assertTrue(S.anuncios_apollo_lote(set()))
        fora = S.apollo_lote_fora("aracatuba")
        self.assertIn(cods[0], fora)
        self.assertLess(len(fora), 60)
        self.assertIn("recus", S.apollo_lote_situacao()["erro"].lower())

    def test_1_suspeito_que_saiu_do_automatico_nao_volta(self):
        cods = muitos(40)
        self.porta.recusados.add(cods[0])
        S.anuncios_apollo_lote(set())
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_canal_v2023 SET estoque_modo='manual'")
            S.DB.commit()
        inserir(anuncio_id="MLBX", ficha="NOVO", modo="automatico")
        proximo_minuto()
        S.anuncios_apollo_lote(set())
        enviados = {c for cs, _ in self.porta.chamadas[-1:] for c in cs}
        self.assertEqual(enviados, {"NOVO"})

    def test_2_peca_so_em_bauru_anunciada_em_aracatuba_e_zero_com_ou_sem_bauru(self):
        inserir(ficha="SOB", modo="automatico")
        self.porta.estoque.update({("SOB", 1): "sem_cadastro", ("SOB", 2): "sem_cadastro", ("SOB", 3): 5})
        S.anuncios_apollo_lote(set())
        self.assertEqual(apollo("SOB")["disponivel"], 0)
        self.assertEqual(apollo("SOB")["respondeu"], 1)

    def test_2_peca_so_em_ribeirao_e_zero(self):
        inserir(ficha="SOR", modo="automatico")
        self.porta.estoque.update({("SOR", 1): "sem_cadastro", ("SOR", 2): 3, ("SOR", 3): "sem_cadastro"})
        S.anuncios_apollo_lote(set())
        self.assertEqual(apollo("SOR")["disponivel"], 0)

    def test_3_sem_numero_repetido_nao_muda_a_hora(self):
        inserir(ficha="P1", modo="automatico")
        self.porta.estoque[("P1", 1)] = None
        S.anuncios_apollo_lote(set())
        primeiro = apollo("P1")["lido_epoch"]
        time.sleep(1.1)
        proximo_minuto()
        S.anuncios_apollo_lote(set())
        self.assertEqual(apollo("P1")["lido_epoch"], primeiro)

    def test_3_rodizio_chega_no_codigo_sem_numero_que_importa(self):
        """Peca inexistente no Apollo nao vai para o rodizio (ele tambem nao acharia); a sem resposta vai."""
        for cod in ("AAA1", "AAA2", "AAA3"):
            inserir(anuncio_id="MLB" + cod, ficha=cod, modo="automatico")
            self.porta.inexistentes.add(cod)
        inserir(anuncio_id="MLBZ", ficha="ZZZ9", modo="automatico")
        self.porta.estoque[("ZZZ9", 1)] = None
        self.apollo["ZZZ9"] = {1: 4}
        S.anuncios_apollo_lote(set())
        self.assertEqual(S.apollo_lote_fora("aracatuba"), {"ZZZ9"})
        S._APOLLO_LOTE_ESTADO["tentado_mono"] = time.monotonic()
        S.anuncios_estoque_volta({})
        self.assertEqual(self.apollo_chamadas, ["ZZZ9"])

    def test_3_tiktok_com_variacao_inexistente_fica_em_dia(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO tiktok_sku_v1964(sku_id,product_id,titulo,seller_sku,status) "
                         "VALUES('S1','PR1','Prod','TK1','ACTIVATE')")
            S.DB.execute("INSERT OR REPLACE INTO tiktok_sku_v1964(sku_id,product_id,titulo,seller_sku,status) "
                         "VALUES('S2','PR2','Prod2','ERRADO9','ACTIVATE')")
            S.DB.commit()
        S._tiktok_config_gravar("extensao_lida_epoch", str(int(time.time())), "teste")
        self.porta.estoque[("TK1", 3)] = 12
        self.porta.inexistentes.add("ERRADO9")
        S.anuncios_apollo_lote(set())
        self.assertTrue(S.apollo_lote_em_dia(tiktok=True))  # o rodizio do TikTok descansa


class ReservaTeste(LoteBase):
    """O rodizio de 3/min so roda quando o lote nao esta em dia."""

    def preparar(self):
        inserir(ficha="P1", modo="automatico")
        self.porta.estoque[("P1", 1)] = 10
        self.apollo["P1"] = {1: 10}
        S.anuncio_estoque_config_definir("aracatuba", "automatico", "luis")
        self.ml.item("MLB1", 3)

    def test_lote_em_dia_o_rodizio_nao_roda(self):
        self.preparar()
        self.assertEqual(S.anuncios_estoque_volta({}), ["aracatuba"])
        self.assertEqual(len(self.porta.chamadas), 1)
        self.assertEqual(self.apollo_chamadas, [])
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 10)

    def test_lote_falhou_o_rodizio_cobre(self):
        self.preparar()
        self.porta.erro = S.PortaErro("A porta do Apollo respondeu HTTP 500: x")
        self.assertEqual(S.anuncios_estoque_volta({}), ["aracatuba"])
        self.assertEqual(self.apollo_chamadas, ["P1"])
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 10)

    def test_lote_velho_volta_o_rodizio(self):
        self.preparar()
        S.anuncios_apollo_lote(set())
        self.assertTrue(S.apollo_lote_em_dia())
        S._APOLLO_LOTE_ESTADO["ok_epoch"] -= S.ANUNCIO_APOLLO_LOTE_RESERVA_S + 1
        self.assertFalse(S.apollo_lote_em_dia())

    def test_venda_baixada_no_balcao_chega_no_minuto(self):
        self.preparar()
        estado = {}
        S.anuncios_estoque_volta(estado)
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 10)
        self.porta.estoque[("P1", 1)] = 6  # balcao vendeu 4
        S._APOLLO_LOTE_ESTADO["tentado_mono"] = 0
        self.assertEqual(S.anuncios_estoque_volta(estado), ["aracatuba"])
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 6)

    def test_pedido_concluido_libera_com_a_leitura_seguinte(self):
        """A trava de queda (V20.26) solta assim que o lote rele a peca depois da queda."""
        self.preparar()
        fila([linha("PED1", "P1", 2)])
        estado = {}
        S.anuncios_estoque_volta(estado)
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 8)
        fila([])  # concluido: o pendente cai; o Apollo ainda mostra 10 ate baixar
        time.sleep(1.1)
        self.porta.estoque[("P1", 1)] = 8  # o Apollo baixou a venda
        S._APOLLO_LOTE_ESTADO["tentado_mono"] = 0
        S.anuncios_estoque_volta(estado)
        self.assertEqual(self.ml.itens["MLB1"]["available_quantity"], 8)


class TikTokExtensaoTeste(LoteBase):
    """Sem a API do TikTok, o rodizio do Apollo de Bauru (tiktok_apollo_v1964) tambem vem do lote."""

    def preparar(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT OR REPLACE INTO tiktok_sku_v1964(sku_id,product_id,titulo,seller_sku,status) "
                         "VALUES('S1','PR1','Prod','TK1','ACTIVATE')")
            S.DB.commit()
        S._tiktok_config_gravar("extensao_lida_epoch", str(int(time.time())), "teste")
        self.porta.estoque[("TK1", 3)] = 12

    def tiktok(self, codigo):
        with S.DB_LOCK:
            r = S.DB.execute("SELECT * FROM tiktok_apollo_v1964 WHERE codigo=?", (codigo,)).fetchone()
        return dict(r) if r else None

    def test_codigos_do_tiktok_entram_no_lote(self):
        self.preparar()
        self.assertTrue(S.anuncios_apollo_lote(set()))
        self.assertEqual(self.porta.chamadas[0], (["TK1"], [1, 2, 3]))
        self.assertEqual(self.tiktok("TK1")["disponivel"], 12)
        self.assertTrue(S.apollo_lote_em_dia(tiktok=True))

    def test_extensao_parada_nao_le(self):
        self.preparar()
        S._tiktok_config_gravar("extensao_lida_epoch", str(int(time.time()) - S.TIKTOK_EXTENSAO_RECENTE - 10), "teste")
        self.assertFalse(S.anuncios_apollo_lote(set()))
        self.assertEqual(self.porta.chamadas, [])

    def test_bauru_sem_resposta_e_nulo(self):
        self.preparar()
        self.porta.estoque[("TK1", 3)] = None
        S.anuncios_apollo_lote(set())
        r = self.tiktok("TK1")
        self.assertIsNone(r["disponivel"])
        self.assertIn("Bauru", r["erro"])

    def test_volta_do_worker_le_o_tiktok_mesmo_sem_anuncio_em_automatico(self):
        self.preparar()
        self.assertEqual(S.anuncios_estoque_volta({}), [])
        self.assertEqual(len(self.porta.chamadas), 1)


class ContratoTeste(LoteBase):

    def test_corpo_da_porta(self):
        enviado = {}

        def falso_urlopen(req, timeout=None):
            enviado["url"], enviado["corpo"] = req.full_url, req.data
            enviado["chave"] = req.get_header("X-chave-expedicao")
            enviado["timeout"] = timeout

            class R:
                def __enter__(s):
                    return s

                def __exit__(s, *a):
                    return False

                def read(s):
                    return b'{"ok": true, "itens": []}'
            return R()
        with mock.patch.object(S, "CHAVE_CRM", "chave-teste"), \
                mock.patch.object(S.urllib.request, "urlopen", side_effect=falso_urlopen):
            d = S._APOLLO_LOTE_HTTP_REAL(["P1"], [1, 3])
        self.assertEqual(d, {"ok": True, "itens": []})
        self.assertTrue(enviado["url"].endswith("/estoque/lote"))
        import json
        self.assertEqual(json.loads(enviado["corpo"]),
                         {"codigos": ["P1"], "revendas": [1, 3], "operador": "anuncios-estoque"})
        self.assertEqual(enviado["chave"], "chave-teste")
        self.assertLessEqual(enviado["timeout"], 60)

    def test_situacao_na_tela(self):
        inserir(ficha="P1", modo="automatico")
        S.anuncios_apollo_lote(set())
        d = S.anuncios_estoque_payload("aracatuba")
        self.assertTrue(d["apollo_lote"]["em_dia"])
        self.assertEqual(d["apollo_lote"]["codigos"], 1)

    def test_tela_sem_o_aviso_antigo_do_guilherme(self):
        from pathlib import Path
        html = Path(S.__file__).with_name("APP").joinpath("anuncios.html").read_text(encoding="utf-8")
        self.assertNotIn("Ligue só depois de confirmar com o Guilherme", html)
        self.assertIn("apollo_lote", html)


if __name__ == "__main__":
    import unittest
    unittest.main()
