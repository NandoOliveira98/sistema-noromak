"""V17.99 — etiqueta Shopee saía em branco.

A Shopee manda a etiqueta como UMA imagem: o comando ~DGR (que baixa a imagem
para a impressora) vem ANTES do ^XA, e o ^XA só manda imprimir essa imagem
(^XGR). A extração cortava o texto a partir do primeiro ^XA, jogando a imagem
fora — a Zebra imprimia só a caixa "PRODUTOS DO PEDIDO" que o Noromak desenha
por cima. As etiquetas importadas pelo TXT não passavam por aqui e saíam certas.
"""

import io
import json
import zipfile

from base import S, TesteNoromak

GRAFICO = "~DGR:DEMO.GRF,124236,102,:Z64:eJzt3V2M40aeGPCiSlbJWVrU2IssG5FFtWexhwDBndoT3MlZRVTvLLwGYmRxecpDgFV7DF8CHBINBohluHdIjeZaHaSvew55yCzgrB3cw73evfnBuKUsozUPE88u8rJ32K:ABCD\n"
IMPRIME = "^XA^MMT^PW812^FO0,0^XGR:DEMO.GRF,1,1^FS^PQ1,0,1,Y^XZ\n^XA^IDR:DEMO.GRF^FS^XZ\n"


def zip_shopee(texto: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("thermal_zpl_shipping_label.txt", texto)
    return buf.getvalue()


class EtiquetaShopeeComGrafico(TesteNoromak):

    def test_extracao_mantem_a_imagem_que_vem_antes_do_XA(self):
        zpl = S._shopee_extract_zpl(zip_shopee(GRAFICO + IMPRIME))
        self.assertTrue(zpl.startswith("~DGR:DEMO.GRF"), "a imagem da etiqueta foi descartada")
        self.assertIn("^XGR:DEMO.GRF", zpl)
        self.assertIn("^IDR:DEMO.GRF", zpl)

    def test_zpl_sem_imagem_continua_igual(self):
        zpl = S._shopee_extract_zpl(b"lixo antes ^XA^FO10,10^FDTESTE^FS^XZ")
        self.assertEqual(zpl, "^XA^FO10,10^FDTESTE^FS^XZ")

    def test_reparo_regrava_etiqueta_salva_sem_imagem(self):
        pasta = S.DB_PATH.parent / "shopee_labels"
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "260918AFJ1JW83_OFG1.zip").write_bytes(zip_shopee(GRAFICO + IMPRIME))
        # Estado deixado pela versão com defeito: só o trecho a partir do ^XA.
        S._shopee_store_linked_label("260918AFJ1JW83", "OFG1", IMPRIME.strip())
        # Etiqueta importada pelo TXT (já correta) não pode ser tocada.
        raw, _, _ = S.get_storage("conf_etiquetas")
        banco = json.loads(raw)
        banco["registros"]["etq_txt"] = {"id": "etq_txt", "marketplace": "shopee", "envio": "^XA^XGR:X.GRF^XZ", "chaves": ["TXT1"]}
        banco["ordem"].append("etq_txt")
        S.save_storage("conf_etiquetas", raw, json.dumps(banco), False, actor="teste", actor_role="sistema")

        reparadas = S.shopee_reparar_etiquetas_sem_grafico()

        self.assertEqual(reparadas, 1)
        banco = json.loads(S.get_storage("conf_etiquetas")[0])
        api = [r for r in banco["registros"].values() if r.get("origem") == "api_shopee"]
        self.assertEqual(len(api), 1)
        self.assertTrue(api[0]["envio"].startswith("~DGR:DEMO.GRF"))
        self.assertEqual(banco["registros"]["etq_txt"]["envio"], "^XA^XGR:X.GRF^XZ")
        # Rodar de novo não refaz nada.
        self.assertEqual(S.shopee_reparar_etiquetas_sem_grafico(), 0)
