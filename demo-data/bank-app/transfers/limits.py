"""Plafonds de virement. Valeurs fictives."""

DAILY_LIMITS = {"standard": 50000, "premium": 200000}

# Nombre d'entrées gardées en mémoire : rien à voir avec un montant.
RECENT_TRANSFERS_CACHE_SIZE = 10000


def daily_limit_for(segment):
    return DAILY_LIMITS.get(segment, DAILY_LIMITS["standard"])
