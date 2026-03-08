import os
import asyncio
import logging
import tempfile
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    print("❌ Token introuvable dans le fichier .env !")
    exit(1)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

SUPPORTED = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "fb.com": "Facebook",
    "twitter.com": "Twitter/X",
    "x.com": "Twitter/X",
    "t.co": "Twitter/X",
}

MAX_SIZE_MB = 49  # légèrement sous 50 Mo pour sécurité

def detect_platform(url: str):
    for domain, name in SUPPORTED.items():
        if domain in url:
            return name
    return None

def get_video_resolution(video_path: Path) -> str:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",")
            if len(parts) == 2:
                h = int(parts[1])
                if h >= 2160: return "4K 🔥"
                elif h >= 1440: return "2K ✨"
                elif h >= 1080: return "Full HD 1080p ⚡"
                elif h >= 720: return "HD 720p 👍"
                else: return f"{h}p"
    except Exception:
        pass
    return "HD"

def get_duration(video_path: Path) -> float:
    """Retourne la durée en secondes."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0

def compress_video(input_path: Path, output_dir: Path) -> Path:
    """
    Compresse la vidéo pour qu'elle soit sous 49 Mo.
    Calcule le bitrate optimal selon la durée.
    """
    output_path = output_dir / "compressed.mp4"
    duration = get_duration(input_path)

    if duration <= 0:
        return None

    # Calcul du bitrate cible (en kbps) pour tenir dans MAX_SIZE_MB
    # taille_cible_bits = MAX_SIZE_MB * 8 * 1024 * 1024
    # bitrate_total = taille / durée
    target_bits = MAX_SIZE_MB * 8 * 1024 * 1024
    total_bitrate = int(target_bits / duration)
    audio_bitrate = 128  # kbps audio fixe
    video_bitrate = max(200, (total_bitrate // 1000) - audio_bitrate)  # kbps vidéo

    logger.info(f"Compression : durée={duration:.1f}s, bitrate vidéo cible={video_bitrate}k")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-c:v", "libx264",
        "-b:v", f"{video_bitrate}k",
        "-maxrate", f"{video_bitrate * 2}k",
        "-bufsize", f"{video_bitrate * 4}k",
        "-c:a", "aac",
        "-b:a", f"{audio_bitrate}k",
        "-movflags", "+faststart",
        str(output_path)
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            logger.info(f"✅ Compressé : {size_mb:.1f} Mo")
            return output_path
        else:
            logger.error(f"ffmpeg error: {result.stderr[:300]}")
    except Exception as e:
        logger.error(f"Compression échouée : {e}")

    return None

def find_file(output_dir: Path):
    try:
        files = [f for f in output_dir.iterdir() if f.suffix in (".mp4", ".webm", ".mov", ".mkv")]
        if files:
            return max(files, key=lambda x: x.stat().st_mtime)
    except Exception:
        pass
    return None

def run_ytdlp(cmd: list, timeout=300) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return True
        logger.warning("yt-dlp stderr: %s", result.stderr[:300])
        return False
    except subprocess.TimeoutExpired:
        logger.error("Timeout yt-dlp")
        return False
    except Exception as e:
        logger.error("Exception yt-dlp: %s", e)
        return False

def download_video(url: str, output_dir: Path, platform: str):
    output_template = str(output_dir / "video.%(ext)s")

    strategies = []

    if platform == "YouTube":
        strategies = [
            ["yt-dlp", "--no-playlist", "--no-warnings",
             "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio",
             "--merge-output-format", "mp4", "-o", output_template, url],
            ["yt-dlp", "--no-playlist", "--no-warnings",
             "-f", "best[ext=mp4]/best", "-o", output_template, url],
        ]
    elif platform in ("Facebook", "Twitter/X"):
        for browser in ["chrome", "edge", "firefox"]:
            strategies.append([
                "yt-dlp", "--no-playlist", "--no-warnings",
                "-f", "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "--cookies-from-browser", browser,
                "-o", output_template, url
            ])
        strategies.append([
            "yt-dlp", "--no-playlist", "--no-warnings",
            "-f", "best", "-o", output_template, url
        ])

    for i, cmd in enumerate(strategies):
        logger.info(f"Stratégie {i+1}/{len(strategies)} pour {platform}...")
        if run_ytdlp(cmd):
            f = find_file(output_dir)
            if f:
                logger.info(f"✅ Téléchargé : {f.name}")
                return f
        for f in output_dir.iterdir():
            try: f.unlink()
            except: pass

    return None

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n\n"
        "Envoie-moi un lien et je télécharge la vidéo en *qualité maximale* !\n\n"
        "✅ *Plateformes supportées :*\n"
        "• 🎬 YouTube\n"
        "• 📘 Facebook\n"
        "• 🐦 Twitter / X\n\n"
        "📌 Colle simplement le lien ici 👇",
        parse_mode="Markdown",
    )

async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith("http"):
        await update.message.reply_text("❌ Envoie un lien valide commençant par `http://`", parse_mode="Markdown")
        return

    platform = detect_platform(url)
    if not platform:
        await update.message.reply_text(
            "❌ *Plateforme non supportée*\n\nJ'accepte : YouTube, Facebook, Twitter/X",
            parse_mode="Markdown",
        )
        return

    icons = {"YouTube": "🎬", "Facebook": "📘", "Twitter/X": "🐦"}
    icon = icons.get(platform, "🎬")

    msg = await update.message.reply_text(f"{icon} Téléchargement {platform} en cours...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video_path = download_video(url, tmp, platform)

        if not video_path:
            await msg.edit_text(
                "❌ *Échec du téléchargement*\n\n"
                "• Vidéo privée ou supprimée ?\n"
                "• Lien incorrect ?\n"
                "• Pour Facebook/Twitter : connecte-toi dans Chrome/Edge",
                parse_mode="Markdown",
            )
            return

        file_size_mb = video_path.stat().st_size / (1024 * 1024)
        resolution = get_video_resolution(video_path)

        # Compression automatique si trop grand
        if file_size_mb > MAX_SIZE_MB:
            await msg.edit_text(
                f"📦 Vidéo originale : {file_size_mb:.1f} Mo ({resolution})\n"
                f"⚙️ Compression automatique en cours..."
            )
            compressed = compress_video(video_path, tmp)
            if compressed:
                video_path = compressed
                file_size_mb = video_path.stat().st_size / (1024 * 1024)
                resolution = resolution + " (compressé)"
            else:
                await msg.edit_text(
                    f"⚠️ Vidéo trop grande ({file_size_mb:.1f} Mo) et compression échouée.\n"
                    "Essaie avec une vidéo plus courte."
                )
                return

        await msg.edit_text(f"📤 Envoi en cours... ({resolution} — {file_size_mb:.1f} Mo)")
        try:
            with open(video_path, "rb") as vf:
                await update.message.reply_video(
                    video=vf,
                    caption=f"✅ {platform} {icon} — {resolution}\n_{file_size_mb:.1f} Mo_",
                    parse_mode="Markdown",
                    supports_streaming=True,
                )
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Erreur lors de l'envoi : {e}")

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    logger.info("🤖 Video Downloader Bot démarré !")
    app.run_polling(poll_interval=1.0, stop_signals=None)

if __name__ == "__main__":
    main()
