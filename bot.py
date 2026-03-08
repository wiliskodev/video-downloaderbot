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

MAX_SIZE_MB = 49

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

def check_has_audio(video_path: Path) -> bool:
    """Vérifie si la vidéo contient un flux audio."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=10
        )
        has = result.returncode == 0 and "audio" in result.stdout
        logger.info(f"Audio présent dans le fichier : {has}")
        return has
    except Exception as e:
        logger.error(f"Vérif audio échouée : {e}")
        return False

def get_duration(video_path: Path) -> float:
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

def add_audio_to_video(video_path: Path, url: str, output_dir: Path, extra_args: list) -> Path:
    """Télécharge l'audio séparément et le fusionne avec la vidéo existante."""
    audio_file = output_dir / "audio_only.m4a"
    final_file = output_dir / "final_with_audio.mp4"

    # Télécharger l'audio seul
    cmd_audio = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-o", str(audio_file),
    ] + extra_args + [url]

    logger.info("🔊 Téléchargement audio séparé...")
    res = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=300)
    
    if res.returncode != 0 or not audio_file.exists():
        logger.error(f"Audio téléchargement échoué: {res.stderr[:200]}")
        return video_path  # retourner vidéo sans audio plutôt que rien

    # Fusionner avec ffmpeg
    cmd_merge = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_file),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ac", "2",
        "-movflags", "+faststart",
        str(final_file)
    ]

    logger.info("🔗 Fusion vidéo + audio...")
    res_m = subprocess.run(cmd_merge, capture_output=True, text=True, timeout=300)
    
    if res_m.returncode == 0 and final_file.exists():
        logger.info(f"✅ Fusion réussie : {final_file.stat().st_size / 1024 / 1024:.1f} Mo")
        return final_file
    
    logger.error(f"Fusion échouée: {res_m.stderr[:300]}")
    return video_path

def compress_video(input_path: Path, output_dir: Path) -> Path:
    output_path = output_dir / "compressed.mp4"
    duration = get_duration(input_path)
    if duration <= 0:
        return None

    target_bits = MAX_SIZE_MB * 8 * 1024 * 1024
    total_bitrate = int(target_bits / duration)
    audio_bitrate = 128
    video_bitrate = max(200, (total_bitrate // 1000) - audio_bitrate)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-c:v", "libx264",
        "-b:v", f"{video_bitrate}k",
        "-maxrate", f"{video_bitrate * 2}k",
        "-bufsize", f"{video_bitrate * 4}k",
        "-c:a", "aac",
        "-b:a", f"{audio_bitrate}k",
        "-ac", "2",
        "-movflags", "+faststart",
        str(output_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and output_path.exists():
            return output_path
        logger.error(f"Compression error: {result.stderr[:300]}")
    except Exception as e:
        logger.error(f"Compression échouée : {e}")
    return None

def download_video(url: str, output_dir: Path, platform: str) -> Path:
    output_file = output_dir / "video.mp4"

    extra = []
    if platform in ("Facebook", "Twitter/X"):
        for browser in ["chrome", "edge", "firefox"]:
            extra = ["--cookies-from-browser", browser]
            break

    # ── Approche unique : laisser yt-dlp choisir le meilleur format avec audio ──
    # On NE force PAS de format spécifique pour éviter de prendre vidéo sans audio
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--merge-output-format", "mp4",
        "-o", str(output_file),
    ] + extra + [url]

    logger.info(f"📥 Téléchargement {platform} (format auto avec audio)...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    logger.info(f"yt-dlp stdout: {result.stdout[:300]}")
    logger.info(f"yt-dlp stderr: {result.stderr[:300]}")

    if result.returncode != 0 or not output_file.exists():
        logger.error("Téléchargement échoué")
        return None

    logger.info(f"✅ Fichier téléchargé : {output_file.stat().st_size / 1024 / 1024:.1f} Mo")

    # ── Vérifier si l'audio est présent ──
    if not check_has_audio(output_file):
        logger.warning("⚠️ Pas d'audio détecté — ajout de l'audio séparément...")
        output_file = add_audio_to_video(output_file, url, output_dir, extra)

    return output_file


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Video Downloader Bot*\n\n"
        "Envoie-moi un lien et je télécharge la vidéo avec son !\n\n"
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
                "• Lien incorrect ?",
                parse_mode="Markdown",
            )
            return

        file_size_mb = video_path.stat().st_size / (1024 * 1024)
        resolution = get_video_resolution(video_path)

        if file_size_mb > MAX_SIZE_MB:
            await msg.edit_text(f"📦 {file_size_mb:.1f} Mo détectés — compression en cours...")
            compressed = compress_video(video_path, tmp)
            if compressed:
                video_path = compressed
                file_size_mb = video_path.stat().st_size / (1024 * 1024)
                resolution = resolution + " (compressé)"
            else:
                await msg.edit_text(f"⚠️ Vidéo trop grande ({file_size_mb:.1f} Mo), compression échouée.")
                return

        await msg.edit_text(f"📤 Envoi... ({resolution} — {file_size_mb:.1f} Mo)")
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
