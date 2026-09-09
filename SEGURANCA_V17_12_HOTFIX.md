# Noromak V17.12 — Hotfix Fase 2

Causa do HTTP 502 encontrada:
- `/api/security/status` usava `CRITICAL_REAUTH`, mas a variável não havia sido criada.
- O Python encerrava a conexão com `NameError`; Nginx/Cloudflare convertiam isso em 502.
- O TOTP também dependia de `struct`, mas o import não havia sido incluído.
- `OWNER_REAUTH_FAILURES` também era referenciado sem inicialização.

Correções:
- Criados `CRITICAL_REAUTH` e `OWNER_REAUTH_FAILURES` em memória.
- Adicionado `import struct`.
- Limpeza automática de autorizações críticas e tentativas expiradas.
- Mantidas todas as proteções da V17.11.
- Nenhum segredo, senha, chave TOTP ou código de recuperação foi incluído no ZIP.

Após publicar:
1. health deve mostrar `17.12-SECURITY-PHASE2-HOTFIX`;
2. Admin > Segurança deve carregar sem 502;
3. `/expedicao/api/security/status` autenticado deve retornar 200;
4. teste Reautenticar OWNER com senha + Authenticator.
