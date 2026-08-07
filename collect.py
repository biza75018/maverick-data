#!/usr/bin/env python3
"""
Maverick — Collecteur Sytadin DiRIF
Flux publics XML mis à jour chaque minute.
Extrait congestion + événements + détail fermetures (route, sens, tronçon)
"""

import urllib.request
import xml.etree.ElementTree as ET
import json
import math
import re
from datetime import datetime
from pyproj import Transformer

transformer = Transformer.from_crs("EPSG:27572","EPSG:4326",always_xy=True)

BASE = "https://www.sytadin.fr/diffusion"
URLS = {
    "segments":    f"{BASE}/xml/segments_dyn.xml",
    "evenements":  f"{BASE}/xml/evenements.xml",
    "chantiers":   f"{BASE}/xml/Chantier.xml",
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
    "fluide":        {"label":"Fluide",       "congestion":10,"status":"green"},
    "pre-sature":    {"label":"Pré-saturé",   "congestion":50,"status":"orange"},
    "sature":        {"label":"Saturé",       "congestion":80,"status":"red"},
    "non renseigne": {"label":"Non renseigné","congestion":0, "status":"unknown"},
}

SENS_MAP = {"Y":"↓ Province","X":"↑ Paris","I":"⟳ Int","E":"⟲ Ext"}

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent":"Maverick/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def dist_m(a1,o1,a2,o2):
    d1=(a2-a1)*111320; d2=(o2-o1)*111320*math.cos(math.radians(a1))
    return math.sqrt(d1*d1+d2*d2)

def on_line(lat,lng,radius=500):
    for lid,pts in LINE_TRACES.items():
        for p in pts:
            if dist_m(lat,lng,p[0],p[1])<radius: return lid
    return None

def in_bbox(lat,lng):
    return BBOX["min_lat"]<=lat<=BBOX["max_lat"] and BBOX["min_lng"]<=lng<=BBOX["max_lng"]

def load_geometry():
    """Charge MIF/MID → id, lat, lng, road, direction, PR, all coords for each segment."""
    geom = {}
    try:
        mif_raw = fetch(URLS["geom_seg"]).decode("latin-1",errors="replace")
        mid_raw = fetch(URLS["geom_seg_id"]).decode("latin-1",errors="replace")

        mif_lines = mif_raw.splitlines()
        header_end = 0
        for i, l in enumerate(mif_lines):
            if l.strip().upper() == "DATA":
                header_end = i + 1
                break

        mid_lines = [l.strip() for l in mid_raw.splitlines() if l.strip()]

        # Parser les infos du MID : ID, description, type, axe, sens, secteur
        seg_info = {}
        for ml in mid_lines:
            parts = ml.split(",")
            sid = parts[0].strip().strip('"')
            desc = parts[1].strip().strip('"') if len(parts) > 1 else ""
            # Extraire route et sens depuis la description "SEG/N6-Y/12+0440/13+0100"
            road = ""
            sens = ""
            pr_range = ""
            m = re.match(r'SEG/([A-Z0-9]+)-([XYIE])/([\d+]+)/([\d+]+)', desc)
            if m:
                road = m.group(1)
                sens = m.group(2)
                pr_range = f"PR{m.group(3)} → PR{m.group(4)}"
            else:
                # Essayer format alternatif
                m2 = re.match(r'([A-Z0-9]+)', desc.replace("SEG/",""))
                if m2:
                    road = m2.group(1)
            secteur = parts[-1].strip().strip('"') if len(parts) > 5 else ""
            seg_info[sid] = {"desc":desc, "road":road, "sens":sens, "pr":pr_range, "secteur":secteur}

        # Parser les géométries du MIF
        idx = 0
        i = header_end
        while i < len(mif_lines) and idx < len(mid_lines):
            l = mif_lines[i].strip().upper()
            sid = mid_lines[idx].split(",")[0].strip().strip('"') if idx < len(mid_lines) else ""

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
                                lon, lat = transformer.transform(x, y)
                                coords.append([round(lat,6), round(lon,6)])
                            except Exception:
                                pass
                if coords:
                    mid_pt = coords[len(coords)//2]
                    info = seg_info.get(sid, {})
                    geom[sid] = {
                        "lat":mid_pt[0], "lng":mid_pt[1],
                        "coords": coords,  # toutes les coordonnées du segment
                        "road": info.get("road",""),
                        "sens": info.get("sens",""),
                        "pr": info.get("pr",""),
                        "desc": info.get("desc",""),
                        "secteur": info.get("secteur",""),
                    }
                idx += 1
                i += n + 1
                continue
            elif l.startswith("LINE"):
                parts = mif_lines[i].strip().split()
                if len(parts) >= 5:
                    try:
                        x1,y1,x2,y2 = float(parts[1]),float(parts[2]),float(parts[3]),float(parts[4])
                        lon1,lat1 = transformer.transform(x1,y1)
                        lon2,lat2 = transformer.transform(x2,y2)
                        info = seg_info.get(sid, {})
                        geom[sid] = {
                            "lat":round((lat1+lat2)/2,6),"lng":round((lon1+lon2)/2,6),
                            "coords":[[round(lat1,6),round(lon1,6)],[round(lat2,6),round(lon2,6)]],
                            "road":info.get("road",""),"sens":info.get("sens",""),
                            "pr":info.get("pr",""),"desc":info.get("desc",""),
                            "secteur":info.get("secteur",""),
                        }
                    except Exception:
                        pass
                idx += 1
            i += 1

        in_bbox_count = sum(1 for g in geom.values() if in_bbox(g["lat"],g["lng"]))
        near_count = sum(1 for g in geom.values() if in_bbox(g["lat"],g["lng"]) and on_line(g["lat"],g["lng"]))
        print(f"  {len(geom)} segments total, {in_bbox_count} bbox, {near_count} sur les tracés")
    except Exception as e:
        import traceback; traceback.print_exc()
    return geom

def parse_segments(geom):
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
            lid = on_line(g["lat"], g["lng"])
            if not lid:
                continue

            info = ETAT_MAP.get(etat, {"label":etat or "Inconnu","congestion":0,"status":"unknown"})
            is_closed = fermeture not in ("Nominal", "")

            sens_label = SENS_MAP.get(g["sens"], g["sens"])

            out.append({
                "id": sid, "lat": g["lat"], "lng": g["lng"], "line": lid,
                "status": "red" if is_closed else info["status"],
                "label": f"Fermé ({fermeture})" if is_closed else info["label"],
                "congestion": 95 if is_closed else info["congestion"],
                "closed": is_closed,
                "fermeture": fermeture if is_closed else "",
                "road": g["road"],
                "sens": sens_label,
                "pr": g["pr"],
                "desc": g["desc"],
                "coords": g["coords"],  # coordonnées du tronçon pour affichage carte
            })
        print(f"  {len(out)} segments sur nos lignes")
        closed = [s for s in out if s["closed"]]
        if closed:
            print(f"  dont {len(closed)} fermés:")
            for c in closed[:5]:
                print(f"    {c['road']} {c['sens']} {c['pr']} — {c['fermeture']}")
    except Exception as e:
        import traceback; traceback.print_exc()
    return out

def parse_evenements(geom):
    out = []
    try:
        raw = fetch(URLS["evenements"])
        root = ET.fromstring(raw)

        for evt in root.findall("Evenement"):
            evt_id = evt.get("ID_EVT", "")
            qual = evt.findtext("QualificationEvenement", "")
            if qual != "EnCours":
                continue

            type_el = evt.find("TypeEvenement")
            evt_type = ""
            if type_el is not None:
                for child in type_el:
                    if child.tag in ("Bouchon","IncidentPanne","Travaux","ChantierFixe","EvenementExceptionnel","General"):
                        evt_type = child.tag
                        break

            commentaire = evt.findtext("Commentaire", "")
            date_debut = evt.findtext("DateDebut", "")

            # Localisation
            loc = evt.find("Localisation")
            axe = ""
            sens = ""
            pr_debut = ""
            pr_fin = ""
            if loc is not None:
                sc = loc.find("SectionCourante")
                if sc is not None:
                    axe_el = sc.find("Axe")
                    if axe_el is not None:
                        axe = axe_el.text.strip() if axe_el.text else ""
                    sens_el = sc.find("Sens")
                    if sens_el is not None:
                        sens = sens_el.text.strip() if sens_el.text else ""
                pr_d = loc.find("PRDebut")
                if pr_d is not None:
                    num = pr_d.findtext("NumPR","")
                    abs_ = pr_d.findtext("Abscisse","")
                    pr_debut = f"PR{num}+{abs_}" if num else ""
                pr_f = loc.find("PRFin")
                if pr_f is not None:
                    num = pr_f.findtext("NumPR","")
                    abs_ = pr_f.findtext("Abscisse","")
                    pr_fin = f"PR{num}+{abs_}" if num else ""

            # Segments et coordonnées
            segments_el = evt.find(".//Segments")
            seg_ids = []
            evt_coords = []
            if segments_el is not None:
                for s in segments_el.findall("Segment"):
                    sid = s.text.strip() if s.text else ""
                    if sid:
                        seg_ids.append(sid)
                        if sid in geom:
                            evt_coords.extend(geom[sid]["coords"])

            # Géolocaliser via le premier segment connu
            for sid in seg_ids:
                if sid in geom:
                    g = geom[sid]
                    if not in_bbox(g["lat"], g["lng"]):
                        continue
                    lid = on_line(g["lat"], g["lng"])
                    if lid:
                        # Récupérer le nom de la route depuis le segment si pas dans l'événement
                        road = axe or g["road"]
                        out.append({
                            "id": evt_id, "lat": g["lat"], "lng": g["lng"],
                            "line": lid, "type": evt_type,
                            "desc": commentaire[:150],
                            "date": date_debut,
                            "road": road,
                            "sens": SENS_MAP.get(sens, sens),
                            "pr_debut": pr_debut,
                            "pr_fin": pr_fin,
                            "coords": evt_coords,  # coordonnées de tous les segments touchés
                        })
                        break
        print(f"  {len(out)} événements sur nos lignes")
        for e in out[:3]:
            print(f"    {e['type']} — {e['road']} {e['sens']} {e['pr_debut']}→{e['pr_fin']} : {e['desc'][:60]}")
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
        print(f"  Ligne {lid}: {avg}% — {len(d['segments'])} seg, {len(d['evenements'])} évts, {closed} fermés")

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
