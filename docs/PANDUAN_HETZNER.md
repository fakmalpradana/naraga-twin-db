# Panduan: deploy twindb (+ clydetwin) di satu VM Hetzner

Tujuan: test live backend + DB dengan biaya paling murah. Satu VM dipakai bersama oleh twindb dan clydetwin, dengan satu **Caddy di host** (bukan container) sebagai pintu HTTPS untuk keduanya.

```
Internet ──443──► Caddy (host, TLS otomatis)
                   ├─ twindb.<ip>.sslip.io ──► 127.0.0.1:8001 ─► twindb api ─► twindb db (3DCityDB)
                   └─ clyde.<ip>.sslip.io  ──► 127.0.0.1:8000 ─► clydetwin api ─► TimescaleDB
```

Kenapa Caddy di host: kedua project punya service bernama `api`. Kalau digabung lewat satu network Docker, nama `api` bisa ambigu. Kalau Caddy di host dan semua API hanya bind ke `127.0.0.1`, tidak perlu trik network apa pun.

## 1. Buat VM

1. Buka console.hetzner.cloud, buat project, lalu **Add Server**.
2. **Type: x86 (Intel/AMD), shared vCPU, 2 vCPU / 4 GB (CX22/CX23).** Jangan pilih **CAX (ARM)**: image `3dcitydb/3dcitydb-pg` hanya ada untuk amd64.
3. Location: Nuremberg, Falkenstein, atau Helsinki (paling murah). Singapore lebih dekat ke Indonesia, tapi lebih mahal.
4. Image: **Ubuntu 24.04**. Tambahkan **SSH key** dari Mac dengan `cat ~/.ssh/id_ed25519.pub`. Jangan pakai login password.
5. **Firewall** (Hetzner Cloud Firewall): inbound hanya TCP 22, 80, 443. Firewall ini berada di luar VM, jadi tidak bisa ditembus Docker. Berbeda dengan `ufw`, yang aturannya dilewati oleh port Docker.
6. Catat IP publiknya. Di bawah ditulis `203.0.113.7`, dan bentuk sslip.io-nya `203-0-113-7.sslip.io`.

## 2. Siapkan VM (sekali saja)

```bash
ssh root@203.0.113.7
```

Di VM:

```bash
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl git
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
```

Swap 2 GB berguna sebagai cadangan, karena di 4 GB RAM berjalan dua Postgres sekaligus.

## 3. Deploy twindb

### 3.1 Ambil kode

Repo-nya private, jadi buat **deploy key** (read-only) khusus untuk VM:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/twindb -N "" && cat ~/.ssh/twindb.pub
```

Tempel isi key itu di GitHub, di `naraga-twin-db` → Settings → Deploy keys. Lalu:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-twindb
  HostName github.com
  IdentityFile ~/.ssh/twindb
EOF
git clone -b feat/kipp-lod1-tiles git@github-twindb:fakmalpradana/naraga-twin-db.git /opt/twindb
```

Ganti `-b feat/kipp-lod1-tiles` dengan `main` setelah branch-nya di-merge.

### 3.2 `.env`

```bash
cd /opt/twindb && cp .env.example .env && nano .env
```

Ubah nilai-nilai berikut. Semua password harus acak; buat dengan `openssl rand -hex 24`.

```
POSTGRES_PASSWORD=<acak>
DATABASE_URL=postgres://postgres:<sama dengan POSTGRES_PASSWORD>@db:5432/postgres?sslmode=disable
APP_DB_PASSWORD=<acak>
API_DB_PASSWORD=<acak>
SECRET_KEY=<acak, panjang>
ADMIN_PASSWORD=<acak>
WRITE_API_TOKEN=<acak, atau kosong untuk menonaktifkan write API>
DEBUG=0
ALLOWED_HOSTS=twindb.203-0-113-7.sslip.io
CSRF_TRUSTED_ORIGINS=https://twindb.203-0-113-7.sslip.io
ALLOWED_ORIGINS=https://twindb.203-0-113-7.sslip.io,http://localhost:3000
DB_PORT=127.0.0.1:55432
API_PORT=127.0.0.1:8001
```

Dua baris terakhir **wajib**. Tanpa keduanya, Docker membuka Postgres dan API ke internet. Port 8001 dipilih karena 8000 sudah dipakai clydetwin.

### 3.3 Salin tiles dan jalankan

`tiles/` tidak masuk git, jadi salin dari Mac (jalankan di Mac):

```bash
rsync -az --progress ~/Desktop/1_work/twindb/tiles/ root@203.0.113.7:/opt/twindb/tiles/
```

Di VM:

```bash
cd /opt/twindb && docker compose up -d --build
docker compose logs -f api
```

Tunggu sampai log menampilkan `Listening at: http://0.0.0.0:8000`. Pada start pertama, DB perlu sekitar 1 menit untuk membuat skema 3DCityDB.

### 3.4 Pindahkan data dari lokal

Cara paling setia adalah dump DB lokal lalu restore di VM. Dengan cara ini layer, tileset, dan admin ikut pindah.

Di Mac (Docker lokal harus jalan):

```bash
cd ~/Desktop/1_work/twindb && docker compose exec -T db pg_dump -U postgres -Fc postgres > twindb.dump
scp twindb.dump root@203.0.113.7:/opt/twindb/
```

Di VM:

```bash
cd /opt/twindb
docker compose stop api
docker compose exec -T db pg_restore -U postgres --clean --if-exists -d postgres < twindb.dump
docker compose start api
```

Restore dilakukan **setelah** api pernah start sekali, karena entrypoint api yang membuat role `twin_app`/`twin_api`. **Jangan pakai `--no-owner`.** Tabel Django dimiliki `twin_app`; kalau owner hilang, api crash dengan `permission denied for table django_migrations`. `CITYDB_SRID` di `.env` VM harus sama dengan lokal (32750). User admin ikut terbawa dari lokal, jadi login `/admin` memakai password lokal; `ADMIN_PASSWORD` di `.env` VM hanya dipakai kalau user admin belum ada. Jangan jalankan `db/tests/invariants.sql` di DB yang sudah berisi data, karena tes itu hanya untuk database kosong.

Cara alternatif tanpa dump: import ulang dari sumber dengan `scripts/import.sh` → validasi → `scripts/publish_tiles.sh`, seperti di README §6.1. Ini bisa jalan di VM karena VM-nya amd64.

## 4. Caddy (untuk kedua project)

```bash
cat > /etc/caddy/Caddyfile <<'EOF'
twindb.203-0-113-7.sslip.io {
	encode {
		zstd
		gzip
		match {
			header Content-Type application/octet-stream*
			header Content-Type application/json*
			header Content-Type text/*
			header Content-Type application/javascript*
		}
	}
	reverse_proxy 127.0.0.1:8001
}

clyde.203-0-113-7.sslip.io {
	reverse_proxy 127.0.0.1:8000
}
EOF
systemctl reload caddy
```

TLS Let's Encrypt otomatis keluar dalam beberapa detik. Blok `match` diperlukan karena `.glb` dikirim sebagai `application/octet-stream`, yang tidak dikompres oleh `encode` bawaan. Dengan blok ini, ukuran tile turun sekitar 5×.

## 5. clydetwin di VM yang sama

Ikuti `clydetwin/deploy/README.md` dengan dua perbedaan:

- Clone ke `/opt/clydetwin`.
- Di `.env` clydetwin, biarkan **`DOMAIN=localhost`** (atau hapus). Dengan begitu `make live-up` tidak menyalakan Caddy milik clydetwin (profile `edge`), yang akan berebut port 80/443 dengan Caddy host. API clydetwin tetap bind ke `127.0.0.1:8000` dan diakses lewat Caddy host di §4.

## 6. Cek

```bash
curl -s https://twindb.203-0-113-7.sslip.io/api/v1/health
curl -s https://twindb.203-0-113-7.sslip.io/api/v1/catalog | head -c 400
docker stats --no-stream   # RAM total kedua stack
```

Buka juga `https://twindb.203-0-113-7.sslip.io/viewer/` dan `/admin`. Smoke test dari Mac:

```bash
make deploy-check URL=https://twindb.203-0-113-7.sslip.io
```

## 7. Update

| Yang berubah | Perintah di VM (`cd /opt/twindb`) |
|---|---|
| Kode | `git pull && docker compose up -d --build` (migrasi jalan otomatis saat api start) |
| Tiles versi baru | Dari Mac: `rsync -az tiles/ root@IP:/opt/twindb/tiles/`. Lalu di VM: `scripts/publish_tiles.sh USER=fairuz DATASET=oikn THEME=building LOD=1 VERSION=N` |
| Data dari file sumber | Salin file ke VM, lalu `scripts/import.sh ...` (README §6) |

Write API (create/update/delete gedung) menulis langsung ke `/opt/twindb/tiles` di VM. Jadi setelah test, **VM adalah sumber kebenaran**. Jangan menimpanya dengan `rsync --delete` dari Mac.

## 8. Backup minimal

Dump DB dan arsip tiles setiap malam, disimpan 7 hari di VM:

```bash
mkdir -p /opt/backups && crontab -l 2>/dev/null | { cat; echo '0 18 * * * cd /opt/twindb && docker compose exec -T db pg_dump -U postgres -Fc postgres > /opt/backups/twindb-$(date +\%F).dump && tar czf /opt/backups/tiles-$(date +\%F).tgz tiles && find /opt/backups \( -name "twindb-*" -o -name "tiles-*" \) -mtime +7 -delete'; } | crontab -
```

Backup ini masih ikut hilang kalau VM hilang. Untuk data penting, jalankan `deploy/backup/` ke bucket S3/R2 (bucket yang sama dengan clydetwin juga bisa), atau aktifkan Hetzner Backups (+20% harga VM).

## Troubleshooting

| Gejala | Penyebab |
|---|---|
| `/admin` login → error CSRF | `CSRF_TRUSTED_ORIGINS` tidak berisi `https://<domain>` |
| `400 Bad Request` | Domain belum ada di `ALLOWED_HOSTS` |
| Viewer/FE gagal memuat tiles (CORS) | Origin FE belum ada di `ALLOWED_ORIGINS` |
| Caddy gagal mendapat sertifikat | Port 80/443 belum dibuka di Hetzner Firewall, atau Caddy clydetwin ikut jalan (`docker ps \| grep caddy`) |
| `exec format error` di db | VM yang dipakai ARM (CAX). Buat ulang sebagai x86 |
| OOM / container mati | `docker stats`. Cek swap aktif (`free -h`), atau naik ke 8 GB |
