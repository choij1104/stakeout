"""Auto-research each traffic-count station so users don't have to answer by hand.

For every station in data/bexar-aadt.json this adds:
  city      - incorporated place the point falls in (US Census TIGER/Line places), or
              "Unincorporated" when outside every place
  signal_m  - distance in meters to the nearest traffic signal (OpenStreetMap)
  stop_m    - distance in meters to the nearest stop sign (OpenStreetMap)
  speed     - posted speed (mph) of the nearest mapped road with a maxspeed tag (OpenStreetMap)
  speed_road- name of that road
  parcels   - land-owner TYPE of parcels within ~45 m (Bexar Appraisal District), counted by
              type. Owner names are kept only for public owners; private owners are never stored.

Usage: python scripts/enrich.py data/bexar-aadt.json data/bexar-enrich.json
"""
import io
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile

UA = {"User-Agent": "stakeout-enrich/0.3 (github.com/choij1104/stakeout)"}
BBOX = (29.10, -98.86, 29.78, -98.10)  # S, W, N, E  (Bexar County)


def get(url, data=None, timeout=240):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------- geometry helpers ----------------
def meters(lat1, lon1, lat2, lon2):
    kx = 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110540)


def seg_dist(lat, lon, a, b):
    kx = 111320 * math.cos(math.radians(lat))
    ax, ay = (a[1] - lon) * kx, (a[0] - lat) * 110540
    bx, by = (b[1] - lon) * kx, (b[0] - lat) * 110540
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + t * dx, ay + t * dy)


def in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi:
            inside = not inside
        j = i
    return inside


# ---------------- 1. city limits (Census TIGER/Line places, Texas = 48) ----------------
def load_places():
    import shapefile  # pyshp
    for year in (2025, 2024):
        url = f"https://www2.census.gov/geo/tiger/TIGER{year}/PLACE/tl_{year}_48_place.zip"
        try:
            raw = get(url)
            break
        except Exception as e:  # noqa: BLE001
            print(f"TIGER {year} unavailable: {e}")
    else:
        raise SystemExit("No TIGER place file reachable")
    z = zipfile.ZipFile(io.BytesIO(raw))
    base = [n[:-4] for n in z.namelist() if n.endswith(".shp")][0]
    r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")))
    S, W, N, E = BBOX
    places = []
    for sr in r.iterShapeRecords():
        x0, y0, x1, y1 = sr.shape.bbox
        if x1 < W or x0 > E or y1 < S or y0 > N:
            continue
        rec = sr.record.as_dict()
        if rec.get("LSAD") not in ("25", "43", "47"):  # city, town, village (skip CDPs)
            continue
        parts = list(sr.shape.parts) + [len(sr.shape.points)]
        rings = [sr.shape.points[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]
        places.append((rec["NAMELSAD"], (x0, y0, x1, y1), rings))
    print(f"TIGER {year}: {len(places)} incorporated places in the Bexar box")
    return places, year


def city_of(lat, lon, places):
    for name, (x0, y0, x1, y1), rings in places:
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        # even-odd over all rings handles holes
        if sum(in_ring(lon, lat, ring) for ring in rings) % 2 == 1:
            return name
    return "Unincorporated"


# ---------------- 2. signals, stop signs, speed limits (OpenStreetMap / Overpass) ----------------
def overpass():
    S, W, N, E = BBOX
    q = f"""[out:json][timeout:300];
(node["highway"="traffic_signals"]({S},{W},{N},{E});
 node["highway"="stop"]({S},{W},{N},{E}););
out;
way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential)(_link)?$"]["maxspeed"]({S},{W},{N},{E});
out tags geom;"""
    for host in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"):
        try:
            raw = get(host, data=urllib.parse.urlencode({"data": q}).encode(), timeout=400)
            return json.loads(raw)
        except Exception as e:  # noqa: BLE001
            print(f"Overpass {host} failed: {e}")
            time.sleep(10)
    raise SystemExit("Overpass unreachable")


def mph(v):
    m = re.match(r"\s*(\d+)\s*(mph)?", v or "")
    if not m:
        return None
    n = int(m.group(1))
    return n if m.group(2) or n <= 85 else round(n / 1.609)


class Grid:
    def __init__(self, cell=0.005):
        self.c, self.g = cell, {}

    def key(self, lat, lon):
        return (int(lat / self.c), int(lon / self.c))

    def add(self, lat, lon, item):
        self.g.setdefault(self.key(lat, lon), []).append(item)

    def near(self, lat, lon, r=1):
        ky, kx = self.key(lat, lon)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                yield from self.g.get((ky + dy, kx + dx), [])


# ---------------- 3. land-owner type (Bexar Appraisal District parcels) ----------------
PARCELS = "https://maps.bexar.org/arcgis/rest/services/Parcels/MapServer/0"
PUBLIC_PATTERNS = [
    ("City", r"\bCITY OF\b|\bTOWN OF\b|\bVILLAGE OF\b"),
    ("County", r"\bCOUNTY\b"),
    ("State", r"\bSTATE OF TEXAS\b|\bTEXAS DEPARTMENT\b|\bTXDOT\b|\bTX DEPT\b|\bSTATE HIGHWAY\b"),
    ("Federal", r"\bUNITED STATES\b|\bUSA\b"),
    ("School", r"\bI\.?S\.?D\b|\bINDEPENDENT SCHOOL\b|\bSCHOOL DIST"),
    ("Utility", r"\bCPS ENERGY\b|\bSAN ANTONIO WATER\b|\bSAWS\b|\bVIA METRO"),
]
RELIGIOUS = r"\bCHURCH\b|\bBAPTIST\b|\bCATHOLIC\b|\bARCHDIOCESE\b|\bDIOCESE\b|\bMETHODIST\b|\bMINISTR|\bTEMPLE\b|\bMOSQUE\b|\bCONGREGATION\b"
BUSINESS = r"\b(LLC|L\.L\.C|INC|LTD|LP|L\.P|CORP|COMPANY|CO\b|PARTNERS|TRUST|HOLDINGS|PROPERTIES|INVESTMENTS|REALTY|BANK)\b"


def owner_type(name):
    n = (name or "").upper()
    for label, pat in PUBLIC_PATTERNS:
        if re.search(pat, n):
            return "Public: " + label, True
    if re.search(RELIGIOUS, n):
        return "Religious", False
    if re.search(BUSINESS, n):
        return "Private: business", False
    if n.strip():
        return "Private: individual", False
    return "Unknown", False


def parcel_owner_field():
    meta = json.loads(get(PARCELS + "?f=json", timeout=60))
    names = [f["name"] for f in meta.get("fields", [])]
    print("Parcel fields:", ", ".join(names))
    for cand in ("OWNER", "Owner", "OWNER_NAME", "OwnerName", "OWNERNAME", "owner_name", "OWN_NAME", "PY_OWNER_NAME"):
        if cand in names:
            return cand
    for n in names:
        if "OWN" in n.upper() and "ADDR" not in n.upper():
            return n
    return None


def parcels_near(lat, lon, field):
    params = {
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "distance": 45, "units": "esriSRUnit_Meter",
        "outFields": field, "returnGeometry": "false", "f": "json",
    }
    j = json.loads(get(PARCELS + "/query?" + urllib.parse.urlencode(params), timeout=60))
    if "error" in j:
        raise RuntimeError(j["error"])
    counts, public = {}, []
    for f in j.get("features", []):
        name = f["attributes"].get(field)
        t, is_public = owner_type(name)
        counts[t] = counts.get(t, 0) + 1
        if is_public and name and name not in public:
            public.append(name.strip())
    return {"types": counts, "public_owners": public[:4]}


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "data/bexar-aadt.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "data/bexar-enrich.json"
    stations = json.load(open(src))["stations"]
    result = {s[0]: {} for s in stations}
    notes = {}

    places, tiger_year = load_places()
    for s in stations:
        result[s[0]]["city"] = city_of(s[3], s[4], places)
    notes["city"] = f"US Census TIGER/Line {tiger_year} places"

    osm = overpass()
    sig, stp, ways = Grid(), Grid(), Grid()
    for el in osm["elements"]:
        if el["type"] == "node":
            (sig if el["tags"].get("highway") == "traffic_signals" else stp).add(el["lat"], el["lon"], (el["lat"], el["lon"]))
        elif el["type"] == "way" and el.get("geometry"):
            v = mph(el["tags"].get("maxspeed"))
            if not v:
                continue
            pts = [(p["lat"], p["lon"]) for p in el["geometry"]]
            item = (v, el["tags"].get("name") or el["tags"].get("ref") or "", pts)
            for p in pts[:: max(1, len(pts) // 20)] + [pts[-1]]:
                ways.add(p[0], p[1], item)
    print(f"OSM: {sum(len(v) for v in sig.g.values())} signals, {sum(len(v) for v in stp.g.values())} stop signs")
    for s in stations:
        lat, lon = s[3], s[4]
        r = result[s[0]]
        d = [meters(lat, lon, a, b) for a, b in sig.near(lat, lon)]
        r["signal_m"] = round(min(d)) if d else None
        d = [meters(lat, lon, a, b) for a, b in stp.near(lat, lon)]
        r["stop_m"] = round(min(d)) if d else None
        best = None
        for v, name, pts in {id(w): w for w in ways.near(lat, lon)}.values():
            dist = min(seg_dist(lat, lon, pts[i], pts[i + 1]) for i in range(len(pts) - 1)) if len(pts) > 1 else 1e9
            if dist < 60 and (best is None or dist < best[0]):
                best = (dist, v, name)
        r["speed"] = best[1] if best else None
        r["speed_road"] = best[2] if best else None
    notes["osm"] = "OpenStreetMap via Overpass, " + time.strftime("%Y-%m-%d")

    try:
        field = parcel_owner_field()
        if not field:
            raise RuntimeError("no owner field found")
        ok = 0
        for i, s in enumerate(stations):
            try:
                result[s[0]]["parcels"] = parcels_near(s[3], s[4], field)
                ok += 1
            except Exception as e:  # noqa: BLE001
                if i < 3:
                    print("parcel query failed:", e)
            time.sleep(0.4)
        notes["parcels"] = f"Bexar Appraisal District parcels via maps.bexar.org ({ok}/{len(stations)} stations)"
        print(notes["parcels"])
    except Exception as e:  # noqa: BLE001
        notes["parcels"] = f"unavailable: {e}"
        print("Parcels skipped:", e)

    json.dump({"generated": time.strftime("%Y-%m-%d"), "notes": notes, "stations": result},
              open(out, "w"), separators=(",", ":"))
    cities = {}
    for v in result.values():
        cities[v["city"]] = cities.get(v["city"], 0) + 1
    print("Cities:", sorted(cities.items(), key=lambda x: -x[1])[:12])
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
