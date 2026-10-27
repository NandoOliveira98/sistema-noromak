"""V19.91 — usuário excluído voltava na lista e travava a aprovação (caso real 27/10).

"mauro" foi aprovado pela tela (o "Aprovar" grava o conf_usuarios inteiro como ADMIN) e depois
excluído pela lixeira (DELETE /api/roles). A gravação do ADMIN tinha deixado uma trava
(admin_manual_overrides, __ENTITY__) que a LEITURA do conf_usuarios reaplicava: o "mauro" voltava
na lista, sem senha, e toda aprovação seguinte ("maurosilvio") era recusada com
"Informe uma senha ... para o novo usuário 'mauro'". A fonte da verdade dos usuários é o
app_users; o conf_usuarios é só o espelho dele e não usa as travas do ADMIN.
"""
from __future__ import annotations

import json

from base import S, TesteNoromak


def logins() -> list[str]:
    valor, _, _ = S.get_storage("conf_usuarios")
    return sorted(str(u.get("Login")) for u in json.loads(valor or "[]"))


def aprovar(login: str, nome: str) -> None:
    """O que o "Aprovar" da tela faz: lista atual + o novo, gravada pela rota do conf_usuarios."""
    valor, _, _ = S.get_storage("conf_usuarios")
    lista = json.loads(valor or "[]")
    lista.append({"Nome": nome, "Login": login, "Senha": "senha-boa-123", "Perfil": "Operador",
                  "Filial": "aracatuba", "Ativo": True, "Aprovado": True})
    novo = S.apply_app_users_payload(json.dumps(lista), "admin")   # como o handler do POST /api/storage
    S.save_storage("conf_usuarios", None, novo, True, actor="admin", actor_role="admin")


class UsuarioExcluido(TesteNoromak):

    def test_excluido_pela_lixeira_nao_volta_e_nao_trava_a_proxima_aprovacao(self):
        aprovar("mauro", "Mauro Almada")
        self.assertIn("mauro", logins())

        st, corpo = self.cliente.pedir("DELETE", "/api/roles?username=mauro")
        self.assertEqual(st, 200, corpo)
        self.assertNotIn("mauro", logins(), "REGRESSÃO: o usuário excluído voltou na lista")

        aprovar("maurosilvio", "Mauro Almada")      # antes: ValueError "... novo usuário 'mauro'"
        self.assertIn("maurosilvio", logins())
        self.assertNotIn("mauro", logins())

    def test_lista_lida_e_a_do_app_users(self):
        aprovar("ana", "Ana")
        with S.DB_LOCK:
            S.DB.execute("UPDATE app_users SET display_name='Ana Paula' WHERE username='ana'")
            S.DB.commit()
        S.sync_app_users_storage()
        valor, _, _ = S.get_storage("conf_usuarios")
        ana = next(u for u in json.loads(valor) if u["Login"] == "ana")
        self.assertEqual(ana["Nome"], "Ana Paula", "trava antiga do ADMIN não pode passar por cima do app_users")
        self.assertNotIn("__adminOverride", ana)

    def test_travas_antigas_do_conf_usuarios_nao_valem_mais(self):
        # Banco de produção já tem a trava do "mauro" gravada: ela não pode ser reaplicada.
        with S.DB_LOCK:
            S._admin_override_upsert("conf_usuarios", "NOME:MAURO ALMADA", "__ENTITY__",
                                     {"Nome": "Mauro Almada", "Login": "mauro", "Senha": "********"},
                                     deleted=False, actor="admin")
            S.DB.commit()
        self.assertNotIn("mauro", logins())
        aprovar("maurosilvio", "Mauro Almada")
        self.assertIn("maurosilvio", logins())
