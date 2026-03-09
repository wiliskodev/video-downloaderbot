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
FACEBOOK_COOKIES = os.getenv("FACEBOOK_COOKIES", "")

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

COOKIES_DIR = Path("/tmp/cookies")
COOKIES_DIR.mkdir(exist_ok=True)
YT_COOKIES_FILE = COOKIES_DIR / "youtube.txt"
FB_COOKIES_FILE = COOKIES_DIR / "facebook.txt"

def fix_cookies(content: str) -> str:
    """
    Railway écrase les vrais sauts de ligne.
    On remplace les \\n littéraux par de vrais sauts de ligne.
    """
    # Remplacer les \n littéraux (2 chars) par de vrais sauts de ligne
    content = content.replace("\\n", "\n")
    # Remplacer aussi \t littéraux par de vrais tabs (format cookies.txt)
    content = content.replace("\\t", "\t")
    return content

def setup_cookies():
    if YOUTUBE_COOKIES:
        fixed = fix_cookies(YOUTUBE_COOKIES)
        YT_COOKIES_FILE.write_text(fixed, encoding="utf-8")
        lines = [l for l in fixed.splitlines() if l.strip() and not l.startswith("#")]
        logger.info(f"✅ Cookies YouTube chargés ({len(lines)} entrées)")
    else:
        logger.warning("⚠️ Pas de cookies YouTube configurés")

    if FACEBOOK_COOKIES:
        fixed = fix_cookies(FACEBOOK_COOKIES)
        FB_COOKIES_FILE.write_text(fixed, encoding="utf-8")
        lines = [l for l in fixed.splitlines() if l.strip() and not l.startswith("#")]
        logger.info(f"✅ Cookies Facebook chargés ({len(lines)} entrées)")
    else:
        logger.warning("⚠️ Pas de cookies Facebook configurés")

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
        logger.error(f"yt-dlp stderr: {result.stderr[:400]}")
    except Exception as e:
        logger.error(f"yt-dlp exception: {e}")
    return False

def convert_to_mp4(input_path: Path, output_dir: Path) -> Path:
    output = output_dir / "converted.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-c:v", "copy", "-c:a", "copy",
        "-movflags", "+faststart", str(output)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and output.exists():
            return output
        # Réencodage si copy échoue
        cmd2 = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-c:v", "libx264", "-c:a", "aac",
            "-movflags", "+faststart", str(output)
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=300)
        if result2.returncode == 0 and output.exists():
            return output
    except Exception as e:
        logger.error(f"Conversion mp4 échouée : {e}")
    return input_path

def get_cookies_args(platform: str) -> list:
    if platform == "YouTube" and YT_COOKIES_FILE.exists():
        return ["--cookies", str(YT_COOKIES_FILE)]
    elif platform == "Facebook" and FB_COOKIES_FILE.exists():
        return ["--cookies", str(FB_COOKIES_FILE)]
    return []

def dl_video_only(url: str, output_dir: Path, platform: str) -> Path:
    output_file = output_dir / "video.mp4"
    cookies = get_cookies_args(platform)

    cmd = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestvideo[height>=720][ext=mp4]/bestvideo[height>=720]/bestvideo[ext=mp4]/bestvideo",
        "--merge-output-format", "mp4",
        "-o", str(output_file),
    ] + cookies + [url]

    if run_ytdlp(cmd) and output_file.exists():
        return output_file

    # Chercher si un autre format a été créé et convertir
    for f in output_dir.iterdir():
        if f.suffix in (".webm", ".mkv", ".mov", ".avi"):
            logger.info(f"Conversion {f.suffix} → mp4...")
            return convert_to_mp4(f, output_dir)

    return None

def dl_audio_only(url: str, output_dir: Path, platform: str) -> Path:
    output_mp3 = output_dir / "audio.mp3"
    cookies = get_cookies_args(platform)

    cmd = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestaudio",
        "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
        "-o", str(output_mp3),
    ] + cookies + [url]

    if run_ytdlp(cmd) and output_mp3.exists():
        return output_mp3

    # Fallback m4a → mp3
    output_m4a = output_dir / "audio_raw.m4a"
    cmd2 = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-o", str(output_m4a),
    ] + cookies + [url]

    if run_ytdlp(cmd2) and output_m4a.exists():
        cmd_conv = [
            "ffmpeg", "-y", "-i", str(output_m4a),
            "-codec:a", "libmp3lame", "-qscale:a", "0", str(output_mp3)
        ]
        try:
            result = subprocess.run(cmd_conv, capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and output_mp3.exists():
                return output_mp3
        except Exception as e:
            logger.error(f"Conversion m4a→mp3 échouée : {e}")
        return output_m4a

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
            caption=f"🎥 *Vidéo HD sans audio* {icon}\n{resolution} — {size_mb:.1f} Mo",
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
            caption=f"🔊 *Audio MP3 haute qualité* {icon}\n{size_mb:.1f} Mo",
            parse_mode="Markdown",
        )

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n\n"
        "Envoie-moi un lien et choisis ce que tu veux :\n\n"
        "🎥 *Vidéo HD* — format MP4 sans audio\n"
        "🔊 *Audio seul* — format MP3 haute qualité\n"
        "📦 *Vidéo + Audio* — MP4 + MP3 séparés\n\n"
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
        [InlineKeyboardButton("🎥 Vidéo HD seulement (MP4)", callback_data="video_only")],
        [InlineKeyboardButton("🔊 Audio seulement (MP3)", callback_data="audio_only")],
        [InlineKeyboardButton("📦 Vidéo HD + Audio (MP4 + MP3)", callback_data="both")],
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
        "video_only": "🎥 Vidéo HD (MP4)",
        "audio_only": "🔊 Audio (MP3)",
        "both": "📦 Vidéo + Audio"
    }

    await query.edit_message_text(f"⏳ Téléchargement {labels.get(choice)} {icon} en cours...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        if choice == "video_only":
            video = dl_video_only(url, tmp, platform)
            if not video:
                await query.edit_message_text("❌ Échec du téléchargement vidéo.")
                return
            await query.edit_message_text("📤 Envoi de la vidéo MP4...")
            await send_video_file(query, video, platform)
            await query.delete_message()

        elif choice == "audio_only":
            audio = dl_audio_only(url, tmp, platform)
            if not audio:
                await query.edit_message_text("❌ Échec du téléchargement audio.")
                return
            await query.edit_message_text("📤 Envoi de l'audio MP3...")
            await send_audio_file(query, audio, platform)
            await query.delete_message()

        elif choice == "both":
            await query.edit_message_text("⏳ Téléchargement vidéo HD (MP4)...")
            video = dl_video_only(url, tmp, platform)
            await query.edit_message_text("⏳ Téléchargement audio (MP3)...")
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
