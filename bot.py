import os
import logging
import tempfile
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES", "")

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

pending_urls = {}

# ── Écrire les cookies dans un fichier temporaire au démarrage ────────────────
COOKIES_FILE = Path("/tmp/youtube_cookies.txt")

def setup_cookies():
    if YOUTUBE_COOKIES:
        COOKIES_FILE.write_text(YOUTUBE_COOKIES)
        logger.info("✅ Cookies YouTube chargés depuis variable d'environnement")
    else:
        logger.warning("⚠️ Pas de cookies YouTube — certaines vidéos peuvent échouer")

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

def get_cookies_args(platform: str) -> list:
    """Retourne les arguments cookies selon la plateforme."""
    args = []
    # Cookies fichier pour YouTube
    if platform == "YouTube" and COOKIES_FILE.exists():
        args += ["--cookies", str(COOKIES_FILE)]
    # Cookies navigateur pour Facebook/Twitter
    elif platform in ("Facebook", "Twitter/X"):
        for browser in ["chrome", "edge", "firefox"]:
            args += ["--cookies-from-browser", browser]
            break
    return args

def dl_video_only(url: str, output_dir: Path, platform: str) -> Path:
    output_file = output_dir / "video.mp4"
    cookies = get_cookies_args(platform)
    cmd = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestvideo[height>=720][ext=mp4]/bestvideo[height>=720]/bestvideo[ext=mp4]/bestvideo",
        "-o", str(output_file),
    ] + cookies + [url]
    if run_ytdlp(cmd) and output_file.exists():
        return output_file
    return None

def dl_audio_only(url: str, output_dir: Path, platform: str) -> Path:
    output_m4a = output_dir / "audio.m4a"
    output_mp3 = output_dir / "audio.mp3"
    cookies = get_cookies_args(platform)

    # Essai 1 : m4a
    cmd = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-o", str(output_m4a),
    ] + cookies + [url]
    if run_ytdlp(cmd) and output_m4a.exists():
        return output_m4a

    # Essai 2 : mp3
    cmd2 = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestaudio",
        "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
        "-o", str(output_mp3),
    ] + cookies + [url]
    if run_ytdlp(cmd2) and output_mp3.exists():
        return output_mp3
    return None

async def send_video_file(update, video_path: Path, platform: str):
    size_mb = video_path.stat().st_size / (1024 * 1024)
    resolution = get_video_resolution(video_path)
    icons = {"YouTube": "🎬", "Facebook": "📘", "Twitter/X": "🐦"}
    icon = icons.get(platform, "🎬")
    if size_mb > 50:
        await update.message.reply_text(f"⚠️ Vidéo trop grande ({size_mb:.1f} Mo > 50 Mo Telegram)")
        return
    with open(video_path, "rb") as vf:
        await update.message.reply_video(
            video=vf,
            caption=f"🎥 *Vidéo sans audio* {icon}\n{resolution} — {size_mb:.1f} Mo",
            parse_mode="Markdown",
            supports_streaming=True,
        )

async def send_audio_file(update, audio_path: Path, platform: str):
    size_mb = audio_path.stat().st_size / (1024 * 1024)
    icons = {"YouTube": "🎬", "Facebook": "📘", "Twitter/X": "🐦"}
    icon = icons.get(platform, "🎬")
    if size_mb > 50:
        await update.message.reply_text(f"⚠️ Audio trop grand ({size_mb:.1f} Mo > 50 Mo Telegram)")
        return
    with open(audio_path, "rb") as af:
        await update.message.reply_audio(
            audio=af,
            caption=f"🔊 *Audio haute qualité* {icon}\n{size_mb:.1f} Mo",
            parse_mode="Markdown",
        )

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n\n"
        "Envoie-moi un lien et choisis ce que tu veux :\n\n"
        "🎥 *Vidéo HD* — sans audio\n"
        "🔊 *Audio seul* — haute qualité\n"
        "📦 *Vidéo + Audio* — deux fichiers séparés\n\n"
        "✅ *Plateformes :*\n"
        "• 🎬 YouTube\n"
        "• 📘 Facebook\n"
        "• 🐦 Twitter / X",
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

    user_id = update.message.from_user.id
    pending_urls[user_id] = {"url": url, "platform": platform}

    icons = {"YouTube": "🎬", "Facebook": "📘", "Twitter/X": "🐦"}
    icon = icons.get(platform, "🎬")

    keyboard = [
        [InlineKeyboardButton("🎥 Vidéo HD seulement", callback_data="video_only")],
        [InlineKeyboardButton("🔊 Audio seulement", callback_data="audio_only")],
        [InlineKeyboardButton("📦 Vidéo HD + Audio (séparés)", callback_data="both")],
    ]
    await update.message.reply_text(
        f"{icon} *{platform}* détecté !\n\nQue veux-tu télécharger ?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    choice = query.data

    if user_id not in pending_urls:
        await query.edit_message_text("❌ Session expirée. Renvoie le lien.")
        return

    data = pending_urls.pop(user_id)
    url = data["url"]
    platform = data["platform"]
    icons = {"YouTube": "🎬", "Facebook": "📘", "Twitter/X": "🐦"}
    icon = icons.get(platform, "🎬")

    labels = {
        "video_only": "🎥 Vidéo HD",
        "audio_only": "🔊 Audio",
        "both": "📦 Vidéo + Audio",
    }
    await query.edit_message_text(f"⏳ Téléchargement {labels.get(choice)} {icon} en cours...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        if choice == "video_only":
            video = dl_video_only(url, tmp, platform)
            if not video:
                await query.edit_message_text("❌ Échec du téléchargement vidéo.")
                return
            await query.edit_message_text("📤 Envoi de la vidéo...")
            await send_video_file(query, video, platform)
            await query.delete_message()

        elif choice == "audio_only":
            audio = dl_audio_only(url, tmp, platform)
            if not audio:
                await query.edit_message_text("❌ Échec du téléchargement audio.")
                return
            await query.edit_message_text("📤 Envoi de l'audio...")
            await send_audio_file(query, audio, platform)
            await query.delete_message()

        elif choice == "both":
            await query.edit_message_text("⏳ Téléchargement vidéo HD...")
            video = dl_video_only(url, tmp, platform)
            await query.edit_message_text("⏳ Téléchargement audio...")
            audio = dl_audio_only(url, tmp, platform)

            if not video and not audio:
                await query.edit_message_text("❌ Échec du téléchargement.")
                return

            await query.edit_message_text("📤 Envoi des fichiers...")
            if video:
                await send_video_file(query, video, platform)
            if audio:
                await send_audio_file(query, audio, platform)
            await query.delete_message()

def main():
    setup_cookies()
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
    app.add_handler(CallbackQueryHandler(handle_choice))
    logger.info("🤖 Video Downloader Bot démarré !")
    app.run_polling(poll_interval=1.0, stop_signals=None)

if __name__ == "__main__":
    main()
