#!/usr/bin/env python3
"""
Maverick — Collecteur DATEX II Traficolor
Télécharge le flux XML DiRIF, filtre autour des 4 lignes, écrit traffic.json
"""

import urllib.request
import xml.etree.ElementTree as ET
import json
import math
import csv
import io
from datetime import datetime

TRAFICOLOR_URL  = "https://transport.data.gouv.fr/resources/79166/download"
REFERENTIEL_URL = "https://transport.data.gouv.fr/resources/79167/download"

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
    "freeFlow":   {"label":"Fluide",       "congestion":10, "status":"green"},
    "heavy":      {"label":"Dense",        "congestion":50, "status":"orange"},
    "congested":  {"label":"Congestionné", "congestion":75, "status":"red"},
    "impossible": {"label":"Impossible",   "congestion":95, "status":"red"},
    "unknown":    {"label":"Inconnu",      "congestion":0,  "status":"unknown"},
}

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

def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Maverick/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def fetch_referentiel():
    stations = {}
    try:
        raw = fetch_url(REFERENTIEL_URL).decode("utf-8-sig")
        # Détecter le séparateur
        sep = ";" if ";" in raw.splitlines()[0] else ","
        reader = csv.DictReader(io.StringIO(raw), delimiter=sep)
        # Normaliser les noms de colonnes (minuscules, sans espaces)
        for row in reader:
            norm = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items()}
            # Chercher les colonnes lat/lng sous différents noms possibles
            lat_key = next((k for k in norm if k in ["lat","latitude","y","wgs84_lat"]), None)
            lng_key = next((k for k in norm if k in ["lon","lng","longitude","x","wgs84_lon","wgs84_lng"]), None)
            id_key  = next((k for k in norm if k in ["id","iu_ac","identifiant","numero","ref"]), None)
            nm_key  = next((k for k in norm if k in ["libelle","nom","name","label","description"]), None)
            if not (lat_key and lng_key and id_key):
                continue
            try:
                lat = float(norm[lat_key].replace(",", "."))
                lng = float(norm[lng_key].replace(",", "."))
                sid = norm[id_key]
                name = norm.get(nm_key, sid) if nm_key else sid
                if in_bbox(lat, lng):
                    stations[sid] = {"lat": lat, "lng": lng, "name": name}
            except Exception:
                continue
        print(f"  {len(stations)} stations dans la bbox IDF")
    except Exception as e:
        print(f"  Erreur référentiel: {e}")
    return stations

def fetch_traficolor(stations):
    measures = []
    try:
        raw = fetch_url(TRAFICOLOR_URL)
        # Nettoyer les namespaces pour simplifier le parsing
        content = raw.decode("utf-8").replace(' xmlns="', ' xmlns_ignored="')
        # Re-encoder
        raw_clean = content.encode("utf-8")
        root = ET.fromstring(raw_clean)

        def strip(tag):
            return tag.split("}")[-1] if "}" in tag else tag

        # Parcourir tous les éléments siteMeasurements
        for site in root.iter():
            if strip(site.tag) != "siteMeasurements":
                continue

            site_ref = None
            traf_status = None

            for child in site:
                ctag = strip(child.tag)
                if ctag == "measurementSiteReference":
                    site_ref = child.get("id") or child.get("ref") or child.text
                    if site_ref:
                        site_ref = site_ref.strip()

                elif ctag == "measuredValue":
                    # Chercher le levelOfService en profondeur
                    for sub in child.iter():
                        stag = strip(sub.tag)
                        if stag == "levelOfService" and sub.text:
                            traf_status = sub.text.strip()
                        elif stag in TRAFICOLOR_MAP:
                            traf_status = stag

            if not site_ref or not traf_status:
                continue

            # Géolocaliser via le référentiel
            if site_ref not in stations:
                continue
            st = stations[site_ref]
            lid = near_line(st["lat"], st["lng"])
            if not lid:
                continue

            info = TRAFICOLOR_MAP.get(traf_status, TRAFICOLOR_MAP["unknown"])
            measures.append({
                "id": site_ref, "name": st["name"],
                "lat": st["lat"], "lng": st["lng"],
                "line": lid, "status": info["status"],
                "label": info["label"], "congestion": info["congestion"],
                "raw": traf_status,
            })

    except Exception as e:
        print(f"  Erreur traficolor: {e}")
        import traceback; traceback.print_exc()
    return measures

def main():
    print("Téléchargement référentiel...")
    stations = fetch_referentiel()
    print(f"  Total: {len(stations)} stations")

    # Debug: afficher les premières clés
    if stations:
        sample = list(stations.items())[:3]
        for sid, st in sample:
            print(f"    {sid}: {st['name']} — {st['lat']},{st['lng']}")

    print("Téléchargement Traficolor XML...")
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
