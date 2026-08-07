#!/usr/bin/env python3
"""
Maverick — Collecteur Sytadin DiRIF
Flux publics XML mis à jour chaque minute.
"""

import urllib.request
import xml.etree.ElementTree as ET
import json
import math
import re
from datetime import datetime

BASE = "https://www.sytadin.fr/diffusion"
URLS = {
    "segments":    f"{BASE}/xml/segments_dyn.xml",
    "evenements":  f"{BASE}/xml/evenements.xml",
    "geom_seg":    f"{BASE}/mifmid/modelisation/Segment.mif",
    "geom_seg_id": f"{BASE}/mifmid/modelisation/Segment.mid",
}

BBOX = {"min_lat":48.830,"max_lat":49.020,"min_lng":2.240,"max_lng":2.580}

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

ETAT_MAP = {
    "fluide":          {"label":"Fluide",       "congestion":10, "status":"green"},
    "pre-sature":      {"label":"Pré-saturé",   "congestion":50, "status":"orange"},
    "sature":          {"label":"Saturé",        "congestion":80, "status":"red"},
    "non renseigne":   {"label":"Non renseigné","congestion":0,  "status":"unknown"},
    "nominal":         {"label":"Nominal",      "congestion":0,  "status":"unknown"},
}

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent":"Maverick/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def dist_m(a1,o1,a2,o2):
    d1=(a2-a1)*111320; d2=(o2-o1)*111320*math.cos(math.radians(a1))
    return math.sqrt(d1*d1+d2*d2)

def near_line(lat,lng,radius=500):
    for lid,pts in LINE_TRACES.items():
        for p in pts:
            if dist_m(lat,lng,p[0],p[1])<radius: return lid
    return None

def in_bbox(lat,lng):
    return BBOX["min_lat"]<=lat<=BBOX["max_lat"] and BBOX["min_lng"]<=lng<=BBOX["max_lng"]

def load_geometry():
    """Charge le MIF/MID et détecte automatiquement la projection."""
    geom = {}
    try:
        mif_raw = fetch(URLS["geom_seg"]).decode("latin-1", errors="replace")
        mid_raw = fetch(URLS["geom_seg_id"]).decode("latin-1", errors="replace")

        # Lire le header MIF pour détecter la projection
        mif_lines = mif_raw.splitlines()
        header_end = 0
        coordsys = ""
        for i, l in enumerate(mif_lines):
            if l.strip().upper() == "DATA":
                header_end = i + 1
                break
            if "coordsys" in l.lower() or "projection" in l.lower():
                coordsys += l.strip() + " "
        print(f"  MIF CoordSys: {coordsys[:200]}")
        print(f"  MIF header ends at line {header_end}")

        # Afficher premières coordonnées pour identifier le système
        sample_coords = []
        for l in mif_lines[header_end:header_end+50]:
            parts = l.strip().split()
            if len(parts) == 2:
                try:
                    x, y = float(parts[0]), float(parts[1])
                    if x > 100 and y > 100:
                        sample_coords.append((x, y))
                except Exception:
                    pass
        if sample_coords:
            print(f"  Exemples coordonnées brutes: {sample_coords[:3]}")
            # Détecter la projection d'après les valeurs
            sx, sy = sample_coords[0]
            if 100000 < sx < 1300000 and 6000000 < sy < 7200000:
                proj = "EPSG:2154"  # Lambert 93
                print(f"  Projection détectée: Lambert 93 ({proj})")
            elif 100000 < sx < 1300000 and 1600000 < sy < 2700000:
                proj = "EPSG:27572"  # Lambert II étendu
                print(f"  Projection détectée: Lambert II étendu ({proj})")
            elif 0 < sx < 20 and 40 < sy < 55:
                proj = None  # Déjà WGS84
                print("  Projection: déjà en WGS84")
            else:
                proj = "EPSG:2154"
                print(f"  Projection inconnue, essai Lambert 93 (x={sx}, y={sy})")

            if proj:
                from pyproj import Transformer
                tr = Transformer.from_crs(proj, "EPSG:4326", always_xy=True)
                # Test conversion
                lon, lat = tr.transform(sx, sy)
                print(f"  Test conversion: ({sx},{sy}) → lat={lat:.6f}, lon={lon:.6f}")

        # Parser les IDs depuis le MID
        mid_lines = [l.strip() for l in mid_raw.splitlines() if l.strip()]
        ids = []
        for l in mid_lines:
            parts = l.split(",")
            ids.append(parts[0].strip().strip('"'))
        print(f"  {len(ids)} IDs dans le MID")

        # Parser les géométries du MIF
        if proj:
            from pyproj import Transformer
            tr = Transformer.from_crs(proj, "EPSG:4326", always_xy=True)
        
        idx = 0
        i = header_end
        in_bbox_count = 0
        near_count = 0
        while i < len(mif_lines) and idx < len(ids):
            l = mif_lines[i].strip().upper()
            if l.startswith("PLINE"):
                parts = mif_lines[i].strip().split()
                n = int(parts[1]) if len(parts) > 1 else 0
                coords = []
                for j in range(1, n+1):
                    if i+j < len(mif_lines):
                        xy = mif_lines[i+j].strip().split()
                        if len(xy) >= 2:
                            try:
                                x, y = float(xy[0]), float(xy[1])
                                if proj:
                                    lon, lat = tr.transform(x, y)
                                else:
                                    lon, lat = x, y
                                coords.append((round(lat,6), round(lon,6)))
                            except Exception:
                                pass
                if coords:
                    mid_pt = coords[len(coords)//2]
                    geom[ids[idx]] = {"lat":mid_pt[0], "lng":mid_pt[1]}
                    if in_bbox(mid_pt[0], mid_pt[1]):
                        in_bbox_count += 1
                        lid = near_line(mid_pt[0], mid_pt[1])
                        if lid:
                            near_count += 1
                idx += 1
                i += n + 1
                continue
            elif l.startswith("LINE"):
                parts = mif_lines[i].strip().split()
                if len(parts) >= 5:
                    try:
                        x1,y1,x2,y2 = float(parts[1]),float(parts[2]),float(parts[3]),float(parts[4])
                        if proj:
                            lon,lat = tr.transform((x1+x2)/2,(y1+y2)/2)
                        else:
                            lon,lat = (x1+x2)/2,(y1+y2)/2
                        geom[ids[idx]] = {"lat":round(lat,6),"lng":round(lon,6)}
                        if in_bbox(lat,lon):
                            in_bbox_count += 1
                            if near_line(lat,lon): near_count += 1
                    except Exception:
                        pass
                idx += 1
            i += 1
        print(f"  {len(geom)} segments géolocalisés total")
        print(f"  {in_bbox_count} dans bbox IDF, {near_count} proches de nos lignes")
    except Exception as e:
        import traceback; traceback.print_exc()
    return geom

def parse_segments(geom):
    """Parse segments_dyn.xml avec la vraie structure Sytadin."""
    out = []
    try:
        raw = fetch(URLS["segments"])
        root = ET.fromstring(raw)
        
        for seg in root.findall("SegmentDynamique"):
            sid = seg.get("ID_SEGMENT")
            etat_el = seg.find("EtatTrafic")
            etat = etat_el.text.strip().lower() if etat_el is not None and etat_el.text else ""
            
            fermeture_el = seg.find(".//EtatFermeture")
            fermeture = fermeture_el.text.strip() if fermeture_el is not None and fermeture_el.text else ""

            if not sid or sid not in geom:
                continue
            g = geom[sid]
            if not in_bbox(g["lat"], g["lng"]):
                continue
            lid = near_line(g["lat"], g["lng"])
            if not lid:
                continue

            info = ETAT_MAP.get(etat, ETAT_MAP.get("non renseigne"))
            
            # Route fermée
            is_closed = fermeture not in ("Nominal", "")
            
            out.append({
                "id": sid, "lat": g["lat"], "lng": g["lng"], "line": lid,
                "status": "red" if is_closed else info["status"],
                "label": f"Fermé ({fermeture})" if is_closed else info["label"],
                "congestion": 95 if is_closed else info["congestion"],
                "closed": is_closed,
            })
        print(f"  {len(out)} segments sur nos lignes")
    except Exception as e:
        import traceback; traceback.print_exc()
    return out

def parse_evenements(geom):
    """Parse evenements.xml — extraire les segments associés pour géolocaliser."""
    out = []
    try:
        raw = fetch(URLS["evenements"])
        root = ET.fromstring(raw)
        
        for evt in root.findall("Evenement"):
            evt_id = evt.get("ID_EVT", "")
            qual = evt.findtext("QualificationEvenement", "")
            if qual != "EnCours":
                continue
            
            # Type d'événement
            type_el = evt.find("TypeEvenement")
            evt_type = ""
            if type_el is not None:
                for child in type_el:
                    tag = child.tag
                    if tag in ("Bouchon","IncidentPanne","Travaux","ChantierFixe","EvenementExceptionnel","General"):
                        evt_type = tag
                        break

            commentaire = evt.findtext("Commentaire", "")
            date_debut = evt.findtext("DateDebut", "")

            # Segments associés
            segments_el = evt.find(".//Segments")
            seg_ids = []
            if segments_el is not None:
                for s in segments_el.findall("Segment"):
                    sid = s.text.strip() if s.text else ""
                    if sid:
                        seg_ids.append(sid)
            
            # Géolocaliser via le premier segment connu
            for sid in seg_ids:
                if sid in geom:
                    g = geom[sid]
                    if not in_bbox(g["lat"], g["lng"]):
                        continue
                    lid = near_line(g["lat"], g["lng"])
                    if lid:
                        out.append({
                            "id": evt_id, "lat": g["lat"], "lng": g["lng"],
                            "line": lid, "type": evt_type,
                            "desc": commentaire[:100],
                            "date": date_debut,
                        })
                        break  # Un seul point par événement
        print(f"  {len(out)} événements sur nos lignes")
    except Exception as e:
        import traceback; traceback.print_exc()
    return out

def main():
    print("=== Chargement géométrie ===")
    geom = load_geometry()

    print("\n=== Segments trafic ===")
    segments = parse_segments(geom)

    print("\n=== Événements ===")
    evenements = parse_evenements(geom)

    # Agrégation
    by_line = {}
    for s in segments:
        lid = s["line"]
        by_line.setdefault(lid, {"segments":[],"congestion":0,"status":"green","evenements":[]})
        by_line[lid]["segments"].append(s)
    for e in evenements:
        lid = e["line"]
        by_line.setdefault(lid, {"segments":[],"congestion":0,"status":"green","evenements":[]})
        by_line[lid]["evenements"].append(e)

    print("\n=== Résultats ===")
    for lid, d in by_line.items():
        vals = [s["congestion"] for s in d["segments"] if s["congestion"] > 0]
        avg = round(sum(vals)/len(vals)) if vals else 0
        d["congestion"] = avg
        d["status"] = "green" if avg < 30 else "orange" if avg < 60 else "red"
        closed = sum(1 for s in d["segments"] if s.get("closed"))
        print(f"  Ligne {lid}: {avg}% — {len(d['segments'])} segments, {len(d['evenements'])} évts, {closed} fermés")

    output = {
        "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Sytadin / DiRIF",
        "lines": by_line,
        "segments": segments,
        "evenements": evenements,
    }
    with open("traffic.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("\ntraffic.json écrit !")

if __name__ == "__main__":
    main()
