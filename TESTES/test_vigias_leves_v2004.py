"""V20.04 — os vigias do ML e a sincronização da Shopee refaziam tudo sem nada mudar.

Medido em produção em 30/10/2026: ~40% de CPU 24 h, banco ocupado 39,7% do tempo, servidor
sem memória às 09:00. Causas:

- a cada 15–20 s, por conta do ML, os vigias remontavam a fila (get_storage), decodificavam
  1,4 MB de etiquetas e abriam o JSON de todos os concluídos (3.897, duas vezes por ciclo);
- a sincronização da Shopee (1 min) trocava ShopeeAtualizadoEm e CriadoEm de cada linha e
  gravava; a versão da fila subia e toda tela aberta baixava a fila inteira de novo.
"""

import time
import unittest

from base import S, TesteNoromak, gravar_como_integracao, linha


def linha_ml(venda: str, sku: str = "SKU1", **extra) -> dict:
    return linha(venda, sku, uid=f"{venda}-{sku}", Marketplace="MERCADO LIVRE",
                 VendaMercadoLivre=venda, OrigemCadastro="API Mercado Livre", **extra)


def concluir(chave: str, venda: str, em: str = "2026-10-30T10:00:00") -> None:
    with S.DB_LOCK:
        S.DB.execute(
            "INSERT INTO completed_orders(order_key,order_id,completed_at,completed_by,payload) VALUES(?,?,?,?,?) "
            "ON CONFLICT(order_key) DO UPDATE SET completed_at=excluded.completed_at,payload=excluded.payload",
            (chave, chave, em, "t", S.json_dumps([{"VendaMercadoLivre": venda}])),
        )
        S.DB.commit()


class Contador:
    """Conta quantas vezes o get_storage foi chamado para cada chave."""

    def __init__(self) -> None:
        self.original = S.get_storage
        self.chamadas: dict[str, int] = {}

    def __enter__(self):
        def contar(chave):
            self.chamadas[chave] = self.chamadas.get(chave, 0) + 1
            return self.original(chave)
        S.get_storage = contar
        return self

    def __exit__(self, *a):
        S.get_storage = self.original


class FilaDosVigias(TesteNoromak):
    def test_mesma_versao_nao_remonta_a_fila(self):
        gravar_como_integracao([linha_ml("V1")])
        S._ml_fila_leitura()
        with Contador() as c:
            for _ in range(5):
                S._ml_fila_leitura()
        self.assertEqual(c.chamadas.get("conf_pedidos", 0), 0)

    def test_fila_que_mudou_aparece_na_hora(self):
        gravar_como_integracao([linha_ml("V1")])
        self.assertEqual({r["VendaMercadoLivre"] for r in S._ml_fila_leitura()}, {"V1"})
        gravar_como_integracao([linha_ml("V1"), linha_ml("V2")])
        self.assertEqual({r["VendaMercadoLivre"] for r in S._ml_fila_leitura()}, {"V1", "V2"})

    def test_tombstone_do_admin_vale_na_hora(self):
        """A exclusão fica em outra chave: sem ela na marca, o vigia veria o pedido excluído."""
        gravar_como_integracao([linha_ml("V1")])
        S._ml_fila_leitura()
        with S.DB_LOCK:
            S.DB.execute(
                "INSERT INTO storage(key,value,version,updated_at) VALUES(?,?,1,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,version=storage.version+1",
                (S.PENDING_ORDER_DELETE_KEY_V1766, S.json_dumps([{"Pedido": "V1", "Ativo": True}]), S.now_iso()),
            )
            S.DB.commit()
        self.assertEqual(S._ml_fila_leitura(), [])

    def test_memoria_vence_sozinha(self):
        """Cache nunca é fonte de verdade: mesmo sem mudar a versão, relê depois do prazo."""
        gravar_como_integracao([linha_ml("V1")])
        S._ml_fila_leitura()
        S._ML_FILA_CACHE["em"] = time.monotonic() - S.ML_CACHE_LEITURA_S - 1
        with Contador() as c:
            S._ml_fila_leitura()
        self.assertEqual(c.chamadas.get("conf_pedidos", 0), 1)

    def test_concluido_sai_da_fila_dos_vigias(self):
        gravar_como_integracao([linha_ml("V1"), linha_ml("V2")])
        self.assertEqual(len(S._ml_fila_leitura()), 2)
        chave = S.normalize("V1")
        concluir(chave, "V1")
        # a conclusão real grava a fila junto (sobe a versão)
        atual, _, _ = S.get_storage("conf_pedidos")
        S.save_storage("conf_pedidos", atual, atual, False, actor="t", actor_role="sistema")
        self.assertEqual({r["VendaMercadoLivre"] for r in S._ml_fila_leitura()}, {"V2"})


class EtiquetasDosVigias(TesteNoromak):
    def _banco(self, *vendas: str) -> None:
        registros = {
            f"R{v}": {"origem": "api_mercadolivre", "envio": "^XA^XZ",
                      "metadados": {"venda": v, "shipment_id": f"S{v}"}}
            for v in vendas
        }
        atual, _, _ = S.get_storage("conf_etiquetas")
        S.save_storage("conf_etiquetas", atual, S.json_dumps({"registros": registros}), True,
                       actor="t", actor_role="sistema")

    def test_nao_decodifica_de_novo_sem_mudar(self):
        self._banco("V1")
        gravar_como_integracao([linha_ml("V1", **{"Etiqueta API Mercado Livre": "VINCULADA"})])
        self.assertEqual(S._ml_linked_order_ids(), {"V1"})
        with Contador() as c:
            for _ in range(5):
                self.assertEqual(S._ml_linked_order_ids(), {"V1"})
        self.assertEqual(c.chamadas.get("conf_etiquetas", 0), 0)
        self.assertEqual(c.chamadas.get("conf_pedidos", 0), 0)

    def test_etiqueta_nova_aparece_na_hora(self):
        gravar_como_integracao([linha_ml("V1", **{"Etiqueta API Mercado Livre": "VINCULADA"})])
        self.assertEqual(S._ml_linked_order_ids(), set())
        self._banco("V1")
        self.assertEqual(S._ml_linked_order_ids(), {"V1"})

    def test_quem_recebe_o_conjunto_pode_alterar(self):
        """O vigia de pedidos novos faz linked_local.add(): não pode sujar a memória."""
        self._banco("V1")
        gravar_como_integracao([linha_ml("V1", **{"Etiqueta API Mercado Livre": "VINCULADA"})])
        S._ml_linked_order_ids().add("OUTRO")
        self.assertEqual(S._ml_linked_order_ids(), {"V1"})


class ConcluidosDosVigias(TesteNoromak):
    def test_payload_aberto_uma_vez_so(self):
        concluir("K1", "V1")
        self.assertEqual(S._ml_completed_order_ids(), {"V1"})
        original = S.json.loads
        abertos = []
        S.json.loads = lambda *a, **k: (abertos.append(1), original(*a, **k))[1]
        try:
            self.assertEqual(S._ml_completed_order_ids(), {"V1"})
        finally:
            S.json.loads = original
        self.assertEqual(abertos, [])

    def test_novo_entra_e_reaberto_sai(self):
        concluir("K1", "V1")
        S._ml_completed_order_ids()
        concluir("K2", "V2")
        self.assertEqual(S._ml_completed_order_ids(), {"V1", "V2"})
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM completed_orders WHERE order_key='K1'")
            S.DB.commit()
        self.assertEqual(S._ml_completed_order_ids(), {"V2"})

    def test_mesma_chave_concluida_de_novo_troca_a_venda(self):
        concluir("K1", "V1", em="2026-10-30T10:00:00")
        S._ml_completed_order_ids()
        concluir("K1", "V9", em="2026-10-30T11:00:00")
        self.assertEqual(S._ml_completed_order_ids(), {"V9"})

    def test_existentes_junta_fila_e_concluidos(self):
        gravar_como_integracao([linha_ml("V1")])
        concluir("K2", "V2")
        self.assertEqual(S._ml_existing_order_ids(), {"V1", "V2"})


class ShopeeSemMudanca(TesteNoromak):
    def setUp(self) -> None:
        super().setUp()
        self._orig = (S.shopee_status_payload, S.shopee_current_shipments, S.shopee_order_details,
                      S._shopee_build_pending_lines, S.avisos_venda_registrar)
        S.shopee_status_payload = lambda: {"connected": True, "shop_id": "1"}
        S.shopee_current_shipments = lambda: [{"order_sn": "SP1", "package_number": "P1"}]
        S.shopee_order_details = lambda sns: [{"order_sn": sn} for sn in sns]
        S.avisos_venda_registrar = lambda *a, **k: None
        self.status = "READY"
        S._shopee_build_pending_lines = lambda order, pacotes, produtos: [self._linha_api()]

    def tearDown(self) -> None:
        (S.shopee_status_payload, S.shopee_current_shipments, S.shopee_order_details,
         S._shopee_build_pending_lines, S.avisos_venda_registrar) = self._orig
        super().tearDown()

    def _linha_api(self) -> dict:
        return linha("SP1", "SKU1", uid="SHOPEEAPI_1", Marketplace="SHOPEE", OrigemCadastro="API_SHOPEE",
                     LineItemIdShopee="1", ItemIdShopee="1", ModelIdShopee="1", StatusShopee=self.status,
                     ShopeeAtualizadoEm=S.now_iso(), CriadoEm=S.now_iso())

    def _versao(self) -> int:
        return S.get_storage("conf_pedidos")[1]

    def _uma(self) -> dict:
        return S._rows_from_storage_value(S.get_storage("conf_pedidos")[0])[0]

    def test_sincronizar_sem_mudanca_nao_sobe_a_versao(self):
        S.shopee_manual_sync()
        versao = self._versao()
        time.sleep(0.01)
        S.shopee_manual_sync()
        S.shopee_manual_sync()
        self.assertEqual(self._versao(), versao)

    def test_dado_novo_grava_e_mantem_criado_em(self):
        S.shopee_manual_sync()
        antes, versao = self._uma(), self._versao()
        time.sleep(0.01)
        self.status = "SHIPPED_PREPARANDO"
        S.shopee_manual_sync()
        depois = self._uma()
        self.assertGreater(self._versao(), versao)
        self.assertEqual(depois["StatusShopee"], "SHIPPED_PREPARANDO")
        self.assertEqual(depois["CriadoEm"], antes["CriadoEm"])
        self.assertNotEqual(depois["ShopeeAtualizadoEm"], antes["ShopeeAtualizadoEm"])

    def test_pedido_que_saiu_da_shopee_ainda_passa_pela_gravacao(self):
        """Com linha removida pelo merge não se pula o save_storage: quem decide é ele
        (a gravação automática é cumulativa, V17.44)."""
        S.shopee_manual_sync()
        S.shopee_current_shipments = lambda: []
        original, chamadas = S.save_storage, []
        S.save_storage = lambda *a, **k: (chamadas.append(1), original(*a, **k))[1]
        try:
            S.shopee_manual_sync()
        finally:
            S.save_storage = original
        self.assertEqual(len(chamadas), 1)

    def test_edicao_manual_continua_valendo(self):
        S.shopee_manual_sync()
        atual, _, _ = S.get_storage("conf_pedidos")
        rows = S._rows_from_storage_value(atual)
        rows[0]["Nome Conferência"] = "EDITADO"
        S.save_storage("conf_pedidos", atual, S.json_dumps(rows), False, actor="adm", actor_role="admin")
        S.shopee_manual_sync()
        self.assertEqual(self._uma()["Nome Conferência"], "EDITADO")


if __name__ == "__main__":
    unittest.main()
