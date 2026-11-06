"""Authentification renforcée de la banque fictive."""

import secrets

CODE_LENGTH = 6


def send_code(phone, code):
    return f"Code {code} envoyé au {phone}"


def require_strong_auth(account):
    """Demande une confirmation par code avant de poursuivre."""
    code = secrets.token_hex(CODE_LENGTH // 2)
    send_code(account.phone, code)
    return code
