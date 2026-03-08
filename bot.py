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
BOT_TOKEN = os.getenv(“TELEGRAM_BOT_TOKEN”)

if not BOT_TOKEN:
print(“❌ Token introuvable dans le fichier .env !”)
exit(1)

logging.basicConfig(format=”%(asctime)s | %(levelname)s | %(message)s”, level=logging.INFO)
logger = logging.getLogger(**name**)

SUPPORTED = {
“youtube.com”: “YouTube”,
“youtu.be”: “YouTube”,
“facebook.com”: “Facebook”,
“fb.watch”: “Facebook”,
“fb.com”: “Facebook”,
“twitter.com”: “Twitter/X”,
“x.com”: “Twitter/X”,
“t.co”: “Twitter/X”,
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
[“ffprobe”, “-v”, “error”, “-select_streams”, “v:0”,
“-show_entries”, “stream=width,height”, “-of”, “csv=p=0”, str(video_path)],
capture_output=True, text=True, timeout=10
)
if result.returncode == 0 and result.stdout.strip():
parts = result.stdout.strip().split(”,”)
if len(parts) == 2:
h = int(parts[1])
if h >= 2160: return “4K 🔥”
elif h >= 1440: return “2K ✨”
elif h >= 1080: return “Full HD 1080p ⚡”
elif h >= 720: return “HD 720p 👍”
else: return f”{h}p”
except Exception:
pass
return “HD”

def get_duration(video_path: Path) -> float:
try:
result = subprocess.run(
[“ffprobe”, “-v”, “error”, “-show_entries”, “format=duration”,
“-of”, “csv=p=0”, str(video_path)],
capture_output=True, text=True, timeout=10
)
if result.returncode == 0:
return float(result.stdout.strip())
except Exception:
pass
return 0

def merge_video_audio(video_file: Path, audio_file: Path, output: Path) -> bool:
“”“Fusionne vidéo + audio avec ffmpeg.”””
cmd = [
“ffmpeg”, “-y”,
“-i”, str(video_file),
“-i”, str(audio_file),
“-c:v”, “copy”,
“-c:a”, “aac”,
“-ac”, “2”,
“-b:a”, “192k”,
“-movflags”, “+faststart”,
str(output)
]
try:
result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
if result.returncode == 0 and output.exists():
logger.info(f”✅ Fusion OK : {output.stat().st_size / 1024 / 1024:.1f} Mo”)
return True
logger.error(f”ffmpeg fusion error: {result.stderr[:300]}”)
except Exception as e:
logger.error(f”Fusion échouée : {e}”)
return False

def compress_video(input_path: Path, output_dir: Path) -> Path:
output_path = output_dir / “compressed.mp4”
duration = get_duration(input_path)
if duration <= 0:
return None

```
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
```

def download_video(url: str, output_dir: Path, platform: str) -> Path:
“””
Télécharge vidéo et audio séparément puis les fusionne avec ffmpeg.
“””
video_file = output_dir / “video_only.mp4”
audio_file = output_dir / “audio_only.m4a”
final_file = output_dir / “final.mp4”

```
extra = []
if platform in ("Facebook", "Twitter/X"):
    for browser in ["chrome", "edge", "firefox"]:
        extra = ["--cookies-from-browser", browser]
        break  # on essaie chrome en premier

# ── Étape 1 : télécharger la meilleure vidéo (sans audio) ──
cmd_video = [
    "yt-dlp", "--no-playlist", "--no-warnings",
    "-f", "bestvideo[ext=mp4]/bestvideo",
    "-o", str(video_file),
] + extra + [url]

logger.info("📥 Téléchargement vidéo (flux vidéo)...")
res_v = subprocess.run(cmd_video, capture_output=True, text=True, timeout=300)
if res_v.returncode != 0 or not video_file.exists():
    logger.warning(f"Flux vidéo échoué, fallback best... stderr: {res_v.stderr[:200]}")
    # Fallback : télécharger best directement (vidéo+audio ensemble)
    cmd_best = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "best[ext=mp4]/best",
        "-o", str(final_file),
    ] + extra + [url]
    res_b = subprocess.run(cmd_best, capture_output=True, text=True, timeout=300)
    if res_b.returncode == 0 and final_file.exists():
        logger.info("✅ Téléchargé via fallback best")
        return final_file
    return None

# ── Étape 2 : télécharger le meilleur audio ──
cmd_audio = [
    "yt-dlp", "--no-playlist", "--no-warnings",
    "-f", "bestaudio[ext=m4a]/bestaudio",
    "-o", str(audio_file),
] + extra + [url]

logger.info("🔊 Téléchargement audio (flux audio)...")
res_a = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=300)
if res_a.returncode != 0 or not audio_file.exists():
    logger.warning("Flux audio échoué")
    # Retourner la vidéo seule si l'audio échoue
    return video_file if video_file.exists() else None

# ── Étape 3 : fusionner vidéo + audio ──
logger.info("🔗 Fusion vidéo + audio...")
if merge_video_audio(video_file, audio_file, final_file):
    return final_file

# Si fusion échoue, retourner la vidéo seule
return video_file if video_file.exists() else None
```

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
“👋 *Video Downloader Bot*\n\n”
“Envoie-moi un lien et je télécharge la vidéo en *qualité maximale avec son* !\n\n”
“✅ *Plateformes supportées :*\n”
“• 🎬 YouTube\n”
“• 📘 Facebook\n”
“• 🐦 Twitter / X\n\n”
“📌 Colle simplement le lien ici 👇”,
parse_mode=“Markdown”,
)

async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
url = update.message.text.strip()

```
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

    # Compression si trop grand
    if file_size_mb > MAX_SIZE_MB:
        await msg.edit_text(
            f"📦 Vidéo originale : {file_size_mb:.1f} Mo ({resolution})\n"
            "⚙️ Compression en cours (son conservé)..."
        )
        compressed = compress_video(video_path, tmp)
        if compressed:
            video_path = compressed
            file_size_mb = video_path.stat().st_size / (1024 * 1024)
            resolution = resolution + " (compressé)"
        else:
            await msg.edit_text(f"⚠️ Vidéo trop grande ({file_size_mb:.1f} Mo) et compression échouée.")
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
```

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
app.add_handler(CommandHandler(“start”, start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
logger.info(“🤖 Video Downloader Bot démarré !”)
app.run_polling(poll_interval=1.0, stop_signals=None)

if **name** == “**main**”:
main()
