"""Builds demo-data/docs/procedure_controles.pdf, so the demo shows a real page citation.

    python scripts/make_demo_pdf.py    (needs pymupdf: use the backend's virtual environment)

Page 2 states the old value, which rules find. Page 3 describes the same rule without any number,
which only search and the LLM can find.
"""

from pathlib import Path

import pymupdf

OUTPUT = Path(__file__).resolve().parent.parent / "demo-data" / "docs" / "procedure_controles.pdf"

PAGES = [
    [
        ("Procédure de contrôle des opérations", 20),
        ("Document fictif de la Banque Démo, destiné aux agents du service conformité.", 11),
        ("1 Objet", 15),
        (
            "Cette procédure décrit les vérifications réalisées par le service conformité "
            "sur les opérations des clients.",
            11,
        ),
    ],
    [
        ("2 Opérations concernées", 15),
        ("2.1 Virements", 13),
        (
            "Tout virement supérieur à 10 000 MAD est soumis à une vérification "
            "supplémentaire avant son exécution.",
            11,
        ),
    ],
    [
        ("3 Rôle de l'agent", 15),
        ("3.1 Traitement d'une alerte", 13),
        (
            "Lorsqu'un virement dépasse le plafond de vérification, l'agent attend la "
            "confirmation du client par SMS avant de libérer l'opération.",
            11,
        ),
        ("3.2 Archivage", 13),
        ("Les justificatifs sont classés dans le dossier du client pendant cinq ans.", 11),
    ],
]


def main() -> None:
    document = pymupdf.open()
    for blocks in PAGES:
        page = document.new_page()
        y = 72.0
        for text, size in blocks:
            box = pymupdf.Rect(60, y, 535, y + 90)
            page.insert_textbox(box, text, fontsize=size, fontname="helv")
            y += size * 2 + (28 if size < 13 else 6)
    document.set_metadata({"title": "Procédure de contrôle (fictif)"})
    OUTPUT.write_bytes(document.tobytes(garbage=4, deflate=True))
    document.close()
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
