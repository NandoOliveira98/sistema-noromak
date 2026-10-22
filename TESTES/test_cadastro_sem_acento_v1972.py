"""V19.72 — cadastro que vai ao Apollo sem acento, ç nem caractere especial.

Aviso do Guilherme (NoromakWeb, 22/10): nome, fantasia, endereço, complemento, bairro
e observação do cliente/criar, cliente/atualizar e cliente/credito não podem levar
acento, ç, travessão, aspas curvas nem º/ª. A porta também limpa, mas o filtro da
Expedição tem que existir (os clientes 83070, 83072... foram criados com acento).
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from base import S


class _Resp:
    def __init__(self):
        self.corpo = b'{"ok": true, "situacao": "atualizado"}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.corpo


class CadastroSemAcento(unittest.TestCase):
    def test_exemplos_do_aviso(self):
        c = S._exp_corpo_sem_acento("cliente/criar", {
            "nome": "JOÃO DA CONCEIÇÃO", "fantasia": "Loja “Bom Preço” – Nº 1",
            "endereco": {"rua": "RUA MOÇAMBIQUE", "bairro": "GENIBAÚ", "complemento": "APTº 2ª ANDAR",
                         "cidade": "SÃO PAULO", "uf": "SP", "cep": "01001000"},
            "observacao": "ELETRICITÁRIOS — ‘fundos’", "email": "a@b.com", "documento": "123", "revenda": 1})
        self.assertEqual(c["nome"], "JOAO DA CONCEICAO")
        self.assertEqual(c["fantasia"], "Loja Bom Preco - No 1")
        self.assertEqual(c["endereco"]["rua"], "RUA MOCAMBIQUE")
        self.assertEqual(c["endereco"]["bairro"], "GENIBAU")
        self.assertEqual(c["endereco"]["complemento"], "APTo 2a ANDAR")
        self.assertEqual(c["endereco"]["cidade"], "SAO PAULO")
        self.assertEqual(c["observacao"], "ELETRICITARIOS - fundos")
        self.assertEqual((c["email"], c["documento"], c["revenda"]), ("a@b.com", "123", 1))
        for texto in json.dumps(c):
            self.assertLess(ord(texto), 128)

    def test_outras_rotas_nao_mexem(self):
        corpo = {"observacao": "VENDA ML – Nº 5"}
        self.assertEqual(S._exp_corpo_sem_acento("romaneio/soltar", corpo), corpo)

    def test_porta_envia_limpo(self):
        enviados = []

        def falso(req, timeout=None):
            enviados.append(json.loads(req.data.decode("utf-8")))
            return _Resp()

        with mock.patch.object(S, "CHAVE_CRM", "x"), mock.patch.object(S.urllib.request, "urlopen", falso):
            for rota in ("cliente/criar", "cliente/atualizar", "cliente/credito"):
                S._exp_porta(rota, {"nome": "JOÃO", "endereco": {"bairro": "GENIBAÚ"}}, escrita=True)
        self.assertEqual(len(enviados), 3)
        for corpo in enviados:
            self.assertEqual(corpo, {"nome": "JOAO", "endereco": {"bairro": "GENIBAU"}})


if __name__ == "__main__":
    unittest.main()
