# Panduan: menjalankan database, endpoint untuk FE, dan update data 3D

Status saat ini (8 Okt 2026): dataset `oikn`, tema `building`, LOD1 berisi 389 gedung (KIPP), layer `published`, tileset v1 aktif (self-hosted).

## 1. Cara kerjanya

```
KIPP_LOD1.city.json ──import.sh──► 3DCityDB (citydb.*)      ◄── atribut gedung (GET /features/{id})
KIPP_LOD1.obj ──build_tiles.py──► tiles/…/tileset.json + .glb
                                         │ didaftarkan oleh publish_tiles.sh
                                         ▼
                                  catalog.tileset (url, versi, aktif)
FE ──GET /api/v1/catalog──► API ──► catalog.v_layer ──► {layer, tileset.url}
FE ──Cesium3DTileset.fromUrl(url)──► /tiles/…  (disajikan API dari folder tiles/)
```

- **Database tidak menyimpan tiles.** Ia menyimpan geometri/atribut (`citydb.*`) dan *pointer* tileset (`catalog.tileset`).
- Tiap gedung di dalam tile membawa `objectid` (= `gml:id` di database, mis. `KIPP_0287`). Klik gedung → FE memanggil `/features/{objectid}` → atribut dari database. Itulah yang menghubungkan tileset dengan database.
- Tileset hanya muncul di FE kalau layer `published` dan punya tileset aktif berstatus `ready`. Database menolak status itu kalau syaratnya tidak terpenuhi.

## 2. Menjalankan

Syarat: Docker Desktop menyala, file `.env` ada (`cp .env.example .env`, ganti password).

```bash
make up                       # = docker compose up -d --build  (db + api)
curl localhost:8000/api/v1/health
```

| Layanan | Alamat |
|---|---|
| API + viewer | http://localhost:8000 (viewer di `/viewer/`, admin di `/admin/`) |
| Postgres | `localhost:55432` (user `postgres`) |
| Tiles | http://localhost:8000/tiles/… (folder `./tiles`, di-mount ke container, tanpa rebuild) |

Hentikan: `make down` (data aman di volume `pgdata`; **jangan** pakai `down -v`, itu menghapus database).
Backup harian: `deploy/backup/backup.sh`.

**FE dari origin lain:** isi `ALLOWED_ORIGINS` di `.env` (mis. `http://localhost:3000`). Berlaku untuk `/api/` dan `/tiles/`.

**Produksi (Railway):** lihat `docs/en/08_RAILWAY_GUIDE.md`. Folder `tiles/` perlu tempat tetap: Railway Volume di `/srv/tiles`, atau tambahkan `COPY tiles tiles` di `api/Dockerfile` (ukuran tile ini hanya ~4 MB), atau pakai Cesium ion (`provider = cesium_ion`).

## 3. Endpoint untuk FE (semua GET, prefix `/api/v1`)

| Endpoint | Fungsi |
|---|---|
| `/catalog?dataset=oikn&theme=building&lod=1` | Layer + tileset aktif. Default hanya `status=published` (`status=all` untuk semua). |
| `/layers/{layerId}/tileset` | Tileset aktif satu layer (404 kalau belum ada). |
| `/features/{objectid}` | Atribut satu gedung (dari klik di peta). |
| `/features?dataset=oikn&bbox=minlon,minlat,maxlon,maxlat&q=teks&limit=50` | Cari objek, hasil GeoJSON (envelope). |
| `/datasets`, `/datasets/{code}`, `/datasets/{code}/stats` | Info dataset. |
| `/health` | Cek API + database. |

Contoh respons `/catalog`:

```json
{"items":[{"layerId":"ec5f6a15-…","lod":1,"status":"published","featureCount":389,"isStale":false,
  "bbox":[116.696,-0.987,116.722,-0.958],
  "tileset":{"provider":"self_hosted","url":"http://localhost:8000/tiles/oikn/building/lod1/v1/tileset.json",
             "version":1,"heightOffsetM":0.0}}]}
```

Pakai client yang sudah ada (`client/naraga-catalog.js`):

```js
import { createClient } from "/client/naraga-catalog.js";
const client = createClient("http://localhost:8000");
const { tileset } = await client.loadTileset(viewer, { dataset: "oikn", theme: "building", lod: 1 });
viewer.zoomTo(tileset);
client.enablePicking(viewer, (f) => console.log(f));   // klik gedung → atribut dari database
```

Tanpa client: `fetch("/api/v1/catalog?...")` → `items[0].tileset.url` → `Cesium.Cesium3DTileset.fromUrl(url)`; id gedung: `feature.getProperty("objectid")`.

Cek `isStale`: `true` berarti data di database berubah setelah tiles dibuat. Bangun tiles baru (bagian 4A).

## 4. Update, tambah, dan hapus data

Prinsip: **database dulu, lalu tiles, lalu daftarkan tileset baru.** Tiles selalu versi baru (`v2`, `v3`, …); versi lama otomatis `retired`, jadi bisa dilacak dan tidak ada downtime.

### A. Mengganti seluruh data (file LOD1 baru menggantikan yang lama)

```bash
# 1. hapus objek lama berdasarkan lineage
NET=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' $(docker compose ps -q db))
docker run --rm --platform linux/amd64 --network $NET 3dcitydb/citydb-tool:1.4.0 delete \
  -H db -d postgres -u postgres -p "$POSTGRES_PASSWORD" --delete-mode=delete -f "lineage = 'oikn.building.lod1'"

# 2. impor file baru (.city.json = CityJSON, .gml = CityGML 2.0); tercatat sebagai import job
scripts/import.sh USER=namaanda FILE=data/baru/KIPP_LOD1.city.json DATASET=oikn THEME=building LOD=1

# 3. bangun tiles dari OBJ yang cocok dengan file tadi → versi baru
python3 scripts/build_tiles.py data/baru/KIPP_LOD1.obj tiles/oikn/building/lod1/v2

# 4. daftarkan + aktifkan (v1 otomatis retired). DRY=1 untuk uji tanpa menyimpan
scripts/publish_tiles.sh USER=namaanda DATASET=oikn THEME=building LOD=1 VERSION=2
```

Syarat penting: nama objek di OBJ (`o KIPP_0000`) **harus sama** dengan `gml:id`/`objectid` di file CityJSON/GML. Kalau tidak, klik gedung tidak menemukan atributnya. Skrip membuat id tile langsung dari nama itu.

Layer yang sudah `published` tetap `published` selama re-impor, hanya berstatus *stale* sampai langkah 4 selesai.

### B. Menambah gedung baru

Impor file berisi gedung baru saja dengan `MODE=skip` (objek dengan id yang sudah ada dilewati):

```bash
scripts/import.sh USER=namaanda FILE=data/tambahan.city.json DATASET=oikn THEME=building LOD=1 MODE=skip
```

Lalu bangun ulang tiles dari OBJ **lengkap** (lama + baru) dan jalankan langkah 3–4 di atas. Satu tileset = satu paket utuh; tiles tidak ditambal sebagian.

### C. Menghapus gedung tertentu

```bash
docker run --rm --platform linux/amd64 --network $NET 3dcitydb/citydb-tool:1.4.0 delete \
  -H db -d postgres -u postgres -p "$POSTGRES_PASSWORD" --delete-mode=delete -f "objectid = 'KIPP_0287'"
```

Lalu bangun ulang tiles tanpa gedung itu (OBJ yang sudah dikurangi), langkah 3–4.

> Penghapusan per `objectid` dan `MODE=skip` belum saya uji di sini. Yang sudah diuji: hapus per `lineage` dan impor penuh. Coba dulu dengan data kecil dan cek `/datasets/oikn/stats`.

### D. Menghapus satu layer sepenuhnya

1. Admin (`/admin/`) → layer → ubah status ke `archived` (tileset otomatis `retired`, tidak lagi disajikan).
2. Kalau datanya juga harus hilang: hapus objek per lineage (langkah A.1). Folder `tiles/…/vN` boleh dihapus manual.

### E. Mengubah tinggi (tileset melayang/tenggelam di terrain)

Tidak perlu bangun ulang: daftarkan versi baru dengan offset, atau ubah `height_offset_m` di admin.
`scripts/publish_tiles.sh … HEIGHT_OFFSET=-25`. Nilai z di data KIPP (alas sekitar 29 m) belum diketahui datumnya (`vertical_datum = unknown`). Tentukan dulu: ellipsoid atau EGM2008 (geoid).

## 5. Pemeriksaan setelah perubahan

```bash
curl -s "localhost:8000/api/v1/datasets/oikn/stats"                      # jumlah objek
curl -s "localhost:8000/api/v1/catalog?dataset=oikn&lod=1" | python3 -m json.tool   # status, isStale, tileset aktif
make test                                                                 # invarian database + tes API (jangan di database produksi)
```

Lalu buka `/viewer/`, pilih dataset `oikn` → `building` → `1` → Load layer, dan klik satu gedung.

## 6. Hal yang belum selesai

- **Validasi geometri:** val3dity tidak bisa dipasang di mesin ini. Job impor ditandai lulus berdasarkan pengecekan sendiri (389 solid tertutup, tiap sisi dipakai tepat dua kali), bukan val3dity. Jalankan val3dity di mesin lain bila perlu resmi.
- **Datum vertikal** dataset `oikn` masih `unknown` dan atribusi kosong. Isi di admin.
- Satu tile besar (4 MB) cukup untuk 389 gedung. Kalau jumlah gedung naik ke puluhan ribu, tiles perlu dibagi hierarkis (3D Tiles tools).
