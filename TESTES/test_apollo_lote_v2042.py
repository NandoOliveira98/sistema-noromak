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

    def http(self, codigos, revendas):
        self.chamadas.append((list(codigos), list(revendas)))
        if self.erro is not None:
            raise self.erro
        if not self.ok:
            return {"ok": False, "erro": "Apollo fora"}
        itens = []
        for c in codigos:
            for r in revendas:
                v = self.estoque.get((c, r), 0)
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
        self.assertEqual(sorted(revendas), [1, 3])
        self.assertEqual(apollo("P1")["disponivel"], 10)
        self.assertEqual(apollo("P2")["disponivel"], 4)
        self.assertEqual(apollo("P1", "bauru")["disponivel"], 7)
        self.assertIsNone(apollo("P2", "bauru"))  # Bauru nao tem anuncio de P2: nao grava
        self.assertEqual(mud, {"aracatuba", "bauru"})
        self.assertEqual(self.apollo_chamadas, [])  # o rodizio (porta estoque, 20/min) nao foi usado

    def test_so_revendas_das_filiais_com_automatico(self):
        inserir(anuncio_id="MLB1", ficha="P1", modo="automatico")
        S.anuncios_apollo_lote(set())
        self.assertEqual(self.porta.chamadas[0][1], [1])

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

    def test_sem_cadastro_no_apollo_e_zero(self):
        inserir(ficha="P1", modo="automatico")
        self.porta.estoque[("P1", 1)] = "sem_cadastro"
        S.anuncios_apollo_lote(set())
        self.assertEqual(apollo("P1")["disponivel"], 0)
        self.assertEqual(apollo("P1")["respondeu"], 1)

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
        S.anuncios_apollo_lote(set())
        S._APOLLO_LOTE_ESTADO["tentado_mono"] -= S.ANUNCIO_APOLLO_LOTE_S + 1
        S.anuncios_apollo_lote(set())
        self.assertEqual(len(self.porta.chamadas), 1)


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
        self.assertEqual(self.porta.chamadas[0], (["TK1"], [3]))
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
