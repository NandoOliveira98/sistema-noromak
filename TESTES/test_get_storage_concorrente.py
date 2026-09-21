"""V18.06 — get_storage sob carga concorrente não pode corromper a conexão.

Bug real (21/09/2026, produção): sob carga alta, get_storage e completed_keys
liam a conexão SQLite COMPARTILHADA (check_same_thread=False) sem o DB_LOCK,
enquanto as escritas usavam o lock. A corrida entre threads corrompia o cursor
e a leitura estourava `IndexError: tuple index out of range` em
`{row[0] for row in DB.execute("SELECT order_key FROM completed_orders")}`,
derrubando de forma intermitente o import da Shopee e a leitura de etiquetas.

Este teste martela leituras e escritas concorrentes. Com o DB_LOCK protegendo
get_storage/completed_keys, nenhuma exceção aparece.
"""

import json
import threading
import unittest

from base import S, USUARIO_ADMIN, reiniciar_banco


def setUpModule():
    reiniciar_banco()


def _salvar(linhas):
    atual, _, _ = S.get_storage("conf_pedidos")
    S.save_storage(
        "conf_pedidos", atual, json.dumps(linhas), False,
        actor="sistema", actor_role="sistema",
        source_screen="Teste concorrente", change_method="Sincronização automática",
        client_ip="127.0.0.1", allow_completed_reimport=False,
    )


class GetStorageConcorrente(unittest.TestCase):
    def test_leituras_e_escritas_juntas_nao_corrompem_a_conexao(self):
        # Popula completed_orders (a tabela do IndexError) e conf_pedidos.
        with S.DB_LOCK:
            S.DB.execute("DELETE FROM completed_orders")
            for i in range(40):
                S.DB.execute(
                    "INSERT OR IGNORE INTO completed_orders(order_key, completed_at, completed_by) VALUES(?,?,?)",
                    (f"2000{i:012d}", S.now_iso(), USUARIO_ADMIN),
                )
            S.DB.commit()
        _salvar([{"Pedido": f"3000{i:012d}", "SKU": "X", "Quantidade": "1",
                  "Marketplace": "SHOPEE", "__uidLinha": f"u{i}"} for i in range(30)])

        erros: list[str] = []

        def leitor():
            for _ in range(250):
                try:
                    S.get_storage("conf_pedidos")
                    S.get_storage("conf_etiquetas")
                except Exception as exc:  # pragma: no cover
                    erros.append(repr(exc))

        def escritor():
            for i in range(80):
                try:
                    _salvar([{"Pedido": f"4000{i:012d}", "SKU": "Y", "Quantidade": "1",
                              "Marketplace": "SHOPEE", "__uidLinha": f"w{i}"}])
                except Exception as exc:  # pragma: no cover
                    erros.append(repr(exc))

        threads = [threading.Thread(target=leitor) for _ in range(6)]
        threads += [threading.Thread(target=escritor) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(erros, [], f"corrida na conexão compartilhada: {erros[:3]}")


if __name__ == "__main__":
    unittest.main()
