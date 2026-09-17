"""V17.84 — documento fiscal do comprador (CPF/CNPJ).

É a chave que liga um pedido de marketplace ao cadastro do cliente no Linx DMS:
a rota ProcurarCliente do ERP busca por CnpjCpf. Sem documento, não há como
atualizar o cadastro de uma venda.
"""

import unittest

from base import S, TesteNoromak


class DocumentoDoPedido(unittest.TestCase):
    """O leitor único, que serve Mercado Livre e Shopee."""

    def test_le_cpf_do_mercado_livre(self):
        d = S.documento_do_pedido({"Pedido": "1", "CPFMercadoLivre": "46414894168"})
        self.assertEqual(d, {"tipo": "CPF", "numero": "46414894168"})

    def test_le_cnpj_do_mercado_livre(self):
        d = S.documento_do_pedido({"Pedido": "1", "CNPJMercadoLivre": "12345678000199"})
        self.assertEqual(d, {"tipo": "CNPJ", "numero": "12345678000199"})

    def test_le_cpf_da_shopee(self):
        d = S.documento_do_pedido({"Pedido": "1", "CPFShopee": "46414894168"})
        self.assertEqual(d, {"tipo": "CPF", "numero": "46414894168"})

    def test_le_cnpj_da_shopee(self):
        d = S.documento_do_pedido({"Pedido": "1", "CNPJShopee": "12345678000199"})
        self.assertEqual(d, {"tipo": "CNPJ", "numero": "12345678000199"})

    def test_remove_pontuacao(self):
        """O Linx recebe só dígitos; a tela e as APIs mandam formatado."""
        d = S.documento_do_pedido({"Pedido": "1", "CPFShopee": "464.148.941-68"})
        self.assertEqual(d["numero"], "46414894168")

    def test_o_tamanho_corrige_campo_trocado(self):
        """Integração que põe CNPJ no campo de CPF não pode enganar o ERP."""
        d = S.documento_do_pedido({"Pedido": "1", "CPFShopee": "12345678000199"})
        self.assertEqual(d["tipo"], "CNPJ", "14 dígitos é CNPJ, esteja onde estiver")

        d = S.documento_do_pedido({"Pedido": "1", "CNPJMercadoLivre": "46414894168"})
        self.assertEqual(d["tipo"], "CPF", "11 dígitos é CPF, esteja onde estiver")

    def test_pedido_sem_documento(self):
        d = S.documento_do_pedido({"Pedido": "1", "Cliente": "FULANO"})
        self.assertEqual(d, {"tipo": "", "numero": ""})

    def test_campo_vazio_nao_conta(self):
        d = S.documento_do_pedido({"Pedido": "1", "CPFMercadoLivre": "", "CPFShopee": "46414894168"})
        self.assertEqual(d["numero"], "46414894168")

    def test_entrada_invalida_nao_quebra(self):
        for entrada in (None, "texto", 42, []):
            self.assertEqual(S.documento_do_pedido(entrada), {"tipo": "", "numero": ""})


class DocumentoDoMercadoLivre(unittest.TestCase):
    """A extração vinda do billing-info do ML, que antes era descartada."""

    def payload(self, tipo, numero):
        return {"buyer": {"billing_info": {
            "first_name": "EDIVAN", "last_name": "BATISTA PAIVA",
            "identification": {"type": tipo, "number": numero},
        }}}

    def test_captura_cpf_junto_do_nome(self):
        d = S._ml_billing_details(self.payload("CPF", "464.148.941-68"))
        self.assertEqual(d["name"], "EDIVAN BATISTA PAIVA")
        self.assertEqual(d["doc_type"], "CPF")
        self.assertEqual(d["doc_number"], "46414894168", "a pontuação devia ter saído")

    def test_captura_cnpj(self):
        d = S._ml_billing_details({"buyer": {"billing_info": {
            "business_name": "COLOMBI VIAGENS E TRANSPORTE LTDA",
            "identification": {"type": "CNPJ", "number": "12345678000199"},
        }}})
        self.assertEqual(d["doc_type"], "CNPJ")
        self.assertEqual(d["doc_number"], "12345678000199")

    def test_deduz_o_tipo_pelo_tamanho_quando_o_ml_omite(self):
        d = S._ml_billing_details({"buyer": {"billing_info": {
            "first_name": "FULANO", "identification": {"number": "12345678000199"},
        }}})
        self.assertEqual(d["doc_type"], "CNPJ")

    def test_pedido_sem_identificacao(self):
        d = S._ml_billing_details({"buyer": {"billing_info": {"first_name": "FULANO"}}})
        self.assertEqual(d["doc_number"], "")
        self.assertEqual(d["name"], "FULANO", "o nome tem de continuar funcionando")

    def test_resposta_vazia_nao_quebra(self):
        d = S._ml_billing_details({})
        self.assertEqual(d["doc_number"], "")
        self.assertEqual(d["name"], "")
        self.assertIn("doc_type", d, "o contrato de retorno precisa ser estável")


class NomeFiscalDoPedido(unittest.TestCase):
    """O nome que vai para o Linx é o de "Dados para a NF-e", e só ele."""

    def test_le_o_nome_fiscal_do_mercado_livre(self):
        nome = S.nome_fiscal_do_pedido({
            "Cliente": "gabriel.oliveira",
            "NomeFiscalMercadoLivre": "Gabriel Arruda de Oliveira",
        })
        self.assertEqual(nome, "Gabriel Arruda de Oliveira")

    def test_le_o_nome_fiscal_da_shopee(self):
        nome = S.nome_fiscal_do_pedido({"NomeFiscalShopee": "ANNA KARLA ALA"})
        self.assertEqual(nome, "ANNA KARLA ALA")

    def test_nunca_cai_para_o_apelido_da_conta(self):
        """Gravar o apelido no ERP produziria nota no nome errado."""
        nome = S.nome_fiscal_do_pedido({"Cliente": "gabriel.oliveira 2024"})
        self.assertEqual(nome, "", "caiu para Cliente quando não havia nome fiscal")

    def test_nunca_cai_para_o_destinatario(self):
        nome = S.nome_fiscal_do_pedido({
            "Cliente": "MARIA (quem recebe)",
            "NomeConferencia": "MARIA (quem recebe)",
        })
        self.assertEqual(nome, "")

    def test_ignora_o_marcador_de_indisponivel_da_shopee(self):
        nome = S.nome_fiscal_do_pedido({"NomeFiscalShopee": "NOME NÃO DISPONÍVEL"})
        self.assertEqual(nome, "")

    def test_entrada_invalida_nao_quebra(self):
        for entrada in (None, "texto", 42, []):
            self.assertEqual(S.nome_fiscal_do_pedido(entrada), "")

    def test_nome_e_documento_vem_da_mesma_fonte(self):
        """Na tela do ML, nome e CPF ficam juntos em Dados para a NF-e."""
        detalhes = S._ml_billing_details({"buyer": {"billing_info": {
            "first_name": "Gabriel", "last_name": "Arruda de Oliveira",
            "identification": {"type": "CPF", "number": "08186845771"},
        }}})
        self.assertEqual(detalhes["name"], "Gabriel Arruda de Oliveira")
        self.assertEqual(detalhes["doc_number"], "08186845771")


class DocumentoSobreviveNaLinha(TesteNoromak):
    """O documento não pode se perder no merge do storage."""

    def linha_ml(self, pedido, doc):
        return {
            "Pedido": pedido, "SKU": "46190130", "Quantidade": 1, "Lido": 0,
            "Cliente": "EDIVAN BATISTA PAIVA", "Marketplace": "Mercado Livre",
            "CPFMercadoLivre": doc, "__uidLinha": pedido + "-L1",
            "Data do Pedido": "2026-09-17",
        }

    def test_documento_sobrevive_a_sincronizacao(self):
        from base import gravar_como_integracao, pedidos
        gravar_como_integracao([self.linha_ml("PED-1", "46414894168")])
        gravar_como_integracao([self.linha_ml("PED-1", "46414894168")])
        atual = pedidos()[0]
        self.assertEqual(S.documento_do_pedido(atual)["numero"], "46414894168")

    def test_documento_sobrevive_a_edicao_do_admin(self):
        from base import gravar_como_integracao, pedidos
        gravar_como_integracao([self.linha_ml("PED-1", "46414894168")])
        linha = pedidos()[0]
        linha["Cliente"] = "NOME CORRIGIDO PELO ADMIN"
        self.salvar_admin([linha])
        atual = pedidos()[0]
        self.assertEqual(atual["Cliente"], "NOME CORRIGIDO PELO ADMIN")
        self.assertEqual(S.documento_do_pedido(atual)["numero"], "46414894168",
                         "a edição do ADMIN apagou o documento")


if __name__ == "__main__":
    unittest.main()
