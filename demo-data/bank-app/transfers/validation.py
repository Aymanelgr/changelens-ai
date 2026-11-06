"""Validation des virements de la banque fictive (données synthétiques)."""

from dataclasses import dataclass

from auth.strong_auth import require_strong_auth
from transfers.limits import daily_limit_for


class TransferRejected(Exception):
    """Le virement ne respecte pas les règles métier."""


@dataclass
class Account:
    iban: str
    balance: int
    phone: str
    segment: str = "standard"


def check_balance(account, amount):
    """Vérifie que le solde couvre le virement."""
    if amount > account.balance:
        raise TransferRejected("Solde insuffisant")


def check_daily_limit(account, amount, already_sent):
    """Applique le plafond journalier du segment client."""
    if already_sent + amount > daily_limit_for(account.segment):
        raise TransferRejected("Plafond journalier dépassé")


def check_positive(amount):
    if amount <= 0:
        raise TransferRejected("Montant invalide")


def validate_transfer(amount, account, already_sent=0):
    """Valide un virement avant son exécution."""
    check_positive(amount)
    check_balance(account, amount)
    if amount > 10000:
        require_strong_auth(account)
    check_daily_limit(account, amount, already_sent)
    return {
        "iban": account.iban,
        "amount": amount,
        "currency": "MAD",
        "status": "VALIDATED",
        "checked": True,
    }
