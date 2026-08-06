#!/usr/bin/env python3
"""
Maverick — Collecteur DATEX II Traficolor
Télécharge le flux XML DiRIF, filtre autour des 4 lignes, écrit traffic.json
"""

import urllib.request
import xml.etree.ElementTree as ET
import json
import math
from datetime import datetime

# ── URL flux DATEX Traficolor IDF ──
TRAFICOLOR_URL = "https://transport.data.gouv.fr/resources/79166/download"
REFERENTIEL_URL = "https://transport.data.gouv.fr/resources/79167/download"

# ── Bounding box autour des 4 lignes (avec marge de 500m) ──
# Couvre : Argenteuil → CDG, Ermont → CDG, Nation → CDG, Porte Chapelle → CDG
BBOX = {
    "min_lat": 48.830,
    "max_lat": 49.020,
    "min_lng": 2.240,
    "max_lng": 2.580,
}

# ── Points du tracé des 4 lignes (sous-échantillon pour filtrage fin) ──
LINE_TRACES = {
    "9509": [
        [48.980085, 2.271463], [48.992343, 2.285093], [48.995262, 2.302928],
        [48.993481, 2.319965], [48.991015, 2.374095], [48.976879, 2.391711],
        [48.973906, 2.401307], [49.010316, 2.559315],
    ],
    "9517": [
        [48.948169, 2.255212], [48.937166, 2.258783], [48.919999, 2.343823],
        [48.917690, 2.344139], [48.917824, 2.352229], [48.919564, 2.361336],
        [48.975823, 2.506361], [48.990645, 2.515786], [49.010533, 2.559397],
    ],
    "350": [
        [48.897746, 2.359558], [48.943245, 2.433633], [48.948302, 2.438097],
        [48.950284, 2.450364], [48.956492, 2.461032], [48.961128, 2.488829],
        [48.972857, 2.511116], [48.983909, 2.515643], [49.010533, 2.559397],
        [49.003339, 2.564324], [49.004370, 2.570944],
    ],
    "351": [
        [48.848355, 2.397844], [48.847221, 2.410367], [48.864686, 2.411968],
        [48.858493, 2.414546], [48.922124, 2.469657], [48.925436, 2.474305],
        [48.929210, 2.479671], [48.917866, 2.485024], [48.994642, 2.523592],
        [49.010533, 2.559397], [49.003339, 2.564324],
    ],
}

TRAFICOLOR_LABELS = {
    "unknown":     {"label": "Inconnu",      "congestion": 0,  "status": "unknown"},
    "freeFlow":    {"label": "Fluide",        "congestion": 10, "status": "green"},
    "heavy":       {"label": "Dense",         "congestion": 50, "status": "orange"},
    "congested":   {"label": "Congestionné",  "congestion": 75, "status": "red"},
    "impossible":  {"label": "Impossible",    "congestion": 95, "status": "red"},
}

def distance_m(lat1, lng1, lat2, lng2):
    """Distance approx en mètres entre deux points GPS."""
    dlat = (lat2 - lat1) * 111320
    dlng = (lng2 - lng1) * 111320 * math.cos(math.radians(lat1))
    return math.sqrt(dlat**2 + dlng**2)

def near_any_line(lat, lng, radius_m=300):
    """Vrai si le point est à moins de radius_m d'un tracé de ligne."""
    for line_id, pts in LINE_TRACES.items():
        for pt in pts:
            if distance_m(lat, lng, pt[0], pt[1]) < radius_m:
                return line_id
    return None

def in_bbox(lat, lng):
    return (BBOX["min_lat"] <= lat <= BBOX["max_lat"] and
            BBOX["min_lng"] <= lng <= BBOX["max_lng"])

def fetch_referentiel():
    """Télécharge le référentiel CSV des stations (id → lat/lng)."""
    stations = {}
    try:
        with urllib.request.urlopen(REFERENTIEL_URL, timeout=30) as r:
            lines = r.read().decode("utf-8").splitlines()
        header = None
        for line in lines:
            cols = line.split(";")
            if header is None:
                header = {c.strip().lower(): i for i, c in enumerate(cols)}
                continue
            try:
                sid  = cols[header.get("iu_ac", 0)].strip()
                lat  = float(cols[header.get("lat", 1)].strip().replace(",", "."))
                lng  = float(cols[header.get("lon", 2)].strip().replace(",", "."))
                name = cols[header.get("libelle", 3)].strip() if "libelle" in header else sid
                stations[sid] = {"lat": lat, "lng": lng, "name": name}
            except Exception:
                continue
    except Exception as e:
        print(f"Erreur référentiel : {e}")
    return stations

def fetch_traficolor(stations):
    """Parse le XML DATEX Traficolor et retourne les mesures filtrées."""
    results = []
    try:
        with urllib.request.urlopen(TRAFICOLOR_URL, timeout=30) as r:
            content = r.read()
        root = ET.fromstring(content)
        ns = {"d": "http://datex2.eu/schema/2/2_0"}

        # Parcourir les publications de mesures
        for pub in root.iter():
            tag = pub.tag.split("}")[-1] if "}" in pub.tag else pub.tag

            if tag == "siteMeasurements":
                site_ref = None
                traf_val = None
                for child in pub:
                    ctag = child.tag.split("}")[-1]
                    if ctag == "measurementSiteReference":
                        site_ref = child.get("id") or child.text
                    elif ctag == "measuredValue":
                        for sub in child.iter():
                            stag = sub.tag.split("}")[-1]
                            if stag == "trafficConcentration" or stag == "levelOfService":
                                traf_val = sub.text
                            if stag in TRAFICOLOR_LABELS:
                                traf_val = stag

                if site_ref and traf_val and site_ref in stations:
                    st = stations[site_ref]
                    if not in_bbox(st["lat"], st["lng"]):
                        continue
                    line_id = near_any_line(st["lat"], st["lng"])
                    if not line_id:
                        continue
                    info = TRAFICOLOR_LABELS.get(traf_val, TRAFICOLOR_LABELS["unknown"])
                    results.append({
                        "id":         site_ref,
                        "name":       st["name"],
                        "lat":        st["lat"],
                        "lng":        st["lng"],
                        "line":       line_id,
                        "status":     info["status"],
                        "label":      info["label"],
                        "congestion": info["congestion"],
                        "raw":        traf_val,
                    })
    except Exception as e:
        print(f"Erreur traficolor : {e}")
    return results

def main():
    print("Téléchargement référentiel...")
    stations = fetch_referentiel()
    print(f"  {len(stations)} stations chargées")

    print("Téléchargement Traficolor...")
    measures = fetch_traficolor(stations)
    print(f"  {len(measures)} mesures filtrées sur nos lignes")

    # Agréger par ligne
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
        "lines": by_line,
        "measures": measures,
    }

    with open("traffic.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("traffic.json écrit !")

if __name__ == "__main__":
    main()
