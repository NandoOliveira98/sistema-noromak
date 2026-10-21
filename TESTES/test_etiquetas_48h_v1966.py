"""V19.66 — etiquetas com mais de 48 h saem do servidor, menos as de pedido pendente."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from base import S, TesteNoromak


def _quando(horas):
    return (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()


def _rec(rid, pedido, horas):
    return {"id": rid, "marketplace": "mercado_livre", "envio": "^XA^XZ", "pedido": pedido,
            "vinculadoA": [pedido], "chaves": [pedido], "adicionadaEm": _quando(horas)}


def _gravar(chave, valor):
    atual, _, _ = S.get_storage(chave)
    S.save_storage(chave, atual, json.dumps(valor), True, actor="admin_teste", actor_role="admin",
                   source_screen="teste", change_method="teste", client_ip="127.0.0.1")


class LimpezaEtiquetas48h(TesteNoromak):

    def test_apaga_antiga_sem_pedido_pendente_e_mantem_o_resto(self):
        _gravar("conf_pedidos", [{"Pedido": "PEND48", "SKU": "X1", "Quantidade": 1}])
        regs = {
            "velha_concluida": _rec("velha_concluida", "FORA48", 50),
            "velha_pendente": _rec("velha_pendente", "PEND48", 50),
            "nova": _rec("nova", "FORA49", 10),
            "sem_data": {**_rec("sem_data", "FORA50", 0), "adicionadaEm": ""},
        }
        _gravar("conf_etiquetas", {"version": 8, "registros": regs, "ordem": list(regs)})
        self.assertEqual(S.limpar_etiquetas_antigas(), 1)
        raw, _, _ = S.get_storage("conf_etiquetas")
        ficou = set(json.loads(raw)["registros"])
        self.assertNotIn("velha_concluida", ficou)
        self.assertEqual(ficou, {"velha_pendente", "nova", "sem_data"})
        self.assertEqual(S.limpar_etiquetas_antigas(), 0)


if __name__ == "__main__":
    unittest.main()
