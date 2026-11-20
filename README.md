# ytmp3-web

Website lokal untuk mengunduh audio YouTube jadi MP3, tanpa iklan. Link
diunduh satu per satu oleh [yt-dlp](https://github.com/yt-dlp/yt-dlp) dan
diubah ke MP3 oleh ffmpeg, lengkap dengan cover dan judul lagu.

Hanya butuh Python 3.10+ (tanpa paket tambahan), yt-dlp, dan ffmpeg.

## Linux

```sh
git clone https://github.com/FYCode-dev12/ytmp3-web.git
cd ytmp3-web
python3 server.py            # buka http://localhost:8765
python3 server.py --lan      # bisa juga dibuka dari HP di Wi-Fi yang sama
```

Hasil masuk ke `~/Music/YouTube`.

## Android (Termux)

Pasang Termux dari [F-Droid](https://f-droid.org/packages/com.termux/) atau
[GitHub](https://github.com/termux/termux-app/releases). Versi di Play Store
sudah lama dan tidak diperbarui.

Sekali saja:

```sh
pkg update && pkg upgrade
pkg install python ffmpeg nodejs git
pip install -U "yt-dlp[default]"
git clone https://github.com/FYCode-dev12/ytmp3-web.git ~/ytmp3-web
termux-setup-storage          # izinkan akses penyimpanan saat ditanya

# yt-dlp butuh JavaScript runtime untuk YouTube
mkdir -p ~/.config/yt-dlp && echo "--js-runtimes node" > ~/.config/yt-dlp/config
```

Kalau `pip install` gagal saat mengompilasi, jalankan `pkg install clang`
lalu ulangi.

Opsional, supaya lagu baru langsung muncul di aplikasi musik: pasang aplikasi
**Termux:API** (dari sumber yang sama dengan Termux), lalu `pkg install termux-api`.

Menjalankan:

```sh
termux-wake-lock              # supaya tidak dihentikan Android saat layar mati
python ~/ytmp3-web/server.py
```

Browser terbuka ke `http://localhost:8765`. Hasil masuk ke folder
**Music/YouTube** di penyimpanan HP.

## Opsi

| Opsi | Arti |
|---|---|
| `--port 8766` | pakai port lain |
| `--output DIR` | folder tujuan (atau variabel `YTMP3_DIR`) |
| `--lan` | bisa dibuka dari perangkat lain di jaringan yang sama |
| `--no-browser` | jangan buka browser otomatis |

Link yang sudah berhasil dicatat di `.ytmp3-arsip.txt` di folder tujuan dan
dilewati kalau dikirim lagi. Gunakan tombol **Unduh ulang** untuk memaksa.

## Memperbarui

```sh
cd ~/ytmp3-web && git pull                # versi terbaru ytmp3-web
yt-dlp -U                                 # Linux (binary resmi)
pip install -U "yt-dlp[default]"          # Termux
```

Kalau unduhan tiba-tiba gagal semua, biasanya YouTube berubah dan yt-dlp
perlu diperbarui.

## Catatan

Gunakan untuk konten yang boleh kamu simpan, misalnya untuk didengarkan
sendiri secara offline atau konten berlisensi bebas. Server ini dibuat untuk
dipakai sendiri di perangkatmu, bukan sebagai layanan publik.

## Lisensi

[MIT](LICENSE)
