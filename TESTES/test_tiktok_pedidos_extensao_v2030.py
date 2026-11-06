"""V20.30 — pedidos do TikTok pela extensão do Seller Center (a API exige gerente de contas).

Decisões do dono (06/11): a extensão lê a aba "Para enviar" a cada 2 min; pedido que sumiu da
aba só sai da fila com o TikTok confirmando (detalhe enviado/cancelado); leitura pela metade
nunca tira nada; o Painel de saúde fica Crítico sem leitura há 30 min no expediente.
Formato dos pedidos = o real lido no Seller Center em 06/11 (main_orders / main_order).
"""

from datetime import datetime, timedelta, timezone
from unittest import mock

from base import S, TesteNoromak

CPF = "12345678909"


def _sc(numero, status=111, itens=(("SK1", "0893600022", 1),), cpf="", cpf_nome="", nome=""):
    """Pedido no formato do Seller Center. itens = (sku_id, seller_sku, quantidade)."""
    linhas_status, skus, n = [], [], 0
    for sku_id, sku, qtd in itens:
        ids = []
        for _ in range(qtd):
            n += 1
            lid = f"{numero}L{n}"
            ids.append(lid)
            st = status if isinstance(status, int) else status.get(lid, 111)
            linhas_status.append({"order_line_id": lid, "main_order_status": 1, "main_sub_order_status": 1,
                                  "sku_display_status": st})
        skus.append({"sku_id": sku_id, "order_line_ids": ids, "seller_sku_name": sku, "product_name": "Desengripante",
                     "quantity": qtd, "sku_unit_price": {"price_val": "89.90"}})
    o = {"main_order_id": numero,
         "trade_order_module": {"create_time": "1794000000", "latest_rts_time": "1794100000"},
         "order_status_module": linhas_status, "sku_module": skus,
         "fulfill_unit_id_mapper": [{"package_id": f"PK{numero}", "order_line_id": []}],
         "buyer_info_module": {"buyer_nickname": "apelido_tt", "cpf": cpf, "cpf_name": cpf_nome}}
    if nome:
        o["buyer_info_module"]["shipping_address"] = {"items": [{"key": "name", "value": nome},
                                                                {"key": "geo_l1", "value": "SP"}]}
    return o


class Base(TesteNoromak):

    def receber(self, pedidos, detalhes=(), completo=True):
        return S.tiktok_pedidos_da_extensao(list(pedidos), list(detalhes), completo=completo, ator="dono")

    def fila(self, pedido=None):
        bruto, _, _ = S.get_storage("conf_pedidos")
        linhas = [r for r in S._rows_from_storage_value(bruto) if isinstance(r, dict)]
        return [r for r in linhas if pedido is None or r.get("Pedido") == pedido]


class Entrada(Base):

    def test_pedido_a_enviar_entra_na_fila_de_bauru(self):
        r = self.receber([_sc("580001", itens=(("SK1", "0893600022", 2),))])
        self.assertEqual(r["abertos"], 1)
        linha = self.fila("580001")[0]
        self.assertEqual((linha["Marketplace"], linha["Casa"], linha["SKU"], linha["Quantidade"], linha["OrigemCadastro"]),
                         ("TIKTOK", "bauru", "0893600022", "2", "API_TIKTOK"))
        self.assertEqual(linha["StatusTikTok"], "AWAITING_SHIPMENT")
        self.assertEqual(linha["PacoteTikTok"], "PK580001")
        self.assertTrue(linha["__uidLinha"].startswith("TIKTOKAPI_"))

    def test_mesmo_uid_da_importacao_pela_api(self):
        """Se a API for liberada um dia, as duas falam do mesmo pedido (sem duplicar)."""
        self.receber([_sc("580001")])
        api = {"id": "580001", "status": "AWAITING_SHIPMENT", "line_items": [
            {"sku_id": "SK1", "seller_sku": "0893600022", "product_name": "x"}]}
        uid_api = S._tiktok_linhas_do_pedido(api, [])[0]["__uidLinha"]
        self.assertEqual(self.fila("580001")[0]["__uidLinha"], uid_api)

    def test_ler_de_novo_nao_duplica(self):
        self.receber([_sc("580001")])
        self.receber([_sc("580001")])
        self.assertEqual(len(self.fila("580001")), 1)

    def test_linha_cancelada_nao_conta_na_quantidade(self):
        o = _sc("580002", status={"580002L1": 140, "580002L2": 111}, itens=(("SK1", "0893600022", 2),))
        self.receber([o])
        self.assertEqual(self.fila("580002")[0]["Quantidade"], "1")

    def test_situacao_desconhecida_nao_entra(self):
        self.receber([_sc("580003", status=105)])  # retido
        self.assertEqual(self.fila("580003"), [])

    def test_nome_do_detalhe_vira_cliente_e_fica(self):
        self.receber([_sc("580004")], [_sc("580004", nome="Maria Souza")])
        self.assertEqual(self.fila("580004")[0]["Cliente"], "Maria Souza")
        self.receber([_sc("580004")])  # só a lista (sem nome): o nome guardado continua
        self.assertEqual(self.fila("580004")[0]["Cliente"], "Maria Souza")

    def test_importacao_desligada_recusa(self):
        S.tiktok_importacao_definir(False, "dono")
        with self.assertRaises(S.TikTokImportacaoDesligada):
            self.receber([_sc("580005")])
        self.assertEqual(self.fila("580005"), [])

    def test_formato_invalido_e_excesso_recusados(self):
        with self.assertRaises(ValueError):
            S.tiktok_pedidos_da_extensao({"x": 1}, [], completo=True, ator="dono")
        with self.assertRaises(ValueError):
            self.receber([_sc(str(600000 + i)) for i in range(S.TIKTOK_SC_MAX_PEDIDOS + 1)])

    def test_outra_rodada_rodando_recusa_sem_gravar(self):
        S.TIKTOK_SYNC_LOCK.acquire()
        try:
            with self.assertRaises(S.TikTokRodadaOcupada):
                self.receber([_sc("580006")])
        finally:
            S.TIKTOK_SYNC_LOCK.release()
        self.assertEqual(self.fila("580006"), [])


class Saida(Base):

    def test_sumiu_da_aba_sem_confirmar_fica(self):
        self.receber([_sc("581001"), _sc("581002")])
        r = self.receber([_sc("581002")])
        self.assertEqual(len(self.fila("581001")), 1, "sem o TikTok confirmar, nada sai")
        self.assertEqual(r["mantidos_sem_confirmar"], 1)

    def test_lista_vazia_nao_tira_nada(self):
        self.receber([_sc("581001")])
        self.receber([])
        self.assertEqual(len(self.fila("581001")), 1)

    def test_enviado_confirmado_sai(self):
        self.receber([_sc("581001"), _sc("581002")])
        r = self.receber([_sc("581002")], [_sc("581001", status=121)])
        self.assertEqual(self.fila("581001"), [])
        self.assertEqual(len(self.fila("581002")), 1)
        self.assertEqual(r["saiu_confirmado"], 1)

    def test_cancelado_confirmado_sai(self):
        self.receber([_sc("581003")])
        self.receber([], [_sc("581003", status=140)])
        self.assertEqual(self.fila("581003"), [])

    def test_detalhe_ainda_a_enviar_fica(self):
        self.receber([_sc("581004")])
        self.receber([], [_sc("581004", status=112)])
        self.assertEqual(len(self.fila("581004")), 1)

    def test_linha_editada_pelo_admin_nao_sai(self):
        self.receber([_sc("581005")])
        linhas = self.fila()
        for l in linhas:
            if l.get("Pedido") == "581005":
                l["Nome Conferência"] = "NOME DO ADMIN"
        self.salvar_admin(linhas)
        self.receber([_sc("581005")])
        self.assertEqual(self.fila("581005")[0].get("Nome Conferência"), "NOME DO ADMIN")

    def test_excluido_pelo_admin_nao_volta(self):
        self.receber([_sc("581006")])
        self.excluir_admin("581006", "TIKTOK")
        self.receber([_sc("581006")])
        self.assertPedidoAusente("581006", "exclusão do ADMIN vence a extensão")

    def test_concluido_nao_volta(self):
        self.receber([_sc("581007")])
        status, corpo = self.cliente.pedir("POST", "/api/order-complete", {"pedido": "581007"})
        self.assertEqual(status, 200, corpo)
        self.receber([_sc("581007")])
        self.assertPedidoAusente("581007", "concluído não volta pela extensão")

    def test_pedido_de_outra_origem_nao_e_tocado(self):
        from base import linha, gravar_como_admin
        gravar_como_admin([linha("581008", "0893600022", Marketplace="TIKTOK")])
        self.receber([], [_sc("581008", status=121)])
        self.assertEqual(len(self.fila("581008")), 1, "pedido manual não sai pela confirmação da extensão")


class NomeCpf(Base):

    def test_cpf_e_nome_da_nota_vao_para_cliente_fiscal(self):
        r = self.receber([_sc("582001")], [_sc("582001", cpf=CPF, cpf_nome="Maria Souza", nome="Maria")])
        self.assertEqual(r["nome_cpf_guardados"], 1)
        with S.DB_LOCK:
            row = S.DB.execute("SELECT documento_numero, nome_fiscal, fonte FROM cliente_fiscal WHERE order_key=?",
                               (S.normalize("582001"),)).fetchone()
        self.assertEqual((row["documento_numero"], row["nome_fiscal"], row["fonte"]), (CPF, "Maria Souza", "tiktok_sc"))

    def test_nao_sobrescreve_o_que_ja_estava_guardado(self):
        self.receber([_sc("582002")])
        S.salvar_cliente_fiscal("582002", "98765432100", "Joao Pereira", fonte="extensao", usuario="x")
        self.receber([_sc("582002")], [_sc("582002", cpf=CPF, cpf_nome="Maria Souza")])
        with S.DB_LOCK:
            row = S.DB.execute("SELECT documento_numero FROM cliente_fiscal WHERE order_key=?",
                               (S.normalize("582002"),)).fetchone()
        self.assertEqual(row["documento_numero"], "98765432100")


class Estado(Base):

    def test_estado_lista_fila_e_detalhes(self):
        self.receber([_sc("583001"), _sc("583002")], [_sc("583001", nome="Ana Lima")])
        e = S.tiktok_sc_estado()
        self.assertEqual(set(e["na_fila"]), {"583001", "583002"})
        self.assertEqual(e["com_detalhe"], ["583001"])
        self.assertTrue(e["ultima_leitura_epoch"])

    def test_leitura_parcial_nao_marca_a_hora(self):
        self.receber([_sc("583003")], completo=False)
        self.assertEqual(S.tiktok_sc_estado()["ultima_leitura_epoch"], 0)

    def test_rotas_pelo_perfil_admin(self):
        st, corpo = self.cliente.pedir("POST", "/api/tiktok-estoque/extensao/pedidos",
                                       {"pedidos": [_sc("583004")], "detalhes": [], "completo": True})
        self.assertEqual(st, 200, corpo)
        st, corpo = self.cliente.pedir("GET", "/api/tiktok-estoque/extensao/pedidos-estado")
        self.assertEqual(st, 200, corpo)
        self.assertIn("583004", corpo["na_fila"])
        S.tiktok_importacao_definir(False, "dono")
        st, _ = self.cliente.pedir("POST", "/api/tiktok-estoque/extensao/pedidos",
                                   {"pedidos": [_sc("583005")], "detalhes": [], "completo": True})
        self.assertEqual(st, 409)


REAL_ENVIADO = {  # resposta real do Seller Center (06/11), sem os dados do comprador
    "main_order_id": "587000531450430927",
    "trade_order_module": {"main_order_id": "587000531450430927", "create_time": "1793886503",
                           "latest_rts_time": "1794020399", "need_invoice_flag": True},
    "order_status_module": [
        {"order_line_id": "587000531450561999", "main_order_status": 102, "main_sub_order_status": 305, "sku_display_status": 121},
        {"order_line_id": "587000531450496463", "main_order_status": 102, "main_sub_order_status": 305, "sku_display_status": 121}],
    "sku_module": [{"sku_id": "1738215800310301739", "order_line_ids": ["587000531450561999", "587000531450496463"],
                    "seller_sku_name": "2008005", "product_name": "Alumax Limpador Aluminio Automotivo Chassi Rodas Motor 5l Vonixx 2008005",
                    "quantity": 2}],
    "fulfill_unit_id_mapper": [{"fulfill_unit_id": "1217524654564541903",
                                "order_line_id": ["587000531450496463", "587000531450561999"], "package_id": "3394906470852756943"}],
    "buyer_info_module": {"buyer_nickname": "x", "cpf": "", "cpf_name": ""}}


class RespostaReal(Base):

    def test_enviado_real_e_lido_como_em_transito(self):
        p = S.tiktok_sc_pedido(REAL_ENVIADO)
        self.assertEqual((p["status"], p["line_items"][0]["quantity"], p["line_items"][0]["seller_sku"], p["packages"][0]["id"]),
                         ("IN_TRANSIT", 2, "2008005", "3394906470852756943"))
        self.assertTrue(S._tiktok_iso(p["create_time"]).startswith("2026-11-0"))

    def test_mesmo_pedido_a_enviar_entra_com_2_unidades(self):
        import copy
        o = copy.deepcopy(REAL_ENVIADO)
        for s in o["order_status_module"]:
            s["sku_display_status"] = 111
        self.receber([o])
        self.assertEqual(self.fila("587000531450430927")[0]["Quantidade"], "2")
        self.receber([], [REAL_ENVIADO])  # sumiu da aba e o detalhe diz enviado: sai
        self.assertEqual(self.fila("587000531450430927"), [])


class Saude(Base):

    def _epoch_br(self, ano, mes, dia, hora, minuto):
        return datetime(ano, mes, dia, hora, minuto, tzinfo=timezone(timedelta(hours=-3))).timestamp()

    def test_nunca_lido_e_info(self):
        self.assertEqual(S._saude_tiktok_pedidos()["nivel"], "info")

    def test_lido_agora_ok(self):
        self.receber([_sc("584001")])
        self.assertEqual(S._saude_tiktok_pedidos()["nivel"], "ok")

    def test_sem_leitura_no_expediente_critico_fora_atencao(self):
        quinta_10h = self._epoch_br(2026, 11, 5, 10, 0)
        quinta_0820 = self._epoch_br(2026, 11, 5, 8, 20)
        sabado_10h = self._epoch_br(2026, 11, 7, 10, 0)
        for agora, nivel in ((quinta_10h, "critico"), (quinta_0820, "atencao"), (sabado_10h, "atencao")):
            S._tiktok_config_gravar("pedidos_extensao_epoch", str(int(agora - 3600)), "x")
            self.assertEqual(S._saude_tiktok_pedidos(agora)["nivel"], nivel)
        S._tiktok_config_gravar("pedidos_extensao_epoch", str(int(quinta_10h - 600)), "x")
        self.assertEqual(S._saude_tiktok_pedidos(quinta_10h)["nivel"], "ok")

    def test_esta_no_painel(self):
        self.assertIn("tiktok_pedidos", {c["id"] for c in S.saude_checks()})


if __name__ == "__main__":
    import unittest
    unittest.main()
