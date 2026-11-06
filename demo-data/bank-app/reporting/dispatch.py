"""Choix dynamique d'un exporteur : l'analyse statique ne peut pas suivre cet appel."""

import reporting.exporters as exporters


def export_report(kind, data):
    handler = getattr(exporters, f"to_{kind}")
    return handler(data)
