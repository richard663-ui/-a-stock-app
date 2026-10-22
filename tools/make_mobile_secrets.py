"""Generate the phone-login secrets for Supabase Edge Functions (run it yourself).

    python tools\\make_mobile_secrets.py

Prompts for a NEW phone access code (hidden input; use a long passphrase: the
old hash was public in git history, so the old code must not be reused),
then prints values to paste into Supabase Dashboard -> Edge Functions ->
Secrets. Nothing is written to disk or sent anywhere.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import secrets
import sys

ITERATIONS = 600_000


def derive(code: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, iterations, dklen=32)).decode()


def main() -> int:
    code = getpass.getpass("New phone access code (min 12 characters, not the old one): ")
    if len(code) < 12:
        print("Too short. Use at least 12 characters, ideally a 4-5 word passphrase.")
        return 1
    if code != getpass.getpass("Repeat: "):
        print("The two entries differ.")
        return 1
    salt = secrets.token_bytes(16)
    values = {
        "ASTOCK_PASSWORD_HASH": derive(code, salt),
        "ASTOCK_PASSWORD_SALT": base64.b64encode(salt).decode(),
        "ASTOCK_PASSWORD_ITERATIONS": str(ITERATIONS),
        "ASTOCK_SESSION_SECRET": base64.b64encode(secrets.token_bytes(32)).decode(),
        "ASTOCK_SESSION_VERSION": "1",
    }
    print("\nPaste these five secrets into Supabase -> Edge Functions -> Secrets (then close this window):\n")
    for key, value in values.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
