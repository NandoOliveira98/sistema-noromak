"""V19.28 — a tela do Admin não remonta kit já aberto (261013H9XUXGFC, 14/10/2026).

A normalização de exibição (normalizarPendentesFiscalKitsAdmin) refazia o kit pelo
Cadastro de Kits a cada carga: o componente excluído voltava, a quantidade era
forçada para kits × qtd por kit e o "Salvar" gravava isso de volta. Roda a função
real do admin.html no Node.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _funcao() -> str:
    html = (RAIZ / "APP" / "admin.html").read_text(encoding="utf-8")
    ini = html.index("function normalizarPendentesFiscalKitsAdmin(")
    fim = html.index("\nconst NOMES_CONFERENCIA_STORAGE_KEY", ini)
    return html[ini:fim]


KIT = {"sku_kit": "0893600022/0893764272", "nome": "Kit", "ativo": True,
       "componentes": [{"sku": "0893764272", "quantidade": 1}, {"sku": "0893600022", "quantidade": 1}]}


def _rodar(linhas):
    js = (
        "const normalizarCodigoBanco=v=>String(v||'').trim().toUpperCase().replace(/[^A-Z0-9/]/g,'');\n"
        "const normalizarBuscaBanco=v=>String(v||'').trim().toLowerCase();\n"
        "const produtosBancoAdmin=[];function preencherPedidoProdutoBanco(){}\n"
        f"const kitsBancoAdmin={json.dumps([KIT])};\n"
        + _funcao()
        + f"\nprocess.stdout.write(JSON.stringify(normalizarPendentesFiscalKitsAdmin({json.dumps(linhas)})));"
    )
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(shutil.which("node"), "Node não instalado")
class KitTelaAdmin(unittest.TestCase):
    def _comp(self, sku, i, qtd="1"):
        return {"Pedido": "261013H9XUXGFC", "SKU": sku, "Quantidade": qtd, "Kit": "SIM",
                "SkuKitOrigem": KIT["sku_kit"], "SkuOrigemImportacao": sku,
                "Quantidade de Kits": "1", "Quantidade por Kit": "1",
                "__uidLinha": f"KITDB_261013H9XUXGFC_X_{sku}_{i}"}

    def test_componente_excluido_nao_volta(self):
        saida = _rodar([self._comp("0893764272", 0)])
        self.assertEqual([r["SKU"] for r in saida], ["0893764272"])

    def test_quantidade_alterada_fica(self):
        saida = _rodar([self._comp("0893764272", 0, "3"), self._comp("0893600022", 1)])
        self.assertEqual({r["SKU"]: r["Quantidade"] for r in saida}, {"0893764272": "3", "0893600022": "1"})

    def test_linha_colada_ainda_abre(self):
        saida = _rodar([{"Pedido": "P1", "SKU": KIT["sku_kit"], "Quantidade": "2"}])
        self.assertEqual(sorted(r["SKU"] for r in saida), ["0893600022", "0893764272"])
        self.assertTrue(all(r["Quantidade"] == "2" for r in saida))


if __name__ == "__main__":
    unittest.main()
