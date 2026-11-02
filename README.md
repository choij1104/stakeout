# Stakeout

Where a yard sign gets seen, and whether you can legally put it there.

Stakeout scores traffic-count locations for yard-sign visibility (traffic volume, how readable a sign is at that road's speed, field checks) and walks each spot through the placement rules that apply to it. First market: Bexar County, Texas.

- `index.html` — the app (Leaflet + OpenStreetMap tiles; GitHub Pages)
- `data/bexar-aadt.json` — TxDOT AADT count stations, refreshed monthly by `.github/workflows/data.yml`
- `scripts/fetch_aadt.py` — pulls one county from TxDOT's public AADT feature service

Data notes
- Traffic counts: TxDOT Annual Average Daily Traffic Counts (Public). TxDOT license: internal / non-commercial use without TxDOT's written consent. Obtain consent before any commercial release.
- Map tiles and reverse-geocoded addresses: © OpenStreetMap contributors (tile.openstreetmap.org, Nominatim — light, user-triggered use only).
- Rules: TxDOT right-of-way policy; City of San Antonio sign guide (Code Ch. 28); Tex. Elec. Code §§61.003, 259.004.

Planning aid, not legal advice.
