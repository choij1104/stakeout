"""Pull TxDOT Annual Average Daily Traffic (AADT) count stations for one Texas county.

Source: TxDOT Annual Average Daily Traffic Counts (Public), ArcGIS feature service
TxDOT_AADT_Annuals_(Public_View). TxDOT license: internal / non-commercial use only
without TxDOT's written consent.

Usage: python scripts/fetch_aadt.py 15 data/bexar-aadt.json   (15 = Bexar County)
"""
import json
import sys
import time
import urllib.parse
import urllib.request

SERVICE = ("https://services.arcgis.com/KTcxiTD9dsQw4r7Z/arcgis/rest/services/"
           "TxDOT_AADT_Annuals_(Public_View)/FeatureServer/0/query")
FIELDS = "TRFC_STATN_ID,ON_ROAD,AADT_RPT_QTY,AADT_RPT_YEAR,LATITUDE,LONGITUDE,CNTY_NM"


def fetch(county_nbr: int):
    rows, offset = [], 0
    while True:
        params = {
            "where": f"CNTY_NBR={county_nbr}",
            "outFields": FIELDS,
            "returnGeometry": "false",
            "orderByFields": "OBJECTID_1",
            "resultOffset": offset,
            "resultRecordCount": 1000,
            "f": "json",
        }
        url = SERVICE + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "stakeout-data/0.2"})
        with urllib.request.urlopen(req, timeout=60) as r:
            page = json.load(r)
        if "error" in page:
            raise SystemExit(f"TxDOT service error: {page['error']}")
        feats = page.get("features", [])
        for f in feats:
            a = f["attributes"]
            if not (a.get("LATITUDE") and a.get("LONGITUDE") and a.get("AADT_RPT_QTY")):
                continue
            rows.append([
                a["TRFC_STATN_ID"],
                (a.get("ON_ROAD") or "").strip(),
                int(a["AADT_RPT_QTY"]),
                round(float(a["LATITUDE"]), 6),
                round(float(a["LONGITUDE"]), 6),
            ])
            year = a.get("AADT_RPT_YEAR")
        offset += len(feats)
        if not feats or not page.get("exceededTransferLimit"):
            break
        time.sleep(1)
    return rows, (year if rows else None)


def main():
    county = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    out = sys.argv[2] if len(sys.argv) > 2 else "data/bexar-aadt.json"
    rows, year = fetch(county)
    if len(rows) < 50:
        raise SystemExit(f"Only {len(rows)} stations returned; refusing to overwrite {out}.")
    doc = {
        "source": "TxDOT Annual Average Daily Traffic Counts (Public)",
        "license": "TxDOT: internal/non-commercial use without written consent",
        "county_nbr": county,
        "aadt_year": year,
        "fetched": time.strftime("%Y-%m-%d"),
        "fields": ["id", "road", "aadt", "lat", "lon"],
        "stations": rows,
    }
    with open(out, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    print(f"Wrote {len(rows)} stations to {out}")


if __name__ == "__main__":
    main()
