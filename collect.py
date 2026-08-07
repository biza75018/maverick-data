#!/usr/bin/env python3
"""
Maverick — Collecteur Sytadin DiRIF — Debug structure XML
"""

import urllib.request
import xml.etree.ElementTree as ET
import json
import math
import re
from datetime import datetime
from pyproj import Transformer

transformer = Transformer.from_crs("EPSG:2154","EPSG:4326",always_xy=True)

BASE = "https://www.sytadin.fr/diffusion"
URLS = {
    "segments":    f"{BASE}/xml/segments_dyn.xml",
    "arcs":        f"{BASE}/xml/arcs_dyn.xml",
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

def main():
    # ═══ SEGMENTS ═══
    print("=== SEGMENTS_DYN.XML — Structure ===")
    raw = fetch(URLS["segments"])
    print(f"Taille: {len(raw)} bytes")
    txt = raw.decode("utf-8",errors="replace")
    # Afficher les 30 premières lignes brutes
    for i, l in enumerate(txt.splitlines()[:30]):
        print(f"  [{i}] {l[:250]}")

    # Parser et montrer les tags
    txt_clean = re.sub(r'\sxmlns[:\w]*="[^"]*"','',txt)
    root = ET.fromstring(txt_clean.encode("utf-8"))
    tags = set()
    for el in root.iter():
        t = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        tags.add(t)
    print(f"\nTags XML trouvés: {sorted(tags)}")

    # Premier élément avec des attributs/enfants intéressants
    shown = 0
    for el in root.iter():
        if shown >= 5: break
        t = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if el.attrib or len(list(el)) > 0:
            children = {(c.tag.split("}")[-1] if "}" in c.tag else c.tag): (c.text or "")[:50] for c in el}
            if children:
                print(f"\n  Element: <{t}> attribs={dict(el.attrib)}")
                print(f"  Children: {children}")
                shown += 1

    # ═══ EVENEMENTS ═══
    print("\n\n=== EVENEMENTS.XML — Structure ===")
    raw2 = fetch(URLS["evenements"])
    print(f"Taille: {len(raw2)} bytes")
    txt2 = raw2.decode("utf-8",errors="replace")
    for i, l in enumerate(txt2.splitlines()[:30]):
        print(f"  [{i}] {l[:250]}")

    txt2_clean = re.sub(r'\sxmlns[:\w]*="[^"]*"','',txt2)
    root2 = ET.fromstring(txt2_clean.encode("utf-8"))
    tags2 = set()
    for el in root2.iter():
        t = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        tags2.add(t)
    print(f"\nTags XML trouvés: {sorted(tags2)}")

    shown2 = 0
    for el in root2.iter():
        if shown2 >= 3: break
        t = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if len(list(el)) >= 3:
            children = {}
            for c in el:
                ct = c.tag.split("}")[-1] if "}" in c.tag else c.tag
                children[ct] = (c.text or "")[:80]
            print(f"\n  Element: <{t}> attribs={dict(el.attrib)}")
            print(f"  Children: {children}")
            shown2 += 1

    # ═══ GEOM — vérifier quelques segments dans la bbox ═══
    print("\n\n=== GÉOMÉTRIE — Segments dans bbox IDF ===")
    geom = load_geometry_sample()

    # JSON vide
    output = {
        "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Sytadin / DiRIF — debug", "lines":{}, "segments":[], "evenements":[]
    }
    with open("traffic.json","w") as f:
        json.dump(output,f)
    print("\nDiagnostic terminé.")

def load_geometry_sample():
    try:
        mif = fetch(URLS["geom_seg"]).decode("latin-1",errors="replace")
        mid = fetch(URLS["geom_seg_id"]).decode("latin-1",errors="replace")
        mid_lines = [l.strip() for l in mid.splitlines() if l.strip()]
        print(f"  MID: {len(mid_lines)} lignes")
        print(f"  MID premiers: {mid_lines[:3]}")

        # Compter les segments dans la bbox
        tr = Transformer.from_crs("EPSG:2154","EPSG:4326",always_xy=True)
        count_bbox = 0
        count_near = 0
        idx = 0
        lines = mif.splitlines()
        i = 0
        while i < len(lines) and idx < len(mid_lines):
            l = lines[i].strip()
            if l.upper().startswith("PLINE"):
                parts = l.split()
                if len(parts) > 1:
                    try:
                        n = int(parts[1])
                        coords = []
                        for j in range(1, min(n+1, len(lines)-i)):
                            xy = lines[i+j].split()
                            if len(xy) >= 2:
                                x, y = float(xy[0]), float(xy[1])
                                lon, lat = tr.transform(x, y)
                                coords.append((round(lat,6), round(lon,6)))
                        if coords:
                            mid_pt = coords[len(coords)//2]
                            if in_bbox(mid_pt[0], mid_pt[1]):
                                count_bbox += 1
                                lid = near_line(mid_pt[0], mid_pt[1])
                                if lid:
                                    count_near += 1
                                    if count_near <= 5:
                                        sid = mid_lines[idx].split(",")[0].strip().strip('"') if idx < len(mid_lines) else "?"
                                        print(f"    Segment {sid} → {mid_pt[0]},{mid_pt[1]} → ligne {lid}")
                        idx += 1
                        i += n
                    except Exception:
                        pass
            i += 1
        print(f"  {count_bbox} segments dans bbox IDF, {count_near} proches de nos lignes")
    except Exception as e:
        import traceback; traceback.print_exc()

if __name__=="__main__":
    main()
