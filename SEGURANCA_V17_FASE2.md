# V17.10 Fase 2
- 2FA TOTP exclusivo do OWNER.
- Códigos de recuperação armazenados no servidor somente como hashes.
- Ações críticas exigem senha + 2FA novamente; autorização temporária padrão de 5 minutos.
- Auditoria de reautenticação.
- Segredos ficam somente em /home/expedicao/.env, nunca no ZIP/Git.
- Mantém a Fase 1 e não adiciona exclusão remota.

Ativação:
python3 /home/expedicao/app/FERRAMENTAS_WINDOWS/CONFIGURAR_OWNER_2FA.py
Depois copie as três linhas EXP_OWNER_* / EXP_CRITICAL_* para /home/expedicao/.env, cadastre a URI no autenticador, guarde os recovery codes offline e reinicie o serviço.
NÃO envie o segredo ou recovery codes pelo chat.
