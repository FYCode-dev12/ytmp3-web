#!/usr/bin/env python3
"""ytmp3-web: website lokal untuk mengunduh audio YouTube jadi MP3.

Hanya memakai pustaka standar Python. Unduhan dikerjakan yt-dlp satu per
satu oleh satu worker; halaman web cukup mengirim link dan membaca status.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
LINK_RE = re.compile(r"(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/[^\s\"'<>]+")
VIDEO_ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/)([\w-]{11})")
QUALITIES = {"0", "320K", "192K", "128K"}
ACTIVE = {"antre", "proses"}
IS_TERMUX = "com.termux" in os.environ.get("PREFIX", "")


def default_output() -> str:
    if os.environ.get("YTMP3_DIR"):
        return os.environ["YTMP3_DIR"]
    if IS_TERMUX:
        # Penyimpanan bersama HP, supaya terlihat oleh aplikasi musik.
        return "~/storage/shared/Music/YouTube"
    return "~/Music/YouTube"


def open_browser(url: str) -> None:
    if shutil.which("termux-open-url"):
        subprocess.run(["termux-open-url", url], check=False)
    else:
        webbrowser.open(url)


@dataclass
class Job:
    url: str
    quality: str = "0"
    playlist: bool = False
    force: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "antre"  # antre, proses, selesai, ada, gagal, batal
    phase: str = ""        # unduh, konversi
    progress: float = 0.0
    title: str = ""
    video_id: str = ""
    files: list[str] = field(default_factory=list)
    error: str = ""
    created: float = field(default_factory=time.time)
    proc: subprocess.Popen | None = field(default=None, repr=False)

    def public(self) -> dict:
        return {
            "id": self.id, "url": self.url, "status": self.status,
            "phase": self.phase, "progress": round(self.progress, 1),
            "title": self.title, "video_id": self.video_id,
            "playlist": self.playlist, "error": self.error,
            "files": [Path(f).name for f in self.files],
        }


class Downloader:
    def __init__(self, out_dir: Path, ytdlp: str):
        self.out_dir = out_dir
        self.ytdlp = ytdlp
        self.archive = out_dir / ".ytmp3-arsip.txt"
        self.jobs: dict[str, Job] = {}
        self.cond = threading.Condition()
        threading.Thread(target=self._worker, daemon=True).start()

    # --- dipanggil dari HTTP handler ---------------------------------------
    def add(self, text: str, quality: str, playlist: bool) -> tuple[int, int]:
        links = list(dict.fromkeys(LINK_RE.findall(text)))
        added = dupes = 0
        with self.cond:
            busy = {j.url for j in self.jobs.values() if j.status in ACTIVE}
            for url in links:
                if url in busy:
                    dupes += 1
                    continue
                m = VIDEO_ID_RE.search(url)
                job = Job(url=url, quality=quality, playlist=playlist,
                          video_id=m.group(1) if m else "")
                self.jobs[job.id] = job
                added += 1
            self.cond.notify_all()
        return added, dupes

    def snapshot(self) -> list[dict]:
        with self.cond:
            return [j.public() for j in self.jobs.values()]

    def retry(self, job_id: str, force: bool) -> bool:
        with self.cond:
            job = self.jobs.get(job_id)
            if not job or job.status in ACTIVE:
                return False
            job.status, job.phase, job.progress, job.error = "antre", "", 0.0, ""
            job.files, job.force = [], force
            # pindahkan ke akhir antrean
            self.jobs[job_id] = self.jobs.pop(job_id)
            self.cond.notify_all()
            return True

    def cancel(self, job_id: str) -> bool:
        with self.cond:
            job = self.jobs.get(job_id)
            if not job or job.status not in ACTIVE:
                return False
            job.status = "batal"
            if job.proc and job.proc.poll() is None:
                job.proc.terminate()
            return True

    def clear_finished(self) -> None:
        with self.cond:
            self.jobs = {k: j for k, j in self.jobs.items() if j.status in ACTIVE}

    def file_path(self, job_id: str, idx: int) -> Path | None:
        with self.cond:
            job = self.jobs.get(job_id)
            if not job or not 0 <= idx < len(job.files):
                return None
            path = Path(job.files[idx]).resolve()
        if path.is_file() and self.out_dir.resolve() in path.parents:
            return path
        return None

    # --- worker: satu per satu ----------------------------------------------
    def _next_job(self) -> Job:
        with self.cond:
            while True:
                for job in self.jobs.values():
                    if job.status == "antre":
                        job.status, job.phase = "proses", "unduh"
                        return job
                self.cond.wait()

    def _worker(self) -> None:
        while True:
            job = self._next_job()
            try:
                self._run(job)
            except Exception as exc:  # jangan sampai worker mati
                with self.cond:
                    job.status, job.error = "gagal", f"Kesalahan internal: {exc}"

    def _command(self, job: Job) -> list[str]:
        cmd = [
            self.ytdlp, "-x", "--audio-format", "mp3", "--audio-quality", job.quality,
            "--embed-thumbnail", "--convert-thumbnails", "jpg", "--embed-metadata",
            "-P", str(self.out_dir),
            "--no-simulate", "--progress", "--newline", "--color", "never",
            "--progress-template", "download:P %(progress._percent_str)s",
            "--print", "before_dl:T %(id)s %(title)s",
            "--print", "after_move:F %(filepath)s",
        ]
        if not job.force:
            cmd += ["--download-archive", str(self.archive)]
        if IS_TERMUX:
            # Penyimpanan bersama Android menolak karakter seperti ? : " | *
            cmd += ["--windows-filenames"]
        if job.playlist:
            cmd += ["--yes-playlist", "-o",
                    "%(playlist_title)s/%(playlist_index)02d - %(title)s.%(ext)s"]
        else:
            cmd += ["--no-playlist", "-o", "%(title)s.%(ext)s"]
        return cmd + ["--", job.url]

    def _run(self, job: Job) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            self._command(job), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, bufsize=1, errors="replace",
        )
        with self.cond:
            job.proc = proc
            if job.status == "batal":  # dibatalkan tepat sebelum mulai
                proc.terminate()
        last_error = ""
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            with self.cond:
                if line.startswith("P "):
                    m = re.search(r"([\d.]+)%", line)
                    if m:
                        job.progress = float(m.group(1))
                        job.phase = "konversi" if job.progress >= 100 else "unduh"
                elif line.startswith("T "):
                    _, vid, *title = line.split(" ", 2)
                    job.video_id = vid or job.video_id
                    job.title = title[0] if title else job.title
                    job.progress, job.phase = 0.0, "unduh"
                elif line.startswith("F "):
                    job.files.append(line[2:])
                elif line.startswith("ERROR:"):
                    last_error = line.removeprefix("ERROR:").strip()
        rc = proc.wait()
        with self.cond:
            job.proc = None
            if job.status == "batal":
                return
            if rc == 0:
                job.status = "selesai" if job.files else "ada"
                job.progress = 100.0
                if job.files and shutil.which("termux-media-scan"):
                    # Supaya lagu baru langsung muncul di aplikasi musik HP.
                    subprocess.Popen(["termux-media-scan", *job.files],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                job.status = "gagal"
                job.error = last_error or f"yt-dlp berhenti dengan kode {rc}"


def make_handler(dl: Downloader, allow_any_host: bool, port: int):
    index_html = (HERE / "index.html").read_bytes()
    local_hosts = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ytmp3-web"

        def log_message(self, fmt, *args):  # cukup tampilkan error
            pass

        # Tolak permintaan dari website lain (CSRF / DNS rebinding).
        def _trusted(self) -> bool:
            host = self.headers.get("Host", "")
            if not allow_any_host and host not in local_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin.split("://", 1)[-1] == host

        def _json(self, data, status=HTTPStatus.OK):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("harus JSON")
            size = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            return json.loads(self.rfile.read(size) or b"{}")

        def do_GET(self):
            if not self._trusted():
                return self._json({"error": "ditolak"}, HTTPStatus.FORBIDDEN)
            parts = self.path.split("?", 1)[0].strip("/").split("/")
            if parts == [""]:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(index_html)))
                self.end_headers()
                self.wfile.write(index_html)
            elif parts == ["api", "jobs"]:
                self._json({"jobs": dl.snapshot(), "out_dir": str(dl.out_dir).replace(str(Path.home()), "~", 1),
                            "local": self.client_address[0] in ("127.0.0.1", "::1")
                                     and bool(shutil.which("xdg-open"))})
            elif len(parts) == 4 and parts[:2] == ["api", "file"] and parts[3].isdigit():
                path = dl.file_path(parts[2], int(parts[3]))
                if not path:
                    return self._json({"error": "file tidak ada"}, HTTPStatus.NOT_FOUND)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Content-Disposition",
                                 f"attachment; filename*=UTF-8''{quote(path.name)}")
                self.end_headers()
                with path.open("rb") as fh:
                    shutil.copyfileobj(fh, self.wfile)
            else:
                self._json({"error": "tidak ditemukan"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            if not self._trusted():
                return self._json({"error": "ditolak"}, HTTPStatus.FORBIDDEN)
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError):
                return self._json({"error": "permintaan tidak valid"}, HTTPStatus.BAD_REQUEST)
            parts = self.path.strip("/").split("/")
            if parts == ["api", "jobs"]:
                quality = str(body.get("quality", "0"))
                if quality not in QUALITIES:
                    quality = "0"
                added, dupes = dl.add(str(body.get("text", "")), quality,
                                      bool(body.get("playlist")))
                self._json({"added": added, "dupes": dupes})
            elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "retry":
                self._json({"ok": dl.retry(parts[2], bool(body.get("force")))})
            elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
                self._json({"ok": dl.cancel(parts[2])})
            elif parts == ["api", "clear"]:
                dl.clear_finished()
                self._json({"ok": True})
            elif parts == ["api", "open-folder"]:
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self._json({"error": "hanya dari laptop ini"}, HTTPStatus.FORBIDDEN)
                if not shutil.which("xdg-open"):
                    return self._json({"error": "tidak didukung di perangkat ini"},
                                      HTTPStatus.NOT_IMPLEMENTED)
                dl.out_dir.mkdir(parents=True, exist_ok=True)
                subprocess.Popen(["xdg-open", str(dl.out_dir)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._json({"ok": True})
            else:
                self._json({"error": "tidak ditemukan"}, HTTPStatus.NOT_FOUND)

    return Handler


def lan_ip() -> str:
    # Tidak mengirim paket; hanya menanyakan ke kernel antarmuka mana yang dipakai.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> None:
    p = argparse.ArgumentParser(description="Website lokal YouTube ke MP3")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--output", default=default_output())
    p.add_argument("--lan", action="store_true",
                   help="bisa dibuka dari HP/laptop lain di Wi-Fi yang sama")
    p.add_argument("--no-browser", action="store_true", help="jangan buka browser otomatis")
    a = p.parse_args()

    url = f"http://localhost:{a.port}"
    os.environ["PATH"] = f"{Path.home() / '.local/bin'}{os.pathsep}{os.environ.get('PATH', '')}"
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp or not shutil.which("ffmpeg"):
        if IS_TERMUX:
            sys.exit("yt-dlp dan ffmpeg harus terpasang dulu:\n"
                     "  pkg install ffmpeg nodejs && pip install -U 'yt-dlp[default]'")
        sys.exit("yt-dlp dan ffmpeg harus terpasang dulu.")
    if IS_TERMUX and not (Path.home() / "storage" / "shared").exists():
        sys.exit("Izin penyimpanan belum diberikan. Jalankan: termux-setup-storage")

    try:
        server = ThreadingHTTPServer(("0.0.0.0" if a.lan else "127.0.0.1", a.port),
                                     make_handler(Downloader(Path(a.output).expanduser(), ytdlp),
                                                  a.lan, a.port))
    except OSError:
        if already_running(a.port):
            print(f"ytmp3-web sudah berjalan di {url}", flush=True)
            if not a.no_browser:
                open_browser(url)
            return
        sys.exit(f"Port {a.port} sedang dipakai program lain. Coba --port 8766")

    print(f"ytmp3-web berjalan di {url}", flush=True)
    if a.lan:
        print(f"Dari HP (Wi-Fi yang sama): http://{lan_ip()}:{a.port}", flush=True)
    print("Tekan Ctrl+C untuk berhenti.", flush=True)
    if not a.no_browser:
        open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBerhenti.", flush=True)


if __name__ == "__main__":
    main()
