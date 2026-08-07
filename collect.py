#!/usr/bin/env python3
"""
Maverick — Collecteur DATEX II Traficolor DiRIF
"""

import urllib.request
import xml.etree.ElementTree as ET
import json
import math
import csv
import io
import re
from datetime import datetime
from pyproj import Transformer

# Convertisseur Lambert 93 → WGS84
transformer = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)

def lambert93_to_wgs84(x, y):
    lon, lat = transformer.transform(x, y)
    return round(lat, 6), round(lon, 6)

# URL de base du répertoire Traficolor
BASE_URL = "https://transport.data.gouv.fr/resources/79166/download"
REFERENTIEL_URL = "https://transport.data.gouv.fr/resources/79167/download"

# Bounding box IDF autour des 4 lignes
BBOX = {
    "min_lat": 48.830, "max_lat": 49.020,
    "min_lng": 2.240,  "max_lng": 2.580,
}

LINE_TRACES = {
    "9509": [[48.980,2.271],[48.992,2.285],[48.995,2.303],[48.993,2.320],
             [48.991,2.374],[48.977,2.392],[48.974,2.401],[49.010,2.559]],
    "9517": [[48.948,2.255],[48.937,2.259],[48.920,2.344],[48.918,2.344],
             [48.918,2.352],[48.920,2.361],[48.976,2.506],[48.991,2.516],[49.011,2.559]],
    "350":  [[48.898,2.360],[48.943,2.434],[48.948,2.438],[48.950,2.450],
             [48.957,2.461],[48.961,2.489],[48.973,2.511],[48.984,2.516],
             [49.011,2.559],[49.003,2.564],[49.004,2.571]],
    "351":  [[48.848,2.398],[48.847,2.410],[48.865,2.412],[48.858,2.415],
             [48.922,2.470],[48.925,2.474],[48.929,2.480],[48.918,2.485],
             [48.995,2.524],[49.011,2.559],[49.003,2.564]],
}

TRAFICOLOR_MAP = {
    "freeFlow":   {"label":"Fluide",        "congestion":10, "status":"green"},
    "heavy":      {"label":"Dense",         "congestion":50, "status":"orange"},
    "congested":  {"label":"Congestionné",  "congestion":75, "status":"red"},
    "impossible": {"label":"Impossible",    "congestion":95, "status":"red"},
    "unknown":    {"label":"Inconnu",       "congestion":0,  "status":"unknown"},
}

def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Maverick/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def dist_m(lat1, lng1, lat2, lng2):
    dlat = (lat2 - lat1) * 111320
    dlng = (lng2 - lng1) * 111320 * math.cos(math.radians(lat1))
    return math.sqrt(dlat**2 + dlng**2)

def near_line(lat, lng, radius=400):
    for lid, pts in LINE_TRACES.items():
        for pt in pts:
            if dist_m(lat, lng, pt[0], pt[1]) < radius:
                return lid
    return None

def in_bbox(lat, lng):
    return BBOX["min_lat"] <= lat <= BBOX["max_lat"] and BBOX["min_lng"] <= lng <= BBOX["max_lng"]

def fetch_referentiel():
    """Parse le CSV référentiel avec coordonnées Lambert 93."""
    stations = {}
    try:
        raw = fetch_url(REFERENTIEL_URL).decode("utf-8-sig", errors="replace")
        sep = ";" if raw.count(";") > raw.count(",") else ","
        reader = csv.DictReader(io.StringIO(raw), delimiter=sep)
        for row in reader:
            norm = {k.strip().lower(): v.strip() for k, v in row.items() if k}
            # ID de la station
            sid = norm.get("code_pme") or norm.get("id") or norm.get("identifiant")
            if not sid or not sid.strip():
                continue
            # Coordonnées Lambert 93
            x_str = norm.get("x_deb") or norm.get("x")
            y_str = norm.get("y_deb") or norm.get("y")
            if not x_str or not y_str:
                continue
            try:
                x = float(x_str.replace(",", "."))
                y = float(y_str.replace(",", "."))
                if x == 0 or y == 0:
                    continue
                lat, lng = lambert93_to_wgs84(x, y)
                if in_bbox(lat, lng):
                    name = norm.get("axe") or norm.get("libelle") or sid
                    stations[sid] = {"lat": lat, "lng": lng, "name": name}
            except Exception:
                continue
        print(f"  {len(stations)} stations dans la bbox IDF")
        for sid, st in list(stations.items())[:3]:
            print(f"    {sid}: {st['name']} → {st['lat']},{st['lng']}")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  Erreur référentiel: {e}")
    return stations

def find_dirif_url():
    """Cherche le fichier XML DiRIF dans le répertoire Traficolor."""
    try:
        html = fetch_url(BASE_URL).decode("utf-8", errors="replace")
        # Chercher les liens vers des dossiers IDF/DIRIF
        folders = re.findall(r'href="([^"]+/)"', html)
        print(f"  Dossiers disponibles: {folders}")
        # Priorité aux dossiers IDF ou DIRIF
        idf_folder = None
        for f in folders:
            fname = f.lower()
            if any(k in fname for k in ["idf", "dirif", "paris", "sytadin", "ile"]):
                idf_folder = f
                break
        if not idf_folder:
            print("  Pas de dossier IDF trouvé, liste complète:")
            for f in folders:
                print(f"    {f}")
            return None
        # Explorer le dossier IDF
        folder_url = BASE_URL.rstrip("/download") + "/" + idf_folder
        print(f"  Dossier IDF: {folder_url}")
        html2 = fetch_url(folder_url).decode("utf-8", errors="replace")
        xmls = re.findall(r'href="([^"]+\.xml)"', html2)
        print(f"  Fichiers XML: {xmls}")
        if xmls:
            return folder_url + xmls[0]
        return None
    except Exception as e:
        print(f"  Erreur recherche DiRIF: {e}")
        return None

def fetch_traficolor(stations):
    """Parse le XML DATEX Traficolor DiRIF."""
    measures = []
    try:
        # Trouver l'URL du fichier XML DiRIF
        xml_url = find_dirif_url()
        if not xml_url:
            print("  URL DiRIF introuvable — tentative directe sur le flux principal")
            xml_url = BASE_URL

        print(f"  Téléchargement: {xml_url}")
        raw = fetch_url(xml_url)
        print(f"  Taille XML: {len(raw)} bytes")
        print(f"  Début: {raw[:200]}")

        # Parser le XML en gérant les namespaces
        text = raw.decode("utf-8", errors="replace")
        # Supprimer les namespaces pour simplifier
        text = re.sub(r'\sxmlns[^"]*"[^"]*"', '', text)
        text = re.sub(r'<[a-zA-Z]+:', '<', text)
        text = re.sub(r'</[a-zA-Z]+:', '</', text)
        root = ET.fromstring(text.encode("utf-8"))

        def stag(t):
            return t.split("}")[-1] if "}" in t else t

        for site in root.iter():
            if stag(site.tag) != "siteMeasurements":
                continue
            site_ref = None
            traf_status = None
            for child in site:
                ct = stag(child.tag)
                if ct == "measurementSiteReference":
                    site_ref = (child.get("id") or child.get("ref") or child.text or "").strip()
                elif ct == "measuredValue":
                    for sub in child.iter():
                        st = stag(sub.tag)
                        if st == "levelOfService" and sub.text:
                            traf_status = sub.text.strip()
                        elif st in TRAFICOLOR_MAP:
                            traf_status = st

            if not site_ref or not traf_status:
                continue
            if site_ref not in stations:
                continue
            s = stations[site_ref]
            lid = near_line(s["lat"], s["lng"])
            if not lid:
                continue
            info = TRAFICOLOR_MAP.get(traf_status, TRAFICOLOR_MAP["unknown"])
            measures.append({
                "id": site_ref, "name": s["name"],
                "lat": s["lat"], "lng": s["lng"],
                "line": lid, "status": info["status"],
                "label": info["label"], "congestion": info["congestion"],
                "raw": traf_status,
            })
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  Erreur traficolor: {e}")
    return measures

def main():
    print("Téléchargement référentiel...")
    stations = fetch_referentiel()

    print("\nRecherche flux DiRIF...")
    measures = fetch_traficolor(stations)
    print(f"\n{len(measures)} mesures filtrées sur nos lignes")

    by_line = {}
    for m in measures:
        lid = m["line"]
        if lid not in by_line:
            by_line[lid] = {"measures": [], "congestion": 0, "status": "green"}
        by_line[lid]["measures"].append(m)

    for lid, data in by_line.items():
        vals = [m["congestion"] for m in data["measures"]]
        avg = round(sum(vals) / len(vals)) if vals else 0
        data["congestion"] = avg
        data["status"] = "green" if avg < 30 else "orange" if avg < 60 else "red"
        print(f"  Ligne {lid}: {avg}% ({data['status']}) — {len(data['measures'])} stations")

    output = {
        "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "DiRIF / DATEX II Traficolor",
        "lines": by_line, "measures": measures,
    }
    with open("traffic.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("traffic.json écrit !")

if __name__ == "__main__":
    main()
