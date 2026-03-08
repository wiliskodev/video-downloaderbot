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

    # Stratégies de téléchargement par ordre de priorité
    strategies = []

    if platform == "YouTube":
        strategies = [
            # 1. Meilleure qualité HD/4K avec fusion ffmpeg
            ["yt-dlp", "--no-playlist", "--no-warnings",
             "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio",
             "--merge-output-format", "mp4",
             "-o", output_template, url],
            # 2. Meilleur mp4 direct sans fusion
            ["yt-dlp", "--no-playlist", "--no-warnings",
             "-f", "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
             "-o", output_template, url],
            # 3. Fallback simple
            ["yt-dlp", "--no-playlist", "--no-warnings",
             "-f", "best",
             "-o", output_template, url],
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
        # Fallback sans cookies
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
            # Nettoyer pour la prochaine tentative
        for f in output_dir.iterdir():
            try: f.unlink()
            except: pass

    return None

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n\n"
        "Envoie-moi un lien et je télécharge la vidéo en *qualité maximale* (HD/4K) !\n\n"
        "✅ *Plateformes supportées :*\n"
        "• 🎬 YouTube\n"
        "• 📘 Facebook\n"
        "• 🐦 Twitter / X\n\n"
        "📌 Colle simplement le lien ici 👇",
        parse_mode="Markdown",
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Aide*\n\n"
        "1. Copie le lien d'une vidéo YouTube, Facebook ou Twitter\n"
        "2. Colle-le dans ce chat\n"
        "3. Reçois la vidéo en HD ou 4K 🎬\n\n"
        "⚠️ *Limites :*\n"
        "• Max 50 Mo (limite Telegram)\n"
        "• Vidéos privées non accessibles",
        parse_mode="Markdown",
    )

async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith("http"):
        await update.message.reply_text(
            "❌ Envoie un lien valide commençant par `http://` ou `https://`",
            parse_mode="Markdown",
        )
        return

    platform = detect_platform(url)
    if not platform:
        await update.message.reply_text(
            "❌ *Plateforme non supportée*\n\n"
            "J'accepte uniquement :\n"
            "• 🎬 YouTube\n"
            "• 📘 Facebook\n"
            "• 🐦 Twitter / X",
            parse_mode="Markdown",
        )
        return

    icons = {"YouTube": "🎬", "Facebook": "📘", "Twitter/X": "🐦"}
    icon = icons.get(platform, "🎬")

    msg = await update.message.reply_text(f"{icon} Téléchargement {platform} en HD/4K en cours...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video_path = download_video(url, tmp, platform)

        if not video_path:
            await msg.edit_text(
                "❌ *Échec du téléchargement*\n\n"
                "💡 *Causes possibles :*\n"
                "• Vidéo privée ou supprimée\n"
                "• Pour Facebook/Twitter : connecte-toi dans Chrome/Edge\n"
                "• Lien incorrect ou vidéo trop longue",
                parse_mode="Markdown",
            )
            return

        file_size_mb = video_path.stat().st_size / (1024 * 1024)
        resolution = get_video_resolution(video_path)

        if file_size_mb > 50:
            await msg.edit_text(
                f"⚠️ *Vidéo trop grande* ({file_size_mb:.1f} Mo)\n\n"
                f"Résolution : {resolution}\n"
                "Telegram limite à 50 Mo. Essaie une vidéo plus courte.",
                parse_mode="Markdown",
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
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    logger.info("🤖 Video Downloader Bot HD/4K démarré !")
    app.run_polling(poll_interval=1.0, stop_signals=None)

if __name__ == "__main__":
    main()
