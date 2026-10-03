import json


def get(client, url):
    r = client.get(url)
    return r.status_code, json.loads(r.content)


def test_health(client):
    assert get(client, "/api/v1/health") == (200, {"status": "ok", "database": "ok"})


def test_catalog_default_is_published_only(client, data):
    code, body = get(client, "/api/v1/catalog?dataset=zz_test")
    assert code == 200 and body["total"] == 1
    item = body["items"][0]
    assert item["lod"] == 1 and item["status"] == "published"
    assert item["tileset"]["ionAssetId"] == 424242 and item["tileset"]["provider"] == "cesium_ion"
    assert item["dataset"]["attribution"] == "(c) test"
    assert item["featureCount"] == 1 and item["isStale"] is False and item["bbox"]


def test_catalog_filters(client, data):
    assert get(client, "/api/v1/catalog?dataset=zz_test&status=all")[1]["total"] == 2
    assert get(client, "/api/v1/catalog?dataset=zz_test&status=all&lod=2")[1]["items"][0]["status"] == "draft"
    assert get(client, "/api/v1/catalog?dataset=zz_test&lod=2")[1]["total"] == 0          # lod2 not published
    assert get(client, "/api/v1/catalog?dataset=zz_test&bbox=116.6,-1.0,116.9,-0.8")[1]["total"] == 1
    assert get(client, "/api/v1/catalog?dataset=zz_test&bbox=0,0,1,1")[1]["total"] == 0
    assert get(client, "/api/v1/catalog?dataset=zz_test&theme=road")[1]["total"] == 0


def test_catalog_pagination(client, data):
    code, body = get(client, "/api/v1/catalog?dataset=zz_test&status=all&limit=1&offset=1")
    assert body["total"] == 2 and len(body["items"]) == 1 and body["limit"] == 1 and body["offset"] == 1


def test_error_format(client, data):
    for url in ("/api/v1/catalog?lod=7", "/api/v1/catalog?status=nope", "/api/v1/catalog?bbox=1,2", "/api/v1/catalog?limit=0"):
        code, body = get(client, url)
        assert code == 422 and set(body["error"]) == {"code", "message", "details"}, url
    code, body = get(client, "/api/v1/datasets/does_not_exist")
    assert code == 404 and body["error"]["code"] == "not_found"


def test_layer_tileset_and_datasets(client, data):
    code, body = get(client, f"/api/v1/layers/{data[1]}/tileset")
    assert code == 200 and body["ionAssetId"] == 424242
    assert get(client, f"/api/v1/layers/{data[2]}/tileset")[0] == 404
    code, body = get(client, "/api/v1/datasets/zz_test")
    assert code == 200 and len(body["layers"]) == 2
    assert any(i["code"] == "zz_test" for i in get(client, "/api/v1/datasets")[1]["items"])
    st = get(client, "/api/v1/datasets/zz_test/stats")[1]["items"]
    assert st and st[0]["featureCount"] == 1


def test_feature_by_id_links_tile_to_citydb(client, data):
    code, body = get(client, "/api/v1/features/zz-b1")
    assert code == 200 and body["class"] == "Building" and body["dataset"] == "zz_test" and body["theme"] == "building"
    attrs = {a["name"]: a for a in body["attributes"]}
    assert attrs["name"]["value"] == "Gedung Uji" and attrs["measuredHeight"]["value"] == 12.5 and attrs["measuredHeight"]["uom"] == "m"
    assert body["lodsAvailable"] == ["1"] and body["bbox"]
    assert get(client, "/api/v1/features/nope")[0] == 404


def test_features_search_geojson(client, data):
    code, body = get(client, "/api/v1/features?dataset=zz_test&q=uji&bbox=116.6,-1,116.9,-0.8")
    assert code == 200 and body["type"] == "FeatureCollection" and len(body["features"]) == 1
    assert body["features"][0]["properties"]["name"] == "Gedung Uji"
    assert get(client, "/api/v1/features?dataset=zz_test&q=zzz")[1]["features"] == []


def test_api_is_read_only(client, data):
    assert client.post("/api/v1/catalog").status_code in (404, 405)
