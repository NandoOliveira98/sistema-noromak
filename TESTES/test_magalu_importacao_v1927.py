"""V19.27 — Magalu, fases 1b e 2: pedidos na fila e etiqueta automática.

A importação começa LIGADA (decisão do dono, 14/10), como nos outros marketplaces. Barreiras iguais às da Shopee: exclusão do ADMIN e pedido concluído nunca
voltam, pedido que já veio pela extensão não duplica, kit vira componentes, edição manual fica.
"""

import json
from unittest import mock

from base import S, TesteNoromak, gravar_como_admin, linha, pedidos


def _gravar(chave: str, valor) -> None:
    atual, _, _ = S.get_storage(chave)
    S.save_storage(chave, atual, json.dumps(valor), False, actor="admin_teste", actor_role="admin",
                   source_screen="Teste", change_method="Teste automatizado", client_ip="127.0.0.1")


def _item(sku, qtd, iid=None):
    return {"info": {"sku": sku, "name": "Peça " + sku, "id": iid or "i-" + sku}, "quantity": qtd}


def _pedido(code, status_entrega="approved", itens=None, nf=""):
    entrega = {"code": code + "-1", "id": "D-" + code, "status": status_entrega,
               "items": itens or [_item("AAA", 1)],
               "shipping": {"handling_time": {"limit_date": "2026-10-16T00:00:00Z"}}}
    if nf:
        entrega["invoices"] = [{"key": nf, "status": {"id": "approved"}}]
    return {"code": code, "id": "O-" + code, "status": "approved", "channel": {"id": "CANAL"},
            "purchased_at": "2026-10-14T12:00:00+00:00", "customer": {"name": "Cliente " + code},
            "deliveries": [entrega]}


class MagaluImportacao(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        self.api = []
        p = mock.patch.object(S, "magalu_pedidos_abertos", side_effect=lambda: list(self.api))
        p.start()
        self.addCleanup(p.stop)
        self.etiquetas = mock.patch.object(S, "_magalu_baixar_etiqueta", return_value="^XA^FDMAGALU^FS^XZ")
        self.baixar = self.etiquetas.start()
        self.addCleanup(self.etiquetas.stop)
        S.magalu_importacao_definir(True, "admin_teste")

    def linhas(self, pedido):
        return [r for r in pedidos() if r.get("Pedido") == pedido]

    def importar(self):
        return S.magalu_importar_pedidos()

    def test_comeca_ligada(self):
        with S.DB_LOCK:  # instalação nova: sem nada gravado
            S.DB.execute("DELETE FROM magalu_config_v1927")
            S.DB.commit()
        self.assertTrue(S.magalu_importacao_ligada(), "decisão do dono: igual aos outros marketplaces")
        st, corpo = self.cliente.pedir("GET", "/api/integracoes/magalu/status")
        self.assertTrue(corpo["magalu"]["importacao_ligada"])

    def test_pedido_entra_na_fila_como_magalu(self):
        self.api = [_pedido("1111")]
        self.importar()
        (l,) = self.linhas("1111")
        self.assertEqual(l["Marketplace"], "MAGALU")
        self.assertEqual(l["OrigemCadastro"], "API_MAGALU")
        self.assertEqual((l["SKU"], l["Quantidade"]), ("AAA", "1"))
        self.assertEqual(l["Etiqueta API Magalu"], "AGUARDANDO NF")
        self.baixar.assert_not_called()

    def test_reimportar_nao_duplica_e_mantem_edicao_manual(self):
        self.api = [_pedido("1111")]
        self.importar()
        rows = pedidos()
        rows[0]["Nome Conferência"] = "MARIA DA SILVA"
        gravar_como_admin(rows)
        self.importar()
        (l,) = self.linhas("1111")
        self.assertEqual(l["Nome Conferência"], "MARIA DA SILVA")

    def test_kit_vira_componentes(self):
        _gravar("conf_kits", [{"sku_kit": "KIT-AB", "nome": "Kit", "componentes": [
            {"sku": "AAA", "quantidade": 1}, {"sku": "BBB", "quantidade": 2}]}])
        self.api = [_pedido("2222", itens=[_item("KIT-AB", 2)])]
        self.importar()
        self.assertEqual({(l["SKU"], l["Quantidade"]) for l in self.linhas("2222")}, {("AAA", "2"), ("BBB", "4")})

    def test_exclusao_do_admin_nao_volta(self):
        self.api = [_pedido("3333")]
        self.importar()
        self.excluir_admin("3333", marketplace="MAGALU")
        self.importar()
        self.assertPedidoAusente("3333", "REGRESSÃO: pedido excluído pelo ADMIN voltou pela API da Magalu")

    def test_linha_excluida_pelo_admin_nao_volta(self):
        self.api = [_pedido("3434", itens=[_item("AAA", 1), _item("BBB", 1)])]
        self.importar()
        self.salvar_admin([r for r in pedidos() if r.get("SKU") != "BBB"], permitir_remocoes=True)
        self.importar()
        self.assertEqual({l["SKU"] for l in self.linhas("3434")}, {"AAA"})

    def test_concluido_nao_volta(self):
        self.api = [_pedido("4444")]
        self.importar()
        st, c = self.cliente.pedir("POST", "/api/order-complete", {"pedido": "4444"})
        self.assertEqual(st, 200, c)
        self.importar()
        self.assertPedidoAusente("4444", "REGRESSÃO: pedido concluído voltou pela API da Magalu")

    def test_pedido_da_extensao_nao_duplica(self):
        gravar_como_admin([linha("5555", "AAA", Marketplace="MAGALU", **{"Nome Conferência": "JOAO"})])
        self.api = [_pedido("5555")]
        self.importar()
        (l,) = self.linhas("5555")
        self.assertEqual(l["Nome Conferência"], "JOAO")
        self.assertNotEqual(l.get("OrigemCadastro"), "API_MAGALU")

    def test_enviado_sai_da_fila(self):
        self.api = [_pedido("6666")]
        self.importar()
        self.api = [_pedido("6666", status_entrega="shipped")]
        self.importar()
        self.assertPedidoAusente("6666", "entrega já enviada continua na fila")

    def test_falha_da_api_nao_apaga_a_fila(self):
        self.api = [_pedido("7777")]
        self.importar()
        with mock.patch.object(S, "magalu_pedidos_abertos", side_effect=RuntimeError("timeout")):
            with self.assertRaises(RuntimeError):
                self.importar()
        self.assertPedidoPresente("7777", "falha da API apagou a fila")

    def test_desligada_nao_importa(self):
        S.magalu_importacao_definir(False, "admin_teste")
        self.api = [_pedido("8888")]
        r = S.magalu_rodada()
        self.assertFalse(r.get("importou"))
        self.assertPedidoAusente("8888", "importou com a chave desligada")

    # ---- fase 2: etiqueta ----
    def test_faturado_baixa_etiqueta_e_vincula(self):
        self.api = [_pedido("9999", status_entrega="invoiced", nf="3526")]
        self.importar()
        self.baixar.assert_called_once()
        corpo = self.baixar.call_args.args[0]
        self.assertEqual(corpo["deliveries"], [{"id": "D-9999"}])
        self.assertEqual(corpo["channel"], {"id": "CANAL"})
        self.assertEqual(corpo["label"]["format"], "zpl")
        bank = json.loads(S.get_storage("conf_etiquetas")[0])
        recs = [r for r in bank["registros"].values() if r.get("marketplace") == "magalu"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["pedido"], "9999")
        self.assertIn("MAGALU", recs[0]["envio"])
        (l,) = self.linhas("9999")
        self.assertEqual(l["Etiqueta API Magalu"], "VINCULADA")
        self.importar()
        self.baixar.assert_called_once()  # não baixa de novo

    def test_etiqueta_com_o_canal_do_vendedor_v1929(self):
        # V19.29: produção 15/10, pedido 1578270107775283 — o pedido real NÃO traz channel.id
        # (só marketplace.document/alias); o canal vazio deu 404 DELIVERY_NOT_FOUND. O canal é o
        # id do vendedor da entrega sem "GENPUB." (como no exemplo da doc) e a filial vai junto.
        p = _pedido("1578270107775283", status_entrega="invoiced", nf="3526")
        p["channel"] = {"marketplace": {"document": "47960950108836"}, "extras": {"alias": "Magazine Luiza"}}
        p["deliveries"][0]["seller"] = {"id": "GENPUB.0087c9e2-69b2-4709-9926-c7285e236e54", "name": "noromak"}
        p["deliveries"][0]["branch"] = {"external_id": "1", "name": "noromak-CD1", "uuid": "858e1c0f"}
        self.api = [p]
        self.importar()
        corpo = self.baixar.call_args_list[0].args[0]
        self.assertEqual(corpo["channel"], {"id": "0087c9e2-69b2-4709-9926-c7285e236e54"})
        self.assertEqual(corpo["deliveries"], [{"id": "D-1578270107775283",
                                                "branch": {"external_id": "1", "name": "noromak-CD1"}}])
        self.assertEqual(self.linhas("1578270107775283")[0]["Etiqueta API Magalu"], "VINCULADA")

    def test_404_tenta_as_outras_formas_do_canal_v1929(self):
        p = _pedido("1212121212121212", status_entrega="invoiced")
        p["channel"] = {}
        p["deliveries"][0]["seller"] = {"id": "GENPUB.abc"}
        p["deliveries"][0]["branch"] = {"external_id": "1", "name": "CD1"}
        self.api = [p]
        self.baixar.side_effect = [S.MagaluNaoEncontrado("404"), S.MagaluNaoEncontrado("404"), "^XA^FDOK^FS^XZ"]
        self.importar()
        canais = [c.args[0]["channel"] for c in self.baixar.call_args_list]
        self.assertEqual(canais, [{"id": "abc"}, {"id": "abc"}, {"id": "GENPUB.abc"}])
        self.assertNotIn("branch", self.baixar.call_args_list[1].args[0]["deliveries"][0], "2ª tentativa sem a filial")
        self.assertEqual(self.linhas("1212121212121212")[0]["Etiqueta API Magalu"], "VINCULADA")

    def test_outro_erro_nao_repete_v1929(self):
        p = _pedido("3434343434343434", status_entrega="invoiced")
        p["deliveries"][0]["seller"] = {"id": "GENPUB.abc"}
        self.api = [p]
        self.baixar.side_effect = RuntimeError("Magalu etiqueta HTTP 422")
        self.importar()
        self.assertEqual(self.baixar.call_count, 1)

    def test_erro_na_etiqueta_nao_impede_o_pedido(self):
        self.baixar.side_effect = RuntimeError("422")
        self.api = [_pedido("9898", status_entrega="invoiced")]
        self.importar()
        (l,) = self.linhas("9898")
        self.assertEqual(l["Etiqueta API Magalu"], "AGUARDANDO")

    def test_rotas(self):
        st, c = self.cliente.pedir("POST", "/api/integracoes/magalu/importacao", {"ligada": False})
        self.assertEqual(st, 200, c)
        self.assertFalse(S.magalu_importacao_ligada())
        self.api = [_pedido("1212")]
        st, c = self.cliente.pedir("POST", "/api/integracoes/magalu/sincronizar", {})
        self.assertEqual(st, 409, "sincronizar com a importação desligada não pode gravar")
        S.magalu_importacao_definir(True, "admin_teste")
        st, c = self.cliente.pedir("POST", "/api/integracoes/magalu/sincronizar", {})
        self.assertEqual(st, 200, c)
        self.assertPedidoPresente("1212", "sincronizar não importou")

    def test_worker_no_painel_de_saude(self):
        self.assertIn("magalu_pedidos_loop", S.SAUDE_WORKERS_INFO)


if __name__ == "__main__":
    import unittest
    unittest.main()
