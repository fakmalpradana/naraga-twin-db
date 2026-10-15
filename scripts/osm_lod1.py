#!/usr/bin/env python3
"""Dummy LOD1 test data: OSM building footprints inside a KML AOI -> extruded CityGML 2.0 (EPSG:32750).
Usage: scripts/osm_lod1.py data/AOI.kml data/osm_lod1.gml"""
import re, sys, random, requests
from shapely.geometry import Polygon, shape
from shapely.geometry.polygon import orient
from pyproj import Transformer

kml, out = sys.argv[1:3]
nums = re.findall(r"(-?\d+\.\d+),(-?\d+\.\d+),", open(kml).read())
aoi = Polygon([(float(x), float(y)) for x, y in nums])
w, s, e, n = aoi.bounds[0], aoi.bounds[1], aoi.bounds[2], aoi.bounds[3]
q = f'[out:json][timeout:120];way["building"]({s},{w},{n},{e});out geom;'
import time
MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
els = None
for attempt in range(4):  # public Overpass is often busy: retry across mirrors
    for u in MIRRORS:
        try:
            r = requests.post(u, data={"data": q}, headers={"User-Agent": "twindb-test/0.1"}, timeout=180)
            if r.ok: els = r.json()["elements"]; break
        except (requests.RequestException, ValueError): pass
    if els is not None: break
    time.sleep(15)
if els is None: sys.exit("all Overpass mirrors failed")
tr = Transformer.from_crs(4326, 32750, always_xy=True)
random.seed(1)  # ponytail: ways only; multipolygon relations skipped (dummy data)

def height(t):
    try:
        if "height" in t: return float(t["height"].split()[0])
        if "building:levels" in t: return float(t["building:levels"]) * 3
    except ValueError: pass
    return round(random.uniform(3, 12), 1)  # dummy height when OSM has none

def poly_xml(ring):
    return ("<gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing><gml:posList>"
            + " ".join(f"{x:.2f} {y:.2f} {z:.2f}" for x, y, z in ring) + "</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>")

members, kept = [], 0
for el in els:
    if len(el.get("geometry", [])) < 4: continue
    ll = [(p["lon"], p["lat"]) for p in el["geometry"]]
    if ll[0] != ll[-1]: continue
    g = Polygon(ll)
    if not g.is_valid or not aoi.contains(g.centroid): continue
    xy = list(orient(Polygon([tr.transform(*p) for p in ll]), 1.0).exterior.coords)  # CCW
    h, t = height(el.get("tags", {})), el.get("tags", {})
    z0 = 0.0  # ponytail: flat base at 0; sample a DTM when real terrain matters
    faces = [poly_xml([(x, y, z0) for x, y in reversed(xy)]), poly_xml([(x, y, z0 + h) for x, y in xy])]
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        faces.append(poly_xml([(x1, y1, z0), (x2, y2, z0), (x2, y2, z0 + h), (x1, y1, z0 + h)]))
    kept += 1
    members.append(f'<core:cityObjectMember><bldg:Building gml:id="osm_{el["id"]}">'
        f'<gen:doubleAttribute name="height"><gen:value>{h}</gen:value></gen:doubleAttribute>'
        f'<bldg:measuredHeight uom="m">{h}</bldg:measuredHeight>'
        f'<bldg:lod1Solid><gml:Solid srsName="urn:ogc:def:crs:EPSG::32750" srsDimension="3"><gml:exterior><gml:CompositeSurface>{"".join(faces)}</gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod1Solid>'
        '</bldg:Building></core:cityObjectMember>')

open(out, "w").write('<?xml version="1.0" encoding="UTF-8"?>\n<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
    'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gen="http://www.opengis.net/citygml/generics/2.0" '
    'xmlns:gml="http://www.opengis.net/gml">' + "".join(members) + "</core:CityModel>")
print(f"{len(els)} OSM ways, {kept} buildings in AOI -> {out}")
