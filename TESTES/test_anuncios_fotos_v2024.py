"""V20.24 - Anuncios fase 2, tarefa 1: fotos da ficha (computador e Estudio de Imagens)."""

import base64
import json
import struct
import urllib.request
import zlib

from base import S, TesteNoromak, Cliente

SENHA = "SenhaTeste#2024"


def png(l=600, a=600) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", l, a, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" * 4)) + chunk(b"IEND", b""))


def jpg(l=800, a=600) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", a, l) + b"\x01\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def webp_x(l=1200, a=1200) -> bytes:
    corpo = b"WEBP" + b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00" + (l - 1).to_bytes(3, "little") + (a - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(corpo)) + corpo


def webp_l(l=700, a=650) -> bytes:
    bits = (l - 1) | ((a - 1) << 14)
    dados = b"\x2f" + bits.to_bytes(4, "little")
    corpo = b"WEBP" + b"VP8L" + struct.pack("<I", len(dados)) + dados
    return b"RIFF" + struct.pack("<I", len(corpo)) + corpo


def webp_lossy(l=640, a=480) -> bytes:
    dados = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", l, a)
    corpo = b"WEBP" + b"VP8 " + struct.pack("<I", len(dados)) + dados
    return b"RIFF" + struct.pack("<I", len(corpo)) + corpo


def b64(d: bytes) -> str:
    return base64.b64encode(d).decode()


class FotosFichaTeste(TesteNoromak):

    def test_jpeg_png_webp_dimensoes(self):
        self.assertEqual(S._imagem_dimensoes(png(600, 600)), ("png", 600, 600))
        self.assertEqual(S._imagem_dimensoes(jpg(800, 600)), ("jpg", 800, 600))
        self.assertEqual(S._imagem_dimensoes(webp_x(1200, 1200)), ("webp", 1200, 1200))
        self.assertEqual(S._imagem_dimensoes(webp_l(700, 650)), ("webp", 700, 650))
        self.assertEqual(S._imagem_dimensoes(webp_lossy(640, 480)), ("webp", 640, 480))

    def test_recusa_nao_imagem(self):
        self.assertIsNone(S._imagem_dimensoes(b"%PDF-1.4 nao sou imagem"))
        self.assertIsNone(S._imagem_dimensoes(b""))
        with self.assertRaises(ValueError):
            S.anuncio_foto_adicionar("P1", b"MZ\x90\x00 executavel", "luis")

    def test_recusa_maior_que_10mb(self):
        with self.assertRaises(ValueError):
            S.anuncio_foto_adicionar("P1", png() + b"\x00" * S.ANUNCIO_FOTO_MAX_BYTES, "luis")

    def test_recusa_menor_que_500px(self):
        with self.assertRaises(ValueError):
            S.anuncio_foto_adicionar("P1", png(400, 900), "luis")
        S.anuncio_foto_adicionar("P1", png(500, 900), "luis")  # no limite passa

    def test_limite_12(self):
        for _ in range(S.ANUNCIO_FOTOS_MAX):
            S.anuncio_foto_adicionar("P1", png(), "luis")
        with self.assertRaises(ValueError):
            S.anuncio_foto_adicionar("P1", png(), "luis")
        S.anuncio_foto_adicionar("P2", png(), "luis")  # outra ficha tem o proprio limite

    def test_arquivo_nomeado_pelo_id(self):
        f = S.anuncio_foto_adicionar("P1", png(), "luis")
        self.assertEqual(f["arquivo"], f"{f['id']}.png")
        self.assertTrue((S.ANUNCIO_FOTOS_DIR / f"{f['id']}.png").is_file())
        # nenhum caminho vindo de fora entra: so id e tipo conferido montam o caminho
        with self.assertRaises(ValueError):
            S._anuncio_foto_caminho(1, "../../x")
        self.assertEqual(f["ordem"], 1)
        self.assertEqual(S.anuncio_foto_adicionar("P1", jpg(), "luis")["ordem"], 2)

    def test_remover_apaga_arquivo(self):
        f = S.anuncio_foto_adicionar("P1", png(), "luis")
        caminho = S.ANUNCIO_FOTOS_DIR / f["arquivo"]
        with self.assertRaises(ValueError):
            S.anuncio_foto_remover("OUTRA", f["id"], "luis")  # so remove da propria ficha
        self.assertTrue(caminho.exists())
        S.anuncio_foto_remover("P1", f["id"], "luis")
        self.assertFalse(caminho.exists())
        self.assertEqual(S.anuncio_fotos("P1"), [])

    def test_ordenar(self):
        a = S.anuncio_foto_adicionar("P1", png(), "luis")["id"]
        b = S.anuncio_foto_adicionar("P1", jpg(), "luis")["id"]
        c = S.anuncio_foto_adicionar("P1", webp_x(), "luis")["id"]
        S.anuncio_foto_ordenar("P1", [c, a, b], "luis")
        self.assertEqual([f["id"] for f in S.anuncio_fotos("P1")], [c, a, b])
        with self.assertRaises(ValueError):
            S.anuncio_foto_ordenar("P1", [a, b], "luis")  # falta uma

    def test_do_estudio(self):
        dados = png(1000, 1000)
        with S.DB_LOCK:
            cur = S.DB.execute(
                "INSERT INTO imagem_trabalho_v2017(tipo,sku,situacao,criado_por,criado_em,atualizado_em,resultados_json)"
                " VALUES('principal','P1','pronta','luis','2026-11-05','2026-11-05',?)",
                (json.dumps([{"nome": "resultado_1.png", "papel": "resultado"}]),))
            S.DB.commit()
        tid = cur.lastrowid
        pasta = S.IMAGENS_DIR / str(tid)
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "resultado_1.png").write_bytes(dados)
        f = S.anuncio_foto_do_estudio("P1", tid, "resultado_1.png", "luis")
        self.assertEqual((f["origem"], f["largura"]), ("estudio", 1000))
        with self.assertRaises(ValueError):
            S.anuncio_foto_do_estudio("P1", tid, "../../x.png", "luis")
        st, c = self.cliente.pedir("GET", "/api/anuncios/estudio?busca=p1")
        self.assertEqual(st, 200, c)
        self.assertEqual(c["trabalhos"][0]["resultados"][0]["nome"], "resultado_1.png")
        st, c = self.cliente.pedir("GET", "/api/anuncios/estudio?busca=zzz")
        self.assertEqual(c["trabalhos"], [])

    def test_rotas_admin(self):
        st, c = self.cliente.pedir("POST", "/api/anuncios/foto", {"codigo": "P1", "base64": b64(png())})
        self.assertEqual(st, 200, c)
        fid = c["foto"]["id"]
        st, c = self.cliente.pedir("POST", "/api/anuncios/foto", {"codigo": "P1", "base64": b64(png(300, 300))})
        self.assertEqual(st, 400, c)
        req = urllib.request.Request(self.url_base + f"/api/anuncios/foto?id={fid}", headers={"Cookie": self.cliente.cookie})
        with urllib.request.urlopen(req, timeout=30) as r:
            self.assertEqual((r.status, r.headers["Content-Type"]), (200, "image/png"))
            self.assertEqual(r.read(), png())
        S.ficha_salvar("P1", {"nome": "x"}, "luis", None)
        st, c = self.cliente.pedir("GET", "/api/anuncios/ficha?codigo=P1")
        self.assertEqual(len(c["fotos"]), 1)
        st, c = self.cliente.pedir("POST", "/api/anuncios/foto/remover", {"codigo": "P1", "id": fid})
        self.assertEqual((st, c["fotos"]), (200, []))

    def test_rotas_permissao(self):
        with S.DB_LOCK:
            S.upsert_app_user("sup_2024", "Supervisor", "operador", True, SENHA, "teste",
                              permissions={"conferencia": True, "admin": True})
            S.DB.commit()
        sup = Cliente(self.url_base)
        sup.entrar("sup_2024", SENHA, area="admin")
        self.assertEqual(sup.pedir("GET", "/api/anuncios/estudio")[0], 200)
        self.assertEqual(sup.pedir("GET", "/api/anuncios/foto?id=999")[0], 404)
        for rota, corpo in (("/api/anuncios/foto", {"codigo": "P1", "base64": b64(png())}),
                            ("/api/anuncios/foto/remover", {"codigo": "P1", "id": 1}),
                            ("/api/anuncios/foto/ordenar", {"codigo": "P1", "ids": []})):
            self.assertEqual(sup.pedir("POST", rota, corpo)[0], 403)
        self.assertEqual(S.anuncio_fotos("P1"), [])
