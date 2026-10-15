# Panduan input dan cek dataset OIKN KIPP (landmark)

Dataset `oikn_kipp` (layer `building` LOD1, lineage `oikn_kipp.building.lod1`) dibuat oleh seed
`db/seeds/oikn/002_oikn_kipp.sql`. Layernya mulai **kosong** dengan status **draft**, sehingga frontend belum melihat apa pun.
Isinya diunggah lewat `POST /imports`.

Langkahnya sudah diuji di stack lokal pada 15 Okt 2026:

| Tahap | Hasil |
|---|---|
| Masuk ke database | 2.373 gedung, sekitar 18 detik, tileset v1 langsung terbentuk |
| Dilewati otomatis oleh API | 18 fitur: poligon self-intersect atau luas < 1 m² |
| Dilewati oleh skrip persiapan | 4 fitur RS KEMENKES tanpa `BHM` |
| Tidak ada GeoJSON | folder `ISTANA WAPRES` (hanya berisi gpkg) |

Variabel yang dipakai di bawah:

```bash
BASE=https://twindb.178-105-182-162.sslip.io
TOKEN=$(ssh twin-vm "grep ^WRITE_API_TOKEN= /opt/twindb/.env | cut -d= -f2")
```

## 0. Pastikan dataset sudah ada di server

Seed ikut ter-deploy setelah commit ini di-push ke `main`: CI hijau, lalu Deploy berjalan.

```bash
curl -s "$BASE/api/v1/catalog?dataset=oikn_kipp&status=all"
```

Hasil yang diharapkan: satu item dengan `"status": "draft"`, `"featureCount": 0` dan `"tileset": null`.

## 1. Siapkan file

**Jalan pintas:** `data/LOD1_landmark/OIKN_KIPP_LOD1.city.json` sudah siap di-upload (tidak di-commit, karena `data/` di-ignore).

| | |
|---|---|
| Format | CityJSON 2.0, EPSG:32750 |
| Isi | 2.373 gedung LOD1 (solid), hasil langkah di bawah |
| Atribut | `nama`, `cat`, `area`, `measuredHeight` |
| Ukuran | 14,6 MB |

File ini sudah diuji: di-import ulang lewat `POST /imports` ke layer kosong, hasilnya 2.373 gedung, extent sama, dan tiles terbentuk.
Kalau memakai file ini, lewati langkah di bawah dan di langkah 3 ganti `file=@...` dengan file ini.

GeoJSON hasil `generator-lod1` belum sesuai format `POST /imports`. Tinggi gedung ada di `BHM` (API butuh `height_m`), tidak ada
`objectid`, dan tidak ada ketinggian tanah. Skrip `scripts/prep_landmark.py` menggabungkan semua folder menjadi satu file:

- `objectid` = `LMK_<NAMA_FOLDER>_<nomor>`, misalnya `LMK_GEDUNG_BI_0001`.
- `nama` = nama folder, `height_m` = `BHM`.
- `base_z` = ketinggian dasar gedung KIPP terdekat, diambil dari export dataset `oikn`.

```bash
curl -fsS "$BASE/api/v1/datasets/oikn/export?format=geojson" -o /tmp/oikn.geojson
python3 scripts/prep_landmark.py data/LOD1_landmark /tmp/oikn.geojson /tmp/oikn_kipp.geojson
```

Mengambil `base_z` dari KIPP terdekat adalah pendekatan kasar. Kalau ada DTM, lebih baik `base_z` diisi dari DTM.

## 2. Backup dulu

```bash
ssh twin-vm 'cd /opt/twindb && docker compose exec -T db pg_dump -U postgres -Fc postgres > /opt/backups/twindb-before-oikn-kipp-$(date +%F).dump'
```

## 3. Upload

```bash
curl -s -X POST "$BASE/api/v1/imports" -H "Authorization: Bearer $TOKEN" -H "X-User: fairuz" \
  -F file=@/tmp/oikn_kipp.geojson -F dataset=oikn_kipp -F theme=building -F lod=1
```

Hasilnya `{"jobId": "...", "status": "running", ...}`. Pantau job-nya sampai `succeeded`, biasanya kurang dari 1 menit:

```bash
curl -s "$BASE/api/v1/imports/<jobId>" | python3 -m json.tool
```

Field yang perlu dibaca:

- `counts.Building`: jumlah gedung yang masuk.
- `report.skipped`: fitur yang ditolak beserta alasannya.
- `report.tileset`: tileset yang baru terbentuk.

## 4. Cek sendiri, saat layer masih draft

**a. Lewat API**

```bash
curl -s "$BASE/api/v1/catalog?dataset=oikn_kipp&status=all"   # featureCount, bbox, tileset.url
curl -s "$BASE/api/v1/features/LMK_GEDUNG_BI_0001"           # atribut + footprint lon/lat
curl -s "$BASE/api/v1/features?dataset=oikn_kipp&q=MASJID&limit=5"
```

Bandingkan koordinat footprint di sini dengan file aslinya. Untuk GEDUNG BI, `GEDUNG BI_koordinat.txt` berisi UTM 50S
466947.7, 9893596.7, kira-kira 116.7044, -0.9633.

**b. Visual (yang terpenting untuk CRS)**

Buka `$BASE/viewer/`, pilih dataset `oikn_kipp`, lalu klik *Load layer*. Viewer juga bisa memuat layer yang belum published. Muat juga
`oikn` untuk pembanding, lalu cek:

- Gedung tidak bergeser dari citra.
- Gedung tidak melayang atau tenggelam.
- Tingginya masuk akal.

Klik satu gedung untuk melihat atributnya.

**c. Lewat DBeaver** (koneksi seperti biasa, user `postgres` atau `twin_api`)

```sql
SELECT p.val_string AS nama, count(*) AS gedung
FROM citydb.feature f
JOIN citydb.property p ON p.feature_id = f.id AND p.name = 'nama' AND p.parent_id IS NULL
WHERE f.lineage = 'oikn_kipp.building.lod1' AND f.termination_date IS NULL
GROUP BY 1 ORDER BY 1;                       -- 22 nama, total 2373 kalau input sama dengan uji lokal

SELECT ST_Extent(ST_Transform(envelope, 4326)) FROM citydb.feature
WHERE lineage = 'oikn_kipp.building.lod1';    -- harus di sekitar lon 116.69-116.73, lat -0.99 sampai -0.95
```

## 5. Publish (agar frontend melihatnya)

Lewat API, cukup satu request. Ambil `layerId` dari langkah 4a, yaitu `GET /catalog?dataset=oikn_kipp&status=all`.

```bash
curl -s -X PUT "$BASE/api/v1/layers/<layerId>/status" -H "Authorization: Bearer $TOKEN" -H "X-User: fairuz" \
  -H "Content-Type: application/json" -d '{"status": "published"}'
# {"layerId": "...", "previousStatus": "draft", "status": "published"}
```

Di Swagger, pakai `PUT /api/v1/layers/{layer_id}/status` dengan body `{"status": "published"}`. Isi juga token di Authorize
dan kolom `X-User`.

Apa yang terjadi di balik request ini:

- Dari `draft`, import terakhir yang sukses otomatis dicatat sebagai validasi (`draft → validated → published`).
- Publish butuh tileset aktif. Tileset ini sudah dibuat oleh upload.
- Setelah publish, `GET /api/v1/catalog` tanpa `status=all` ikut menampilkan layer ini.

Kalau dari `draft` belum ada import yang sukses, atau perpindahan status tidak diizinkan (misalnya `published → draft`), API
menjawab 409 dengan alasannya.

## 6. Kalau hasilnya salah (misalnya CRS masih bergeser)

**Hapus semua gedung landmark, perbaiki file, lalu upload ulang.** Jalankan sebagai `postgres`:

```sql
BEGIN;
SELECT count(*) FROM citydb_pkg.delete_feature(ARRAY(
  SELECT id FROM citydb.feature
  WHERE lineage = 'oikn_kipp.building.lod1'
    AND objectclass_id = (SELECT id FROM citydb.objectclass WHERE classname = 'Building')));   -- 2373
COMMIT;   -- uji dulu dengan ROLLBACK
```

Setelah itu ulangi langkah 3. Import baru membangun ulang tiles, sehingga tiles lama ikut tergantikan. Selama belum di-upload
ulang, tiles lama masih tampil.

**Atau sembunyikan saja dari frontend tanpa menghapus data:** pakai endpoint yang sama dengan body `{"status": "validated"}`.
Layer hilang dari `/catalog`, tapi masih bisa dicek di viewer, dan nanti cukup di-publish lagi. Status `"archived"` juga
memensiunkan tileset-nya. Untuk mengisi layer lagi setelah archived, set dulu ke `"draft"`.

**Atau restore backup** dari langkah 2 (lihat `docs/PANDUAN_HETZNER.md`).

## Catatan

- Folder `KEMENKO` juga ada di KIPP (`KEMENKO_LOD1`, 30 gedung), jadi di peta kedua dataset akan menumpuk di lokasi yang sama.
- `id` dan `cat` dari file asli ikut tersimpan sebagai atribut. `id` di file asli kosong (`null`), jadi tidak ikut tersimpan.
- `ISTANA WAPRES` belum masuk karena hanya ada gpkg. Ekspor dulu menjadi `LAS_lod1.geojson` (CRS84, kolom `BHM`) di foldernya,
  lalu jalankan ulang langkah 1. Gedung yang sudah ada akan ditolak 409, yang baru akan masuk.
