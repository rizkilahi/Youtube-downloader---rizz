#!/usr/bin/env python3
"""
yt_cli_downloader.py
Command-line front-end for YouTube Downloader Pro (built on downloader_core).

Interactive:       python yt_cli_downloader.py
Non-interactive:   python yt_cli_downloader.py URL [--audio mp3] [--res 720p]
                       [--start 1:30 --end 3:00] [--subs] [--items 1,3,5-7]
Web UI:            python yt_cli_downloader.py --web
Update yt-dlp:     python yt_cli_downloader.py --update
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence

try:
    from colorama import Fore, Style, init as colorama_init

    colorama_init(autoreset=True)
except ModuleNotFoundError:  # colours are optional
    class _NoColor:
        def __getattr__(self, _name: str) -> str:
            return ""

    Fore = Style = _NoColor()  # type: ignore[assignment]

import downloader_core as core
from downloader_core import DownloadOptions, DownloadProgress

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_selection(raw: str, count: int) -> List[int]:
    """
    Parse a 1-based selection like "1,3,5-7" into sorted, unique 0-based indices.
    Raises ValueError on malformed input or when nothing valid is selected.
    """
    raw = (raw or "").strip().lower()
    if raw in ("", "all", "*"):
        return list(range(count))
    picked: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = (p.strip() for p in part.split("-", 1))
            if not (lo_s.isdigit() and hi_s.isdigit()):
                raise ValueError(f"Invalid range '{part}'")
            lo, hi = sorted((int(lo_s), int(hi_s)))
            picked.update(range(lo, hi + 1))
        elif part.isdigit():
            picked.add(int(part))
        else:
            raise ValueError(f"Invalid item '{part}'")
    indices = sorted(i - 1 for i in picked if 1 <= i <= count)
    if not indices:
        raise ValueError(f"No valid items selected (choose between 1 and {count})")
    return indices


def ask(prompt: str, choices: Sequence[str], input_fn: Callable[[str], str] = input) -> str:
    while True:
        print(prompt)
        for idx, c in enumerate(choices, 1):
            print(f" {idx}. {c}")
        sel = input_fn(f" Choose [1-{len(choices)}]: ").strip()
        if sel.isdigit() and 1 <= int(sel) <= len(choices):
            return choices[int(sel) - 1]
        print(Fore.YELLOW + "  ⚠️  Invalid choice.\n")


def ask_time(prompt: str, input_fn: Callable[[str], str] = input) -> Optional[str]:
    while True:
        value = input_fn(prompt).strip()
        try:
            core.parse_time(value)
            return value or None
        except ValueError as e:
            print(Fore.YELLOW + f"  ⚠️  {e}")


class ProgressPrinter:
    """Renders a single-line progress bar for a DownloadProgress stream."""

    def __init__(self, width: int = 30) -> None:
        self.width = width
        self._last_len = 0

    def __call__(self, p: DownloadProgress) -> None:
        if p.status == "downloading":
            filled = int(self.width * p.percent / 100)
            bar = "█" * filled + "░" * (self.width - filled)
            line = f"   [{bar}] {p.percent:5.1f}%  {p.speed_str:>12}  ETA {p.eta_str or '--:--'}"
        elif p.status == "processing":
            line = "   ⚙️  Processing (merge / convert / trim)…"
        else:
            return
        pad = max(self._last_len - len(line), 0)
        sys.stdout.write("\r" + line + " " * pad)
        sys.stdout.flush()
        self._last_len = len(line)

    def done(self) -> None:
        if self._last_len:
            sys.stdout.write("\r" + " " * self._last_len + "\r")
            sys.stdout.flush()
            self._last_len = 0


# ---------------------------------------------------------------------------
# Download orchestration
# ---------------------------------------------------------------------------

def download_items(
    targets: List[dict],
    base_options: DownloadOptions,
    playlist_folder: Optional[str],
) -> int:
    """Download each target; returns the number of failures."""
    failures = 0
    for n, target in enumerate(targets, 1):
        title = target.get("title") or target["url"]
        prefix = f"[{n}/{len(targets)}] " if len(targets) > 1 else ""
        print(Fore.CYAN + f"🔽 {prefix}{title}")
        printer = ProgressPrinter()
        options = DownloadOptions(**{
            **base_options.__dict__,
            "url": target["url"],
            "title": target.get("title"),
            "playlist_folder": playlist_folder,
        })
        try:
            path = core.perform_download(options, progress_callback=printer)
            printer.done()
            print(Fore.GREEN + f"   ✅ Saved: {path}")
        except KeyboardInterrupt:
            printer.done()
            raise
        except Exception as e:
            printer.done()
            failures += 1
            print(Fore.RED + f"   ❌ Failed: {e}")
            print(Fore.CYAN + f"   💡 You can retry this URL later: {target['url']}")
    return failures


def run(args: argparse.Namespace, input_fn: Callable[[str], str] = input) -> int:
    interactive = args.url is None
    print(Fore.CYAN + Style.BRIGHT + "\n===   YouTube Downloader Pro (CLI)   ===\n")

    url = args.url or input_fn("Paste YouTube URL: ").strip()
    if not url:
        print(Fore.RED + "❌ URL is required.")
        return 2

    print("🔎 Fetching information…")
    try:
        info = core.extract_info(url)
    except Exception as e:
        print(Fore.RED + f"❌ Could not read this URL: {e}")
        return 1

    # Which items?
    if info.is_playlist:
        print(Fore.CYAN + f"\n📃 Playlist: {info.title} ({info.playlist_count} accessible videos)\n")
        if not info.entries:
            print(Fore.YELLOW + "⚠️ No accessible videos found in this playlist")
            return 1
        selection = args.items
        if selection is None and interactive:
            choice = ask("Download semua atau pilih sebagian?",
                         ["Semua video", "Pilih sebagian"], input_fn)
            if choice.startswith("Pilih"):
                for i, entry in enumerate(info.entries, 1):
                    print(f" {i:>3}. {entry['title']}  ({entry['duration_str']})")
                while True:
                    raw = input_fn(f"\nNomor video (mis. 1,3,5-7) [1-{len(info.entries)}]: ")
                    try:
                        parse_selection(raw, len(info.entries))
                        selection = raw
                        break
                    except ValueError as e:
                        print(Fore.YELLOW + f"  ⚠️  {e}")
        try:
            indices = parse_selection(selection or "all", len(info.entries))
        except ValueError as e:
            print(Fore.RED + f"❌ {e}")
            return 2
        targets = [info.entries[i] for i in indices]
        playlist_folder: Optional[str] = info.title
    else:
        print(Fore.CYAN + f"\n🎬 {info.title}  ({info.duration_str}, {info.uploader})\n")
        targets = [{"url": info.url, "title": info.title}]
        playlist_folder = None

    # Mode / quality
    if args.audio:
        mode, audio_fmt, resolution = "audio", args.audio, "auto"
    elif args.res or not interactive:
        mode, audio_fmt, resolution = "video", "mp3", args.res or "1080p"
    else:
        choice = ask("Download what?", ["Video (mp4)", "Audio only"], input_fn)
        if choice.startswith("Audio"):
            mode, resolution = "audio", "auto"
            audio_fmt = ask("\nChoose audio format:", list(core.AUDIO_FORMATS), input_fn)
        else:
            mode, audio_fmt = "video", "mp3"
            choices = info.available_resolutions if not info.is_playlist else list(core.RESOLUTIONS)
            resolution = ask("\nChoose video resolution:", list(choices), input_fn)

    # Trim range
    start, end = args.start, args.end
    if interactive and start is None and end is None:
        partial = ask("\nFull length or just a section?", ["Full", "Specific time range"], input_fn)
        if partial.startswith("Specific"):
            while True:
                start = ask_time("Start time (kosongkan = dari awal, MM:SS / HH:MM:SS): ", input_fn)
                end = ask_time("End time (kosongkan = sampai akhir): ", input_fn)
                try:
                    core.validate_time_range(start, end)
                    break
                except ValueError as e:
                    print(Fore.YELLOW + f"  ⚠️  {e}")
    try:
        core.validate_time_range(start, end)
    except ValueError as e:
        print(Fore.RED + f"❌ {e}")
        return 2

    base = DownloadOptions(
        url="",
        mode=mode,
        resolution=resolution,
        audio_fmt=audio_fmt,
        start_time=start,
        end_time=end,
        subtitles=bool(args.subs) and mode == "video",
        subtitle_lang=args.sub_lang,
        output_dir=Path(args.output) if args.output else None,
    )
    out_dir = core.resolve_output_dir(DownloadOptions(**{**base.__dict__, "playlist_folder": playlist_folder}))
    print(Fore.BLUE + f"\n📁 Output folder: {out_dir}\n")

    try:
        failures = download_items(targets, base, playlist_folder)
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n⏹️  Dibatalkan oleh pengguna.")
        return 130

    ok = len(targets) - failures
    colour = Fore.GREEN if failures == 0 else Fore.YELLOW
    print(colour + Style.BRIGHT + f"\n✅ Selesai: {ok} berhasil, {failures} gagal. Folder: {out_dir}\n")
    if failures:
        print(Fore.CYAN + "💡 Tips: coba resolusi lebih rendah (480p/360p), mode audio, "
                          "atau jalankan --update untuk memperbarui yt-dlp.\n")
    return 0 if failures == 0 else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yt_cli_downloader.py",
        description="YouTube Downloader Pro – CLI (yt-dlp + FFmpeg). "
                    "Run without arguments for interactive mode.",
    )
    p.add_argument("url", nargs="?", help="Video or playlist URL (omit for interactive mode)")
    p.add_argument("--audio", nargs="?", const="mp3", choices=core.AUDIO_FORMATS,
                   help="Download audio only (default format: mp3)")
    p.add_argument("--res", choices=core.RESOLUTIONS, help="Video resolution (default: 1080p)")
    p.add_argument("--start", help="Clip start (SS, MM:SS or HH:MM:SS)")
    p.add_argument("--end", help="Clip end (SS, MM:SS or HH:MM:SS)")
    p.add_argument("--subs", action="store_true", help="Embed subtitles (video only)")
    p.add_argument("--sub-lang", default="en", help="Subtitle language (default: en)")
    p.add_argument("--items", help="Playlist items to download, e.g. 1,3,5-7 (default: all)")
    p.add_argument("-o", "--output", help="Custom output folder")
    p.add_argument("--web", action="store_true", help="Launch the Web UI instead")
    p.add_argument("--update", action="store_true", help="Upgrade yt-dlp and exit")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    core.make_console_safe()

    if args.update:
        print("💡 Updating yt-dlp…")
        if core.update_yt_dlp():
            print(Fore.GREEN + "✅ yt-dlp updated (restart to use the new version)")
            return 0
        print(Fore.RED + "❌ Could not update yt-dlp")
        return 1

    if args.web:
        from app import start_server

        start_server()
        return 0

    if not core.find_ffmpeg():
        print("💡 FFmpeg not found – attempting automatic install…")
    core.ensure_ffmpeg()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
