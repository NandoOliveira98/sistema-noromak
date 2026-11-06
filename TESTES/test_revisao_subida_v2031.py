"""revisar-antes-de-subir de 06/11 sobre a V20.30 (pedidos do TikTok pela extensão) e a V20.31 (agente de
anúncios). O dono pediu "corrija tudo": cada teste reproduz um achado confirmado no código."""

import json
import threading
import time
from pathlib import Path
from unittest import mock

from base import S

from test_agente_anuncios_v2031 import ATRIBUTOS_ML, TITULOS, EtapasBase
from test_tiktok_pedidos_extensao_v2030 import Base as BaseTikTok, _sc


class AgenteRevisao(EtapasBase):

    def gastos(self):
        with S.DB_LOCK:
            return S.DB.execute("SELECT COUNT(*) FROM imagem_gasto_v2017 WHERE trabalho_id=?", (self.tid,)).fetchone()[0]

    def pronta(self):
        self.textos()
        t = S.imagens_agente_proximo(True)["trabalho"]
        S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_atributos", "atributos": [
            {"id": "BRAND", "valor": "Vonixx", "origem": "rotulo"}]})

    # --- achado 4: o módulo Imagens não mexe nos pedidos de anúncio ---
    def test_tela_do_estudio_nao_refaz_apaga_nem_baixa_pedido_de_anuncio(self):
        self.pronta()
        t = self.trabalho(self.tid)
        with self.assertRaises(S.ImagemNaoEncontrada):
            S.imagens_refazer("operador", {"id": self.tid, "versao": t["versao"]})
        self.assertEqual(S.imagens_apagar("operador", {"ids": [self.tid]})["apagados"], [])
        self.assertFalse(self.trabalho(self.tid)["apagado_em"])
        nome = json.loads(t["arquivos_json"])[0]["nome"]
        with self.assertRaises(S.ImagemNaoEncontrada):
            S.imagens_arquivo(self.tid, nome)
        dados, _, _ = S.imagens_arquivo(self.tid, nome, so_originais=True, anuncio_ok=True)  # o agente baixa
        self.assertTrue(dados)

    # --- achado 5: entrega repetida no meio da leitura do ML ---
    def test_entrega_repetida_enquanto_o_ml_responde_nao_conta_gasto_em_dobro(self):
        t = S.imagens_agente_proximo(True)["trabalho"]
        recusada = []

        def sugerir(*a, **k):
            try:  # o agente recebeu 504 do nginx e mandou "erro"
                S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_textos", "situacao": "erro",
                                           "mensagem": "HTTP 504", "tokens_entrada": 1000, "tokens_saida": 100})
            except S.ImagemConflito:
                recusada.append(True)
            return [{"categoria": "MLB1234", "categoria_nome": "Limpadores"}]

        self.mocks["ml_categoria_sugerir"].side_effect = sugerir
        S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_textos", "titulos": TITULOS, "descricao": "x",
                                   "tokens_entrada": 1000, "tokens_saida": 100})
        self.assertEqual(recusada, [True], "a 2ª entrega é recusada")
        self.assertEqual(self.gastos(), 1)
        self.assertEqual(self.trabalho(self.tid)["situacao"], "aguardando_atributos")

    def test_ml_lento_responde_ao_agente_sem_esperar(self):
        t = S.imagens_agente_proximo(True)["trabalho"]
        libera = threading.Event()

        def sugerir(*a, **k):
            libera.wait(5)
            return [{"categoria": "MLB1234", "categoria_nome": "Limpadores"}]

        self.mocks["ml_categoria_sugerir"].side_effect = sugerir
        with mock.patch.object(S, "ANUNCIO_AGENTE_ESPERA_ML", 0.05):
            r = S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_textos", "titulos": TITULOS, "descricao": "x"})
        self.assertEqual(r["situacao"], "buscando_categoria")
        libera.set()
        fim = time.time() + 5
        while time.time() < fim and self.trabalho(self.tid)["situacao"] == "buscando_categoria":
            time.sleep(0.02)
        self.assertEqual(self.trabalho(self.tid)["situacao"], "aguardando_atributos")

    def test_busca_de_categoria_que_nao_terminou_vira_proposta(self):
        with S.DB_LOCK:
            S.DB.execute("UPDATE imagem_trabalho_v2017 SET situacao='buscando_categoria', reservado_ate=1, textos_json=? "
                         "WHERE id=?", (json.dumps({"titulos": TITULOS}), self.tid))
            S.DB.commit()
        S.imagens_agente_proximo(True)
        t = self.trabalho(self.tid)
        self.assertEqual(t["situacao"], "proposta")
        self.assertIn("categoria", t["mensagem"].lower())

    # --- achado 6: nenhum título válido ---
    def test_nenhum_titulo_valido_vira_erro_com_o_motivo_e_o_gasto(self):
        t = S.imagens_agente_proximo(True)["trabalho"]
        r = S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_textos",
                                       "titulos": [{"texto": "X" * 61, "recomendado": True}], "descricao": "x",
                                       "tokens_entrada": 1000, "tokens_saida": 100})
        self.assertTrue(r["ok"])
        tr = self.trabalho(self.tid)
        self.assertEqual(tr["situacao"], "erro")
        self.assertIn("60", tr["mensagem"])
        self.assertEqual(self.gastos(), 1)

    # --- baixo: categoria sem atributo a pedir ---
    def test_categoria_sem_atributo_obrigatorio_vai_direto_para_a_proposta(self):
        self.mocks["ml_categoria_atributos"].return_value = {"categoria": "MLB1234", "nome": "Limpadores", "atributos": [
            {"id": "COLOR", "obrigatorio": False, "condicional": False, "valores": []}]}
        self.textos()
        self.assertEqual(self.trabalho(self.tid)["situacao"], "proposta")

    def test_vencida_sem_atributos_da_categoria_vira_proposta(self):
        with S.DB_LOCK:
            S.DB.execute("UPDATE imagem_trabalho_v2017 SET situacao='analisando', reservado_ate=1, textos_json=? "
                         "WHERE id=?", (json.dumps({"titulos": TITULOS}), self.tid))
            S.DB.commit()
        S.imagens_agente_proximo(True)
        self.assertEqual(self.trabalho(self.tid)["situacao"], "proposta")

    # --- achado 7: aceitar com a proposta mudando no meio ---
    def test_aceitar_reserva_a_proposta_e_trocar_categoria_no_meio_e_recusado(self):
        self.pronta()
        original = S.anuncio_rascunho_salvar
        recusada = []

        def salvar(*a, **k):
            try:
                S.anuncio_agente_trocar_categoria(self.tid, "MLB999", "outra_aba")
            except S.ImagemConflito:
                recusada.append(True)
            return original(*a, **k)

        with mock.patch.object(S, "anuncio_rascunho_salvar", side_effect=salvar):
            S.anuncio_agente_aceitar(self.tid, {"titulo": TITULOS[0]["texto"], "atributos": []}, "admin",
                                     S.anuncio_rascunho_ler(self.rid)["versao"])
        self.assertEqual(recusada, [True])
        self.assertEqual(self.trabalho(self.tid)["situacao"], "aceita")
        self.assertEqual(S.anuncio_rascunho_ler(self.rid)["categoria"], "MLB1234")

    def test_aceitar_de_proposta_que_mudou_nao_marca_aceita(self):
        self.pronta()
        v = S.anuncio_rascunho_ler(self.rid)["versao"]
        S.anuncio_agente_trocar_categoria(self.tid, "MLB999", "outra_aba")
        with self.assertRaises(S.ImagemConflito):
            S.anuncio_agente_aceitar(self.tid, {"titulo": TITULOS[0]["texto"], "atributos": []}, "admin", v)
        self.assertEqual(self.trabalho(self.tid)["situacao"], "aguardando_atributos")
        self.assertTrue(S._imagem_pasta(self.tid).is_dir(), "as fotos do agente continuam")

    # --- achado 8: pedir de novo com falha na cópia ---
    def test_novo_pedido_com_falha_na_copia_mantem_a_proposta_antiga(self):
        self.pronta()
        with mock.patch.object(S.shutil, "copyfile", side_effect=OSError("disco cheio")):
            with self.assertRaises(ValueError):
                S.anuncio_agente_pedir(self.rid, "admin")
        t = self.trabalho(self.tid)
        self.assertFalse(t["apagado_em"])
        self.assertEqual(S.anuncio_agente_estado(self.rid)["id"], self.tid)

    def test_cancelar_durante_a_copia_nao_deixa_pasta_orfa(self):
        S.anuncio_agente_cancelar(self.tid, "admin")
        original = S.shutil.copyfile
        novo = []

        def copiar(origem, destino):
            if not novo:
                tid = self.trabalho()["id"]
                novo.append(tid)
                S.anuncio_agente_cancelar(tid, "admin")
            return original(origem, destino)

        with mock.patch.object(S.shutil, "copyfile", side_effect=copiar):
            with self.assertRaises(S.ImagemConflito):
                S.anuncio_agente_pedir(self.rid, "admin")
        self.assertFalse(S._imagem_pasta(novo[0]).exists())
        self.assertTrue(self.trabalho(novo[0])["apagado_em"])

    # --- baixo: cancelado com o agente trabalhando ---
    def test_cancelado_com_o_agente_trabalhando_registra_o_gasto_uma_vez(self):
        t = S.imagens_agente_proximo(True)["trabalho"]
        S.anuncio_agente_cancelar(self.tid, "admin")
        corpo = {"id": t["id"], "etapa": "anuncio_textos", "titulos": TITULOS, "descricao": "x",
                 "tokens_entrada": 1000, "tokens_saida": 100}
        for _ in range(2):
            with self.assertRaises(S.ImagemConflito):
                S.anuncio_agente_entregar(dict(corpo))
        self.assertEqual(self.gastos(), 1)

    # --- baixo: origem do que o agente preencheu na ficha ---
    def test_ficha_preenchida_pelo_agente_tem_origem_propria_e_o_ml_corrige(self):
        S.ficha_salvar("ABC123", {"modelo": ""}, "admin", S.ficha_ler("ABC123")["versao"])
        with S.DB_LOCK:
            S.DB.execute("UPDATE anuncio_ficha_v2023 SET origem_json='{}' WHERE codigo='ABC123'")
            S.DB.execute("UPDATE anuncio_ficha_v2023 SET campos_json=? WHERE codigo='ABC123'",
                         (json.dumps({"marca": "Vonixx"}),))
            S.DB.commit()
        self.pronta()
        S.anuncio_agente_aceitar(self.tid, {"titulo": TITULOS[0]["texto"], "descricao": True, "atributos": []}, "admin",
                                 S.anuncio_rascunho_ler(self.rid)["versao"])
        ficha = S.ficha_ler("ABC123")
        self.assertEqual(ficha["campos"]["nome"], TITULOS[0]["texto"])
        self.assertEqual(ficha["origem"]["nome"], "agente")
        S.ficha_importar("ABC123", {"nome": "Título do anúncio no ML"}, "anuncio_ml")
        self.assertEqual(S.ficha_ler("ABC123")["campos"]["nome"], "Título do anúncio no ML")

    # --- baixo: fontes e valor sem limite ---
    def test_fontes_e_valor_de_atributo_tem_limite(self):
        fontes = {f"campo{i}": "https://x.com/" + "a" * 2000 for i in range(100)}
        fontes["ruim"] = "javascript:alert(1)"
        self.textos(fontes=fontes)
        guardadas = self.proposta()["fontes"]
        self.assertLessEqual(len(guardadas), 20)
        self.assertNotIn("javascript:alert(1)", guardadas.values())
        self.assertTrue(all(len(v) <= 300 for v in guardadas.values()))
        t = S.imagens_agente_proximo(True)["trabalho"]
        S.anuncio_agente_entregar({"id": t["id"], "etapa": "anuncio_atributos", "atributos": [
            {"id": "VOLUME", "valor": "5" * 1000, "origem": "rotulo"}]})
        self.assertLessEqual(len(self.proposta()["atributos"][0]["valor"]), 255)

    # --- revisão das correções ---
    def test_aceite_que_parou_no_meio_volta_a_proposta(self):
        self.pronta()
        with S.DB_LOCK:
            S.DB.execute("UPDATE imagem_trabalho_v2017 SET situacao='aceitando', reservado_ate=1 WHERE id=?", (self.tid,))
            S.DB.commit()
        self.assertEqual(S.anuncio_agente_estado(self.rid)["situacao"], "proposta")

    def test_busca_presa_destrava_pela_tela(self):
        with S.DB_LOCK:
            S.DB.execute("UPDATE imagem_trabalho_v2017 SET situacao='buscando_categoria', reservado_ate=1 WHERE id=?",
                         (self.tid,))
            S.DB.commit()
        self.assertEqual(S.anuncio_agente_estado(self.rid)["situacao"], "proposta")

    def test_falha_na_ficha_nao_derruba_o_aceite(self):
        self.pronta()
        with mock.patch.object(S, "ficha_importar", side_effect=RuntimeError("disco")):
            r = S.anuncio_agente_aceitar(self.tid, {"titulo": TITULOS[0]["texto"], "atributos": []}, "admin",
                                         S.anuncio_rascunho_ler(self.rid)["versao"])
        self.assertEqual(r["ficha_preenchida"], [])
        self.assertEqual(self.trabalho(self.tid)["situacao"], "aceita")
        self.assertEqual(S.anuncio_rascunho_ler(self.rid)["titulo"], TITULOS[0]["texto"])

    def test_tela_conhece_as_situacoes_novas(self):
        html = (Path(S.__file__).resolve().parent / "APP" / "anuncios.html").read_text(encoding="utf-8")
        self.assertIn('"buscando_categoria"', html)
        self.assertIn('"aceitando"', html)
        self.assertIn('agente: "do agente', html)


class TikTokRevisao(BaseTikTok):

    def _gravar_lido(self, pedido, lido, sku=None):
        linhas = self.fila()
        for l in linhas:
            if l.get("Pedido") == pedido and (sku is None or l.get("SKU") == sku):
                l["Lido"] = lido
        S.save_storage("conf_pedidos", S.get_storage("conf_pedidos")[0], S.json_dumps(linhas), False,
                       actor="conferente", actor_role="operador", source_screen="Conferência",
                       change_method="bip", client_ip="127.0.0.1")

    # --- achado 1: situação desconhecida ---
    def test_situacao_desconhecida_deixa_o_painel_em_atencao(self):
        self.receber([_sc("593001", status=987)])
        self.assertEqual(self.fila("593001"), [])
        saude = S._saude_tiktok_pedidos()
        self.assertEqual(saude["nivel"], "atencao")
        self.assertIn("987", saude["detalhe"] + saude.get("acao", "") + saude.get("titulo", "") + json.dumps(saude))
        self.receber([_sc("593002")])
        self.assertEqual(S._saude_tiktok_pedidos()["nivel"], "ok", "leitura sem código desconhecido limpa o aviso")

    # --- achado 2: cancelado inteiro já bipado ---
    def test_pedido_cancelado_inteiro_ja_bipado_fica_com_aviso(self):
        self.receber([_sc("593003")])
        self._gravar_lido("593003", 1)
        self.receber([], [_sc("593003", status=140)])
        linhas = self.fila("593003")
        self.assertEqual(len(linhas), 1, "a peça já bipada não some calada")
        self.assertIn("cancelado ou já enviado", linhas[0].get("Observação", ""))

    # --- achado 3: quantidade que não veio ---
    def test_quantidade_que_nao_veio_nao_tira_a_linha(self):
        itens = (("SK1", "0893600022", 1), ("SK2", "0893764272", 1))
        self.receber([_sc("593004", itens=itens)])
        o = _sc("593004", itens=itens)
        sk2 = o["sku_module"][1]
        sk2.pop("order_line_ids")
        sk2.pop("quantity")
        self.receber([o])
        self.assertEqual(sorted(l["SKU"] for l in self.fila("593004")), ["0893600022", "0893764272"])
        self.assertEqual(S._saude_tiktok_pedidos()["nivel"], "atencao")

    # --- baixo: Cliente trocado pelo apelido ---
    def test_cliente_da_linha_nao_e_trocado_pelo_apelido(self):
        self.receber([_sc("593005")])
        linhas = self.fila()
        for l in linhas:
            if l.get("Pedido") == "593005":
                l["Cliente"] = "João Pereira"
        S.save_storage("conf_pedidos", S.get_storage("conf_pedidos")[0], S.json_dumps(linhas), False,
                       actor="conferente", actor_role="operador", source_screen="Conferência",
                       change_method="teste", client_ip="127.0.0.1")
        self.receber([_sc("593005")])
        self.assertEqual(self.fila("593005")[0]["Cliente"], "João Pereira")

    # --- baixo: nome/CPF da nota nunca sobrescreve ---
    def test_cliente_fiscal_so_se_vazio_nao_sobrescreve(self):
        self.receber([_sc("593006")])
        S.salvar_cliente_fiscal("593006", "52998224725", "Outra Pessoa Silva", fonte="linx")
        r = S.salvar_cliente_fiscal("593006", "12345678909", "Maria Souza", fonte="tiktok_sc", so_se_vazio=True)
        self.assertFalse(r.get("ok"))
        with S.DB_LOCK:
            nome = S.DB.execute("SELECT nome_fiscal FROM cliente_fiscal WHERE order_key=?",
                                (S.normalize("593006"),)).fetchone()[0]
        self.assertEqual(nome, "Outra Pessoa Silva")

    # --- baixo: amostra sem comprador em módulo que não conhecemos ---
    def test_amostra_sem_dados_do_comprador_em_modulo_novo(self):
        o = _sc("593007", cpf="12345678909", cpf_nome="Maria Souza")
        o["invoice_module"] = {"receiver": {"name": "Maria Souza", "tax_id": "12345678909", "phone": "11999999999"}}
        texto = json.dumps(S._tiktok_sc_sem_comprador(o), ensure_ascii=False)
        for dado in ("Maria Souza", "12345678909", "11999999999"):
            self.assertNotIn(dado, texto)
        self.assertIn("593007", texto, "o formato continua")
        self.assertIn('"cpf_name"', texto, "as chaves continuam visíveis para conferir")

    def test_sincronizar_agora_tambem_respeita_a_extensao(self):
        S._tiktok_config_gravar("pedidos_extensao_epoch", str(int(time.time())), "x")
        with mock.patch.object(S, "tiktok_importar_pedidos") as importar:
            self.assertTrue(S.tiktok_sincronizar_iniciar())
            for t in threading.enumerate():
                if t.name == "tiktok-sincronizar-agora":
                    t.join(5)
        importar.assert_not_called()

    # --- latente: API e extensão juntas ---
    def test_api_nao_importa_enquanto_a_extensao_le(self):
        S._tiktok_config_gravar("pedidos_extensao_epoch", str(int(time.time())), "x")
        with mock.patch.object(S, "tiktok_status_payload", return_value={"connected": True}), \
                mock.patch.object(S, "tiktok_access_token", return_value="t"), \
                mock.patch.object(S, "tiktok_importar_pedidos") as importar:
            r = S.tiktok_rodada()
        self.assertFalse(r["importou"])
        self.assertEqual(r["motivo"], "extensao_em_uso")
        importar.assert_not_called()

    def test_constante_definida_uma_vez(self):
        fonte = Path(S.__file__).read_text(encoding="utf-8")
        self.assertEqual(fonte.count("\nTIKTOK_SC_TENTATIVAS_CPF ="), 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
