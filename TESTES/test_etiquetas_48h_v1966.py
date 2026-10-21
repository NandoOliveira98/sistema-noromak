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

    def test_roda_uma_vez_por_dia_a_partir_das_6(self):
        with S.DB_LOCK:
            S.DB.execute("DROP TABLE IF EXISTS etiquetas_limpeza_v1966")
        madrugada = datetime(2026, 10, 22, 5, 59, tzinfo=S.BR_TZ)
        seis = datetime(2026, 10, 22, 6, 0, tzinfo=S.BR_TZ)
        self.assertIsNone(S.etiquetas_limpeza_rodada(madrugada), "antes das 06:00 não roda")
        self.assertIsNotNone(S.etiquetas_limpeza_rodada(seis))
        self.assertIsNone(S.etiquetas_limpeza_rodada(seis + timedelta(hours=5)), "2ª vez no mesmo dia não roda")
        self.assertIsNotNone(S.etiquetas_limpeza_rodada(seis + timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
