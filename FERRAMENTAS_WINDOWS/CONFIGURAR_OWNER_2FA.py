#!/usr/bin/env python3
import base64,hashlib,os,secrets
secret=base64.b32encode(os.urandom(20)).decode().rstrip("=")
codes=[secrets.token_hex(4).upper() for _ in range(10)]
print("EXP_OWNER_TOTP_SECRET="+secret)
print("EXP_OWNER_RECOVERY_HASHES="+",".join(hashlib.sha256(c.encode()).hexdigest() for c in codes))
print("EXP_CRITICAL_REAUTH_TTL_SECONDS=300")
print("\nURI autenticador:\notpauth://totp/Noromak%3Aluis?secret="+secret+"&issuer=Noromak&digits=6&period=30")
print("\nCODIGOS DE RECUPERACAO - GUARDE OFFLINE:")
for c in codes: print(c)
