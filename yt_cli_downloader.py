#!/usr/bin/env python3
from __future__ import annotations
import importlib
import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parent / "bin"

def ensure_pip_package(pkg_name: str) -> None:
    try:
        importlib.import_module(pkg_name.replace("-", "_"))
    except ModuleNotFoundError:
        print(f"\n💡 Installing Python package '{pkg_name}' …")
        subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", pkg_name])
        print(f"✅ Installed {pkg_name}\n")

def ensure_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    system = platform.system()
    print("\n💡 ffmpeg not found – attempting automatic install …")
    try:
        if system == "Linux" and shutil.which("apt-get"):
            subprocess.check_call(["sudo", "apt-get", "update", "-y"])
            subprocess.check_call(["sudo", "apt-get", "install", "-y", "ffmpeg"])
            return shutil.which("ffmpeg") or "ffmpeg"
        if system == "Darwin" and shutil.which("brew"):
            subprocess.check_call(["brew", "install", "ffmpeg"])
            return shutil.which("ffmpeg") or "ffmpeg"
        if system == "Windows":
            BIN_DIR.mkdir(exist_ok=True)
            exe = BIN_DIR / "ffmpeg.exe"
            if not exe.exists():
                import urllib.request, zipfile, io, tempfile
                url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
                print("   → downloading static build …")
                with urllib.request.urlopen(url) as resp:
                    with zipfile.ZipFile(io.BytesIO(resp.read())) as zf:
                        member = [m for m in zf.namelist() if m.endswith("/bin/ffmpeg.exe")][0]
                        with tempfile.TemporaryDirectory() as td:
                            zf.extract(member, path=td)
                            shutil.copy(Path(td) / member, exe)
            os.environ["PATH"] += os.pathsep + str(BIN_DIR)
            return str(exe)
    except subprocess.CalledProcessError:
        pass
    sys.exit(textwrap.dedent("""❌ Automatic ffmpeg installation failed."""))

ensure_pip_package("yt-dlp")
ensure_pip_package("colorama")
auto_ffmpeg = ensure_ffmpeg()

import yt_dlp
from yt_dlp.utils import sanitize_filename
from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

def ask(prompt: str, choices: list[str]) -> str:
    while True:
        print(prompt)
        for idx, c in enumerate(choices, 1):
            print(f" {idx}. {c}")
        sel = input(f" Choose [1-{len(choices)}]: ").strip()
        if sel.isdigit() and 1 <= int(sel) <= len(choices):
            return choices[int(sel) - 1]
        print(Fore.YELLOW + "  ⚠️  Invalid choice.\n")

def unique_template(directory: Path, base: str, ext: str) -> str:
    n = 0
    suffix = ""
    while True:
        fname = directory / f"{base}{suffix}.{ext}"
        if not fname.exists():
            break
        n += 1
        suffix = f" ({n})"
    return str(directory / f"{base}{suffix}.%(ext)s")

def main() -> None:
    print(Fore.CYAN + "\n===   YouTube Downloader (yt-dlp)   ===\n")

    url = input("Paste YouTube URL: ").strip()
    if not url:
        sys.exit("❌ URL is required.")

    is_playlist = "list=" in url
    download_all = True
    selected_indices = []

    if is_playlist:
        with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True}) as ydl_info:
            playlist_info = ydl_info.extract_info(url, download=False)
        entries = playlist_info.get("entries", [])
        print(Fore.CYAN + f"\n📃 Detected playlist: {playlist_info.get('title', 'Untitled')} ({len(entries)} videos)\n")
        choice = ask("Download semua atau pilih sebagian?", ["Semua video", "Pilih sebagian"])
        if choice.startswith("Pilih"):
            download_all = False
            print("\nDaftar video dalam playlist:")
            for i, entry in enumerate(entries, 1):
                print(f" {i}. {entry.get('title', 'Unknown Title')}")
            raw = input("\nMasukkan nomor video yang ingin didownload (pisahkan dengan koma, contoh: 1,3,5): ")
            try:
                selected_indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip().isdigit()]
                selected_indices = [i for i in selected_indices if 0 <= i < len(entries)]
            except Exception:
                sys.exit("❌ Input tidak valid. Program dihentikan.")

    mode = ask("\nDownload what?", ["Video (mp4)", "Audio only"])
    is_audio = mode.startswith("Audio")

    if is_audio:
        audio_fmt = ask("\nChoose audio format:", ["mp3", "m4a", "opus"])
        quality_tag_base = audio_fmt
        start, end = None, None
        partial = ask("\nDownload full audio or just a section?", ["Full audio", "Specific time range"])
        if partial.startswith("Specific"):
            start = input("Enter start time (MM:SS or HH:MM:SS): ").strip()
            end = input("Enter end time (MM:SS or HH:MM:SS): ").strip()
            if len(start.split(":")) == 2:
                start = "00:" + start
            if len(end.split(":")) == 2:
                end = "00:" + end
            quality_tag_base += f"_{start.replace(':', '').zfill(6)}-{end.replace(':', '').zfill(6)}"
    else:
        res = ask("\nChoose video resolution:", ["1080p", "720p", "480p", "360p", "auto"])
        quality_tag_base = res
        start, end = None, None
        partial = ask("\nDownload full video or just a section?", ["Full video", "Specific time range"])
        if partial.startswith("Specific"):
            start = input("Enter start time (MM:SS or HH:MM:SS): ").strip()
            end = input("Enter end time (MM:SS or HH:MM:SS): ").strip()
            if len(start.split(":")) == 2:
                start = "00:" + start
            if len(end.split(":")) == 2:
                end = "00:" + end
            quality_tag_base += f"_{start.replace(':', '').zfill(6)}-{end.replace(':', '').zfill(6)}"

    # (⬇️⬇️⬇️ INI BAGIAN YANG DIMODIFIKASI UNTUK FOLDER KHUSUS PLAYLIST)
    base_output = Path.cwd() / ("audio" if is_audio else "videos")

    if is_playlist:
        playlist_title = sanitize_filename(playlist_info.get("title", "playlist"), restricted=True)
        out_dir = base_output / playlist_title
    else:
        out_dir = base_output

    out_dir.mkdir(parents=True, exist_ok=True)
    print(Fore.BLUE + f"\n📁 Output folder: {out_dir}\n")

    # (⬇️ Ambil metadata semua video)
    if not is_playlist:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl_info:
            info = ydl_info.extract_info(url, download=False)
        entries = [info]
    else:
        with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": False}) as ydl_info:
            full_info = ydl_info.extract_info(url, download=False)
        all_entries = full_info.get("entries", [])
        entries = [e for i, e in enumerate(all_entries) if download_all or i in selected_indices]

    try_fragment_cut = bool(start and end)
    default_ext = "mp4" if not is_audio else audio_fmt

    ydl_opts_base = {
        "ffmpeg_location": auto_ffmpeg,
        "quiet": True,
    }

    print("\n🔽️  Starting download …\n")

    for entry in entries:
        if not entry:
            continue
        video_url = entry.get("webpage_url")
        title = sanitize_filename(entry.get("title", "video"), restricted=True)
        base_name = f"{title}_{quality_tag_base}"
        outtmpl = str(out_dir / f"{base_name}.%(ext)s")

        ydl_opts = ydl_opts_base.copy()
        ydl_opts["outtmpl"] = outtmpl

        if is_audio:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": audio_fmt,
                        "preferredquality": "192",
                    }
                ]
            })
        else:
            qmap = {
                "1080p": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]",
                "720p": "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]",
                "480p": "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/best[ext=mp4][height<=480]",
                "360p": "bestvideo[ext=mp4][height<=360]+bestaudio[ext=m4a]/best[ext=mp4][height<=360]",
                "auto": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
            }
            ydl_opts.update({
                "format": qmap.get(res, "best"),
                "postprocessors": [
                    {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
                ]
            })

        if try_fragment_cut:
            ydl_opts["download_sections"] = [f"*{start}-{end}"]
            ydl_opts["force_keyframes_at_cuts"] = True

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl_each:
                ydl_each.download([video_url])
        except Exception:
            print(Fore.YELLOW + f"\n⚠️ Section download failed for: {title}. Retrying full with post-cut.")
            ydl_opts.pop("download_sections", None)
            ydl_opts.pop("force_keyframes_at_cuts", None)
            ydl_opts["postprocessor_args"] = ["-ss", start, "-to", end]
            with yt_dlp.YoutubeDL(ydl_opts) as ydl_each:
                ydl_each.download([video_url])

    print(Style.BRIGHT + f"\n✅ Finished! Files saved in: {out_dir}\n")

if __name__ == "__main__":
    main()
