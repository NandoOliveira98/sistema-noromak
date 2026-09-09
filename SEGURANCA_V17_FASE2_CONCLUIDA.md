# Noromak V17.11 — Segurança Máxima — Fase 2 concluída

## Proteções ativas
- OWNER exclusivo definido por EXP_OWNER_USUARIO.
- Conta OWNER não pode ser rebaixada/desativada pela sincronização normal.
- 2FA TOTP do OWNER via segredo somente no /home/expedicao/.env.
- Códigos de recuperação armazenados somente como hashes.
- Reautenticação para ações críticas: senha + 2FA.
- Liberação crítica isolada por sessão e válida por 5 minutos (configurável).
- Máximo de 5 falhas de reautenticação em 5 minutos por sessão.
- Alteração de usuários/admins exige OWNER + reautenticação.
- Desconectar Mercado Livre/Shopee exige OWNER + reautenticação.
- Renovar token do agente Zebra exige OWNER + reautenticação.
- Painel Admin > Segurança mostra estado real da proteção sem revelar segredos.
- Headers HTTP de segurança e PBKDF2 600.000 mantidos.
- Exclusão remota do servidor continua DESATIVADA.

## Correções importantes V17.11
- Corrigida verificação da senha do OWNER da V17.10.
- Corrigida janela crítica para ser exclusiva da sessão atual.
- Endpoint /api/security/status passa a mostrar 2FA e estado da reautenticação.
- Edição de usuários não é mais persistida automaticamente; exige botão Salvar + confirmação OWNER.

## Teste sugerido
1. Abra Admin > Segurança.
2. Confirme OWNER=luis, 2FA=Ativo e recovery codes configurados.
3. Clique Reautenticar OWNER.
4. Informe senha e código atual do Authenticator.
5. Estado deve mudar para Liberado por ~300 segundos.
6. Tente salvar usuários ou renovar token Zebra: dentro da janela não pede novamente; depois de expirar pede nova confirmação.
