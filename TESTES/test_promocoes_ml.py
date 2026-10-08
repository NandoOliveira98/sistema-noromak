"""V18.23 — promoções do Mercado Livre com a conta do Precificador.

O botão sobe preço de verdade num marketplace real. A trava do preço mínimo
precisa morar no SERVIDOR: a tela avisa, mas quem recusa é o backend. Estes
testes garantem que nenhum caminho aplica promoção no prejuízo, e que a conta
é a mesma do Precificador (preço − comissão − taxa − imposto − custo).
"""

import json
import unittest

from base import S, TesteNoromak, reiniciar_banco


def setUpModule():
    reiniciar_banco()


PROD = {
    "sku": "TESTE1", "produto": "Produto de teste", "curva": "A",
    "custo": 34.10, "imposto": 0.12, "comissao": 0.12, "taxa_fixa": 6.75, "margem_alvo": 0.15,
}


class ContaDaPromocao(unittest.TestCase):
    def test_a_conta_bate_com_o_precificador(self):
        c = S.promo_conta(PROD, 66.97)
        self.assertAlmostEqual(c["comissao"], 8.04, places=2)
        self.assertAlmostEqual(c["imposto"], 8.04, places=2)
        # o lucro sai das parcelas SEM arredondar (arredonda só no fim)
        esperado = 66.97 - 66.97 * 0.12 - 6.75 - 66.97 * 0.12 - 34.10
        self.assertAlmostEqual(c["lucro"], round(esperado, 2), places=2)
        self.assertAlmostEqual(c["margem"], 0.15, places=2)

    def test_b_minimo_zera_o_lucro(self):
        minimo = S.promo_preco_minimo(PROD)
        self.assertAlmostEqual(S.promo_conta(PROD, minimo)["lucro"], 0.0, places=2)

    def test_c_ideal_entrega_a_margem_alvo(self):
        ideal = S.promo_preco_ideal(PROD)
        self.assertAlmostEqual(S.promo_conta(PROD, ideal)["margem"], PROD["margem_alvo"], places=3)

    def test_d_sem_custo_nao_calcula(self):
        sem = dict(PROD, custo=0.0)
        self.assertIsNone(S.promo_preco_minimo(sem))
        self.assertIsNone(S.promo_preco_ideal(sem))


class TravaDoPrecoMinimo(unittest.TestCase):
    """A barreira não pode depender da tela."""

    def setUp(self):
        produtos = [{
            "sku": "TESTE1", "produto": "Produto de teste", "custo": 34.10, "imposto": 0.12, "curva": "A",
            "marketplaces": {"ml": {"taxaFixa": 6.75, "comissao": 0.12, "frete": 0, "margem": 0.15}},
        }]
        atual, _, _ = S.get_storage("precificador_online_v1")
        S.save_storage("precificador_online_v1", atual, json.dumps({"products": produtos}), False,
                       actor="teste", actor_role="admin")
        vinc, _, _ = S.get_storage("ml_sku_mlb_links_v1")
        S.save_storage("ml_sku_mlb_links_v1", vinc,
                       json.dumps({"TESTE1": [{"mlb": "MLB999000111", "status": "Vinculado"}]}),
                       False, actor="teste", actor_role="admin")

    def test_a_preco_no_prejuizo_e_recusado(self):
        # mínimo fica ~R$ 53,75; qualquer coisa abaixo tem de ser barrada ANTES
        # de qualquer chamada ao Mercado Livre.
        with self.assertRaises(ValueError) as c:
            S.ml_promocao_aplicar("MLB999000111", 49.90, "2026-09-23T00:00:00", "2026-10-23T00:00:00", "teste")
        msg = str(c.exception)
        self.assertIn("abaixo do preço mínimo", msg)
        self.assertIn("prejuízo", msg)

    def test_b_sem_custo_no_precificador_recusa(self):
        with self.assertRaises(ValueError) as c:
            S.ml_promocao_aplicar("MLB000000000", 100.0, "2026-09-23T00:00:00", "2026-10-23T00:00:00", "teste")
        self.assertIn("Precificador", str(c.exception))

    def test_c_preco_invalido_recusa(self):
        for ruim in ("", "abc", 0, -5):
            with self.assertRaises(ValueError):
                S.ml_promocao_aplicar("MLB999000111", ruim, "2026-09-23T00:00:00", "2026-10-23T00:00:00", "teste")


if __name__ == "__main__":
    unittest.main()


class RotasRespondem(TesteNoromak):
    """V18.23: a rota GET foi parar dentro do do_POST e a tela recebia
    "Arquivo não encontrado". Um teste de rota pega isso na hora."""

    def test_a_get_promocoes_nao_cai_no_servidor_de_arquivos(self):
        status, corpo = self.cliente.pedir("GET", "/api/precificador/promocoes")
        # Sem token do ML o conteúdo falha, mas TEM de ser JSON desta rota —
        # nunca 404 de arquivo.
        self.assertNotEqual(status, 404, "a rota GET não está registrada no do_GET")
        self.assertIsInstance(corpo, dict)
        self.assertIn("ok", corpo)

    def test_b_post_aplicar_esta_registrada(self):
        status, corpo = self.cliente.pedir(
            "POST", "/api/precificador/promocoes/aplicar", {"mlb": "MLB1", "preco": 10})
        self.assertNotEqual(status, 404, "a rota POST não está registrada no do_POST")
        self.assertIsInstance(corpo, dict)
        self.assertIn("ok", corpo)


class SemMercadoLivreConectado(unittest.TestCase):
    """V18.25: sem ML conectado a listagem levantava KeyError('seller_id') e a
    tela mostrava "Não foi possível carregar: 'seller_id'". Tem de ser um erro
    que explique o que fazer."""

    def setUp(self):
        # o cache de anúncios é de módulo: sem limpar, outro teste mascara este
        S._ML_ANUNCIOS_CACHE["ids"] = []
        S._ML_ANUNCIOS_CACHE["quando"] = 0.0

    def test_a_mensagem_clara(self):
        with self.assertRaises(RuntimeError) as c:
            S.ml_itens_do_vendedor(0, 10)
        msg = str(c.exception)
        self.assertIn("não está conectado", msg)
        self.assertNotIn("seller_id", msg)


class DataVoltaParaAPlanilha(unittest.TestCase):
    """V18.26: há DUAS fontes de data — a API do ML e a base da planilha que o
    monitor de promoções usa. Se aplicar a promoção não atualizar a planilha, o
    anúncio continua aparecendo como VENCIDO lá, e alguém corrige na mão."""

    def setUp(self):
        base = {"rows": [
            {"mlb": "1059999471", "date": "2026-09-18", "manualZero": False, "priceOk": False},
            {"mlb": "1050401594", "date": "2026-08-30", "manualZero": True, "priceOk": False},
        ], "deleted": [], "updatedAt": ""}
        atual, _, _ = S.get_storage(S.PROMO_DATAS_KEY)
        S.save_storage(S.PROMO_DATAS_KEY, atual, json.dumps(base), False,
                       actor="teste", actor_role="admin")

    def _linhas(self):
        raw, _, _ = S.get_storage(S.PROMO_DATAS_KEY)
        return {str(x.get("mlb")): x for x in json.loads(raw or "{}").get("rows", [])}

    def test_a_atualiza_a_data_do_anuncio(self):
        # aceita com e sem o prefixo MLB, e com data ISO completa
        self.assertTrue(S.promo_atualizar_data_planilha("MLB1059999471", "2026-10-23T00:00:00", "teste"))
        self.assertEqual(self._linhas()["1059999471"]["date"], "2026-10-23")

    def test_b_nao_mexe_nos_outros(self):
        S.promo_atualizar_data_planilha("1059999471", "2026-10-23", "teste")
        outro = self._linhas()["1050401594"]
        self.assertEqual(outro["date"], "2026-08-30")
        self.assertTrue(outro["manualZero"], "a marcação de estoque zerado foi perdida")

    def test_c_anuncio_novo_entra_na_base(self):
        self.assertTrue(S.promo_atualizar_data_planilha("MLB777888999", "2026-11-05", "teste"))
        self.assertEqual(self._linhas()["777888999"]["date"], "2026-11-05")

    def test_d_data_invalida_nao_grava(self):
        for ruim in ("", "amanhã", "05/11/2026", None):
            self.assertFalse(S.promo_atualizar_data_planilha("MLB1059999471", ruim, "teste"))
        self.assertEqual(self._linhas()["1059999471"]["date"], "2026-09-18")


class SkuDoAnuncioVemDoAtributoCerto(unittest.TestCase):
    """V18.27: o anúncio MLB1061351533 tem seller_custom_field='Id Aleph:
    46347830' (campo livre, lixo) e o SKU de verdade no atributo SELLER_SKU
    ('1304-6008.004.206.00.6'). A tela mostrava o Id Aleph. O atributo manda."""

    def _extrai(self, corpo):
        sku = ""
        for attr in corpo.get("attributes") or []:
            if str(attr.get("id") or "").upper() == "SELLER_SKU":
                sku = str(attr.get("value_name") or attr.get("value_id") or "").strip()
                break
        if not sku:
            sku = str(corpo.get("seller_custom_field") or "").strip()
        return sku

    def test_a_atributo_tem_prioridade(self):
        corpo = {"seller_custom_field": "Id Aleph: 46347830",
                 "attributes": [{"id": "SELLER_SKU", "name": "SKU", "value_name": "1304-6008.004.206.00.6"}]}
        self.assertEqual(self._extrai(corpo), "1304-6008.004.206.00.6")

    def test_b_sem_atributo_usa_o_campo_livre(self):
        corpo = {"seller_custom_field": "CAM1019", "attributes": []}
        self.assertEqual(self._extrai(corpo), "CAM1019")

    def test_c_o_sku_e_o_que_vem_depois_do_traco(self):
        conhecidos = {"6008.004.206.00.6": {}, "CAM1019": {}, "816.6090-0": {}}
        # regra do dono: o código é o que vem depois do traço
        self.assertEqual(S.ml_sku_do_anuncio("1304-6008.004.206.00.6", conhecidos), "6008.004.206.00.6")
        self.assertEqual(S.ml_sku_do_anuncio("157507-0893556005", conhecidos), "0893556005")
        # sem traço, é o próprio
        self.assertEqual(S.ml_sku_do_anuncio("CAM1019", conhecidos), "CAM1019")

    def test_d_codigo_que_ja_tem_traco_nao_pode_ser_cortado(self):
        # 816.6090-0 é um código do Apollo; cortar cegamente viraria "0"
        conhecidos = {"816.6090-0": {}}
        self.assertEqual(S.ml_sku_do_anuncio("816.6090-0", conhecidos), "816.6090-0")
        self.assertEqual(S.ml_sku_do_anuncio("816.6090-0", {}), "816.6090-0")
        # com prefixo numérico na frente, corta só o prefixo
        self.assertEqual(S.ml_sku_do_anuncio("1304-816.6090-0", conhecidos), "816.6090-0")

    def test_e_funciona_sem_o_produto_cadastrado(self):
        # mesmo fora do Precificador, o dono precisa VER o SKU real para agir
        self.assertEqual(S.ml_sku_do_anuncio("1304-6008.004.206.00.6", {}), "6008.004.206.00.6")

    def test_f_texto_livre_fica_como_esta(self):
        # "Id Aleph: 46347830" nao tem traco: nao vira codigo, mas aparece
        self.assertEqual(S.ml_sku_do_anuncio("ID ALEPH: 46347830", {}), "ID ALEPH: 46347830")


class MargemAlvoEPorProduto(unittest.TestCase):
    """V18.28: a tela classificava "margem cheia" contra 15% fixo, mas cada
    produto tem a sua margem desejada no Precificador (o MANIPULO usa 20%).
    O ideal tem de sair da margem do próprio produto."""

    def test_a_ideal_respeita_a_margem_do_produto(self):
        prod20 = dict(PROD, margem_alvo=0.20, custo=23.71, taxa_fixa=7.75)
        ideal = S.promo_preco_ideal(prod20)
        self.assertAlmostEqual(ideal, 56.18, places=1)          # bate com a tela do Precificador
        self.assertAlmostEqual(S.promo_conta(prod20, ideal)["margem"], 0.20, places=3)
        self.assertAlmostEqual(S.promo_conta(prod20, ideal)["lucro"], 11.24, places=1)

    def test_b_minimo_nao_depende_da_margem(self):
        a = dict(PROD, margem_alvo=0.15, custo=23.71, taxa_fixa=7.75)
        b = dict(a, margem_alvo=0.30)
        self.assertEqual(S.promo_preco_minimo(a), S.promo_preco_minimo(b))
        self.assertAlmostEqual(S.promo_preco_minimo(a), 41.39, places=1)


class ListaTodosOsAnuncios(unittest.TestCase):
    """V18.28: a tela mostrava menos anúncios do que existem. Duas causas:
    `status=active` escondia 380 pausados (1174 de 1591) e a paginação por
    offset estoura em ~1000 ("Invalid limit and offset values"). A listagem
    passou a usar search_type=scan, que não tem esse teto."""

    def setUp(self):
        S._ML_ANUNCIOS_CACHE["ids"] = []
        S._ML_ANUNCIOS_CACHE["quando"] = 0.0

    def test_a_usa_scan_e_nao_filtra_por_status(self):
        chamadas = []

        def falso_get(url, **kw):
            chamadas.append(url)
            if "scroll_id" in url:
                return {"results": [], "scroll_id": ""}
            return {"results": ["MLB1", "MLB2"], "scroll_id": "abc"}

        original_get, original_row = S.ml_api_get, S.ml_integration_row
        S.ml_api_get = falso_get
        S.ml_integration_row = lambda: {"seller_id": "224625106"}
        try:
            ids = S.ml_todos_os_anuncios(forcar=True)
        finally:
            S.ml_api_get, S.ml_integration_row = original_get, original_row
        self.assertEqual(ids, ["MLB1", "MLB2"])
        self.assertTrue(chamadas, "nenhuma chamada foi feita")
        self.assertIn("search_type=scan", chamadas[0])
        for url in chamadas:
            self.assertNotIn("status=", url, "o filtro de status esconde os pausados")

    def test_b_pagina_alem_do_teto_de_offset(self):
        # com 1.500 anúncios, offset 1.200 tem de funcionar (pelo offset da API
        # daria HTTP 400)
        S._ML_ANUNCIOS_CACHE["ids"] = ["MLB%d" % i for i in range(1500)]
        S._ML_ANUNCIOS_CACHE["quando"] = 9e18   # cache "sempre válido" no teste
        pag = S.ml_itens_do_vendedor(1200, 40)
        self.assertEqual(pag["total"], 1500)
        self.assertEqual(len(pag["mlbs"]), 40)
        self.assertEqual(pag["mlbs"][0], "MLB1200")

    def test_c_ultima_pagina_nao_estoura(self):
        S._ML_ANUNCIOS_CACHE["ids"] = ["MLB%d" % i for i in range(1010)]
        S._ML_ANUNCIOS_CACHE["quando"] = 9e18
        pag = S.ml_itens_do_vendedor(1000, 40)
        self.assertEqual(len(pag["mlbs"]), 10)


class EmbutirSoDoProprioSistema(unittest.TestCase):
    """V18.29: para a tela de Promoções abrir o Precificador dentro do painel,
    o X-Frame-Options saiu de DENY para SAMEORIGIN. Isso NÃO pode virar
    permissão geral: site externo continua proibido de embutir o Noromak."""

    def test_a_cabecalhos_permitem_so_a_propria_origem(self):
        import inspect
        fonte = inspect.getsource(S.AppHandler.send_security_headers)
        self.assertIn('"X-Frame-Options", "SAMEORIGIN"', fonte)
        self.assertNotIn('"X-Frame-Options", "DENY"', fonte)
        # frame-ancestors é o que os navegadores modernos obedecem
        self.assertIn("frame-ancestors 'self'", fonte)
        self.assertNotIn("frame-ancestors *", fonte)


class BuscaEmTodosOsAnuncios(unittest.TestCase):
    """V18.40: a busca da tela filtrava só os 40 anúncios da página aberta.
    O MLB4490267444 existia, mas estava em outra página — "0 de 38". E os
    anúncios encerrados ocupavam lugar na página e davam erro 400 do ML."""

    def setUp(self):
        S._ML_ANUNCIOS_CACHE["ids"] = ["MLB%d" % i for i in range(1000, 1600)] + ["MLB4490267444", "MLB5264557600"]
        S._ML_ANUNCIOS_CACHE["quando"] = 9e18
        self.original = S.ml_indice_anuncios
        indice = {m: {"titulo": "Produto %s" % m, "sku_ml": "", "status": "active"}
                  for m in S._ML_ANUNCIOS_CACHE["ids"]}
        indice["MLB4490267444"] = {"titulo": "Silicone Neutro De Alta Temperatura",
                                   "sku_ml": "152188-0893321600", "status": "active"}
        indice["MLB5264557600"] = {"titulo": "Anúncio encerrado", "sku_ml": "", "status": "closed"}
        S.ml_indice_anuncios = lambda mlbs, forcar=False: indice

    def tearDown(self):
        S.ml_indice_anuncios = self.original
        S._ML_ANUNCIOS_CACHE["ids"] = []
        S._ML_ANUNCIOS_CACHE["quando"] = 0.0

    def test_a_acha_pelo_numero_fora_da_primeira_pagina(self):
        for q in ("4490267444", "MLB4490267444", "mlb 4490267444"):
            pag = S.ml_itens_do_vendedor(0, 40, busca=q)
            self.assertEqual(pag["mlbs"], ["MLB4490267444"], q)
            self.assertEqual(pag["total"], 1)

    def test_b_acha_pelo_sku_e_pelo_titulo_sem_acento(self):
        self.assertEqual(S.ml_itens_do_vendedor(0, 40, busca="0893321600")["mlbs"], ["MLB4490267444"])
        self.assertEqual(S.ml_itens_do_vendedor(0, 40, busca="silicone temperatura")["mlbs"], ["MLB4490267444"])

    def test_c_encerrado_fica_na_lista_mas_no_fim(self):
        # V18.41: estoque zerado/encerrado também tem de aparecer — só vai pro fim
        pag = S.ml_itens_do_vendedor(0, 40)
        self.assertEqual(pag["total"], 602)
        self.assertNotIn("MLB5264557600", pag["mlbs"])
        self.assertEqual(S.ml_itens_do_vendedor(600, 40)["mlbs"][-1], "MLB5264557600")
        self.assertEqual(S.ml_itens_do_vendedor(0, 40, busca="5264557600")["mlbs"], ["MLB5264557600"])

    def test_d_sem_indice_nao_some_anuncio(self):
        def quebra(mlbs, forcar=False):
            raise RuntimeError("ML fora do ar")
        S.ml_indice_anuncios = quebra
        self.assertEqual(S.ml_itens_do_vendedor(0, 40)["total"], 602)

    def test_e_codigo_curto_procura_no_sku_e_nao_no_numero_do_mlb(self):
        # V18.40: Schulz = códigos que começam com 816. "816" no meio do número
        # do MLB não é Schulz e não pode aparecer.
        S._ML_ANUNCIOS_CACHE["ids"] += ["MLB7000000001", "MLB7000000002", "MLB8160000003"]
        indice = S.ml_indice_anuncios([])
        indice["MLB7000000001"] = {"titulo": "Filtro compressor", "sku_ml": "1304-816.6090-0", "status": "active"}
        indice["MLB7000000002"] = {"titulo": "Peça", "sku_ml": "6008.816.206", "status": "active"}
        indice["MLB8160000003"] = {"titulo": "Outra marca", "sku_ml": "1304-123.456", "status": "active"}
        achados = S.ml_itens_do_vendedor(0, 40, busca="816")["mlbs"]
        self.assertEqual(achados[0], "MLB7000000001", "quem começa com 816 vem primeiro")
        self.assertIn("MLB7000000002", achados)
        self.assertNotIn("MLB8160000003", achados)
        self.assertEqual(S.ml_itens_do_vendedor(0, 40, busca="816.6090")["mlbs"], ["MLB7000000001"])


class SemEstoqueApareceNaTabela(unittest.TestCase):
    """V18.41: anúncio com estoque zerado, pausado ou encerrado sumia da tabela
    (virava só uma linha no aviso vermelho). Tem de aparecer, com o motivo."""

    def setUp(self):
        self.orig = (S.ml_itens_detalhe, S.ml_promocoes_do_item)
        det = {
            "MLB1": {"titulo": "Ativo", "preco": 50, "sku_ml": "", "status": "active", "sub_status": [], "estoque": 3},
            "MLB2": {"titulo": "Zerado", "preco": 60, "sku_ml": "", "status": "paused",
                     "sub_status": ["out_of_stock"], "estoque": 0},
            "MLB3": {"titulo": "Encerrado", "preco": 70, "sku_ml": "", "status": "closed", "sub_status": [], "estoque": 0},
        }
        self.chamados = []

        def promos(mlb):
            self.chamados.append(mlb)
            if mlb == "MLB2":
                raise RuntimeError("Mercado Livre respondeu HTTP 400: item paused")
            return []
        S.ml_itens_detalhe = lambda mlbs: {m: det[m] for m in mlbs if m in det}
        S.ml_promocoes_do_item = promos

    def tearDown(self):
        S.ml_itens_detalhe, S.ml_promocoes_do_item = self.orig

    def test_a_todos_aparecem_com_o_motivo(self):
        r = S.promocoes_painel(["MLB1", "MLB2", "MLB3"])
        por = {x["mlb"]: x for x in r["itens"]}
        self.assertEqual(sorted(por), ["MLB1", "MLB2", "MLB3"])
        self.assertEqual(r["erros"], [])
        self.assertEqual(por["MLB2"]["estoque_ml"], 0)
        self.assertEqual(por["MLB2"]["preco_cheio"], 60)
        self.assertTrue(por["MLB2"]["erro_promo"])
        self.assertEqual(por["MLB3"]["status_ml"], "closed")
        self.assertNotIn("MLB3", self.chamados, "encerrado nem deve ser consultado (o ML dá 400)")

    def test_b_ativo_com_erro_continua_no_aviso(self):
        S.ml_promocoes_do_item = lambda mlb: (_ for _ in ()).throw(RuntimeError("HTTP 500"))
        r = S.promocoes_painel(["MLB1"])
        self.assertEqual(r["itens"], [])
        self.assertEqual(r["erros"][0]["mlb"], "MLB1")


class PromocaoEmVigorDeQualquerTipo(unittest.TestCase):
    """V18.42: o MLB1061351533 estava no ML "R$ 78,99 em promoção a R$ 54,22",
    mas a tela dizia "sem promoção" — ela só olhava o desconto próprio
    (PRICE_DISCOUNT). A promoção em vigor era de outro tipo."""

    PROMOS = [
        {"type": "PRICE_DISCOUNT", "status": "candidate", "original_price": 78.99,
         "suggested_discounted_price": 70.0, "min_discounted_price": 22.36, "max_discounted_price": 75.0},
        {"type": "SMART", "status": "started", "price": 54.22, "original_price": 78.99,
         "start_date": "2026-09-20T00:00:00", "finish_date": "2026-10-20T00:00:00", "name": "Campanha ML"},
        {"type": "BANK", "status": "started", "start_date": "2026-09-01T00:00:00"},
    ]

    def setUp(self):
        self.orig = (S.ml_itens_detalhe, S.ml_promocoes_do_item)
        S.ml_itens_detalhe = lambda mlbs: {m: {"titulo": "Manopla", "preco": 78.99, "sku_ml": "",
                                               "status": "active", "sub_status": [], "estoque": 1} for m in mlbs}
        S.ml_promocoes_do_item = lambda mlb: [dict(x) for x in self.PROMOS]

    def tearDown(self):
        S.ml_itens_detalhe, S.ml_promocoes_do_item = self.orig

    def test_a_escolhe_a_que_esta_valendo(self):
        v = S.ml_promo_vigente(self.PROMOS)
        self.assertEqual(v["type"], "SMART", "BANK (Pix) não muda o preço; candidata não está valendo")
        self.assertIsNone(S.ml_promo_vigente([self.PROMOS[0]]))

    def test_b_propria_tem_preferencia(self):
        propria = {"type": "PRICE_DISCOUNT", "status": "started", "price": 60.0}
        self.assertEqual(S.ml_promo_vigente(self.PROMOS + [propria])["type"], "PRICE_DISCOUNT")

    def test_c_a_tela_mostra_a_promocao_em_vigor(self):
        it = S.promocoes_painel(["MLB1061351533"])["itens"][0]
        self.assertEqual(it["status"], "started")
        self.assertEqual(it["preco_promo"], 54.22)
        self.assertEqual(it["fim"], "2026-10-20T00:00:00")
        self.assertEqual(it["promo_tipo"], "SMART")
        self.assertEqual(it["promo_tipo_nome"], "campanha do ML")
        # piso/teto do ML continuam vindo do desconto próprio (o que o Noromak aplica)
        self.assertEqual(it["piso_ml"], 22.36)


class KitTemCustoNasPromocoes(unittest.TestCase):
    """V18.80: a tela Promoções lia _precificador_produtos_ml(), que PULA os kits — anúncio
    de kit aparecia sempre "Sem custo". E kit sem cadastro (KIT2-PA18, KIT-ACP126/0893764272:
    66 anúncios da lista de promoções em 08/10) não abria em componentes em nenhuma tela."""

    ML = {"taxaFixa": 0, "comissao": 0.12, "frete": 0, "margem": 0.2}

    def setUp(self):
        self.orig = (S.ml_itens_detalhe, S.ml_promocoes_do_item, S._ml_vinculos_mlb_sku)
        self.det = {}
        S.ml_itens_detalhe = lambda mlbs: {m: self.det[m] for m in mlbs if m in self.det}
        S.ml_promocoes_do_item = lambda mlb: []
        S._ml_vinculos_mlb_sku = lambda: {}
        self._storage("precificador_online_v1", {"products": [
            {"sku": "PA18", "produto": "Peça A", "custo": 10.0, "imposto": 0.12, "marketplaces": {"ml": self.ML}},
            {"sku": "0893764272", "produto": "Peça B", "custo": 4.0, "imposto": 0.12, "marketplaces": {"ml": self.ML}},
            {"sku": "KITP", "produto": "Kit do Precificador", "custo": 1.0, "imposto": 0.12, "isKit": True,
             "kitItems": [{"sku": "PA18", "quantidade": 3}], "marketplaces": {"ml": self.ML}},
        ]})
        self._storage("conf_kits", [])

    def tearDown(self):
        S.ml_itens_detalhe, S.ml_promocoes_do_item, S._ml_vinculos_mlb_sku = self.orig

    def _storage(self, chave, valor):
        atual, _, _ = S.get_storage(chave)
        S.save_storage(chave, atual, S.json_dumps(valor), False, actor="teste", actor_role="admin")

    def _anuncio(self, mlb, sku):
        self.det[mlb] = {"titulo": "Kit", "preco": 99.0, "sku_ml": sku, "status": "active",
                         "sub_status": [], "estoque": 5}
        return S.promocoes_painel([mlb])["itens"][0]

    def test_a_kit_do_precificador_tem_custo_na_tela(self):
        it = self._anuncio("MLB1", "KITP")
        self.assertTrue(it["tem_custo"])
        self.assertAlmostEqual(it["precificador"]["custo"], 30.0)   # 3 x 10, custo de hoje

    def test_b_kit_pelo_prefixo_kitn(self):
        it = self._anuncio("MLB2", "155230-KIT2-PA18")
        self.assertTrue(it["tem_custo"])
        self.assertAlmostEqual(it["precificador"]["custo"], 20.0)

    def test_c_kit_a_barra_b_com_prefixo_kit(self):
        it = self._anuncio("MLB3", "KIT-PA18/0893764272")
        self.assertTrue(it["tem_custo"])
        self.assertAlmostEqual(it["precificador"]["custo"], 14.0)

    def test_d_componente_sem_custo_fica_sem_custo(self):
        self.assertFalse(self._anuncio("MLB4", "KIT-PA18/NAOEXISTE")["tem_custo"])
        self.assertFalse(self._anuncio("MLB5", "155230-PSD820/1")["tem_custo"])

    def test_e_kit_como_rotulo_nao_vira_quantidade(self):
        # V18.19: "KIT27200155" e "KIT10256081 - 10256080" são rótulo, não "N unidades de"
        self.assertIsNone(S._promo_kit_pelo_sku("KIT27200155", S._promo_produtos()))
        self.assertIsNone(S._promo_kit_pelo_sku("KIT10256081 - 10256080", S._promo_produtos()))

    def test_f_renovacao_e_aplicar_usam_o_mesmo_kit(self):
        produtos = S._promo_produtos()
        sku = S._promo_resolver_sku("MLB9", "KIT2-PA18", produtos, {})
        self.assertEqual(sku, "KIT2-PA18")
        # ml_promocao_aplicar recebe o sku e recalcula com um dict novo
        self.assertAlmostEqual(S._promo_produto(S._promo_produtos(), sku)["custo"], 20.0)

    def _kit_como_produto(self, sku, custo, comissao=0.165):
        atual, _, _ = S.get_storage("precificador_online_v1")
        dados = json.loads(atual)
        dados["products"].append({"sku": sku, "produto": "Kit no Precificador", "custo": custo, "imposto": 0.12,
                                  "marketplaces": {"ml": dict(self.ML, comissao=comissao)}})
        self._storage("precificador_online_v1", dados)

    def test_g_custo_do_kit_no_precificador_manda(self):
        # kit cadastrado no Precificador como produto comum, com custo digitado: vale ESSE custo
        self._kit_como_produto("KIT2-PA18", 50.0)
        it = self._anuncio("MLB10", "KIT2-PA18")
        self.assertAlmostEqual(it["precificador"]["custo"], 50.0)

    def test_h_kit_do_precificador_com_custo_zero_soma_as_pecas(self):
        # decisão do dono (08/10): custo 0 no Precificador = soma das peças, com as taxas DO KIT
        self._kit_como_produto("KIT2-PA18", 0, comissao=0.165)
        it = self._anuncio("MLB11", "KIT2-PA18")
        self.assertTrue(it["tem_custo"])
        self.assertAlmostEqual(it["precificador"]["custo"], 20.0)
        self.assertAlmostEqual(it["precificador"]["comissao"], 0.165)
        produtos = S._promo_produtos()
        self.assertAlmostEqual(S._promo_produto(produtos, S._promo_resolver_sku("MLB11", "KIT2-PA18", produtos, {}))["custo"], 20.0)
