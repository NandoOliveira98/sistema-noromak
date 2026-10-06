"""V18.70 — teste REAL do "Cadastro + crédito pela API" em UM cliente do Linx.

Usa as mesmas funções do botão (servidor_web.py), com um banco temporário: não
toca no banco do Noromak, não sobe servidor e não roda worker nenhum.

Credenciais: só nas variáveis de ambiente da janela onde você roda (nunca no
arquivo). No PowerShell:

    $env:LINX_API_CHAVE   = "chave da assinatura (portal auto-smartapi)"
    $env:LINX_API_USUARIO = "usuario de integracao do Linx DMS"
    $env:LINX_API_SENHA   = "senha desse usuario"

1) Só leitura (mostra o cadastro e o que seria enviado):
    python FERRAMENTAS_WINDOWS\\teste_linx_credito.py 74592343115

2) Gravar de verdade (pede para digitar SIM antes):
    python FERRAMENTAS_WINDOWS\\teste_linx_credito.py 74592343115 --gravar [--nome "NOME DA NF-E"]

O cadastro de ANTES e o de DEPOIS ficam salvos em linx_teste_<cpf>_<hora>.json,
na pasta atual, para conferir ou corrigir no Apollo.
"""
import json
import os
import sys
import tempfile
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(AQUI)
TMP = tempfile.mkdtemp(prefix="linx_teste_")
os.environ.update({"EXP_DB_PATH": os.path.join(TMP, "teste.db"), "EXP_BACKUP_DIR": os.path.join(TMP, "bk"),
                   "EXP_MIGRATION_DIR": os.path.join(TMP, "mig"), "EXP_WORKERS": "0"})
sys.path.insert(0, REPO)
import servidor_web as S  # noqa: E402

CAMPOS_TELA = (("Nome", "Nome"), ("SituacaoCredito", "Cadastro (421)  [1 = Aprovado]"),
               ("BloqueioCredito", "Situação de Crédito (420)  [1 = Crédito normal]"),
               ("DataLimiteCredito", "Validade de Crédito"), ("DDDTelefone", "DDD"), ("Telefone", "Telefone"),
               ("DDDCelular", "DDD celular"), ("Celular", "Celular"), ("EmailCasa", "E-mail"),
               ("LogradouroEntrega", "Endereço"), ("BairroEntrega", "Bairro"), ("MunicipioEntrega", "Cidade"))


def mostrar(titulo, reg):
    print(f"\n== {titulo} ==")
    for campo, rotulo in CAMPOS_TELA:
        valor = reg.get(campo, "(a consulta não devolve)")
        print(f"  {rotulo:<48} {valor}")


def main():
    args = [a for a in sys.argv[1:]]
    if not args or not args[0].replace(".", "").replace("-", "").isdigit():
        print(__doc__)
        return 1
    documento = "".join(ch for ch in args[0] if ch.isdigit())
    gravar = "--gravar" in args
    nome = args[args.index("--nome") + 1] if "--nome" in args else ""
    faltando = S.linx_configuracao_faltando()
    if faltando:
        print("Faltam as variáveis de ambiente: " + ", ".join(faltando))
        return 1

    registros = S._linx_registros_do_documento(documento)
    codigos = {str(r.get("Cliente")) for r in registros}
    if len(codigos) != 1:
        print(f"O Linx devolveu {len(codigos)} clientes para este CPF/CNPJ ({', '.join(codigos) or 'nenhum'}). Parei.")
        return 1
    antes = registros[0]
    cliente = str(antes["Cliente"])
    nome_linx = S._linx_sem_acento(" ".join((nome or str(antes.get("Nome") or "")).split())).upper()
    print(f"Cliente {cliente} — {antes.get('Nome')}")
    mostrar("CADASTRO ATUAL NO LINX", antes)
    print(f"\nO botão vai gravar: nome {nome_linx!r}, telefones 11 111111111, e-mail {S.LINX_CADASTRO_EMAIL},")
    print("Cadastro (421) = 1, Situação de Crédito (420) = 1, Validade = hoje; resto do cadastro como está, sem acento.")
    if not gravar:
        corpo = S._linx_corpo_simplificado(antes, documento)
        print("\nCorpo do Simplificado (sem os 3 campos de crédito):")
        print(json.dumps(corpo, ensure_ascii=False, indent=1))
        print("\nSó leitura: nada foi gravado. Para gravar, rode de novo com --gravar.")
        return 0

    if input("\nDigite SIM para gravar no Linx: ").strip() != "SIM":
        print("Cancelado. Nada foi gravado.")
        return 0
    arquivo = f"linx_teste_{documento}_{datetime.now():%Y%m%d_%H%M%S}.json"
    registro = {"antes": antes}
    # Mesma sequência do linx_cadastro_atualizar: AAR (nome/telefone/e-mail) e depois o crédito.
    S.linx_chamar("POST", f"{S.LINX_PATH_CLIENTES}/Geral/ManutencaoClienteSimplificado/AtualizarCadastroClienteAAR", {
        "Nome": nome_linx, "Email": S.LINX_CADASTRO_EMAIL, "CPFCNPJ": documento,
        "DDDTelefone": S.LINX_CADASTRO_DDD, "Telefone": S.LINX_CADASTRO_TELEFONE,
        "DDDCelular": S.LINX_CADASTRO_DDD, "Celular": S.LINX_CADASTRO_TELEFONE,
        "Empresa": S.LINX_EMPRESA, "Revenda": S.LINX_REVENDA, "Cliente": int(cliente),
    })
    meio = S._linx_registro_do_cliente(documento, cliente)
    registro["depois_aar"] = meio
    print(f"\nNome depois do AAR: {meio.get('Nome')!r} ({'OK' if S.normalize(meio.get('Nome')) == S.normalize(nome_linx) else 'NÃO PEGOU'})")
    try:
        credito = S._linx_credito_atualizar(documento, cliente)
    except S.LinxErro as exc:
        credito = {"ok": False, "error": str(exc)}
    registro["credito"] = credito
    depois = S._linx_registro_do_cliente(documento, cliente)
    registro["depois"] = depois
    with open(arquivo, "w", encoding="utf-8") as f:
        json.dump(registro, f, ensure_ascii=False, indent=1, default=str)
    mostrar("CADASTRO DEPOIS", depois)
    print("\n== CRÉDITO ==")
    if not credito.get("ok"):
        print("  A Linx recusou ou não confirmou: " + str(credito.get("error")))
    else:
        rotulos = {"cadastro_aprovado": "Cadastro (421) = Aprovado", "situacao_credito": "Situação de Crédito (420) = Crédito normal",
                   "validade": "Validade de Crédito = hoje"}
        for chave, rotulo in rotulos.items():
            v = credito["campos"][chave]
            marca = "OK" if v is True else "NÃO PEGOU" if v is False else "? (a consulta não devolve — veja no Apollo)"
            print(f"  {rotulo:<45} {marca}")
        for d in credito.get("outros_campos_alterados") or []:
            print(f"  ATENÇÃO: {d['campo']} mudou de {d['antes']!r} para {d['depois']!r}")
    print(f"\nAntes e depois salvos em {arquivo}. Confira o cliente {cliente} no Apollo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
