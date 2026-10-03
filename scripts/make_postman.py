"""Generate postman/naraga-catalog.postman_collection.json (+ environments). Re-run after API changes.
Run: python3 scripts/make_postman.py   Headless: npx newman run postman/naraga-catalog.postman_collection.json -e postman/local.postman_environment.json"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "postman"
ERR = "pm.test('error format', () => { const j = pm.response.json(); pm.expect(j.error).to.have.keys('code','message','details'); });"
FAST = "pm.test('response time < 1500 ms', () => pm.expect(pm.response.responseTime).to.be.below(1500));"


def req(name, path, tests, method="GET"):
    url = "{{baseUrl}}/api/v1" + path
    return {"name": name, "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": tests + [FAST]}}],
            "request": {"method": method, "header": [], "url": {"raw": url, "host": ["{{baseUrl}}"],
                        "path": ["api", "v1"] + [p for p in path.split("?")[0].split("/") if p],
                        "query": [{"key": k, "value": v} for k, v in (kv.split("=", 1) for kv in path.split("?")[1].split("&"))] if "?" in path else []}}}


def status(code): return f"pm.test('status {code}', () => pm.response.to.have.status({code}));"


folders = [
    ("Service", [req("Health", "/health", [status(200), "pm.test('db ok', () => pm.expect(pm.response.json().database).to.eql('ok'));"])]),
    ("Catalog", [
        req("Published layers (default)", "/catalog", [status(200),
            "const j = pm.response.json();",
            "pm.test('shape', () => { pm.expect(j).to.have.keys('items','limit','offset','total'); });",
            "pm.test('only published', () => j.items.forEach(i => pm.expect(i.status).to.eql('published')));",
            "pm.test('each has an active tileset', () => j.items.forEach(i => pm.expect(i.tileset).to.be.an('object')));",
            "if (j.items.length) { pm.collectionVariables.set('layerId', j.items[0].layerId); pm.collectionVariables.set('datasetCode', j.items[0].dataset.code); }"]),
        req("All layers", "/catalog?status=all", [status(200), "pm.test('has total', () => pm.expect(pm.response.json().total).to.be.a('number'));"]),
        req("Filter lod=1", "/catalog?lod=1&status=all", [status(200), "pm.test('lod respected', () => pm.response.json().items.forEach(i => pm.expect(i.lod).to.eql(1)));"]),
        req("Filter lod=2", "/catalog?lod=2&status=all", [status(200), "pm.test('lod respected', () => pm.response.json().items.forEach(i => pm.expect(i.lod).to.eql(2)));"]),
        req("Filter theme=building", "/catalog?theme=building&status=all", [status(200), "pm.test('theme respected', () => pm.response.json().items.forEach(i => pm.expect(i.theme).to.eql('building')));"]),
        req("Filter bbox (IKN area)", "/catalog?bbox=116.0,-1.5,117.5,-0.0&status=all", [status(200)]),
        req("Pagination limit=1", "/catalog?status=all&limit=1&offset=0", [status(200), "pm.test('limit', () => pm.expect(pm.response.json().items.length).to.be.at.most(1));"]),
    ]),
    ("Layers and datasets", [
        req("Layer tileset", "/layers/{{layerId}}/tileset", ["if (!pm.collectionVariables.get('layerId')) { pm.test.skip('no published layer yet', () => {}); } else {", status(200),
            "pm.test('provider', () => pm.expect(pm.response.json().provider).to.be.oneOf(['cesium_ion','self_hosted'])); }"]),
        req("Datasets", "/datasets", [status(200), "pm.test('items', () => pm.expect(pm.response.json().items).to.be.an('array'));"]),
        req("Dataset detail", "/datasets/oikn", [status(200), "pm.test('layers', () => pm.expect(pm.response.json().layers).to.be.an('array'));"]),
        req("Dataset stats", "/datasets/oikn/stats", [status(200)]),
    ]),
    ("Features", [
        req("Search (GeoJSON)", "/features?dataset=oikn&limit=5", [status(200), "pm.test('FeatureCollection', () => pm.expect(pm.response.json().type).to.eql('FeatureCollection'));",
            "const f = pm.response.json().features; if (f.length) pm.collectionVariables.set('objectId', f[0].properties.objectid);"]),
        req("Feature by id", "/features/{{objectId}}?dataset=oikn", ["if (!pm.collectionVariables.get('objectId')) { pm.test.skip('no features imported yet', () => {}); } else {", status(200),
            "pm.test('attributes', () => pm.expect(pm.response.json().attributes).to.be.an('array')); }"]),
    ]),
    ("Error cases", [
        req("lod out of range", "/catalog?lod=9", [status(422), ERR]),
        req("unknown status", "/catalog?status=nope", [status(422), ERR]),
        req("bad bbox", "/catalog?bbox=1,2", [status(422), ERR]),
        req("limit too small", "/catalog?limit=0", [status(422), ERR]),
        req("unknown dataset", "/datasets/does_not_exist", [status(404), ERR]),
        req("unknown feature", "/features/does-not-exist", [status(404), ERR]),
        req("bad layer id", "/layers/not-a-uuid/tileset", [status(422), ERR]),
    ]),
]
collection = {"info": {"name": "Digital Twin Catalog API", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                       "description": "Read-only API tests. Select an environment (local or railway) and run the collection."},
              "item": [{"name": n, "item": i} for n, i in folders],
              "variable": [{"key": k, "value": ""} for k in ("layerId", "datasetCode", "objectId")]}
(OUT / "naraga-catalog.postman_collection.json").write_text(json.dumps(collection, indent=2))
for env, url in (("local", "http://localhost:8000"), ("railway", "https://YOUR-SERVICE.up.railway.app")):
    (OUT / f"{env}.postman_environment.json").write_text(json.dumps({"name": env, "values": [
        {"key": "baseUrl", "value": url, "enabled": True},
        {"key": "ionToken", "value": "", "type": "secret", "enabled": True}]}, indent=2))
print("written", OUT)
