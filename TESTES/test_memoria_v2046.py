"""V20.46 — memória presa do Noromak devolvida ao sistema.

Medido em produção (09/11): 410 MB (RSS + swap) com 54 min no ar, ~200 MB em 20 blocos
anônimos de 5–17 MB — as "arenas" que o malloc do glibc cria para cada thread (103
threads) e não devolve. Num servidor de 1 GB isso vai para o swap e o cadastro e o
romaneio passavam de 60 s (o nginx cortava: "erro HTTP" na extensão).

O que não pode quebrar:
- fora do Linux (testes no Windows) nada acontece e nada quebra;
- o limite de arenas é pedido ao glibc com M_ARENA_MAX (-8) = 2;
- cada devolução mede antes/depois e guarda o que voltou (histórico curto, total);
- falha do malloc_trim nunca derruba o worker;
- o Painel de saúde mostra quanto o Noromak usa e quanto devolveu;
- o worker está no Painel de saúde e sobe no main().
"""

import inspect
import unittest
import unittest.mock

from base import S


class LibcFalsa:
    def __init__(self, falhar: bool = False):
        self.chamadas: list[tuple] = []
        self.falhar = falhar

    def mallopt(self, param, valor):
        self.chamadas.append(("mallopt", param, valor))
        return 1

    def malloc_trim(self, pad):
        self.chamadas.append(("malloc_trim", pad))
        if self.falhar:
            raise OSError("falhou")
        return 1


def leitor(*valores):
    seq = list(valores)

    def ler():
        return seq.pop(0) if seq else None
    return ler


class Memoria(unittest.TestCase):
    def setUp(self):
        S._MEMORIA_ESTADO.update({"ultimo": None, "historico": [], "total_devolvido_mb": 0.0, "vezes": 0})

    def test_limita_arenas_com_m_arena_max(self):
        libc = LibcFalsa()
        self.assertTrue(S.memoria_limitar_arenas(libc))
        self.assertIn(("mallopt", -8, 2), libc.chamadas)

    def test_sem_libc_nao_faz_nada(self):
        with unittest.mock.patch.object(S, "_memoria_libc", return_value=None):
            self.assertIsNone(S.memoria_limitar_arenas())
            self.assertIsNone(S.memoria_devolver())

    def test_devolve_e_registra(self):
        libc = LibcFalsa()
        r = S.memoria_devolver(libc, leitor({"rss_mb": 220.0, "swap_mb": 190.0},
                                            {"rss_mb": 150.0, "swap_mb": 60.0}))
        self.assertIn(("malloc_trim", 0), libc.chamadas)
        self.assertEqual(r["antes_mb"], 410.0)
        self.assertEqual(r["depois_mb"], 210.0)
        self.assertEqual(r["devolvido_mb"], 200.0)
        self.assertEqual(S._MEMORIA_ESTADO["vezes"], 1)
        self.assertEqual(S._MEMORIA_ESTADO["total_devolvido_mb"], 200.0)
        self.assertIs(S._MEMORIA_ESTADO["ultimo"], r)

    def test_crescer_entre_as_leituras_nao_conta_negativo(self):
        r = S.memoria_devolver(LibcFalsa(), leitor({"rss_mb": 100.0, "swap_mb": 0.0},
                                                   {"rss_mb": 105.0, "swap_mb": 0.0}))
        self.assertEqual(r["devolvido_mb"], 0.0)

    def test_historico_curto(self):
        for _ in range(S.MEMORIA_HISTORICO_MAX + 5):
            S.memoria_devolver(LibcFalsa(), leitor({"rss_mb": 10.0, "swap_mb": 0.0},
                                                   {"rss_mb": 9.0, "swap_mb": 0.0}))
        self.assertEqual(len(S._MEMORIA_ESTADO["historico"]), S.MEMORIA_HISTORICO_MAX)

    def test_falha_do_trim_nao_derruba(self):
        r = S.memoria_devolver(LibcFalsa(falhar=True), leitor({"rss_mb": 10.0, "swap_mb": 0.0},
                                                               {"rss_mb": 10.0, "swap_mb": 0.0}))
        self.assertIsNone(r)
        self.assertEqual(S._MEMORIA_ESTADO["vezes"], 0)

    def test_painel_mostra_o_uso_e_o_devolvido(self):
        S.memoria_devolver(LibcFalsa(), leitor({"rss_mb": 220.0, "swap_mb": 190.0},
                                               {"rss_mb": 150.0, "swap_mb": 60.0}))
        with unittest.mock.patch.object(S, "memoria_processo", return_value={"rss_mb": 150.0, "swap_mb": 60.0}):
            item = S._saude_memoria_noromak()
        self.assertEqual(item["id"], "memoria_noromak")
        self.assertEqual(item["nivel"], "ok")
        self.assertIn("210 MB", item["resumo"])
        self.assertIn("200 MB", item["resumo"])
        with unittest.mock.patch.object(S, "memoria_processo", return_value={"rss_mb": 300.0, "swap_mb": 150.0}):
            self.assertEqual(S._saude_memoria_noromak()["nivel"], "atencao")
        with unittest.mock.patch.object(S, "memoria_processo", return_value=None):
            self.assertEqual(S._saude_memoria_noromak()["nivel"], "info")
        ids = [i["id"] for i in S._saude_maquina()]
        self.assertIn("memoria_noromak", ids)

    def test_worker_no_painel_e_no_main(self):
        self.assertIn("memoria_loop", S.SAUDE_WORKERS_INFO)
        fonte = inspect.getsource(S.main)
        self.assertIn("_worker_protegido(memoria_loop)", fonte)
        self.assertIn("memoria_thread.start()", fonte)


if __name__ == "__main__":
    unittest.main()
