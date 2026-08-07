#!/usr/bin/env python3
"""
Maverick — Diagnostic DATEX II Traficolor
Affiche le contenu brut des fichiers pour comprendre leur structure
"""

import urllib.request
import json
from datetime import datetime

TRAFICOLOR_URL  = "https://transport.data.gouv.fr/resources/79166/download"
REFERENTIEL_URL = "https://transport.data.gouv.fr/resources/79167/download"

def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Maverick/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(), r.headers.get("Content-Type", "")

def main():
    # ── Référentiel ──
    print("=== RÉFÉRENTIEL ===")
    try:
        raw, ct = fetch_url(REFERENTIEL_URL)
        print(f"Content-Type: {ct}")
        print(f"Taille: {len(raw)} bytes")
        text = raw.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        print(f"Lignes: {len(lines)}")
        for i, l in enumerate(lines[:5]):
            print(f"  [{i}] {l[:200]}")
    except Exception as e:
        print(f"Erreur: {e}")

    # ── Traficolor ──
    print("\n=== TRAFICOLOR XML ===")
    try:
        raw, ct = fetch_url(TRAFICOLOR_URL)
        print(f"Content-Type: {ct}")
        print(f"Taille: {len(raw)} bytes")
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        print(f"Lignes: {len(lines)}")
        for i, l in enumerate(lines[:15]):
            print(f"  [{i}] {l[:300]}")
    except Exception as e:
        print(f"Erreur: {e}")

    # Écrire un JSON vide pour éviter l'erreur de commit
    output = {
        "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "DiRIF / DATEX II Traficolor",
        "lines": {}, "measures": []
    }
    with open("traffic.json", "w") as f:
        json.dump(output, f)
    print("\nDiagnostic terminé.")

if __name__ == "__main__":
    main()
