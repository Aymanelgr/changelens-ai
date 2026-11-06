"""Alertes pour l'équipe conformité."""


def flag_large_transfer(amount):
    """Signale les gros virements au tableau de bord conformité."""
    if amount >= 10000:
        return "ALERTE"
    return "OK"
