#!/usr/bin/env python3
"""
Maverick — collecteur Sytadin (DiRIF), version 2.

Ne conserve que ce qui se trouve SUR le tracé des lignes et DANS le sens de
circulation concerné (aller ou retour) :
  - état du trafic par tronçon (fluide / dense / saturé),
  - fermetures (totales, sécurité, travaux) et voies fermées,
  - événements en cours (accidents, bouchons, travaux, chantiers, exceptionnels).

Tracés aller et retour : lines.json (issus du référentiel IDFM).
Sortie : traffic.json, lu par l'appli Maverick.
"""

import csv
import io
import json
import math
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from pyproj import Transformer

BASE = "https://www.sytadin.fr/diffusion"
URL_SEGMENTS = f"{BASE}/xml/segments_dyn.xml"
URL_EVENEMENTS = f"{BASE}/xml/evenements.xml"
URL_MIF = f"{BASE}/mifmid/modelisation/Segment.mif"
URL_MID = f"{BASE}/mifmid/modelisation/Segment.mid"

MAX_DIST = 35      # m : distance maximale entre un point Sytadin et le tracé de la ligne
MIN_SHARE = 0.6    # part minimale des points du tronçon situés sur le tracé
MIN_ALIGN = 0.5    # cosinus minimal entre le sens du tronçon et le sens de circulation de la ligne

TRANSFORMER = Transformer.from_crs("EPSG:27572", "EPSG:4326", always_xy=True)
LAT0 = 48.93
KX = 111320 * math.cos(math.radians(LAT0))
KY = 111320
CELL = 100         # m : taille des cases de l'index spatial

ETAT_VALEUR = {"fluide": 10, "dense": 50, "sature": 80, "nd": 0}
SENS_LIBELLE = {"X": "vers Paris", "Y": "vers province", "I": "intérieur", "E": "extérieur"}


# ── Outils ─────────────────────────────────────────────────────────────
def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Maverick/2.0 (Transdev Express Roissy)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def xy(p):
    """[lat, lng] → mètres locaux."""
    return (p[1] * KX, p[0] * KY)


def sans_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower()


def etat(texte):
    t = sans_accents(texte)
    if "fluide" in t:
        return "fluide"
    if "pre" in t:
        return "dense"
    if "satur" in t:
        return "sature"
    return "nd"


# ── Index spatial des tracés ───────────────────────────────────────────
class Traces:
    def __init__(self, lines):
        self.edges = []      # (ligne, sens, ax, ay, bx, by)
        self.grid = {}
        for lid, dirs in lines.items():
            for sens, pts in dirs.items():
                for a, b in zip(pts, pts[1:]):
                    (ax, ay), (bx, by) = xy(a), xy(b)
                    k = len(self.edges)
                    self.edges.append((lid, sens, ax, ay, bx, by))
                    for cx in range(int(min(ax, bx) // CELL) - 1, int(max(ax, bx) // CELL) + 2):
                        for cy in range(int(min(ay, by) // CELL) - 1, int(max(ay, by) // CELL) + 2):
                            self.grid.setdefault((cx, cy), []).append(k)

    def nearest(self, p):
        """Arête la plus proche par (ligne, sens) : {(lid, sens): (distance, vecteur de l'arête)}."""
        px, py = xy(p)
        best = {}
        for k in self.grid.get((int(px // CELL), int(py // CELL)), []):
            lid, sens, ax, ay, bx, by = self.edges[k]
            dx, dy = bx - ax, by - ay
            L = dx * dx + dy * dy
            u = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L)) if L else 0.0
            d = math.hypot(px - ax - u * dx, py - ay - u * dy)
            key = (lid, sens)
            if key not in best or d < best[key][0]:
                best[key] = (d, (dx, dy))
        return best

    def match(self, coords):
        """Liste des (ligne, sens) dont le tracé porte ce tronçon, dans le même sens de circulation."""
        if len(coords) < 2:
            return []
        hits, vecs = {}, {}
        for p in coords:
            for key, (d, v) in self.nearest(p).items():
                if d <= MAX_DIST:
                    hits[key] = hits.get(key, 0) + 1
                    vx, vy = vecs.get(key, (0.0, 0.0))
                    vecs[key] = (vx + v[0], vy + v[1])
        (sx, sy), (ex, ey) = xy(coords[0]), xy(coords[-1])
        seg = (ex - sx, ey - sy)
        nseg = math.hypot(*seg)
        out = []
        for key, n in hits.items():
            if n < max(2, math.ceil(len(coords) * MIN_SHARE)):
                continue
            tv = vecs[key]
            nt = math.hypot(*tv)
            if nseg < 1 or nt < 1:
                continue
            if (seg[0] * tv[0] + seg[1] * tv[1]) / (nseg * nt) >= MIN_ALIGN:
                out.append(key)
        return out


# ── Référentiel géométrique Sytadin ────────────────────────────────────
def parse_mif(lines, start):
    """Lit les objets MapInfo dans l'ordre (un objet par ligne du fichier MID).
    Gère NONE, POINT, LINE, PLINE, PLINE MULTIPLE et REGION ; ignore les lignes de style."""
    def pt(x, y):
        lon, lat = TRANSFORMER.transform(float(x), float(y))
        return [round(lat, 6), round(lon, 6)]

    def read(i, k):
        return [pt(*lines[i + j].split()[:2]) for j in range(k)], i + k

    objs, i, n = [], start, len(lines)
    while i < n:
        tok = lines[i].split()
        if not tok:
            i += 1
            continue
        kw = tok[0].upper()
        if kw == "NONE":
            objs.append([]); i += 1
        elif kw == "POINT":
            objs.append([[pt(tok[1], tok[2])]]); i += 1
        elif kw == "LINE":
            objs.append([[pt(tok[1], tok[2]), pt(tok[3], tok[4])]]); i += 1
        elif kw == "PLINE":
            if len(tok) > 1 and tok[1].upper() == "MULTIPLE":
                sections, i = [], i + 1
                for _ in range(int(tok[2])):
                    k = int(lines[i].split()[0]); i += 1
                    sec, i = read(i, k); sections.append(sec)
                objs.append(sections)
            else:
                if len(tok) > 1:
                    k, i = int(tok[1]), i + 1
                else:
                    k, i = int(lines[i + 1].split()[0]), i + 2
                sec, i = read(i, k)
                objs.append([sec])
        elif kw == "REGION":
            sections, i = [], i + 1
            for _ in range(int(tok[1])):
                k = int(lines[i].split()[0]); i += 1
                sec, i = read(i, k); sections.append(sec)
            objs.append(sections)
        else:
            i += 1          # PEN, BRUSH, SYMBOL, SMOOTH, CENTER…
    return objs


def load_geometry():
    mif = fetch(URL_MIF).decode("latin-1", errors="replace").splitlines()
    start = next(i for i, l in enumerate(mif) if l.strip().upper() == "DATA") + 1
    delim = next((l.split(None, 1)[1].strip().strip('"') for l in mif[:start] if l.strip().lower().startswith("delimiter")), "\t")
    # le MID se lit comme un CSV : un libellé entre guillemets peut contenir des virgules ou des retours à la ligne
    mid_txt = fetch(URL_MID).decode("latin-1", errors="replace")
    mid = [r for r in csv.reader(io.StringIO(mid_txt), delimiter=delim) if any(c.strip() for c in r)]
    objs = parse_mif(mif, start)
    print(f"  {len(objs)} objets MIF, {len(mid)} enregistrements MID (séparateur {delim!r})")
    if len(objs) != len(mid):
        raise SystemExit("ERREUR : géométrie et attributs Sytadin désalignés — collecte interrompue pour ne pas afficher de positions fausses")

    geom = {}
    for parts, paths in zip(mid, objs):
        parts = [p.strip() for p in parts]
        desc = parts[1] if len(parts) > 1 else ""
        road, sens, pr = "", "", ""
        m = re.match(r"SEG/([A-Z]+\d+[A-Z]?)-([A-Z])/([\d+]+)/([\d+]+)", desc)
        if m:
            road, sens = m.group(1), m.group(2)
            pr = f"PR{m.group(3)} → PR{m.group(4)}"
        paths = [p for p in paths if len(p) > 1]
        if paths:
            geom[parts[0]] = {"id": parts[0], "desc": desc, "road": road, "sens": sens, "pr": pr,
                              "paths": paths, "coords": [c for p in paths for c in p]}
    return geom


# ── Collecte ───────────────────────────────────────────────────────────
def main():
    lines = json.load(open("lines.json", encoding="utf-8"))
    traces = Traces(lines)

    print("Géométrie Sytadin…")
    geom = load_geometry()
    seg_lines = {sid: traces.match(g["coords"]) for sid, g in geom.items()}
    # contrôle de cohérence : quelques tronçons retenus, avec leur route et leur position
    for sid in list(k for k, v in seg_lines.items() if v)[:8]:
        g = geom[sid]; c = g["coords"][len(g["coords"]) // 2]
        print(f"    {sid} {g['desc'][:32]:<32} {c[0]:.4f},{c[1]:.4f} → {seg_lines[sid]}")
    sur_trace = {sid: m for sid, m in seg_lines.items() if m}
    print(f"  {len(geom)} tronçons, {len(sur_trace)} sur le tracé des lignes")

    out = {lid: {"aller": {"segments": []}, "retour": {"segments": []}, "segments": [], "evenements": []} for lid in lines}

    print("État du trafic…")
    root = ET.fromstring(fetch(URL_SEGMENTS))
    for s in root.iter("SegmentDynamique"):
        sid = s.get("ID_SEGMENT")
        if sid not in sur_trace:
            continue
        g = geom[sid]
        fermeture = (s.findtext(".//EtatFermeture") or "Nominal").strip()
        voies = sum(int(s.findtext(f".//{t}") or 0) for t in ("NbVoiesFermeesDroite", "NbVoiesFermeesCentre", "NbVoiesFermeesGauche"))
        bau = (s.findtext(".//BAUFermee") or "0").strip() not in ("0", "", "false")
        e = etat(s.findtext("EtatTrafic"))
        closed = fermeture.lower() != "nominal"
        f = sans_accents(fermeture)
        closure_type = ("nocturne" if "travaux" in f or "chantier" in f else "securite" if "secur" in f else "fermeture") if closed else ""
        for lid, sens in sur_trace[sid]:
            rec = {
                "id": sid, "dir": sens, "etat": e,
                "congestion": 95 if closed else ETAT_VALEUR[e],
                "closed": closed, "closure_type": closure_type, "fermeture": fermeture if closed else "",
                "voies_fermees": voies, "bau_fermee": bau,
                "road": g["road"], "sens": SENS_LIBELLE.get(g["sens"], ""), "pr": g["pr"],
                "paths": g["paths"],
                "lat": g["coords"][len(g["coords"]) // 2][0], "lng": g["coords"][len(g["coords"]) // 2][1],
            }
            out[lid]["segments"].append(rec)
            out[lid][sens]["segments"].append(rec)

    print("Événements en cours…")
    root = ET.fromstring(fetch(URL_EVENEMENTS))
    for ev in root.iter("Evenement"):
        if (ev.findtext("QualificationEvenement") or "") != "EnCours":
            continue
        typ = ""
        te = ev.find("TypeEvenement")
        if te is not None:
            for c in te:
                if c.tag in ("Bouchon", "IncidentPanne", "Travaux", "ChantierFixe", "EvenementExceptionnel", "General"):
                    typ = c.tag
                    break
        ids = [x.text.strip() for x in ev.iter("Segment") if x.text and x.text.strip()]
        by_line = {}
        for sid in ids:
            for key in sur_trace.get(sid, []):
                by_line.setdefault(key, []).append(geom[sid])
        if not by_line:
            continue
        pd, pf = ev.find(".//PRDebut"), ev.find(".//PRFin")
        prd = f"PR{pd.findtext('NumPR')}+{pd.findtext('Abscisse')}" if pd is not None and pd.findtext("NumPR") else ""
        prf = f"PR{pf.findtext('NumPR')}+{pf.findtext('Abscisse')}" if pf is not None and pf.findtext("NumPR") else ""
        for (lid, sens), segs in by_line.items():
            coords = [c for g in segs for c in g["coords"]]
            road = next((g["road"] for g in segs if g["road"]), "")
            out[lid]["evenements"].append({
                "id": ev.get("ID_EVT", ""), "dir": sens, "type": typ,
                "road": road, "sens": SENS_LIBELLE.get(segs[0]["sens"], ""),
                "pr_debut": prd, "pr_fin": prf,
                "date": ev.findtext("DateDebut") or "", "fin_prevue": ev.findtext("DateFinPrevue") or "",
                "desc": (ev.findtext("Commentaire") or "")[:200],
                "paths": [p for g in segs for p in g["paths"]],
                "lat": coords[len(coords) // 2][0], "lng": coords[len(coords) // 2][1],
            })

    print("Synthèse :")
    for lid, d in out.items():
        for sens in ("aller", "retour"):
            vals = [s["congestion"] for s in d[sens]["segments"] if s["congestion"] > 0]
            c = round(sum(vals) / len(vals)) if vals else 0
            d[sens]["congestion"] = c
            d[sens]["status"] = "unknown" if not vals else "green" if c < 30 else "orange" if c < 60 else "red"
            del d[sens]["segments"]
        n_ferm = sum(1 for s in d["segments"] if s["closed"])
        n_dense = sum(1 for s in d["segments"] if s["etat"] in ("dense", "sature"))
        print(f"  {lid}: aller {d['aller']['congestion']} % | retour {d['retour']['congestion']} % | "
              f"{len(d['segments'])} tronçons, {n_dense} denses/saturés, {n_ferm} fermés, {len(d['evenements'])} événements")

    json.dump({
        "version": 2,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Sytadin / DiRIF",
        "lines": out,
    }, open("traffic.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("traffic.json écrit.")


if __name__ == "__main__":
    main()
