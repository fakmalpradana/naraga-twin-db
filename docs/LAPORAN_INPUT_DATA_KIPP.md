# Laporan: data KIPP di 3DCityDB online, endpoint FE, dan panduan tambah data via API

Tanggal: 15 Oktober 2026 · Server: `twin-vm` (Hetzner cx23, Nuremberg) · Base URL: **https://twindb.178-105-182-162.sslip.io**

## 1. Status saat ini

| Item | Nilai |
|---|---|
| Layer published | `oikn` / `building` / LOD1, judul "OIKN buildings LOD1" (layerId `ec5f6a15-5850-4f44-903c-dab494370892`) |
| Isi | 389 gedung KIPP (`KIPP_0000` … `KIPP_0388`), 22 lokasi (atribut `nama`, mis. `MASJID NUSANTARA_BO`) |
| Tileset aktif | v17, self-hosted, `/tiles/oikn/building/lod1/v17/tileset.json` (dibangun langsung dari database) |
| Sumber kebenaran | 3DCityDB 5.1.4 (skema `citydb`) di VM. Tiles selalu dibangun ulang dari tabel `citydb.*`, bukan dari file |
| Akses DB langsung | Tertutup dari internet; lewat SSH tunnel (lihat §6) |

`data/LOD1_landmark` **tidak** dimasukkan (CRS sumber masih salah). Muatan uji yang sempat masuk sudah dihapus dengan me-restore DB ke kondisi KIPP-only, dan folder tiles-nya juga dihapus.

## 2. Bagaimana data KIPP masuk ke database

```
data/kipp/KIPP_LOD1.city.json ──scripts/import.sh (citydb-tool 1.4.0)──► 3DCityDB lokal (lineage oikn.building.lod1)
                                                                          │ catalog.import_job: 389 Building, validation_passed
                                                                          ▼
                                                             pg_dump lokal ──pg_restore──► 3DCityDB di VM
                                                                                            │
                                         POST /api/v1/layers/{id}/rebuild ◄─────────────────┘
                                         (tiles dibangun dari citydb.* → v17 aktif)
```

1. **Sumber.** `data/kipp/KIPP_LOD1.city.json` adalah CityJSON 2.0 di EPSG:32750, 389 gedung LOD1, dengan atribut `nama`, `h_ground`, `h_roof`, `flag`, dan `measuredHeight`. `KIPP_LOD1.gpkg` dan `KIPP_LOD1.obj` adalah turunan dari data yang sama: footprint dan mesh. ID di OBJ (`o KIPP_xxxx`) sama dengan `objectid` di DB.
2. **Import (8 Okt 2026, lokal).**
   ```bash
   scripts/import.sh USER=fairuz FILE=data/kipp/KIPP_LOD1.city.json DATASET=oikn THEME=building LOD=1
   ```
   Hasilnya satu `catalog.import_job` (succeeded, `{"Building": 389, "RoofSurface": 389, "WallSurface": 389, "GroundSurface": 389}`) dengan lineage `oikn.building.lod1`. Setelah itu job ditandai `validation_passed`, dan layer dipindah draft → validated → published lewat SQL di `docs/en/07_RUNBOOK.md`.
3. **Pindah ke VM (15 Okt).**
   ```bash
   pg_dump -Fc   # di Mac
   pg_restore --clean --if-exists   # di VM, tanpa --no-owner
   ```
   Restore dijalankan setelah api start sekali, supaya role `twin_app`/`twin_api` sudah ada. Dump awal disimpan di `/opt/backups/initial-from-local.dump`.
4. **Tiles.** Sejak write API ada, tiles dibangun ulang dari database (`api/catalog/tiles.py`) setiap kali ada perubahan, atau lewat `POST /layers/{id}/rebuild`. Versi lama otomatis dihapus dari disk; 2 versi terbaru disimpan.

Pengecekan konsistensi hari ini: 389 feature `Building` aktif di lineage `oikn.building.lod1`, sama dengan 389 objek di CityJSON sumber. `featureCount` di `/catalog` juga 389, dan tileset v17 berisi 389 gedung.

## 3. Endpoint untuk frontend (tanpa token)

| Kebutuhan | Endpoint |
|---|---|
| Daftar layer + URL tileset aktif | `GET /api/v1/catalog` (filter: `dataset`, `theme`, `lod`, `bbox`, `status=all`) |
| File 3D Tiles | `GET /tiles/oikn/building/lod1/v<N>/tileset.json` (ambil URL dari `/catalog`, jangan di-hardcode, karena versinya naik setiap ada edit) |
| Atribut satu gedung (saat diklik) | `GET /api/v1/features/{objectid}` |
| Cari gedung (GeoJSON) | `GET /api/v1/features?dataset=oikn&bbox=minLon,minLat,maxLon,maxLat&q=...` |
| Statistik dataset | `GET /api/v1/datasets/oikn/stats` |
| Dokumentasi interaktif (OpenAPI) | `/api/v1/docs` |
| Viewer contoh | `/viewer/` |

CORS saat ini mengizinkan `https://twindb.178-105-182-162.sslip.io`, `http://localhost:3000`, dan `http://localhost:8000`. Origin FE lain perlu ditambahkan ke `ALLOWED_ORIGINS` di `/opt/twindb/.env`, lalu jalankan `docker compose up -d api`.

### Cesium (paling ringkas)

```js
import { createClient } from "https://twindb.178-105-182-162.sslip.io/client/naraga-catalog.js";
const client = createClient("https://twindb.178-105-182-162.sslip.io");
const { tileset } = await client.loadTileset(viewer, { dataset: "oikn", theme: "building", lod: 1 });
viewer.zoomTo(tileset);
client.enablePicking(viewer, (feature) => console.log(feature));   // klik gedung -> atribut dari 3DCityDB
```

Tanpa library: ambil `items[0].tileset.url` dari `/catalog?dataset=oikn&lod=1`, lalu jalankan `Cesium.Cesium3DTileset.fromUrl(url)`. Setiap gedung di tile membawa properti `objectid`, dipakai untuk `GET /features/{objectid}`. Library lain yang mendukung 3D Tiles 1.1 (deck.gl, three.js + 3d-tiles-renderer, CesiumJS) bisa memakai URL yang sama.

Catatan FE:
- Datum vertikal `unknown`, jadi gedung bisa tampak melayang atau tenggelam terhadap terrain. Kalau perlu, geser lewat `heightOffsetM`.
- Setelah ada edit, reload katalog untuk mendapat URL tileset versi baru.

## 4. Panduan menambah dan mengubah data lewat API

### 4.1 Autentikasi

Setiap request write wajib membawa dua header berikut:

```
Authorization: Bearer <WRITE_API_TOKEN>
X-User: <nama pengubah>
```

Token hanya ada di VM:

```bash
ssh twin-vm "grep WRITE_API_TOKEN /opt/twindb/.env"
```

Jangan taruh token di kode FE yang publik. Write API hanya untuk admin atau tool internal.

```bash
U=https://twindb.178-105-182-162.sslip.io
H=(-H "Authorization: Bearer $TOKEN" -H "X-User: fairuz" -H "Content-Type: application/json")
```

### 4.2 Tambah gedung (Create)

```bash
curl -X POST "$U/api/v1/features" "${H[@]}" -d '{
  "dataset": "oikn", "theme": "building",
  "objectid": "KIPP_0389",
  "footprint": [[116.7090,-0.9720],[116.7094,-0.9720],[116.7094,-0.9717],[116.7090,-0.9717]],
  "holes": [],
  "height_m": 25,
  "base_z": 26.3,
  "attributes": {"nama": "Gedung Baru", "lantai": 6}
}'
```

Penjelasan field:

| Field | Keterangan |
|---|---|
| `footprint` | Ring luar dalam **lon/lat (EPSG:4326)**. Server mengubahnya ke EPSG:32750. Minimal 3 titik, poligon valid, luas ≥ 1 m². |
| `holes` | Opsional, berisi ring dalam. |
| `height_m` | Tinggi gedung, 0 < `height_m` ≤ 1000 meter. Bukan elevasi. |
| `base_z` | Elevasi tanah dalam sistem tinggi data KIPP (sama dengan atribut `h_ground`). Kalau dikosongkan, server memakai rata-rata ground layer, sekitar 26 m. **Isi nilai yang benar** supaya gedung tidak melayang atau tenggelam. |
| `objectid` | Opsional; huruf, angka, `_ . -`, maksimal 64 karakter. Harus unik di seluruh DB. Kalau dikosongkan, server membuat `API_xxxxxxxxxx`. |
| `attributes` | Bebas. Nilai string atau angka, nama atribut berupa huruf/angka/underscore. |

Hasilnya `201` berisi `objectid`, `jobId`, dan `tileset` baru (versi, URL, jumlah gedung). Dalam satu request, gedung ditulis ke 3DCityDB oleh `citydb-tool`, tiles dibangun ulang dari DB, versi baru diaktifkan, dan `catalog.import_job` mencatat siapa mengubah apa.

### 4.3 Ubah gedung (Update)

```bash
curl -X PATCH "$U/api/v1/features/KIPP_0389" "${H[@]}" -d '{"height_m": 40, "attributes": {"lantai": 10, "catatan": null}}'
```

Kirim hanya yang berubah. Atribut di-merge dengan yang lama; nilai `null` menghapus atribut. Ganti bentuk dengan `footprint` (dan `holes`). Di belakang layar, update adalah hapus lalu import ulang gedung itu. Kalau import ulang gagal, blok lama dipulihkan.

### 4.4 Hapus gedung (Delete)

```bash
curl -X DELETE "$U/api/v1/features/KIPP_0389" "${H[@]}"
```

Layer harus selalu menyisakan minimal satu gedung (`409`).

### 4.5 Banyak perubahan sekaligus

Setiap write dengan rebuild di layer KIPP butuh sekitar 11–17 detik: sekitar 7 detik untuk start `citydb-tool` dan sekitar 8 detik untuk rebuild tiles. Untuk banyak gedung:

1. Kirim setiap request dengan `?rebuild=false` (sekitar 7–10 detik per gedung). Respons akan berisi `"stale": true`.
2. Setelah semua masuk, rebuild sekali:

```bash
curl -X POST "$U/api/v1/layers/ec5f6a15-5850-4f44-903c-dab494370892/rebuild" "${H[@]}"
```

```python
# contoh batch dari GeoJSON (lon/lat) dengan requests
import json, requests
U = "https://twindb.178-105-182-162.sslip.io/api/v1"
H = {"Authorization": "Bearer <TOKEN>", "X-User": "fairuz"}
for f in json.load(open("gedung_baru.geojson"))["features"]:
    body = {"dataset": "oikn", "objectid": f["properties"]["id"], "footprint": f["geometry"]["coordinates"][0],
            "holes": f["geometry"]["coordinates"][1:], "height_m": f["properties"]["tinggi"],
            "base_z": f["properties"]["h_ground"], "attributes": {"nama": f["properties"]["nama"]}}
    r = requests.post(f"{U}/features", params={"rebuild": "false"}, json=body, headers=H, timeout=120)
    print(body["objectid"], r.status_code, r.json().get("error", {}).get("message", ""))
requests.post(f"{U}/layers/ec5f6a15-5850-4f44-903c-dab494370892/rebuild", headers=H, timeout=300)
```

Untuk ratusan gedung atau lebih, ada `scripts/bulk_create.py` (belum di-commit). Script ini memakai pemeriksaan dan builder CityGML yang sama dengan `POST /features`, tapi dengan satu import `citydb-tool`, satu import job, dan satu rebuild untuk seluruh batch. Inputnya list body `POST /features`. Pada uji sebelumnya, 2372 gedung masuk dalam 2,5 menit; lewat API satu per satu akan makan sekitar 5 jam. Cara jalannya ada di header script. Untuk mengganti seluruh isi layer dari file CityGML/CityJSON, tetap pakai `scripts/import.sh` (README §6).

### 4.6 Kode error

| HTTP | Kapan |
|---|---|
| 401 | Token kosong atau salah |
| 400 | Header `X-User` tidak ada |
| 404 | Layer (`dataset`/`theme`/LOD1) atau `objectid` tidak ada |
| 409 | `objectid` sudah dipakai; atau menghapus gedung terakhir di layer |
| 422 | Footprint tidak valid (self-intersection, < 3 titik, luas < 1 m², bukan lon/lat), tinggi di luar 0–1000, nama atribut tidak valid |
| 502 | Data tersimpan tapi tiles gagal dibangun. Panggil endpoint rebuild |
| 503 | Write API dimatikan (`WRITE_API_TOKEN` kosong) |

Format error selalu: `{"error": {"code", "message", "details"}}`.

### 4.7 Yang belum bisa lewat API

- Membuat dataset atau layer baru. Ini dilakukan lewat Django admin (`/admin`) atau seed SQL `db/seeds/<project>/`, karena API hanya menulis ke layer yang sudah ada.
- Mem-publish layer baru (draft → validated → published). Lewat admin atau SQL di runbook.
- Gedung selain blok LOD1 (footprint diekstrusi). Model LOD2+ di-import lewat `scripts/import.sh`.

## 5. Hasil uji CRUD (15 Okt 2026, lewat URL publik, layer `oikn`)

| # | Kasus | Hasil | Waktu |
|---|---|---|---|
| C1 | Create tanpa token | 401 ✅ | 0,1 s |
| C2 | Create tanpa `X-User` | 400 ✅ | 1,0 s |
| C3 | Footprint bow-tie (self-intersection) | 422 ✅ | 0,2 s |
| C4 | `height_m: 0` | 422 ✅ | 0,2 s |
| C5 | Layer tidak ada (`dataset: nope`) | 404 ✅ | 0,1 s |
| C6 | Create `TEST_CRUD_001` (rebuild) | 201 ✅, tileset v14, 390 gedung | 13,2 s |
| C7 | Create `objectid` duplikat | 409 ✅ | 0,2 s |
| R1 | Read `TEST_CRUD_001` | 200 ✅ footprint, `baseZ` 26,31, `heightM` 25 | 0,2 s |
| R2 | Search bbox | 200 ✅ gedung uji ditemukan | 0,2 s |
| R3 | `/catalog` setelah create | `featureCount` 390 ✅ | 0,2 s |
| U1 | Patch tinggi 40 + atribut (rebuild) | 200 ✅, tileset v15 | 16,6 s |
| U2 | Read setelah patch | `heightM` 40 ✅ | 0,2 s |
| U3 | Patch objek tidak ada | 404 ✅ | 0,2 s |
| D1 | Delete (rebuild) | 200 ✅, tileset v16, 389 gedung | 11,5 s |
| D2 | Read setelah delete | 404 ✅ | 0,2 s |
| D3 | Delete lagi | 404 ✅ | 0,2 s |
| X1 | `/catalog` setelah delete | `featureCount` 389 ✅ | 0,2 s |
| X2 | `POST /layers/{id}/rebuild` | 200 ✅, tileset v17, 389 gedung | 8,7 s |

Pemeriksaan tambahan:
- `buildings.glb` v15 memuat `TEST_CRUD_001`, sedangkan v16 tidak. Jadi tiles benar-benar mengikuti isi DB.
- Tiga baris audit tercatat di `catalog.import_job`: `api:POST`, `api:PATCH`, dan `api:DELETE` untuk `TEST_CRUD_001`, semuanya `succeeded`.
- Viewer `/viewer/` memuat layer dan klik gedung mengembalikan atribut dari DB. Ini dicek dengan layer uji sebelum di-rollback; endpoint dan kodenya sama untuk KIPP.

Belum dicek: isi atribut setelah PATCH (`lantai` 10, `status` "uji") tidak saya baca ulang secara eksplisit sebelum gedung uji dihapus. Yang terverifikasi hanya tinggi barunya.

Gedung uji sudah dihapus. Data KIPP kembali 389 gedung, sama seperti sebelum uji. Yang tersisa dari uji: baris audit di `import_job` dan nomor versi tileset yang naik (v14–v17).

## 6. Catatan dan batasan

- **Timeout.** Gunicorn memotong request setelah 30 detik. Write dengan rebuild di KIPP butuh 11–17 detik, jadi masih aman. Layer yang jauh lebih besar (ribuan gedung) butuh rebuild lebih dari 1 menit. Untuk layer seperti itu, naikkan `--timeout` di `api/entrypoint.sh` dan pakai `rebuild=false` lalu satu kali rebuild.
- **`citydb-tool` commit di luar transaksi API.** Kalau request terputus setelah langkah `citydb-tool` (misalnya kena timeout), perubahan gedung tetap tersimpan, tapi tiles dan catatan job tidak ikut. Pulihkan dengan `POST /layers/{id}/rebuild`, lalu jalankan `SELECT catalog.refresh_derived()` lewat psql untuk memperbarui `featureCount`.
- **Akses DB langsung** (QGIS, DBeaver, psql):
  ```bash
  ssh -N -L 55433:127.0.0.1:55432 twin-vm
  ```
  Lalu sambungkan ke `localhost:55433`, user `postgres`, database `postgres`. Password ada di `/opt/twindb/.env` (`POSTGRES_PASSWORD`).
- **Backup.** Cron jalan setiap hari jam 18:00 UTC ke `/opt/backups` (dump DB + tiles, disimpan 7 hari). Backup ini masih berada di VM yang sama.
- **Kompresi.** Caddy mengompres respons API dan tiles (gzip/zstd).
- **`data/LOD1_landmark`** ditunda sampai CRS-nya diperbaiki. Pipeline yang sudah teruji untuk data itu: GeoJSON footprint + BHM → body `POST /features`, dengan `base_z` dari `h_ground` gedung KIPP yang menaungi → `bulk_create.py` → layer `oikn_landmark`. Converter dan seed-nya sudah dihapus, karena perlu dibuat ulang setelah CRS benar.
