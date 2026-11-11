"""V19.45 — Cliente das vendas do Full corrigido pela NF-e do ML (decisões do dono, 16/10).

Caso real: venda Full de 10/09, NF-e 55455 série 2 para M2 SOLUCOES GRAFICAS LTDA, mas o
Apollo tinha o cliente com o nome da conta ("Premium Flex"). NUNCA cria cliente.
"""
from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

from base import S, Cliente, iniciar_servidor, reiniciar_banco
from test_expedicao_apollo_v1930 import PortaFalsa

VENDA = "2000013258104871"
MLB = "MLB111"
XML = """<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe><dest>
<CNPJ>39467085000158</CNPJ><xNome>M2 SOLUÇÕES GRÁFICAS LTDA</xNome>
<enderDest><xLgr>Rua Bolívia</xLgr><nro>SN</nro><xCpl>PL E QD 13 LOTE 22</xCpl><xBairro>Cidade Jardim Marajoara</xBairro>
<xMun>Japeri</xMun><UF>RJ</UF><CEP>26413030</CEP></enderDest><indIEDest>1</indIEDest><IE>11862454</IE>
</dest></infNFe></NFe></nfeProc>"""


def _venda_full(dias_atras=0, anuncio=MLB):
    hoje = datetime.now(S.BR_TZ).date()
    dia = (hoje - timedelta(days=dias_atras)).isoformat()
    with S.DB_LOCK:
        S.DB.execute("INSERT OR REPLACE INTO ml_estoque_dia_v1872(item_id,dia,logistica) VALUES(?,?,'fulfillment')",
                     (MLB, hoje.isoformat()))
        S.DB.execute("INSERT INTO vendas_v1799(marketplace,order_id,line_key,sale_at,sale_day,anuncio,amount,updated_at) "
                     "VALUES('ml',?,'1',?,?,?,67.7,?)", (VENDA, dia, dia, anuncio, dia))
        S.DB.commit()


# Resposta REAL da API de notas do ML (16/10, venda 2000018994767276), resumida.
NOTA_ML = {"id": 7240190018, "status": "authorized",
           "recipient": {"name": "DIRECOES HIDRAULICAS JOINVILLE LTDA",
                         "address": {"street_name": "Rua Lindóia", "street_number": "65",
                                     "complement": "Mecanica Schumacher Referencia: Se a oficina estiver fechada",
                                     "neighborhood": "Glória", "city": "Joinville", "zip_code": "89216300", "state": "SC"},
                         "identifications": {"cnpj": "34151586000107", "crt": "simples", "ie": "260148008",
                                             "ie_type": "contribuinte"}}}


class FormatoReal(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()

    def test_le_o_recipient_da_api(self):
        n = S._full_cad_destinatario_ml(NOTA_ML)
        self.assertEqual((n["documento"], n["ie"]), ("34151586000107", "260148008"))
        self.assertEqual(n["endereco"]["complemento"], "Mecanica Schumacher")
        self.assertEqual(n["endereco"]["uf"], "SC")

    def test_nao_contribuinte_vai_sem_ie(self):
        nota = {**NOTA_ML, "recipient": {**NOTA_ML["recipient"], "identifications": {"cnpj": "34151586000107", "ie": "", "ie_type": "nao_contribuinte"}}}
        self.assertEqual(S._full_cad_destinatario_ml(nota)["ie"], "")

    def test_nota_nao_autorizada_e_sem_nota(self):
        with mock.patch.object(S, "ml_api_get", return_value={**NOTA_ML, "status": "processing"}),                 mock.patch.object(S, "ml_integration_row", return_value={"seller_id": "1"}):
            self.assertIsNone(S.ml_nota_da_venda("1"))

    def test_corpo_do_apollo_com_a_nota_real(self):
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "encontrado", "cliente": 83065, "nome": "SHC COMPONENTES HIDRAULICOS EI"}
        with mock.patch.object(S, "_exp_porta", side_effect=porta):
            r = S.full_cadastro_atualizar("2000018994767276", S._full_cad_destinatario_ml(NOTA_ML))
        self.assertTrue(r["ok"], r)
        c = porta.corpo("cliente/atualizar")
        self.assertEqual(c["nome"], "DIRECOES HIDRAULICAS JOINVILLE LTDA")
        self.assertEqual((c["endereco"]["rua"], c["endereco"]["bairro"]), ("RUA LINDOIA", "GLORIA"))
        self.assertEqual((c["categoria"], c["inscricao_estadual"]), (3, "260148008"))


class Rodada(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        S.cadastro_full_ligar(True, "admin")

    def _rodar(self, porta, nota=XML, agora=None):
        def ml_get(caminho, **_):
            if nota is None:
                raise RuntimeError("Mercado Livre HTTP 404: not found")
            return {"id": "55455", "xml": nota}
        with mock.patch.object(S, "_exp_porta", side_effect=porta), mock.patch.object(S, "EXP_CADASTRO_API", True), \
                mock.patch.object(S, "ml_api_get", side_effect=ml_get), \
                mock.patch.object(S, "ml_integration_row", return_value={"seller_id": "224625106"}):
            return S.cadastro_full_rodada(agora=agora)

    def test_corrige_nome_endereco_sem_acento_e_ie(self):
        _venda_full()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "encontrado", "cliente": 51599, "nome": "PREMIUM FLEX"}
        self.assertEqual(self._rodar(porta).get("feito"), 1)
        corpo = porta.corpo("cliente/atualizar")
        self.assertEqual(corpo["nome"], "M2 SOLUCOES GRAFICAS LTDA")
        self.assertEqual(corpo["endereco"]["rua"], "RUA BOLIVIA")
        self.assertEqual(corpo["endereco"]["bairro"], "CIDADE JARDIM MARAJOARA")
        self.assertEqual(corpo["endereco"]["numero"], "SN")  # V20.50: sem número vai "SN" (o Linx exige ", número")
        self.assertEqual((corpo["categoria"], corpo["inscricao_estadual"]), (3, "11862454"))
        self.assertEqual(porta.corpo("cliente/buscar")["documento"], "39467085000158")

    def test_nunca_cria_e_desiste_depois_de_1_dia(self):
        _venda_full()
        porta = PortaFalsa()
        porta.buscar = {"ok": True, "situacao": "nao_cadastrado"}
        t0 = time.time()
        self._rodar(porta, agora=t0)
        self._rodar(porta, agora=t0 + 600)                     # menos de 1 h: não tenta
        self.assertEqual(porta.rotas().count("cliente/buscar"), 1)
        self._rodar(porta, agora=t0 + 90000)
        with S.DB_LOCK:
            sit = S.DB.execute("SELECT situacao FROM cadastro_full_v1945").fetchone()["situacao"]
        self.assertEqual(sit, "nao_encontrado")
        self.assertNotIn("cliente/criar", porta.rotas())

    def test_sem_nota_nao_vai_ao_apollo(self):
        _venda_full()
        porta = PortaFalsa()
        self._rodar(porta, nota=None)
        self.assertEqual(porta.chamadas, [])

    def test_venda_que_passou_pela_expedicao_fica_de_fora(self):
        _venda_full()
        with S.DB_LOCK:
            S.DB.execute("INSERT INTO completed_orders(order_key,order_id,completed_at,completed_by,payload) VALUES(?,?,?,?,?)",
                         (VENDA, VENDA, "x", "x", "{}"))
            S.DB.commit()
        porta = PortaFalsa()
        self._rodar(porta)
        self.assertEqual(porta.chamadas, [])

    def test_anuncio_que_nao_e_full_fica_de_fora(self):
        _venda_full(anuncio="MLB999")
        porta = PortaFalsa()
        self._rodar(porta)
        self.assertEqual(porta.chamadas, [])

    def test_desligado_nao_faz_nada(self):
        S.cadastro_full_ligar(False, "admin")
        _venda_full()
        porta = PortaFalsa()
        self._rodar(porta)
        self.assertEqual(porta.chamadas, [])

    def test_antiga_so_a_noite(self):
        _venda_full(dias_atras=10)
        porta = PortaFalsa()
        meio_dia = datetime.now(S.BR_TZ).replace(hour=12, minute=0).timestamp()
        self._rodar(porta, agora=meio_dia)
        self.assertEqual(porta.chamadas, [])
        noite = datetime.now(S.BR_TZ).replace(hour=22, minute=0).timestamp()
        self._rodar(porta, agora=noite)
        self.assertIn("cliente/atualizar", porta.rotas())

    def test_cnpj_sem_ie_vai_isento(self):
        _venda_full()
        porta = PortaFalsa()
        self._rodar(porta, nota=XML.replace("<IE>11862454</IE>", "").replace("<indIEDest>1", "<indIEDest>9"))
        corpo = porta.corpo("cliente/atualizar")
        self.assertEqual((corpo["categoria"], corpo["inscricao_estadual"]), (2, "ISENTO"))


class Rotas(unittest.TestCase):
    def setUp(self):
        reiniciar_banco()
        self.url = iniciar_servidor()

    def test_admin_liga_e_ve(self):
        admin = Cliente(self.url)
        admin.entrar()
        status, d = admin.pedir("POST", "/api/expedicao-apollo/cadastro-full", {"ligado": True})
        self.assertEqual((status, d["ligado"]), (200, True))
        status, d = admin.pedir("GET", "/api/expedicao-apollo/cadastro-full")
        self.assertEqual((status, d["ligado"]), (200, True))

    def test_sem_login_401(self):
        status, _ = Cliente(self.url).pedir("POST", "/api/expedicao-apollo/cadastro-full", {"ligado": True})
        self.assertEqual(status, 401)
        self.assertFalse(S.cadastro_full_config()["ligado"])

    def test_worker_no_painel(self):
        self.assertIn("cadastro_full_loop", S.SAUDE_WORKERS_INFO)


if __name__ == "__main__":
    unittest.main()
