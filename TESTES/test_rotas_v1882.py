"""V18.82 — cada tela tem o seu endereço e o menu lateral é um só.

Antes, tudo do Admin morava em /expedicao/admin: F5 voltava para o Dashboard, não dava para
mandar o link de uma tela para alguém nem abrir duas telas lado a lado. Agora
/expedicao/pedidos-dia, /expedicao/usuarios... abrem o mesmo admin.html já na tela certa.

A lista de telas aparece em quatro lugares (servidor, menu, login e sincronização). Se um
deles ficar para trás, a tela abre como "Sistema" no shared-storage (audita errado) ou o
login trata a página como portal (o Admin não carrega). Este teste prende os quatro juntos.
"""

import re
import unittest
import urllib.error
import urllib.request

from base import RAIZ, S, TesteNoromak

APP = RAIZ / "APP"
SENHA = "Senha-de-teste-1882"


def _menu_itens() -> list[dict]:
    """Lê os itens do nk-menu.js (id, href, aba, módulo) sem precisar de um motor de JS."""
    texto = (APP / "nk-menu.js").read_text(encoding="utf-8")
    itens = []
    for bruto in re.findall(r"\{ id: \"[^\n]*?\}", texto):
        item = {}
        for chave in ("id", "href", "aba", "modulo"):
            m = re.search(chave + r': "([^"]+)"', bruto)
            if m:
                item[chave] = m.group(1)
        itens.append(item)
    return itens


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


class RotasDasTelas(TesteNoromak):

    def abrir(self, rota: str, cookie: str | None = None) -> tuple[int, str, str]:
        req = urllib.request.Request(self.url_base + rota)
        if cookie is not None:
            req.add_header("Cookie", cookie)
        abridor = urllib.request.build_opener(_SemRedirecionar)
        try:
            with abridor.open(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace"), r.headers.get("Location") or ""
        except urllib.error.HTTPError as erro:
            return erro.code, erro.read().decode("utf-8", "replace"), erro.headers.get("Location") or ""

    def test_cada_tela_do_admin_abre_o_admin_pelo_proprio_endereco(self):
        for rota in S.ROTAS_ADMIN_V1882:
            for variante in (rota, rota + "/"):
                status, corpo, _ = self.abrir(variante, self.cliente.cookie)
                self.assertEqual(status, 200, f"{variante} não abriu")
                self.assertIn("nkMenuDesenhar", corpo, f"{variante} não entregou o admin.html")

    def test_sem_login_volta_para_o_acesso(self):
        for rota in S.ROTAS_ADMIN_V1882:
            status, _, destino = self.abrir(rota, cookie=None)
            self.assertEqual(status, 302, f"{rota} abriu sem login")
            self.assertTrue(destino.endswith("/expedicao/"), destino)

    def test_operador_sem_admin_nao_abre_tela_do_admin(self):
        with S.DB_LOCK:
            S.upsert_app_user("op_1882", "Operador", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": False, "estacao": False, "precificador": False})
            S.DB.commit()
        from base import Cliente
        op = Cliente(self.url_base)
        op.entrar("op_1882", SENHA, area="conferencia")
        for rota in ("/usuarios", "/seguranca", "/pedidos-dia"):
            status, _, _ = self.abrir(rota, op.cookie)
            self.assertEqual(status, 403, f"operador sem Admin abriu {rota}")

    def test_menu_e_servidor_tem_a_mesma_lista_de_telas_do_admin(self):
        itens = _menu_itens()
        self.assertGreater(len(itens), 20, "a leitura do nk-menu.js parou de achar os itens")
        do_menu ={i["href"] for i in itens if i.get("aba")}
        self.assertEqual(do_menu, set(S.ROTAS_ADMIN_V1882),
                         "nk-menu.js e ROTAS_ADMIN_V1882 do servidor estão diferentes")
        admin = (APP / "admin.html").read_text(encoding="utf-8")
        for i in itens:
            if i.get("aba") and i["aba"] != "dashboard":
                self.assertIn(f'id="aba-{i["aba"]}"', admin, f"a tela {i['aba']} do menu não existe no admin.html")

    def test_login_e_sincronizacao_reconhecem_as_telas_do_admin(self):
        """Sem isso o web-auth trata a página como portal e o shared-storage audita como 'Sistema'."""
        for arquivo in ("web-auth.js", "shared-storage.js"):
            texto = (APP / arquivo).read_text(encoding="utf-8")
            m = re.search(r"const ROTAS_ADMIN_V1882 = /(.+)/i;", texto)
            self.assertIsNotNone(m, f"{arquivo} sem a lista ROTAS_ADMIN_V1882")
            padrao = re.compile(m.group(1).replace("\\/", "/"), re.I)
            for rota in S.ROTAS_ADMIN_V1882:
                self.assertTrue(padrao.search("/expedicao" + rota), f"{arquivo} não reconhece {rota}")
            # Telas que NÃO são do Admin não podem cair na lista.
            for fora in ("/expedicao/ruptura", "/expedicao/conferencia", "/expedicao/pedidos", "/expedicao/precificador"):
                self.assertFalse(padrao.search(fora), f"{arquivo} trata {fora} como Admin")

    def test_todo_item_do_menu_abre_uma_tela(self):
        for i in _menu_itens():
            status, _, _ = self.abrir(i["href"], self.cliente.cookie)
            self.assertEqual(status, 200, f"o item {i['id']} do menu aponta para {i['href']}, que não abre")

    def test_itens_do_menu_abrem_em_aba_nova(self):
        """V18.87: o dono pediu de volta — cada item do menu abre em aba nova (as telas do Admin
        trocam no lugar só quando o clique é dentro do próprio Admin)."""
        js = (APP / "nk-menu.js").read_text(encoding="utf-8")
        m = re.search(r"function itemHTML\(i\) \{(.*?)\n  \}", js, re.S)
        self.assertIsNotNone(m)
        self.assertIn('target="_blank" rel="noopener"', m.group(1))
        admin = (APP / "admin.html").read_text(encoding="utf-8")
        self.assertIn('a.v15-nav-item[data-nk-aba]', admin, "o Admin deixou de trocar as próprias telas no lugar")

    def test_telas_com_menu_carregam_o_menu_unico(self):
        for pagina in ("financeiro", "conversao", "analise-ads", "promocoes-auto",
                       "ruptura", "analise-full", "compras", "precificador", "tarefas"):
            texto = (APP / f"{pagina}.html").read_text(encoding="utf-8")
            self.assertIn('nk-menu.js?v=', texto, f"{pagina}.html sem o menu único")
            self.assertIn("data-shell", texto, f"{pagina}.html não monta o menu")


if __name__ == "__main__":
    unittest.main()
