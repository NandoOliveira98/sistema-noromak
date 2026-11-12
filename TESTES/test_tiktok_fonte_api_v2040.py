"""V20.40 — TikTok Shop pronto para a API (decisões do dono, 07/11).

- Uma fonte de cada vez: "extensao" (padrão) ou "api", escolhida pelo Admin no cartão. Com "api" a extensão
  do Seller Center é recusada (o estoque dela grava por diferença; as duas juntas mandariam em dobro). Virar para
  a API só conectado e com a loja lida.
- Com a fonte "api", o atendimento no Apollo (V20.33) recebe da API preço, endereço e nome/CPF da nota.
- Os anúncios do TikTok de Bauru aparecem na plataforma de anúncios ligados à ficha; o estoque continua com a
  tela Estoque TikTok (controle "tiktok").
"""

import json
import time
from unittest import mock

from base import S, TesteNoromak


def _conectar(loja=True):
    agora = int(time.time())
    S._tiktok_gravar_token({"code": 0, "data": {"access_token": "AT", "refresh_token": "RT",
                                                "access_token_expire_in": agora + 86400,
                                                "refresh_token_expire_in": agora + 86400 * 30}}, conectando=True)
    if loja:
        with S.DB_LOCK:
            S.DB.execute("UPDATE tiktok_integration SET shop_cipher='CIPHER' WHERE id=1")
            S.DB.commit()


def _line(numero, i, sku_id="SK1", sku="0893600022", cheio="119.90", loja="45.10", tiktok="5.05", status="AWAITING_SHIPMENT"):
    return {"id": f"{numero}-{i}", "sku_id": sku_id, "seller_sku": sku, "product_name": "Desengripante",
            "display_status": status, "original_price": cheio, "sale_price": "69.75",
            "seller_discount": loja, "platform_discount": tiktok}


def _pedido(numero, linhas=None, **extra):
    p = {"id": numero, "status": "AWAITING_SHIPMENT", "create_time": 1760000000, "rts_sla_time": 1760090000,
         "packages": [{"id": f"PK{numero}"}], "line_items": linhas if linhas is not None else [_line(numero, 0)],
         "recipient_address": {"name": "Maria Souza", "address_line1": "Rua das Flores", "address_line2": "120",
                               "address_line3": "Apto 3", "postal_code": "17012-000",
                               "district_info": [{"address_level": "L0", "address_name": "Brasil"},
                                                 {"address_level": "L1", "address_name": "SP"},
                                                 {"address_level": "L2", "address_name": "Bauru"},
                                                 {"address_level": "L3", "address_name": "Centro"}]},
         "cpf": "123.456.789-09", "cpf_name": "Maria Souza"}
    p.update(extra)
    return p


class Base(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        for nome, valor in (("TIKTOK_APP_KEY", "K"), ("TIKTOK_APP_SECRET", "S"), ("TIKTOK_SERVICE_ID", "X")):
            p = mock.patch.object(S, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        self.chamadas = []

    def _api(self, pedidos=(), relidos=None):
        teste = self

        def falso(metodo, path, params=None, body=None, loja=True):
            teste.chamadas.append((metodo, path, params, body))
            if path == "/order/202309/orders/search":
                st = (body or {}).get("order_status")
                return {"code": 0, "data": {"orders": [o for o in pedidos if o.get("status") == st]}}
            if path == "/order/202309/orders":
                if relidos is None:
                    raise RuntimeError("sem resposta")
                return {"code": 0, "data": {"orders": relidos}}
            return {"code": 0, "data": {}}
        return mock.patch.object(S, "tiktok_api", side_effect=falso)

    def _sc(self, pedido):
        with S.DB_LOCK:
            r = S.DB.execute("SELECT * FROM tiktok_sc_pedido_v2030 WHERE pedido=?", (pedido,)).fetchone()
        return dict(r) if r else None

    def _hub(self, sku_id):
        with S.DB_LOCK:
            r = S.DB.execute("SELECT * FROM anuncio_canal_v2023 WHERE canal='tiktok' AND conta='bauru' AND anuncio_id=?",
                             (sku_id,)).fetchone()
        return dict(r) if r else None


class Fonte(Base):

    def test_comeca_na_extensao(self):
        self.assertEqual(S.tiktok_fonte(), "extensao")
        self.assertEqual(S.tiktok_status_payload()["fonte"], "extensao")

    def test_api_so_conectado_e_com_a_loja_lida(self):
        with self.assertRaises(ValueError):
            S.tiktok_fonte_definir("api", "dono")
        _conectar(loja=False)
        with self.assertRaises(ValueError):
            S.tiktok_fonte_definir("api", "dono")
        _conectar()
        self.assertEqual(S.tiktok_fonte_definir("api", "dono"), "api")
        self.assertEqual(S.tiktok_fonte_definir("extensao", "dono"), "extensao")
        with self.assertRaises(ValueError):
            S.tiktok_fonte_definir("as duas", "dono")

    def test_com_a_api_a_extensao_e_recusada(self):
        _conectar()
        S.tiktok_fonte_definir("api", "dono")
        with self.assertRaises(S.TikTokFonteApi):
            S.tiktok_produtos_da_extensao([{"product_id": "P", "skus": [{"id": "S1"}]}], completo=False)
        with self.assertRaises(S.TikTokFonteApi):
            S.tiktok_extensao_iniciar("dono")
        with self.assertRaises(S.TikTokFonteApi):
            S.tiktok_pedidos_da_extensao([], [], completo=False, ator="dono")

    def test_com_a_extensao_a_api_nao_importa_nem_le_anuncios(self):
        _conectar()
        with self._api([_pedido("TT1")]):
            r = S.tiktok_rodada()
            ev = mock.Mock()
            ev.is_set.side_effect = [False, True]
            with mock.patch.object(S, "crm_stock_lookup") as apollo:
                S.tiktok_estoque_loop(ev)
        self.assertFalse(r["importou"])
        self.assertFalse([c for c in self.chamadas if c[1].startswith("/product/")], "catálogo/estoque só com a API")
        apollo.assert_not_called()

    def test_com_a_api_importa(self):
        _conectar()
        S.tiktok_fonte_definir("api", "dono")
        with self._api([_pedido("TT1")]):
            r = S.tiktok_rodada()
        self.assertTrue(r["importou"])
        bruto, _, _ = S.get_storage("conf_pedidos")
        self.assertTrue([l for l in S._rows_from_storage_value(bruto) if l.get("Pedido") == "TT1"])


class AtendimentoPelaApi(Base):

    def setUp(self) -> None:
        super().setUp()
        _conectar()
        S.tiktok_fonte_definir("api", "dono")

    def test_preco_endereco_e_cpf_vem_da_api(self):
        with self._api([_pedido("TT1", [_line("TT1", 0), _line("TT1", 1)])]):
            r = S.tiktok_importar_pedidos()
        self.assertEqual(r["atendimento"]["pedidos"], 1)
        g = S._tiktok_sc_guardado("TT1")
        self.assertEqual(len(g["venda"]["itens"]), 1)
        item = g["venda"]["itens"][0]
        self.assertEqual(item["qtd"], 2, "um line_item por unidade")
        self.assertAlmostEqual(item["valor"], 2 * 74.80, places=2, msg="cheio − desconto da loja; subsídio fora")
        self.assertEqual(g["endereco"]["bairro"], "Centro")
        self.assertIn("Rua das Flores, 120, Apto 3, Bauru, SP, 17012000", g["endereco"]["texto"])
        self.assertEqual(self._sc("TT1")["status"], "AWAITING_SHIPMENT")
        conhecido = S._exp_cliente_do_pedido("TT1")
        self.assertEqual(conhecido["documento"], "12345678909")
        self.assertIn("TT1", S._tiktok_atendimento_candidatos(), "a fila da API vira candidata ao atendimento")

    def test_unidade_cancelada_fica_fora_e_preco_ausente_e_erro(self):
        linhas = [_line("TT2", 0), _line("TT2", 1, status="CANCELLED")]
        with self._api([_pedido("TT2", linhas), _pedido("TT3", [_line("TT3", 0, cheio="")])]):
            S.tiktok_importar_pedidos()
        self.assertEqual(S._tiktok_sc_guardado("TT2")["venda"]["itens"][0]["qtd"], 1)
        self.assertIn("erro", S._tiktok_sc_guardado("TT3")["venda"], "preço que não veio nunca é adivinhado")

    def test_endereco_mascarado_nao_vale_e_cpf_alheio_nao_e_trocado(self):
        p = _pedido("TT4")
        p["recipient_address"]["address_line1"] = "R*** das F***"
        sem_cpf = {**p, "cpf": "", "cpf_name": ""}
        with self._api([sem_cpf]):
            S.tiktok_importar_pedidos()  # o pedido entra na fila sem CPF
        self.assertTrue(S.salvar_cliente_fiscal("TT4", "52998224725", "Outro Nome", fonte="admin", usuario="dono")["ok"])
        with self._api([p]):
            S.tiktok_importar_pedidos()  # agora a API traz outro CPF: o que já estava guardado fica
        self.assertIsNone(S._tiktok_sc_guardado("TT4")["endereco"])
        self.assertEqual(S._exp_cliente_do_pedido("TT4")["documento"], "52998224725")

    def test_pedido_que_sumiu_e_relido(self):
        with self._api([_pedido("TT5"), _pedido("TT6")]):
            S.tiktok_importar_pedidos()
        with self._api([], relidos=[{"id": "TT5", "status": "CANCELLED"}]):
            S.tiktok_importar_pedidos()
        self.assertEqual(self._sc("TT5")["status"], "CANCELLED")
        self.assertTrue(self._sc("TT5")["saiu_epoch"])
        self.assertEqual(self._sc("TT6")["status"], "AWAITING_SHIPMENT", "não veio na releitura = fica como está")
        self.assertFalse(self._sc("TT6")["saiu_epoch"])

    def test_releitura_sem_resposta_nao_derruba_e_nao_marca_saida(self):
        with self._api([_pedido("TT7")]):
            S.tiktok_importar_pedidos()
        with self._api([], relidos=None):
            r = S.tiktok_importar_pedidos()
        self.assertTrue(r["ok"])
        self.assertFalse(self._sc("TT7")["saiu_epoch"])


class Anuncios(Base):

    def _variacoes(self):
        S._tiktok_sku_gravar({
            "V1": {"product_id": "P1", "titulo": "Desengripante 300ml", "status": "ACTIVATE", "seller_sku": "0893600022",
                   "warehouse_id": "W", "armazens": 1, "qtd": 7},
            "V2": {"product_id": "P2", "titulo": "Kit", "status": "SELLER_DEACTIVATED", "seller_sku": "151490-KIT2",
                   "warehouse_id": "W", "armazens": 1, "qtd": None},
        }, apagar_ausentes=False)

    def test_variacao_vira_anuncio_ligado_a_ficha(self):
        self._variacoes()
        a = self._hub("V1")
        self.assertEqual(a["ficha_codigo"], "0893600022")
        self.assertEqual(a["controle"], "tiktok")
        self.assertEqual(a["estoque"], 7)
        self.assertEqual(a["status"], "active")
        self.assertEqual(a["user_product_id"], "P1")
        self.assertEqual(self._hub("V2")["status"], "paused")
        self.assertIsNone(self._hub("V2")["estoque"], "quantidade que não veio fica vazia, nunca zero")
        ficha = S.anuncio_ficha_payload("0893600022")
        self.assertEqual([x["canal"] for x in ficha["anuncios"]], ["tiktok"])
        lista = S.anuncios_lista_payload()
        item = next(f for f in lista["fichas"] if f["codigo"] == "0893600022")
        self.assertEqual(item["controles"], ["tiktok"])

    def test_vinculo_a_mao_troca_a_ficha(self):
        self._variacoes()
        S.tiktok_vinculo_definir("V2", "2011004", "dono")
        self.assertEqual(self._hub("V2")["ficha_codigo"], "2011004")
        S.tiktok_vinculo_definir("V2", "", "dono")
        # V20.59: sem o prefixo de anúncio "151490-" (o mesmo código da separação e do estoque)
        self.assertEqual(self._hub("V2")["ficha_codigo"], "KIT2")

    def test_removido_pelo_admin_nao_volta_e_controle_nao_muda(self):
        self._variacoes()
        S._anuncio_marcar_removido("tiktok", "bauru", "V1", "dono", True)
        self._variacoes()
        self.assertTrue(self._hub("V1")["removido_em"])
        with self.assertRaises(ValueError):
            S.anuncio_controle_definir("tiktok", "bauru", "V2", "noromak", "dono", confirmou_alephee=True)
        self.assertEqual(self._hub("V2")["controle"], "tiktok")

    def test_boot_espelha_o_que_ja_existe(self):
        self._variacoes()
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM anuncio_canal_v2023 WHERE canal='tiktok'")
            S.DB.commit()
        S.tiktok_anuncios_espelhar_boot()
        self.assertIsNotNone(self._hub("V1"))


class RevisaoAntesDeSubir(Base):
    """Achados do revisar-antes-de-subir da V20.40 (07/11, "corrigir todos")."""

    def setUp(self) -> None:
        super().setUp()
        _conectar()
        S.tiktok_fonte_definir("api", "dono")

    def _fila(self):
        bruto, _, _ = S.get_storage("conf_pedidos")
        return S._rows_from_storage_value(bruto)

    def _pedidos(self):
        return {l.get("Pedido") for l in self._fila()}

    def _bipar(self, pedido, n=1):
        linhas = self._fila()
        for l in linhas:
            if l.get("Pedido") == pedido:
                l["Lido"] = str(n)
        S.save_storage("conf_pedidos", None, json.dumps(linhas), True, actor="teste", actor_role="admin")

    # --- ALTO: saída só com o TikTok confirmando
    def test_lista_vazia_nao_tira_ninguem(self):
        with self._api([_pedido("TT1"), _pedido("TT2")]):
            S.tiktok_importar_pedidos()
        with self._api([], relidos=[]):
            S.tiktok_importar_pedidos()
        self.assertEqual({"TT1", "TT2"} & self._pedidos(), {"TT1", "TT2"})

    def test_cancelado_confirmado_com_bip_fica_com_aviso(self):
        with self._api([_pedido("TT1")]):
            S.tiktok_importar_pedidos()
        self._bipar("TT1")
        with self._api([], relidos=[{"id": "TT1", "status": "CANCELLED"}]):
            S.tiktok_importar_pedidos()
        linha = next(l for l in self._fila() if l.get("Pedido") == "TT1")
        self.assertIn("TikTok:", linha.get("Observação", ""), "peça já bipada não some calada")

    def test_parcialmente_enviado_fica_so_com_o_que_falta(self):
        linhas = [_line("TT1", 0), _line("TT1", 1, status="IN_TRANSIT")]
        with self._api([_pedido("TT1", linhas, status="PARTIALLY_SHIPPING")]):
            S.tiktok_importar_pedidos()
        linha = next(l for l in self._fila() if l.get("Pedido") == "TT1")
        self.assertEqual(linha["Quantidade"], "1", "a unidade já enviada não volta a ser separada")
        self.assertIn("PARTIALLY_SHIPPING", S.TIKTOK_PEDIDOS_STATUS)

    def test_fila_sem_mudanca_nao_e_regravada(self):
        with self._api([_pedido("TT1")]):
            S.tiktok_importar_pedidos()
            _, v1, _ = S.get_storage("conf_pedidos")
            S.tiktok_importar_pedidos()
            _, v2, _ = S.get_storage("conf_pedidos")
        self.assertEqual(v1, v2, "a versão da fila só sobe com mudança real")

    # --- ALTO: amostra sem dado pessoal
    def test_amostra_da_api_mascara_o_comprador(self):
        resp = {"code": 0, "data": {"orders": [_pedido("TT9")]}}
        with mock.patch.object(S, "_tiktok_http", return_value=resp), \
                mock.patch.object(S, "tiktok_access_token", return_value="AT"):
            S.tiktok_api("GET", "/order/202309/orders", params={"ids": "TT9"})
        with S.DB_LOCK:
            txt = S.DB.execute("SELECT resposta FROM tiktok_amostra_v1963 WHERE tipo='GET /order/202309/orders'").fetchone()[0]
        for dado in ("123.456.789-09", "Maria Souza", "Rua das Flores", "17012-000"):
            self.assertNotIn(dado, txt)
        self.assertIn('"cpf"', txt, "as chaves continuam para conferir o formato")

    def test_amostra_uma_por_rota_de_produto_e_categoria(self):
        # revisar-antes-de-subir (V20.41): /products/<id> e /categories/<id> não viram uma linha por produto
        with mock.patch.object(S, "_tiktok_http", return_value={"code": 0, "data": {}}),                 mock.patch.object(S, "tiktok_access_token", return_value="AT"):
            for pid in ("111", "222"):
                S.tiktok_api("GET", f"/product/202309/products/{pid}")
                S.tiktok_api("GET", f"/product/202309/categories/{pid}/attributes")
        with S.DB_LOCK:
            tipos = [r[0] for r in S.DB.execute("SELECT tipo FROM tiktok_amostra_v1963 WHERE tipo LIKE 'GET /product/%'")]
        self.assertEqual(sorted(tipos), ["GET /product/202309/categories/{id}/attributes",
                                         "GET /product/202309/products/{id}"])

    # --- MÉDIOS
    def test_virar_para_api_volta_o_estoque_a_simulacao(self):
        S.tiktok_fonte_definir("extensao", "dono")
        S.tiktok_estoque_config_definir("modo_estoque", "automatico", "dono")
        S.tiktok_fonte_definir("api", "dono")
        self.assertEqual(S.tiktok_estoque_config()["modo_estoque"], "simulacao")

    def test_rodada_da_extensao_aberta_segura_a_api(self):
        S.tiktok_fonte_definir("extensao", "dono")
        S.TIKTOK_RODADA.update(id="R1", desde=time.time(), por="dono")
        self.addCleanup(S.TIKTOK_RODADA.clear)
        with self.assertRaises(S.TikTokRodadaOcupada):
            S.tiktok_fonte_definir("api", "dono")
        S.tiktok_estoque_config_definir("modo_estoque", "lote", "dono")
        with self._api():
            r = S.tiktok_estoque_enviar("dono")
        self.assertEqual(r["enviados"], 0)
        self.assertIn("aguardando", r)

    def test_catalogo_vazio_ou_cortado_nao_apaga(self):
        S._tiktok_sku_gravar({f"V{i}": {"product_id": "P", "titulo": "T", "status": "ACTIVATE", "seller_sku": f"S{i}",
                                        "warehouse_id": "W", "armazens": 1, "qtd": 1} for i in range(20)},
                             apagar_ausentes=False)
        respostas = [{"code": 0, "data": {"products": []}},
                     {"code": 0, "data": {"products": [{"id": "P", "status": "ACTIVATE", "skus": [
                         {"id": "V0", "seller_sku": "S0", "inventory": [{"warehouse_id": "W", "quantity": 1}]}]}]}}]
        for resp in respostas:
            with mock.patch.object(S, "tiktok_api", return_value=resp):
                S.tiktok_produtos_sincronizar()
            self.assertEqual(len(S._tiktok_linhas_sku()), 20)

    def test_endereco_sem_numero_claro_nao_vale(self):
        p = _pedido("TT1")
        p["recipient_address"]["address_line2"] = "Apto 12"
        self.assertIsNone(S._tiktok_api_endereco(p))
        p["recipient_address"]["address_line2"] = ""
        self.assertIsNone(S._tiktok_api_endereco(p))

    def test_endereco_pelo_nome_do_nivel(self):
        p = _pedido("TT1")
        p["recipient_address"]["district_info"] = [
            {"address_level": "L1", "address_level_name": "City", "address_name": "Bauru"},
            {"address_level": "L2", "address_level_name": "State", "address_name": "SP"},
            {"address_level": "L3", "address_level_name": "District", "address_name": "Centro"}]
        e = S._tiktok_api_endereco(p)
        self.assertIn("Bauru, SP, 17012000", e["texto"])

    def test_erro_de_preco_nao_apaga_o_preco_bom(self):
        with self._api([_pedido("TT1")]):
            S.tiktok_importar_pedidos()
        with self._api([_pedido("TT1", [_line("TT1", 0, cheio="")])]):
            S.tiktok_importar_pedidos()
        self.assertNotIn("erro", S._tiktok_sc_guardado("TT1")["venda"])

    # --- BAIXOS
    def test_com_a_api_a_extensao_nem_le(self):
        self.assertFalse(S.tiktok_sc_estado()["importacao"])
        p = S.tiktok_extensao_pendentes()
        self.assertFalse(p["pode_enviar"])
        self.assertEqual(p["itens"], [])

    def test_saude_ignora_aviso_antigo_da_extensao(self):
        S._tiktok_config_gravar("pedidos_desconhecidos", json.dumps({"codigos": ["999"], "pedidos": ["X"]}), "x")
        S._tiktok_config_gravar("pedidos_api_epoch", str(int(time.time())), "x")
        self.assertEqual(S._saude_tiktok_pedidos()["nivel"], "ok")

    def test_estoque_enviado_aparece_na_plataforma(self):
        S._tiktok_sku_gravar({"V1": {"product_id": "P1", "titulo": "T", "status": "ACTIVATE", "seller_sku": "0893600022",
                                     "warehouse_id": "W", "armazens": 1, "qtd": 2}}, apagar_ausentes=False)
        with S.DB_LOCK:
            S._tiktok_anuncio_estoque("V1", 9)
            S.DB.commit()
        self.assertEqual(self._hub("V1")["estoque"], 9)

    def test_atualizar_roda_em_segundo_plano_e_enviar_nao_espera_o_worker(self):
        with mock.patch.object(S, "tiktok_produtos_sincronizar"):
            st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/atualizar", {})
        self.assertEqual(st, 200, corpo)
        self.assertTrue(corpo.get("iniciado"))
        S.tiktok_estoque_config_definir("modo_estoque", "lote", "dono")
        S.TIKTOK_ESTOQUE_LOCK.acquire()
        try:
            with self.assertRaises(S.TikTokEstoqueOcupado):
                S.tiktok_estoque_enviar("dono", espera=0.1)
        finally:
            S.TIKTOK_ESTOQUE_LOCK.release()


class SegundaRevisao(Base):
    """2ª revisão (só das correções) da V20.40."""

    def setUp(self) -> None:
        super().setUp()
        _conectar()
        S.tiktok_fonte_definir("api", "dono")

    def _parcial(self):
        enviado = _line("TT1", 0, status="IN_TRANSIT")
        enviado["package_id"] = "PKA"
        aberto = _line("TT1", 1)
        aberto["package_id"] = "PKB"
        return _pedido("TT1", [enviado, aberto], status="PARTIALLY_SHIPPING",
                       packages=[{"id": "PKA"}, {"id": "PKB"}])

    def test_parcial_usa_o_pacote_que_falta_enviar(self):
        with self._api([self._parcial()]):
            S.tiktok_importar_pedidos()
        bruto, _, _ = S.get_storage("conf_pedidos")
        linha = next(l for l in S._rows_from_storage_value(bruto) if l.get("Pedido") == "TT1")
        self.assertEqual(linha["PacoteTikTok"], "PKB", "o pacote já despachado nunca vai para a etiqueta")
        self.assertEqual(S._tiktok_pacotes(S._tiktok_api_pacote_aberto_primeiro(self._parcial()))[0], "PKB")

    def test_parcial_preco_so_do_que_falta_enviar(self):
        venda = S._tiktok_api_venda(self._parcial())
        self.assertEqual(venda["itens"][0]["qtd"], 1, "preço e fila contam as mesmas unidades (senão vai a revisar)")

    def test_trocar_a_fonte_nao_espera_o_worker(self):
        ocupada = mock.Mock()
        ocupada.acquire.return_value = False  # o worker segurando a trava além do prazo
        with mock.patch.object(S, "TIKTOK_ESTOQUE_LOCK", ocupada):
            with self.assertRaises(S.TikTokRodadaOcupada):
                S.tiktok_fonte_definir("extensao", "dono")
        self.assertEqual(ocupada.acquire.call_args.kwargs.get("timeout"), 20)
        self.assertEqual(S.tiktok_fonte(), "api")


class Rotas(Base):

    def test_fonte_pela_rota(self):
        st, corpo = self.cliente.pedir("POST", "/api/integracoes/tiktok/fonte", {"fonte": "api"})
        self.assertEqual(st, 400, corpo)
        _conectar()
        st, corpo = self.cliente.pedir("POST", "/api/integracoes/tiktok/fonte", {"fonte": "api"})
        self.assertEqual(st, 200, corpo)
        self.assertEqual(S.tiktok_fonte(), "api")
        st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/extensao/produtos",
                                       {"produtos": [{"product_id": "P", "skus": [{"id": "S1"}]}], "completo": False})
        self.assertEqual(st, 409, corpo)
        st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/extensao/iniciar", {})
        self.assertEqual(st, 409, corpo)

    def test_com_a_extensao_sincronizar_e_enviar_pela_api_sao_recusados(self):
        _conectar()
        for rota in ("/api/integracoes/tiktok/sincronizar", "/api/tiktok-estoque/enviar", "/api/tiktok-estoque/atualizar"):
            st, corpo = self.cliente.pedir("POST", rota, {})
            self.assertEqual(st, 409, (rota, corpo))

    def test_saude_segue_a_fonte(self):
        _conectar()
        S.tiktok_fonte_definir("api", "dono")
        self.assertIn("A API do TikTok ainda não leu", json.dumps(S._saude_tiktok_pedidos(), ensure_ascii=False))
        S._tiktok_config_gravar("pedidos_api_epoch", str(int(time.time())), "x")
        self.assertEqual(S._saude_tiktok_pedidos()["nivel"], "ok")

    def test_telas(self):
        admin = (S.APP_DIR / "admin.html").read_text(encoding="utf-8")
        self.assertIn('id="btnTiktokFonte"', admin)
        self.assertIn("/expedicao/api/integracoes/tiktok/fonte", admin)
        anuncios = (S.APP_DIR / "anuncios.html").read_text(encoding="utf-8")
        self.assertIn('tiktok: ["Extensão do TikTok"', anuncios)  # V20.58: a tela Estoque TikTok saiu do menu


if __name__ == "__main__":
    import unittest
    unittest.main()
