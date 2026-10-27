"""V19.88 — Devoluções (decisões do dono, 27/10).

Operador (módulo conferencia, tela /devolucoes): bipa a peça ou digita o cliente, acha o pedido
CONCLUÍDO, confere com a venda, marca bom/ruim (ruim com motivo) e conclui. Admin (/devolucoes-admin):
trata no Apollo e marca "tratada" com o nº do documento; corrige com histórico; só ele reabre.
O que não pode quebrar:
- registrar devolução nunca reabre o pedido (completed_orders e a fila ficam iguais);
- nunca volta mais do que foi vendido, somando todas as devoluções do pedido;
- filial: o operador de Bauru não vê nem registra devolução de Araçatuba;
- a busca não varre o histórico inteiro a cada bip (índice próprio, preenchido aos poucos).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from base import S, SENHA_ADMIN, Cliente, TesteNoromak, gravar_como_admin, linha, pedidos


def concluir(pedido: str, linhas: list[dict], quando: str | None = None, usuario: str = "admin") -> None:
    """Conclui como a rota /api/order-complete faz: completed_orders + histórico permanente."""
    quando = quando or S.now_iso()
    with S.DB_LOCK:
        for tabela in ("completed_orders", "order_completion_history"):
            S.DB.execute(f"INSERT OR REPLACE INTO {tabela}(order_key,order_id,completed_at,completed_by,payload) "
                         "VALUES(?,?,?,?,?)", (S.normalize(pedido), pedido, quando, usuario, json.dumps(linhas)))
        S.DB.commit()


class Devolucoes(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        S._DEVOLUCAO_EAN_CACHE.clear()
        concluir("P100", [linha("P100", "AAA", 2, Cliente="José da Silva", **{"Código de Barras": "7890001"},
                                Descrição="Parafuso"),
                          linha("P100", "BBB/CCC", 1, Cliente="José da Silva", Marketplace="SHOPEE")])
        concluir("P200", [linha("P200", "DDD", 1, Cliente="Maria Souza", Casa="bauru",
                                Marketplace="MERCADO LIVRE")])

    def novo_operador(self, login: str, filial: str) -> Cliente:
        with S.DB_LOCK:
            S.upsert_app_user(login, login.title(), "operador", True, SENHA_ADMIN, "teste")
            S.DB.execute("UPDATE app_users SET casa=? WHERE username=?", (filial, login))
            S.DB.commit()
        c = Cliente(self.url_base)
        c.entrar(login, SENHA_ADMIN, area="conferencia")
        return c

    def buscar(self, q: str, filial: str = "todas") -> list[str]:
        return [p["pedido"] for p in S.devolucao_buscar(q, filial)["pedidos"]]

    def registrar(self, itens: list[dict], chave: str = "P100", filial: str = "todas", **extra) -> dict:
        return S.devolucao_registrar({"chave": chave, "itens": itens, **extra}, "joao", filial)

    # --- busca ---

    def test_acha_pelo_ean_sku_cliente_e_numero(self):
        self.assertEqual(self.buscar("7890001"), ["P100"])
        self.assertEqual(self.buscar("bbb"), ["P100"])            # peça do kit "BBB/CCC"
        self.assertEqual(self.buscar("jose silva"), ["P100"])     # sem acento, palavras soltas
        self.assertEqual(self.buscar("p200"), ["P200"])
        self.assertEqual(self.buscar("xyz"), [])

    def test_ean_do_cadastro_de_produtos_leva_ao_sku(self):
        # A linha do pedido não tinha o código de barras; o cadastro de produtos tem.
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO storage(key,value,version,updated_at) VALUES('conf_produtos',?,1,'x') "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value, version=version+1",
                         (json.dumps([{"SKU": "DDD", "Código de Barras": "7895555"}]),))
            S.DB.commit()
        self.assertEqual(self.buscar("7895555"), ["P200"])
        # A venda manda os EANs da peça para o bip da tela achar a linha.
        self.assertIn("7895555", S.devolucao_pedido("P200", "todas")["itens"][0]["eans"])

    def test_pendente_e_antigo_nao_aparecem(self):
        gravar_como_admin([linha("P300", "AAA", 1)])               # na fila, não concluído
        velho = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        concluir("P400", [linha("P400", "AAA", 1)], quando=velho)
        self.assertEqual(sorted(self.buscar("AAA")), ["P100"])

    def test_filial_do_operador(self):
        self.assertEqual(self.buscar("DDD", "aracatuba"), [])
        self.assertEqual(self.buscar("DDD", "bauru"), ["P200"])
        with self.assertRaises(PermissionError):
            self.registrar([{"sku": "DDD", "qtd": 1, "estado": "bom"}], chave="P200", filial="aracatuba")

    def test_reconcluido_nao_duplica(self):
        concluir("P100", [linha("P100", "EEE", 1)])               # reaberto e concluído de novo
        self.assertEqual(self.buscar("EEE"), ["P100"])
        self.assertEqual(self.buscar("AAA"), [])
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM devolucao_busca_v1988 WHERE order_key='P100'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_indice_por_lotes(self):
        for i in range(5):
            concluir(f"L{i}", [linha(f"L{i}", "LOTE", 1)])
        with mock.patch.object(S, "DEVOLUCAO_INDICE_LOTE", 2):
            self.assertEqual(S.devolucao_indexar(), 2)   # setUp já tinha 2; agora 2 de 5
            S.devolucao_indexar()
            S.devolucao_indexar()
        self.assertEqual(len(self.buscar("LOTE")), 5)

    def test_etiqueta_de_devolucao_do_ml(self):
        concluir("2000777", [linha("2000777", "FFF", 1, Marketplace="MERCADO LIVRE",
                                   VendaMercadoLivre="2000777001")])
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_reclamacao_v1888(claim_id,order_id,devolucao_envios) VALUES('C1','2000777001',?)",
                         (",44001122,TRK99,",))
            S.DB.commit()
        self.assertEqual(self.buscar("44001122"), ["2000777"])
        self.assertEqual(self.buscar("trk99"), ["2000777"])

    def test_posvenda_guarda_o_envio_da_devolucao_sem_chamada_a_mais(self):
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO ml_reclamacao_v1888(claim_id,order_id,status,tem_devolucao) "
                         "VALUES('C9','555','closed',1)")
            S.DB.commit()
        chamadas = []

        def api(caminho, *a, **k):
            chamadas.append(caminho)
            if caminho.endswith("/returns"):
                return {"status": "delivered", "shipments": [
                    {"type": "return", "shipment_id": 4455, "tracking_number": "BR123",
                     "destination": {"name": "seller_address"}},
                    {"type": "original", "shipment_id": 1}]}
            raise RuntimeError("HTTP 404")
        with mock.patch.object(S, "ml_api_get", side_effect=api):
            S._posvenda_detalhar("C9")
        with S.DB_LOCK:
            envios = S.DB.execute("SELECT devolucao_envios FROM ml_reclamacao_v1888 WHERE claim_id='C9'").fetchone()[0]
        self.assertEqual(envios, ",4455,BR123,")
        self.assertEqual(sum(c.endswith("/returns") for c in chamadas), 1)

    # --- venda ---

    def test_venda_abre_kit_em_pecas(self):
        v = S.devolucao_pedido("P100", "todas")
        self.assertEqual({i["sku"]: i["qtd_vendida"] for i in v["itens"]}, {"AAA": 2, "BBB": 1, "CCC": 1})
        self.assertEqual(v["cliente"], "José da Silva")

    # --- registrar ---

    def test_registrar_nao_reabre_o_pedido(self):
        antes_fila = pedidos()
        r = self.registrar([{"sku": "AAA", "qtd": 1, "estado": "bom"},
                            {"sku": "BBB", "qtd": 1, "estado": "ruim", "motivo": "avariado"}])
        self.assertTrue(r["ok"])
        with S.DB_LOCK:
            self.assertIsNotNone(S.DB.execute("SELECT 1 FROM completed_orders WHERE order_key='P100'").fetchone())
        self.assertEqual(pedidos(), antes_fila)
        d = S.devolucoes_lista({}, "todas")["devolucoes"][0]
        self.assertEqual((d["situacao"], d["recebido_por"], d["filial"]), ("a_tratar", "joao", "aracatuba"))
        self.assertEqual({i["sku"]: (i["qtd"], i["estado"]) for i in d["itens"]},
                         {"AAA": (1, "bom"), "BBB": (1, "ruim")})

    def test_ruim_exige_motivo_e_estado_valido(self):
        with self.assertRaises(ValueError):
            self.registrar([{"sku": "AAA", "qtd": 1, "estado": "ruim"}])
        with self.assertRaises(ValueError):
            self.registrar([{"sku": "AAA", "qtd": 1, "estado": "ruim", "motivo": "qualquer"}])
        with self.assertRaises(ValueError):
            self.registrar([{"sku": "AAA", "qtd": 1, "estado": "talvez"}])
        with self.assertRaises(ValueError):
            self.registrar([])

    def test_nunca_volta_mais_que_o_vendido_somando_devolucoes(self):
        with self.assertRaises(ValueError):
            self.registrar([{"sku": "AAA", "qtd": 3, "estado": "bom"}])
        self.registrar([{"sku": "AAA", "qtd": 1, "estado": "bom"}])
        self.registrar([{"sku": "AAA", "qtd": 1, "estado": "bom"}])
        with self.assertRaises(ValueError):
            self.registrar([{"sku": "AAA", "qtd": 1, "estado": "bom"}])
        v = S.devolucao_pedido("P100", "todas")
        self.assertEqual({i["sku"]: i["qtd_devolvida"] for i in v["itens"]}["AAA"], 2)

    def test_peca_fora_da_venda_entra_marcada(self):
        self.registrar([{"sku": "ZZZ", "qtd": 1, "estado": "ruim", "motivo": "veio_outra"}])
        item = S.devolucoes_lista({}, "todas")["devolucoes"][0]["itens"][0]
        self.assertTrue(item["fora_da_venda"])

    def test_sem_pedido(self):
        with self.assertRaises(ValueError):
            S.devolucao_registrar({"sem_pedido": True, "itens": [{"sku": "AAA", "qtd": 1, "estado": "bom"}]},
                                  "joao", "aracatuba")   # falta o cliente
        r = S.devolucao_registrar({"sem_pedido": True, "cliente": "Fulano",
                                   "itens": [{"sku": "AAA", "qtd": 5, "estado": "bom"}]}, "joao", "bauru")
        d = S.devolucoes_lista({}, "todas")["devolucoes"][0]
        self.assertEqual((d["id"], d["sem_pedido"], d["filial"], d["cliente"]), (r["id"], True, "bauru", "Fulano"))

    # --- admin ---

    def test_tratar_exige_documento_e_versao(self):
        r = self.registrar([{"sku": "AAA", "qtd": 1, "estado": "bom"}])
        d = S.devolucoes_lista({}, "todas")["devolucoes"][0]
        with self.assertRaises(ValueError):
            S.devolucao_tratar(r["id"], "", d["versao"], "chefe")
        with self.assertRaises(S.DevolucaoConflito):
            S.devolucao_tratar(r["id"], "NF 123", d["versao"] + 5, "chefe")
        S.devolucao_tratar(r["id"], "NF 123", d["versao"], "chefe")
        d = S.devolucoes_lista({"situacao": "tratada"}, "todas")["devolucoes"][0]
        self.assertEqual((d["documento_apollo"], d["tratada_por"]), ("NF 123", "chefe"))
        self.assertEqual(S.devolucoes_lista({"situacao": "a_tratar"}, "todas")["devolucoes"], [])
        with self.assertRaises(ValueError):    # tratada não é tratada de novo
            S.devolucao_tratar(r["id"], "NF 9", d["versao"], "chefe")
        S.devolucao_reabrir(r["id"], d["versao"], "chefe")
        self.assertEqual(S.devolucoes_lista({}, "todas")["devolucoes"][0]["situacao"], "a_tratar")

    def test_corrigir_guarda_o_original(self):
        r = self.registrar([{"sku": "AAA", "qtd": 2, "estado": "bom"}])
        d = S.devolucoes_lista({}, "todas")["devolucoes"][0]
        item_id = d["itens"][0]["id"]
        S.devolucao_corrigir(r["id"], [{"id": item_id, "qtd": 1, "estado": "ruim", "motivo": "usado"}],
                             d["versao"], "chefe")
        d = S.devolucoes_lista({}, "todas")["devolucoes"][0]
        self.assertEqual((d["itens"][0]["qtd"], d["itens"][0]["estado"], d["itens"][0]["motivo"]), (1, "ruim", "usado"))
        corr = [e for e in d["eventos"] if e["acao"] == "corrigida"][0]
        self.assertEqual(corr["quem"], "chefe")
        self.assertEqual(corr["detalhe"]["antes"][0]["qtd"], 2)
        with self.assertRaises(ValueError):   # correção também não passa do vendido
            S.devolucao_corrigir(r["id"], [{"id": item_id, "qtd": 3, "estado": "bom"}], d["versao"], "chefe")

    # --- rotas e permissões ---

    def test_rotas_operador_e_admin(self):
        op = self.novo_operador("lucas", "bauru")
        st, corpo = op.pedir("GET", "/api/devolucoes/buscar?q=DDD")
        self.assertEqual((st, [p["pedido"] for p in corpo["pedidos"]]), (200, ["P200"]))
        st, _ = op.pedir("GET", "/api/devolucoes/buscar?q=AAA")
        self.assertEqual(op.pedir("GET", "/api/devolucoes/buscar?q=AAA")[1]["pedidos"], [])
        st, corpo = op.pedir("POST", "/api/devolucoes/registrar",
                             {"chave": "P100", "itens": [{"sku": "AAA", "qtd": 1, "estado": "bom"}]})
        self.assertEqual(st, 403, corpo)
        st, corpo = op.pedir("POST", "/api/devolucoes/registrar",
                             {"chave": "P200", "itens": [{"sku": "DDD", "qtd": 1, "estado": "bom"}]})
        self.assertEqual(st, 200, corpo)
        self.assertEqual(op.pedir("GET", "/api/devolucoes")[0], 403)          # tela do admin
        self.assertEqual(op.pedir("POST", "/api/devolucoes/tratar", {"id": corpo["id"], "documento": "X",
                                                                      "versao": 1})[0], 403)
        st, lista = self.cliente.pedir("GET", "/api/devolucoes?situacao=a_tratar")
        self.assertEqual((st, len(lista["devolucoes"])), (200, 1))
        d = lista["devolucoes"][0]
        st, corpo = self.cliente.pedir("POST", "/api/devolucoes/tratar",
                                       {"id": d["id"], "documento": "NF 1", "versao": d["versao"] + 9})
        self.assertEqual(st, 409, corpo)
        st, corpo = self.cliente.pedir("POST", "/api/devolucoes/registrar", {"chave": "P100", "itens": []})
        self.assertEqual(st, 400, corpo)

    def test_telas_menu_e_worker(self):
        self.assertTrue((S.APP_DIR / "devolucoes.html").exists())
        self.assertTrue((S.APP_DIR / "devolucoes-admin.html").exists())
        menu = (S.APP_DIR / "nk-menu.js").read_text(encoding="utf-8")
        self.assertIn("/devolucoes-admin", menu)
        self.assertIn("devolucoes_indice_loop", S.SAUDE_WORKERS_INFO)
        op = self.novo_operador("lucas", "aracatuba")
        import urllib.error
        import urllib.request

        def status(rota: str) -> int:
            req = urllib.request.Request(op.url_base + rota, headers={"Cookie": op.cookie})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
        self.assertEqual(status("/devolucoes"), 200)
        self.assertEqual(status("/devolucoes-admin"), 403)


if __name__ == "__main__":
    import unittest
    unittest.main()
