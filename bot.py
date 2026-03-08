import os
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

def run_ytdlp(cmd: list, timeout=300) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return True
        logger.error(f"yt-dlp stderr: {result.stderr[:300]}")
    except Exception as e:
        logger.error(f"yt-dlp exception: {e}")
    return False

def download_video_only(url: str, output_dir: Path, extra: list) -> Path:
    output_file = output_dir / "video.mp4"
    cmd = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestvideo[ext=mp4]/bestvideo",
        "-o", str(output_file),
    ] + extra + [url]
    logger.info("📥 Téléchargement flux vidéo...")
    if run_ytdlp(cmd) and output_file.exists():
        return output_file
    return None

def download_audio_only(url: str, output_dir: Path, extra: list) -> Path:
    output_file = output_dir / "audio.m4a"
    cmd = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-o", str(output_file),
    ] + extra + [url]
    logger.info("🔊 Téléchargement flux audio...")
    if run_ytdlp(cmd) and output_file.exists():
        return output_file
    # Essai alternatif en mp3
    output_mp3 = output_dir / "audio.mp3"
    cmd2 = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestaudio",
        "--extract-audio", "--audio-format", "mp3",
        "-o", str(output_mp3),
    ] + extra + [url]
    if run_ytdlp(cmd2) and output_mp3.exists():
        return output_mp3
    return None


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n\n"
        "Envoie-moi un lien et je t'envoie :\n"
        "🎥 *Fichier vidéo* (sans audio, haute qualité)\n"
        "🔊 *Fichier audio* (haute qualité)\n\n"
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

    extra = []
    if platform in ("Facebook", "Twitter/X"):
        for browser in ["chrome", "edge", "firefox"]:
            extra = ["--cookies-from-browser", browser]
            break

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # ── Téléchargement vidéo ──
        await msg.edit_text("📥 Téléchargement de la vidéo (sans audio)...")
        video_path = download_video_only(url, tmp, extra)

        # ── Téléchargement audio ──
        await msg.edit_text("🔊 Téléchargement de l'audio...")
        audio_path = download_audio_only(url, tmp, extra)

        if not video_path and not audio_path:
            await msg.edit_text(
                "❌ *Échec du téléchargement*\n\n"
                "• Vidéo privée ou supprimée ?\n"
                "• Lien incorrect ?",
                parse_mode="Markdown",
            )
            return

        await msg.edit_text("📤 Envoi des fichiers...")

        # ── Envoyer la vidéo ──
        if video_path and video_path.exists():
            size_mb = video_path.stat().st_size / (1024 * 1024)
            resolution = get_video_resolution(video_path)
            if size_mb <= 50:
                with open(video_path, "rb") as vf:
                    await update.message.reply_video(
                        video=vf,
                        caption=f"🎥 *Vidéo sans audio* — {resolution}\n_{size_mb:.1f} Mo_",
                        parse_mode="Markdown",
                        supports_streaming=True,
                    )
            else:
                await update.message.reply_text(f"⚠️ Vidéo trop grande ({size_mb:.1f} Mo > 50 Mo Telegram)")
        else:
            await update.message.reply_text("⚠️ Vidéo non disponible")

        # ── Envoyer l'audio ──
        if audio_path and audio_path.exists():
            size_mb = audio_path.stat().st_size / (1024 * 1024)
            if size_mb <= 50:
                with open(audio_path, "rb") as af:
                    await update.message.reply_audio(
                        audio=af,
                        caption=f"🔊 *Audio haute qualité*\n_{size_mb:.1f} Mo_",
                        parse_mode="Markdown",
                    )
            else:
                await update.message.reply_text(f"⚠️ Audio trop grand ({size_mb:.1f} Mo > 50 Mo Telegram)")
        else:
            await update.message.reply_text("⚠️ Audio non disponible")

        await msg.delete()

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
