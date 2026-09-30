"""V18.49 — duas pessoas no Precificador ao mesmo tempo não apagam o trabalho uma da outra.

Em produção (30/09/2026): o banco do Precificador é UM objeto
{"products": [...], "selectedId": ..., "updatedAt": ...}. O merge do servidor só
sabia juntar listas; para objeto ficava com a última gravação INTEIRA. Como o
Precificador grava o banco todo até ao clicar num produto (ou no F5), quem abria a
tela com uma cópia antiga apagava o que o outro tinha acabado de alterar.

Regra agora: junta produto por produto (pelo _id) e campo por campo. Cada
gravação só aplica o que ELA mudou em relação ao que tinha lido.
"""

from __future__ import annotations

import json
import unittest

from base import S, TesteNoromak

CHAVE = "precificador_online_v1"


def _banco(*produtos: dict, selecionado: str = "") -> str:
    return json.dumps({"products": list(produtos), "selectedId": selecionado, "updatedAt": "2026-09-30T10:00:00Z"})


def _prod(pid: str, sku: str, custo: float, margem: float = 20.0, **extra) -> dict:
    return {"_id": pid, "sku": sku, "produto": f"Produto {sku}", "custo": custo,
            "marketplaces": {"ml": {"margem": margem}}, **extra}


def _gravar(antigo: str | None, novo: str, quem: str = "admin_teste") -> None:
    S.save_storage(CHAVE, antigo, novo, False, actor=quem, actor_role="admin", source_screen="Precificador",
                   change_method="Teste automatizado", client_ip="127.0.0.1")


def _produtos() -> dict[str, dict]:
    raw, _, _ = S.get_storage(CHAVE)
    return {p["_id"]: p for p in json.loads(raw)["products"]}


class PrecificadorDuasPessoas(TesteNoromak):

    def setUp(self) -> None:
        super().setUp()
        atual, _, _ = S.get_storage(CHAVE)
        self.base = _banco(_prod("p1", "AAA", 10.0), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 30.0))
        _gravar(atual, self.base)

    def test_clique_com_copia_antiga_nao_desfaz_alteracao_do_outro(self) -> None:
        # Pessoa A muda o custo do AAA.
        _gravar(self.base, _banco(_prod("p1", "AAA", 11.5), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 30.0)), "pessoa_a")
        # Pessoa B abriu antes e só clicou no BBB: grava o banco inteiro, antigo, com outro selectedId.
        _gravar(self.base, _banco(_prod("p1", "AAA", 10.0), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 30.0),
                                  selecionado="p2"), "pessoa_b")
        self.assertEqual(_produtos()["p1"]["custo"], 11.5, "o clique da pessoa B apagou a alteração da pessoa A")

    def test_duas_pessoas_em_produtos_diferentes(self) -> None:
        _gravar(self.base, _banco(_prod("p1", "AAA", 11.5), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 30.0)), "pessoa_a")
        _gravar(self.base, _banco(_prod("p1", "AAA", 10.0), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 33.0)), "pessoa_b")
        p = _produtos()
        self.assertEqual(p["p1"]["custo"], 11.5)
        self.assertEqual(p["p3"]["custo"], 33.0)

    def test_duas_pessoas_no_mesmo_produto_em_campos_diferentes(self) -> None:
        _gravar(self.base, _banco(_prod("p1", "AAA", 11.5), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 30.0)), "pessoa_a")
        _gravar(self.base, _banco(_prod("p1", "AAA", 10.0, margem=25.0), _prod("p2", "BBB", 20.0),
                                  _prod("p3", "CCC", 30.0)), "pessoa_b")
        p1 = _produtos()["p1"]
        self.assertEqual(p1["custo"], 11.5, "custo da pessoa A")
        self.assertEqual(p1["marketplaces"]["ml"]["margem"], 25.0, "margem da pessoa B")

    def test_mesmo_campo_vale_a_ultima_gravacao(self) -> None:
        _gravar(self.base, _banco(_prod("p1", "AAA", 11.5), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 30.0)), "pessoa_a")
        _gravar(self.base, _banco(_prod("p1", "AAA", 12.0), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 30.0)), "pessoa_b")
        self.assertEqual(_produtos()["p1"]["custo"], 12.0)

    def test_produto_novo_do_outro_nao_some(self) -> None:
        _gravar(self.base, _banco(_prod("p1", "AAA", 10.0), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 30.0),
                                  _prod("p4", "DDD", 40.0)), "pessoa_a")
        _gravar(self.base, _banco(_prod("p1", "AAA", 10.0), _prod("p2", "BBB", 21.0), _prod("p3", "CCC", 30.0)), "pessoa_b")
        p = _produtos()
        self.assertIn("p4", p, "o produto cadastrado pela pessoa A sumiu")
        self.assertEqual(p["p2"]["custo"], 21.0)

    def test_exclusao_continua_funcionando(self) -> None:
        _gravar(self.base, _banco(_prod("p1", "AAA", 10.0), _prod("p3", "CCC", 30.0)), "pessoa_a")
        self.assertNotIn("p2", _produtos())
        # Cópia antiga da pessoa B ainda tem o BBB, sem mudança: não pode ressuscitar.
        _gravar(self.base, _banco(_prod("p1", "AAA", 10.5), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 30.0)), "pessoa_b")
        p = _produtos()
        self.assertNotIn("p2", p, "produto excluído voltou")
        self.assertEqual(p["p1"]["custo"], 10.5)

    def test_ordem_dos_produtos_preservada(self) -> None:
        _gravar(self.base, _banco(_prod("p1", "AAA", 10.0), _prod("p2", "BBB", 20.0), _prod("p3", "CCC", 30.0),
                                  _prod("p4", "DDD", 40.0)), "pessoa_a")
        _gravar(self.base, _banco(_prod("p1", "AAA", 10.0), _prod("p2", "BBB", 22.0), _prod("p3", "CCC", 30.0)), "pessoa_b")
        raw, _, _ = S.get_storage(CHAVE)
        self.assertEqual([p["_id"] for p in json.loads(raw)["products"]], ["p1", "p2", "p3", "p4"])


class PrecificadorBancoSemId(TesteNoromak):
    """Banco antigo sem _id: cada tela cria o _id na primeira gravação."""

    def test_primeira_gravacao_com_id_nao_passa_por_cima_do_outro(self) -> None:
        atual, _, _ = S.get_storage(CHAVE)
        base = json.dumps({"products": [{"sku": "AAA", "custo": 10.0}, {"sku": "BBB", "custo": 20.0}]})
        _gravar(atual, base)
        _gravar(base, json.dumps({"products": [{"_id": "a-1", "sku": "AAA", "custo": 11.5},
                                               {"_id": "a-2", "sku": "BBB", "custo": 20.0}]}), "pessoa_a")
        _gravar(base, json.dumps({"products": [{"_id": "b-1", "sku": "AAA", "custo": 10.0},
                                               {"_id": "b-2", "sku": "BBB", "custo": 22.0}]}), "pessoa_b")
        raw, _, _ = S.get_storage(CHAVE)
        prods = json.loads(raw)["products"]
        self.assertEqual(len(prods), 2, "o mesmo produto não pode duplicar")
        por_sku = {p["sku"]: p for p in prods}
        self.assertEqual(por_sku["AAA"]["custo"], 11.5)
        self.assertEqual(por_sku["BBB"]["custo"], 22.0)


class PrecificadorSemOverrideRaiz(TesteNoromak):
    """A gravação de um Admin não pode congelar a lista inteira de produtos."""

    def setUp(self) -> None:
        super().setUp()
        atual, _, _ = S.get_storage(CHAVE)
        self.base = _banco(_prod("p1", "AAA", 10.0), _prod("p2", "BBB", 20.0))
        _gravar(atual, self.base)

    def test_admin_nao_grava_override_do_banco_inteiro(self) -> None:
        _gravar(self.base, _banco(_prod("p1", "AAA", 11.0), _prod("p2", "BBB", 20.0)))
        with S.DB_LOCK:
            n = S.DB.execute("SELECT COUNT(*) FROM admin_manual_overrides WHERE storage_key=?", (CHAVE,)).fetchone()[0]
        self.assertEqual(n, 0, "override __ROOT__ congelaria o campo products inteiro")

    def test_atualizacao_do_apollo_nao_e_descartada_nem_apaga_edicao(self) -> None:
        # A atualização geral lê o banco no começo...
        lido, _, _ = S.get_storage(CHAVE)
        # ...enquanto ela roda (até 1 h), a pessoa altera o BBB.
        _gravar(self.base, _banco(_prod("p1", "AAA", 10.0), _prod("p2", "BBB", 25.0)), "pessoa_a")
        # No fim, a atualização grava o custo novo do AAA, como faz o worker.
        dados = json.loads(lido)
        dados["products"][0]["custo"] = 12.34
        S.save_storage(CHAVE, lido, json.dumps(dados), False, actor="sistema", actor_role="operador",
                       source_screen="precificador", change_method="apollo.bulk")
        p = _produtos()
        self.assertEqual(p["p1"]["custo"], 12.34, "custo do Apollo descartado")
        self.assertEqual(p["p2"]["custo"], 25.0, "a atualização do Apollo apagou a edição feita no meio")


if __name__ == "__main__":
    unittest.main()
