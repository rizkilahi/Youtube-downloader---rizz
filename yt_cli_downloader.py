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

def update_yt_dlp() -> None:
    """Check and update yt-dlp to latest version to avoid format issues"""
    try:
        print("\n💡 Checking for yt-dlp updates...")
        # Use DEVNULL to suppress output but still allow the update to proceed
        with open(os.devnull, 'w') as devnull:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"], 
                                stdout=devnull, stderr=devnull)
        print("✅ yt-dlp updated to latest version\n")
    except subprocess.CalledProcessError:
        print("⚠️ Could not update yt-dlp, continuing with current version\n")

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
update_yt_dlp()  # Update yt-dlp to handle YouTube changes
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
        # Anti-detection measures
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "extractor_retries": 3,
        "fragment_retries": 3,
        "retry_sleep_functions": {"http": lambda n: 2 ** n},
        "sleep_interval_requests": 1,
        "sleep_interval_subtitles": 1,
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
                ],
            })
            if try_fragment_cut:
                # Audio trimming can be done directly as postprocessor args
                ydl_opts["postprocessor_args"] = ["-ss", start, "-to", end]
        else:
            # Precise format selection for requested resolution
            res_to_height = {"1080p": 1080, "720p": 720, "480p": 480, "360p": 360}
            if res != "auto":
                h = res_to_height.get(res, 1080)
                fmt = (
                    f"bv*[height={h}][vcodec~='(avc1|h264)']+ba[ext=m4a]/"  # exact height, common codec
                    f"bv*[height={h}]+ba/"  # exact height any codec
                    f"bv*[height<={h}][vcodec~='(avc1|h264)']+ba/"  # fallback <= height avc1
                    f"bv*[height<={h}]+ba/"  # fallback <= height any
                    f"b[height<={h}]"  # merged progressive
                )
            else:
                fmt = "bv*+ba/b"  # best available
            ydl_opts.update({
                "format": fmt,
                "merge_output_format": "mp4",
                "postprocessors": [
                    {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
                ],
            })

        print(f"🔽 Downloading: {title}")
        success = False
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl_each:
                ydl_each.download([video_url])
            success = True
            # After successful full download, perform manual trim for video if requested
            if success and try_fragment_cut and not is_audio:
                try:
                    # Find the downloaded file (could be mp4/mkv/webm before convert)
                    downloaded_candidates = list(out_dir.glob(f"{base_name.split('_'+quality_tag_base)[0]}*{quality_tag_base}.*"))
                    if not downloaded_candidates:
                        downloaded_candidates = list(out_dir.glob(f"{base_name}.*"))
                    if downloaded_candidates:
                        src_file = max(downloaded_candidates, key=lambda p: p.stat().st_mtime)
                        final_file = out_dir / f"{base_name}.mp4"
                        if src_file != final_file:
                            # ensure final file name matches expected pattern
                            temp_input = src_file
                        else:
                            temp_input = src_file
                        # Perform trim into a temp file then replace
                        trimmed_tmp = out_dir / f"{base_name}.clip.tmp.mp4"
                        cmd = [
                            auto_ffmpeg,
                            "-y",
                            "-ss", start,
                            "-to", end,
                            "-i", str(temp_input),
                            "-c", "copy",
                            str(trimmed_tmp),
                        ]
                        rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if rc != 0 or not trimmed_tmp.exists() or trimmed_tmp.stat().st_size < 1024:
                            # Retry without copy (re-encode) for keyframe mismatch
                            cmd = [
                                auto_ffmpeg,
                                "-y",
                                "-ss", start,
                                "-to", end,
                                "-i", str(temp_input),
                                "-c:v", "libx264",
                                "-preset", "fast",
                                "-crf", "18",
                                "-c:a", "aac",
                                str(trimmed_tmp),
                            ]
                            subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if trimmed_tmp.exists() and trimmed_tmp.stat().st_size > 1024:
                            final_file = out_dir / f"{base_name}.mp4"
                            if final_file.exists():
                                final_file.unlink()
                            trimmed_tmp.rename(final_file)
                            # Optionally remove the original full file if different
                            if src_file.exists() and src_file != final_file:
                                try:
                                    src_file.unlink()
                                except OSError:
                                    pass
                            print(Fore.GREEN + f"✂️  Trimmed section saved: {final_file.name}")
                        else:
                            print(Fore.YELLOW + "⚠️ Failed to trim with ffmpeg; keeping full file.")
                except Exception as te:
                    print(Fore.YELLOW + f"⚠️ Trimming error: {te}")
            print(Fore.GREEN + f"✅ Successfully downloaded: {title}")
        except Exception as e:
            error_msg = str(e).lower()
            if "sabr" in error_msg or "format" in error_msg:
                print(Fore.YELLOW + f"⚠️ SABR/Format issue detected for: {title}. Trying alternative method...")
                # Fallback: simpler format
                ydl_opts_fallback = ydl_opts_base.copy()
                ydl_opts_fallback["outtmpl"] = outtmpl
                ydl_opts_fallback["format"] = "b[height<=480]/b" if not is_audio else "bestaudio/best"
                if is_audio and try_fragment_cut:
                    ydl_opts_fallback["postprocessor_args"] = ["-ss", start, "-to", end]
                try:
                    with yt_dlp.YoutubeDL(ydl_opts_fallback) as ydl_fallback:
                        ydl_fallback.download([video_url])
                    success = True
                    if success and try_fragment_cut and not is_audio:
                        # Repeat trimming for fallback
                        try:
                            src_file = max(out_dir.glob(f"{base_name}.*"), key=lambda p: p.stat().st_mtime)
                            trimmed_tmp = out_dir / f"{base_name}.clip.tmp.mp4"
                            cmd = [auto_ffmpeg, "-y", "-ss", start, "-to", end, "-i", str(src_file), "-c", "copy", str(trimmed_tmp)]
                            rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            if rc == 0 and trimmed_tmp.exists():
                                final_file = out_dir / f"{base_name}.mp4"
                                if final_file.exists():
                                    final_file.unlink()
                                trimmed_tmp.rename(final_file)
                                if src_file.exists() and src_file != final_file:
                                    try: src_file.unlink()
                                    except OSError: pass
                                print(Fore.GREEN + f"✂️  Trimmed section saved: {final_file.name}")
                        except Exception:
                            pass
                    print(Fore.GREEN + f"✅ Downloaded with fallback method: {title}")
                except Exception:
                    print(Fore.RED + f"❌ Download failed completely for: {title}")
            else:
                print(Fore.RED + f"❌ Download failed for: {title} - {str(e)}")
        
        if not success:
            print(Fore.CYAN + f"💡 You can try this URL manually: {video_url}")

    print(Style.BRIGHT + f"\n✅ Download process completed! Check folder: {out_dir}")
    
    # Count successful downloads
    downloaded_files = list(out_dir.glob("*"))
    video_files = [f for f in downloaded_files if f.suffix.lower() in ['.mp4', '.mkv', '.webm', '.mp3', '.m4a', '.opus']]
    
    if video_files:
        print(Fore.GREEN + f"📁 {len(video_files)} file(s) successfully downloaded")
        for file in video_files:
            print(f"   → {file.name}")
    else:
        print(Fore.YELLOW + "⚠️ No files were downloaded. This might be due to:")
        print("   • YouTube SABR streaming restrictions")
        print("   • Video region/age restrictions")
        print("   • Network connectivity issues")
        print("   • Video format not available")
        print(Fore.CYAN + "\n💡 Try:")
        print("   • Different video quality (360p or 480p)")
        print("   • Audio-only download")
        print("   • Try again later")
    print()

if __name__ == "__main__":
    main()
