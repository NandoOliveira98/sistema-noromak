"""V19.00 — gravação do ADMIN com a tela desatualizada e filial trocada em outra aba.

Achados da verificação de 11/10 (reproduzidos num banco temporário antes da correção):

1. O delta do ADMIN era calculado contra o BANCO, não contra o que a tela tinha. Um pedido
   que a integração trouxe depois de a tela carregar (e antes do polling) faltava na lista
   gravada e virava tombstone ADMIN: sumia para sempre, nem a reimportação trazia de volta.
   Pelo mesmo motivo, um campo que a integração atualizou nesse meio-tempo virava "alteração
   do ADMIN" com o valor velho da tela — e ficava congelado.

2. O seletor de filial era lido do localStorage A CADA requisição. Trocado numa aba, as
   outras abas continuavam com a fila da filial antiga e passavam a gravar com o cabeçalho
   da nova: aba montada em Bauru gravando como "Todas" apagava Araçatuba; aba montada em
   "Todas" gravando como Bauru remarcava Araçatuba e Shopee como Bauru. Acontecia também com
   o operador cuja filial o Admin mudou com a Conferência aberta.
"""

import json
import re

from base import RAIZ, S, SENHA_ADMIN, Cliente, TesteNoromak, gravar_como_integracao, linha, pedidos


def linha_ml(venda: str, sku: str, casa: str = "", **extra) -> dict:
    campos = dict(Marketplace="MERCADO LIVRE", VendaMercadoLivre=venda, ShipmentIdMercadoLivre="SHIP-" + venda,
                  OrigemCadastro="API Mercado Livre", **extra)
    if casa:
        campos.update(Casa=casa, ContaMercadoLivre=casa)
    return linha(venda, sku, uid=f"MLAPI_{venda}_{sku}", **campos)


class Base(TesteNoromak):

    def pedir(self, cliente: Cliente, metodo: str, rota: str, corpo: dict | None = None,
              cabecalhos: dict | None = None):
        import urllib.error
        import urllib.request
        dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
        req = urllib.request.Request(cliente.url_base + rota, data=dados, method=metodo)
        req.add_header("Content-Type", "application/json")
        req.add_header("Cookie", cliente.cookie)
        for k, v in (cabecalhos or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                texto = r.read().decode("utf-8")
                return r.status, (json.loads(texto) if texto.strip() else {})
        except urllib.error.HTTPError as e:
            texto = e.read().decode("utf-8")
            try:
                return e.code, json.loads(texto)
            except ValueError:
                return e.code, {"corpo": texto}

    def ler_fila(self, cliente: Cliente, filial: str = "") -> str:
        """O que a tela guarda no cache (o valor bruto que ela devolve como oldValue)."""
        cab = {"X-Noromak-Filial": filial} if filial else {}
        status, corpo = self.pedir(cliente, "GET", "/api/storage?key=conf_pedidos", cabecalhos=cab)
        self.assertEqual(status, 200, corpo)
        return corpo["value"]

    def gravar(self, cliente: Cliente, antigo: str, novo: list, *, filial: str = "", excluir: bool = False) -> dict:
        cab = {"X-Noromak-Filial": filial} if filial else {}
        status, corpo = self.pedir(cliente, "POST", "/api/storage",
                                   {"key": "conf_pedidos", "oldValue": antigo, "newValue": json.dumps(novo),
                                    "allowPendingDeletes": excluir}, cabecalhos=cab)
        self.assertEqual(status, 200, corpo)
        return corpo

    def vendas(self) -> dict[str, str]:
        """Venda → filial gravada na linha ('' = sem marca = Araçatuba)."""
        return {str(r.get("VendaMercadoLivre") or r.get("Pedido") or ""): str(r.get("Casa") or "")
                for r in pedidos()}

    def tombstones_admin(self) -> set[str]:
        with S.DB_LOCK:
            rows = S.DB.execute("SELECT entity_id FROM admin_manual_overrides WHERE storage_key='conf_pedidos' "
                                "AND field_name='__ENTITY__' AND is_deleted=1").fetchall()
        return {str(r[0]) for r in rows}


class TelaDesatualizada(Base):
    """Achado 1: o ADMIN só decide sobre o que a tela dele mostrava."""

    def setUp(self) -> None:
        super().setUp()
        gravar_como_integracao([linha_ml("A1", "SKU-A"), linha_ml("X1", "SKU-X")], origem="API Mercado Livre")

    def test_excluir_com_tela_desatualizada_nao_apaga_pedido_que_chegou_depois(self):
        cache = self.ler_fila(self.cliente)
        # O vigia do ML traz Y1 depois de a tela carregar e antes do polling (até 10 s).
        gravar_como_integracao([linha_ml("Y1", "SKU-Y")], origem="API Mercado Livre")
        novo = [r for r in json.loads(cache) if r.get("VendaMercadoLivre") != "X1"]
        self.gravar(self.cliente, cache, novo, excluir=True)
        fila = self.vendas()
        self.assertNotIn("X1", fila, "a exclusão pedida pelo ADMIN tem de valer")
        self.assertIn("Y1", fila, "REGRESSÃO: excluir X1 com a tela desatualizada apagou Y1, que a tela nem mostrava")
        self.assertEqual({t for t in self.tombstones_admin() if "Y1" in t}, set(),
                         "Y1 virou tombstone ADMIN sem o ADMIN ter visto o pedido")
        gravar_como_integracao([linha_ml("Y1", "SKU-Y")], origem="API Mercado Livre")
        self.assertIn("Y1", self.vendas())

    def test_salvar_com_tela_desatualizada_nao_apaga_pedido_que_chegou_depois(self):
        cache = self.ler_fila(self.cliente)
        gravar_como_integracao([linha_ml("Y1", "SKU-Y")], origem="API Mercado Livre")
        novo = json.loads(cache)
        novo[0]["Observacao"] = "editado no Admin"
        self.gravar(self.cliente, cache, novo)
        self.assertIn("Y1", self.vendas(), "REGRESSÃO: salvar com a tela desatualizada apagou Y1")
        self.assertEqual({t for t in self.tombstones_admin() if "Y1" in t}, set())
        self.assertEqual(next(r for r in pedidos() if r.get("VendaMercadoLivre") == "A1").get("Observacao"),
                         "editado no Admin", "a edição do ADMIN tem de valer")

    def test_campo_que_a_integracao_mudou_nao_vira_alteracao_do_admin(self):
        cache = self.ler_fila(self.cliente)
        # A integração atualiza o rastreio de A1; a tela ainda mostra o valor velho.
        atual = pedidos()
        for r in atual:
            if r.get("VendaMercadoLivre") == "A1":
                r["Rastreio"] = "BR123"
        gravar_como_integracao(atual, origem="API Mercado Livre")
        novo = json.loads(cache)
        for r in novo:
            if r.get("VendaMercadoLivre") == "X1":
                r["Observacao"] = "editado no Admin"
        self.gravar(self.cliente, cache, novo)
        a1 = next(r for r in pedidos() if r.get("VendaMercadoLivre") == "A1")
        self.assertEqual(a1.get("Rastreio"), "BR123",
                         "REGRESSÃO: o valor velho da tela apagou o rastreio que a integração trouxe")
        with S.DB_LOCK:
            congelados = S.DB.execute("SELECT COUNT(*) FROM admin_manual_overrides WHERE storage_key='conf_pedidos' "
                                      "AND field_name='Rastreio'").fetchone()[0]
        self.assertEqual(congelados, 0, "campo que o ADMIN não mexeu virou override do ADMIN")

    def test_exclusao_da_conferencia_por_admin_continua_valendo(self):
        """A Conferência monta a lista nova a partir do próprio cache: a exclusão segue igual."""
        cache = self.ler_fila(self.cliente)
        novo = [r for r in json.loads(cache) if r.get("VendaMercadoLivre") != "X1"]
        self.gravar(self.cliente, cache, novo, excluir=True)
        self.assertNotIn("X1", self.vendas())
        gravar_como_integracao([linha_ml("X1", "SKU-X")], origem="API Mercado Livre")
        self.assertNotIn("X1", self.vendas(), "exclusão do ADMIN não pode ser revertida pela integração")

    def test_sem_oldvalue_continua_comparando_com_o_banco(self):
        """Gravação sem o snapshot da tela (rotas internas antigas) mantém o comportamento de antes."""
        novo = [r for r in pedidos() if r.get("VendaMercadoLivre") != "X1"]
        status, corpo = self.pedir(self.cliente, "POST", "/api/storage",
                                   {"key": "conf_pedidos", "newValue": json.dumps(novo), "allowPendingDeletes": True})
        self.assertEqual(status, 200, corpo)
        gravar_como_integracao([linha_ml("X1", "SKU-X")], origem="API Mercado Livre")
        self.assertNotIn("X1", self.vendas())


class FilialTrocadaEmOutraAba(Base):
    """Achado 2: a cópia da tela e o cabeçalho de filial de abas diferentes."""

    def setUp(self) -> None:
        super().setUp()
        gravar_como_integracao([linha_ml("A1", "SKU-A"), linha_ml("B1", "SKU-B", casa="bauru"),
                                linha_ml("B2", "SKU-B2", casa="bauru")], origem="API Mercado Livre")
        gravar_como_integracao([linha("SHP1", "SKU-S", uid="SHP1-S")], origem="API Shopee")

    def novo_usuario(self, login: str, filial: str) -> Cliente:
        with S.DB_LOCK:
            S.upsert_app_user(login, login.title(), "operador", True, SENHA_ADMIN, "teste")
            S.DB.execute("UPDATE app_users SET casa=? WHERE username=?", (filial, login))
            S.DB.commit()
        cliente = Cliente(self.url_base)
        cliente.entrar(login, SENHA_ADMIN, area="conferencia")
        return cliente

    def test_aba_de_bauru_gravando_como_todas_nao_apaga_aracatuba(self):
        cache = self.ler_fila(self.cliente, "bauru")
        novo = [r for r in json.loads(cache) if r.get("VendaMercadoLivre") != "B2"]
        self.gravar(self.cliente, cache, novo, excluir=True)  # cabeçalho já é "Todas"
        fila = self.vendas()
        self.assertNotIn("B2", fila)
        self.assertIn("A1", fila, "REGRESSÃO: a aba de Bauru gravando como Todas apagou Araçatuba")
        self.assertIn("SHP1", fila, "REGRESSÃO: a aba de Bauru gravando como Todas apagou a Shopee")
        self.assertIn("B1", fila)

    def test_aba_de_bauru_gravando_como_aracatuba_nao_apaga_aracatuba(self):
        cache = self.ler_fila(self.cliente, "bauru")
        novo = [r for r in json.loads(cache) if r.get("VendaMercadoLivre") != "B2"]
        self.gravar(self.cliente, cache, novo, filial="aracatuba", excluir=True)
        fila = self.vendas()
        self.assertIn("A1", fila, "REGRESSÃO: a aba de Bauru gravando como Araçatuba apagou Araçatuba")
        self.assertIn("SHP1", fila)
        self.assertIn("B1", fila)
        self.assertEqual({t for t in self.tombstones_admin() if "A1" in t or "SHP1" in t}, set())

    def test_aba_em_todas_gravando_como_bauru_nao_muda_a_filial(self):
        cache = self.ler_fila(self.cliente)
        novo = json.loads(cache)
        for r in novo:
            if r.get("VendaMercadoLivre") == "B1":
                r["Observacao"] = "editado"
        self.gravar(self.cliente, cache, novo, filial="bauru")
        fila = self.vendas()
        self.assertNotEqual(fila.get("A1"), "bauru", "REGRESSÃO: pedido de Araçatuba foi remarcado como Bauru")
        self.assertNotEqual(fila.get("SHP1"), "bauru", "REGRESSÃO: pedido da Shopee foi remarcado como Bauru")
        self.assertEqual(fila.get("B1"), "bauru")
        self.assertEqual(next(r for r in pedidos() if r.get("VendaMercadoLivre") == "B1").get("Observacao"), "editado")

    def test_operador_com_filial_trocada_e_tela_aberta_nao_leva_a_fila(self):
        operador = self.novo_usuario("paulo", "aracatuba")
        cache = self.ler_fila(operador)
        self.assertEqual({r.get("VendaMercadoLivre") or r.get("Pedido") for r in json.loads(cache)}, {"A1", "SHP1"})
        # O Admin passa o Paulo para Bauru com a Conferência dele aberta.
        with S.DB_LOCK:
            S.DB.execute("UPDATE app_users SET casa='bauru' WHERE username='paulo'")
            S.DB.commit()
        novo = json.loads(cache)
        novo[0]["Conferido"] = "1"
        self.gravar(operador, cache, novo)
        fila = self.vendas()
        self.assertNotEqual(fila.get("A1"), "bauru", "REGRESSÃO: a cópia velha do operador levou A1 para Bauru")
        self.assertNotEqual(fila.get("SHP1"), "bauru")

    def test_linha_nova_de_quem_ve_bauru_continua_nascendo_em_bauru(self):
        cache = self.ler_fila(self.cliente, "bauru")
        novo = json.loads(cache) + [linha("MAN-B", "SKU-M", uid="MAN-B")]
        self.gravar(self.cliente, cache, novo, filial="bauru")
        self.assertEqual(self.vendas().get("MAN-B"), "bauru")


class FilialPorPagina(TesteNoromak):
    """Achado 2, lado do navegador: a filial é lida uma vez, quando a página abre."""

    def test_shared_storage_le_a_filial_uma_vez_por_pagina(self):
        js = (RAIZ / "APP" / "shared-storage.js").read_text(encoding="utf-8")
        self.assertIn("FILIAL_DA_PAGINA", js)
        corpo = re.search(r"function filialEscolhida\(\)\s*\{(.*?)\n  \}", js, re.S)
        self.assertIsNotNone(corpo, "filialEscolhida() sumiu do shared-storage.js")
        self.assertNotIn("localStorage", corpo.group(1),
                         "filialEscolhida() voltou a ler o localStorage a cada requisição")

    def test_menu_mostra_a_filial_da_pagina(self):
        js = (RAIZ / "APP" / "nk-menu.js").read_text(encoding="utf-8")
        self.assertIn("appStorage.filialEscolhida", js,
                      "o seletor do menu tem de mostrar a filial com que ESTA página trabalha")


if __name__ == "__main__":
    import unittest
    unittest.main()
