# Brief FE: menampilkan data KIPP (3D) dari twindb

**Base URL:** `https://twindb.178-105-182-162.sslip.io` (server test). Data GET tidak butuh token.
**Docs interaktif:** `/api/v1/docs` · **Contoh jalan:** `/viewer/`

## Langkah

1. **Origin FE kamu harus diizinkan (CORS).** `http://localhost:3000` dan `http://localhost:8000` sudah diizinkan. Untuk domain lain, minta ke backend agar ditambahkan.

2. **Ambil daftar layer dan URL tileset aktif.**
   ```js
   const BASE = "https://twindb.178-105-182-162.sslip.io";
   const { items } = await (await fetch(`${BASE}/api/v1/catalog?dataset=oikn&theme=building&lod=1`)).json();
   const layer = items[0];                 // OIKN buildings LOD1, 389 gedung
   const url = layer.tileset.url;          // absolut, mis. …/tiles/oikn/building/lod1/v17/tileset.json
   ```
   **Jangan hardcode URL tileset.** Versinya naik setiap kali data diedit.

3. **Tampilkan di Cesium.**
   ```js
   const tileset = await Cesium.Cesium3DTileset.fromUrl(url);
   viewer.scene.primitives.add(tileset);
   viewer.zoomTo(tileset);
   ```
   Kalau `layer.tileset.heightOffsetM` tidak 0, geser tileset sebesar nilai itu (meter) ke arah vertikal. Format tiles adalah 3D Tiles 1.1, jadi deck.gl atau three.js (`3d-tiles-renderer`) juga bisa memakai URL yang sama.

4. **Klik gedung untuk menampilkan atribut dari database.**
   ```js
   new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas).setInputAction(async (e) => {
     const id = viewer.scene.pick(e.position)?.getProperty?.("objectid");   // mis. "KIPP_0136"
     if (!id) return;
     const f = await (await fetch(`${BASE}/api/v1/features/${id}`)).json();
     // f.attributes = [{name:"nama", value:"MASJID NUSANTARA_BO"}, {name:"h_ground", value:…}, …]
     // f.geometry = {footprint:[[[lon,lat],…]], baseZ, heightM}
   }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
   ```

5. **Opsional: cari dan tampilkan daftar gedung.**
   ```js
   await fetch(`${BASE}/api/v1/features?dataset=oikn&q=masjid&limit=50`)
   ```
   Hasilnya GeoJSON. `q` mencocokkan `objectid` atau nama gedung. Geometrinya berupa kotak batas (bounding box), bukan footprint asli.

**Jalan pintas:** cara singkat untuk langkah 2–4 sekaligus:
```js
import { createClient } from "https://twindb.178-105-182-162.sslip.io/client/naraga-catalog.js";
const c = createClient(BASE);
const { tileset } = await c.loadTileset(viewer, { dataset: "oikn", lod: 1 });
viewer.zoomTo(tileset);
c.enablePicking(viewer, (f) => console.log(f));
```

## Yang perlu ditangani

- **`tileset` bisa `null`.** Layer belum punya tileset aktif; tampilkan pesan.
- **`isStale: true`.** Data sudah berubah tapi tiles belum dibangun ulang; tampilkan peringatan kecil.
- **Format error selalu sama:** `{"error": {"code", "message", "details"}}`.
- **Datum tinggi masih `unknown`.** Di atas terrain, gedung bisa tampak melayang atau tenggelam beberapa meter.

## Mengubah data (khusus admin atau tool internal)

`POST`, `PATCH`, dan `DELETE /api/v1/features` butuh token dan header `X-User`. **Token jangan pernah dimasukkan ke bundle FE publik.** Panduan lengkapnya ada di `docs/LAPORAN_INPUT_DATA_KIPP.md` §4.
