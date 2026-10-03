// Tiny client for the Digital Twin Catalog API (ES module, no dependencies; needs the global `Cesium` for tile helpers).
export function createClient(baseUrl) {
  const base = baseUrl.replace(/\/$/, "") + "/api/v1";
  async function get(path, params = {}) {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
    const res = await fetch(`${base}${path}${qs.size ? "?" + qs : ""}`);
    const body = await res.json();
    if (!res.ok) throw Object.assign(new Error(body?.error?.message || res.statusText), { status: res.status, body });
    return body;
  }

  /** Layers with their active tileset. Defaults to published layers. */
  const listLayers = (filters = {}) => get("/catalog", filters).then((r) => r.items);
  const getFeature = (objectid, dataset) => get(`/features/${encodeURIComponent(objectid)}`, { dataset });

  /** Load the active tileset of the first layer matching the filters (e.g. {lod: 1}). Needs Cesium.Ion.defaultAccessToken for ion tiles. */
  async function loadTileset(viewer, filters = {}) {
    const [layer] = await listLayers(filters);
    if (!layer) throw new Error("No published layer matches " + JSON.stringify(filters));
    const t = layer.tileset;
    const tileset = t.provider === "cesium_ion"
      ? await Cesium.Cesium3DTileset.fromIonAssetId(t.ionAssetId)
      : await Cesium.Cesium3DTileset.fromUrl(t.url);
    if (t.heightOffsetM) {   // lift/lower along the local vertical so buildings sit on the terrain
      const c = tileset.boundingSphere.center;
      const up = Cesium.Cartesian3.normalize(c, new Cesium.Cartesian3());
      tileset.modelMatrix = Cesium.Matrix4.fromTranslation(Cesium.Cartesian3.multiplyByScalar(up, t.heightOffsetM, new Cesium.Cartesian3()));
    }
    viewer.scene.primitives.add(tileset);
    return { layer, tileset };
  }

  /** Click a building: read its gml:id from the tile and fetch its attributes from the database.
   *  The property that holds the id depends on the converter; `idKeys` are tried in order. */
  function enablePicking(viewer, onFeature, { idKeys = ["objectid", "gml_id", "gmlId", "id", "name"], dataset } = {}) {
    const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
    handler.setInputAction(async (click) => {
      const picked = viewer.scene.pick(click.position);
      if (!Cesium.defined(picked) || !picked.getProperty) return onFeature(null);
      const key = idKeys.find((k) => picked.hasProperty?.(k));
      if (!key) return onFeature(null, new Error("Picked tile object has no id property (tried " + idKeys.join(", ") + ")"));
      try { onFeature(await getFeature(picked.getProperty(key), dataset)); } catch (e) { onFeature(null, e); }
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
    return () => handler.destroy();
  }

  return { listLayers, getFeature, loadTileset, enablePicking, get };
}
