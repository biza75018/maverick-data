#!/usr/bin/env python3
"""
Maverick — Collecteur Sytadin (DiRIF Île-de-France)
Flux publics XML, mis à jour toutes les minutes.
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
    "arcs":        f"{BASE}/xml/arcs_dyn.xml",
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

# États de trafic Sytadin
ETAT_MAP = {
    "1": {"label":"Fluide",       "congestion":10, "status":"green"},
    "2": {"label":"Dense",        "congestion":45, "status":"orange"},
    "3": {"label":"Ralenti",      "congestion":65, "status":"orange"},
    "4": {"label":"Bouché",       "congestion":85, "status":"red"},
    "0": {"label":"Inconnu",      "congestion":0,  "status":"unknown"},
    "fluide":       {"label":"Fluide","congestion":10,"status":"green"},
    "dense":        {"label":"Dense","congestion":45,"status":"orange"},
    "ralenti":      {"label":"Ralenti","congestion":65,"status":"orange"},
    "bouche":       {"label":"Bouché","congestion":85,"status":"red"},
    "bouché":       {"label":"Bouché","congestion":85,"status":"red"},
}

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent":"Maverick/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
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

def stag(t):
    return t.split("}")[-1] if "}" in t else t

def load_geometry():
    """Charge la géométrie des segments depuis MIF/MID (Lambert 93 → WGS84)."""
    geom = {}
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:2154","EPSG:4326",always_xy=True)

        mif = fetch(URLS["geom_seg"]).decode("latin-1",errors="replace")
        mid = fetch(URLS["geom_seg_id"]).decode("latin-1",errors="replace")

        # Le MID contient les IDs (une ligne par objet)
        ids = [l.split(",")[0].strip().strip('"') for l in mid.splitlines() if l.strip()]

        # Le MIF contient les géométries
        idx = 0
        lines = mif.splitlines()
        i = 0
        while i < len(lines) and idx < len(ids):
            l = lines[i].strip()
            if l.upper().startswith("PLINE") or l.upper().startswith("LINE"):
                parts = l.split()
                if l.upper().startswith("PLINE") and len(parts)>1:
                    try:
                        n = int(parts[1])
                        coords=[]
                        for j in range(1,n+1):
                            if i+j < len(lines):
                                xy = lines[i+j].split()
                                if len(xy)>=2:
                                    x,y = float(xy[0]), float(xy[1])
                                    lon,lat = tr.transform(x,y)
                                    coords.append((round(lat,6),round(lon,6)))
                        if coords:
                            mid_pt = coords[len(coords)//2]
                            geom[ids[idx]] = {"lat":mid_pt[0],"lng":mid_pt[1]}
                        idx += 1
                        i += n
                    except Exception:
                        pass
                elif l.upper().startswith("LINE") and len(parts)>=5:
                    try:
                        x1,y1,x2,y2 = map(float,parts[1:5])
                        lon,lat = tr.transform((x1+x2)/2,(y1+y2)/2)
                        geom[ids[idx]] = {"lat":round(lat,6),"lng":round(lon,6)}
                        idx += 1
                    except Exception:
                        pass
            i += 1
        print(f"  {len(geom)} segments géolocalisés")
    except Exception as e:
        import traceback; traceback.print_exc()
    return geom

def parse_segments(geom):
    """Parse segments_dyn.xml — état de trafic par segment."""
    out = []
    try:
        raw = fetch(URLS["segments"])
        print(f"  segments_dyn.xml : {len(raw)} bytes")
        txt = raw.decode("utf-8",errors="replace")
        txt = re.sub(r'\sxmlns[:\w]*="[^"]*"','',txt)
        root = ET.fromstring(txt.encode("utf-8"))

        count=0
        for el in root.iter():
            if stag(el.tag).lower() not in ("segment","seg"): continue
            count+=1
            sid = el.get("id") or el.get("Id") or el.get("code") or ""
            etat = el.get("etat") or el.get("Etat") or el.get("etatTrafic") or ""
            if not etat:
                for c in el:
                    if stag(c.tag).lower() in ("etat","etattrafic","couleur") and c.text:
                        etat = c.text.strip()
            if not sid or not etat: continue
            g = geom.get(sid)
            if not g: continue
            if not in_bbox(g["lat"],g["lng"]): continue
            lid = near_line(g["lat"],g["lng"])
            if not lid: continue
            info = ETAT_MAP.get(str(etat).lower(), ETAT_MAP["0"])
            out.append({
                "id":sid,"lat":g["lat"],"lng":g["lng"],"line":lid,
                "status":info["status"],"label":info["label"],
                "congestion":info["congestion"],
            })
        print(f"  {count} segments dans le XML, {len(out)} sur nos lignes")
    except Exception as e:
        import traceback; traceback.print_exc()
    return out

def parse_evenements():
    """Parse evenements.xml — incidents et accidents."""
    out=[]
    try:
        raw = fetch(URLS["evenements"])
        print(f"  evenements.xml : {len(raw)} bytes")
        txt = raw.decode("utf-8",errors="replace")
        txt = re.sub(r'\sxmlns[:\w]*="[^"]*"','',txt)
        root = ET.fromstring(txt.encode("utf-8"))
        count=0
        for el in root.iter():
            if "even" not in stag(el.tag).lower(): continue
            count+=1
            d = {stag(c.tag).lower():(c.text or "").strip() for c in el}
            d.update({k.lower():v for k,v in el.attrib.items()})
            # Chercher lat/lng
            lat = d.get("lat") or d.get("latitude") or d.get("y")
            lng = d.get("lon") or d.get("lng") or d.get("longitude") or d.get("x")
            if not lat or not lng: continue
            try:
                lat,lng = float(lat), float(lng)
            except Exception: continue
            if not in_bbox(lat,lng): continue
            lid = near_line(lat,lng)
            if not lid: continue
            out.append({
                "lat":lat,"lng":lng,"line":lid,
                "type": d.get("type") or d.get("nature") or "Incident",
                "desc": d.get("description") or d.get("libelle") or "",
            })
        print(f"  {count} événements dans le XML, {len(out)} sur nos lignes")
    except Exception as e:
        import traceback; traceback.print_exc()
    return out

def main():
    print("=== Chargement géométrie segments ===")
    geom = load_geometry()

    print("\n=== État de trafic (segments_dyn) ===")
    segments = parse_segments(geom)

    print("\n=== Événements ===")
    evenements = parse_evenements()

    # Agrégation par ligne
    by_line={}
    for s in segments:
        lid=s["line"]
        by_line.setdefault(lid,{"segments":[],"congestion":0,"status":"green","evenements":[]})
        by_line[lid]["segments"].append(s)
    for e in evenements:
        lid=e["line"]
        by_line.setdefault(lid,{"segments":[],"congestion":0,"status":"green","evenements":[]})
        by_line[lid]["evenements"].append(e)

    print("\n=== Résultats ===")
    for lid,d in by_line.items():
        vals=[s["congestion"] for s in d["segments"]]
        avg=round(sum(vals)/len(vals)) if vals else 0
        d["congestion"]=avg
        d["status"]="green" if avg<30 else "orange" if avg<60 else "red"
        print(f"  Ligne {lid}: {avg}% — {len(d['segments'])} segments, {len(d['evenements'])} événements")

    output={
        "updated_at":datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source":"Sytadin / DiRIF",
        "lines":by_line,
        "segments":segments,
        "evenements":evenements,
    }
    with open("traffic.json","w",encoding="utf-8") as f:
        json.dump(output,f,ensure_ascii=False,indent=2)
    print("\ntraffic.json écrit !")

if __name__=="__main__":
    main()
