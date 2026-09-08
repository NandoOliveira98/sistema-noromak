# V17 Segurança Máxima — Fase 1 de teste

Esta versão cria a camada OWNER sem adicionar nenhum botão de exclusão.

No servidor, configure:
EXP_OWNER_USUARIO=seu_login_atual
EXP_PASSWORD_ITERATIONS=600000
EXP_SESSION_TTL_SECONDS=14400
EXP_SECURITY_HEADERS=1

Proteções desta fase:
- OWNER é sempre Admin, ativo e com todos os módulos.
- Somente OWNER altera usuários/administradores.
- Somente OWNER desconecta Mercado Livre/Shopee.
- Somente OWNER renova credencial do agente de impressão.
- Senhas novas usam PBKDF2 600.000 iterações.
- Sessão web padrão: 4 horas.
- Headers de segurança HTTP.
- /expedicao/api/security/status mostra o estado da camada OWNER.

IMPORTANTE:
- EXP_OWNER_USUARIO cai no EXP_ADMIN_USUARIO atual se não for definido, evitando bloqueio no primeiro teste.
- Não coloque senhas, Partner Key, Client Secret ou chaves privadas no Git/ZIP.
- Exclusão protegida, 2FA/TOTP e backups imutáveis entram somente após validar esta fase.
