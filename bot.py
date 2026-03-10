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

# ─── Configuration ────────────────────────────────────────────────────────────

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES", "")

if not BOT_TOKEN:
    print("❌ Token introuvable dans le fichier .env !")
    exit(1)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Cookies ──────────────────────────────────────────────────────────────────

COOKIES_DIR = Path("/tmp/cookies")
COOKIES_DIR.mkdir(exist_ok=True)
YT_COOKIES_FILE = COOKIES_DIR / "youtube.txt"

def fix_cookies(content: str) -> str:
    """Corrige les sauts de ligne écrasés par Railway."""
    return content.replace("\\n", "\n").replace("\\t", "\t")

def validate_cookies_format(content: str) -> bool:
    """
    Vérifie que le fichier cookies est au format Netscape valide.
    Chaque ligne de données doit avoir exactement 7 champs séparés par des tabs.
    """
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            logger.warning(f"⚠️ Ligne cookies invalide ({len(fields)} champs au lieu de 7) : {line[:80]}")
            return False
    return True

def setup_cookies():
    if YOUTUBE_COOKIES:
        fixed = fix_cookies(YOUTUBE_COOKIES)

        # Validation du format Netscape
        if not validate_cookies_format(fixed):
            logger.error(
                "❌ Format cookies invalide ! "
                "Le fichier doit être au format Netscape (7 colonnes séparées par des tabs). "
                "Exporte-les depuis ton navigateur avec l'extension 'Get cookies.txt LOCALLY'."
            )
        
        # Vérifier qu'il y a bien un cookie d'auth YouTube
        has_auth = any(
            "SAPISID" in line or "SID" in line or "__Secure-3PSID" in line
            for line in fixed.splitlines()
            if not line.startswith("#")
        )
        if not has_auth:
            logger.warning("⚠️ Aucun cookie d'authentification YouTube détecté (SAPISID / SID). Les cookies sont peut-être expirés.")

        YT_COOKIES_FILE.write_text(fixed, encoding="utf-8")
        lines = [l for l in fixed.splitlines() if l.strip() and not l.startswith("#")]
        logger.info(f"✅ Cookies YouTube chargés ({len(lines)} entrées)")
    else:
        logger.warning("⚠️ Pas de cookies YouTube configurés — certaines vidéos seront inaccessibles")

def get_cookies_args() -> list:
    if YT_COOKIES_FILE.exists():
        return ["--cookies", str(YT_COOKIES_FILE)]
    return []

# ─── Sessions utilisateur ─────────────────────────────────────────────────────

# Structure : { user_id: { "url": str, "step": "quality"|"format" } }
sessions = {}

# ─── Détection YouTube ────────────────────────────────────────────────────────

def is_youtube(url: str) -> bool:
    return any(d in url for d in ("youtube.com", "youtu.be"))

# ─── Helpers yt-dlp ───────────────────────────────────────────────────────────

# Formats progressifs : essaie MP4 natif d'abord, puis webm/any, puis fallback total
QUALITY_FORMATS = {
    "Auto": (
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[ext=mp4]+bestaudio"
        "/bestvideo+bestaudio"
        "/best"
    ),
    "720p":  (
        "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=720][ext=mp4]+bestaudio"
        "/bestvideo[height<=720]+bestaudio"
        "/best[height<=720]"
        "/best"
    ),
    "1080p": (
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=1080][ext=mp4]+bestaudio"
        "/bestvideo[height<=1080]+bestaudio"
        "/best[height<=1080]"
        "/best"
    ),
    "4K": (
        "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=2160][ext=mp4]+bestaudio"
        "/bestvideo[height<=2160]+bestaudio"
        "/bestvideo+bestaudio"
        "/best"
    ),
}

# Formats vidéo seule (sans audio)
QUALITY_VIDEO_ONLY = {
    "Auto": "bestvideo[ext=mp4]/bestvideo",
    "720p":  "bestvideo[height<=720][ext=mp4]/bestvideo[height<=720]/bestvideo",
    "1080p": "bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]/bestvideo",
    "4K":    "bestvideo[height<=2160][ext=mp4]/bestvideo[height<=2160]/bestvideo",
}

class YtdlpError(Exception):
    """Erreur yt-dlp avec message utilisateur lisible."""
    def __init__(self, user_msg: str, technical: str = ""):
        self.user_msg = user_msg
        self.technical = technical
        super().__init__(user_msg)

def run_ytdlp(cmd: list, timeout=300) -> subprocess.CompletedProcess:
    """Lance yt-dlp et lève YtdlpError avec un message lisible en cas d'échec."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return result

        stderr = result.stderr
        logger.error(f"yt-dlp stderr: {stderr[:500]}")

        if "Sign in to confirm" in stderr or "cookies" in stderr.lower():
            raise YtdlpError(
                "🍪 *Cookies YouTube expirés ou invalides*\n\n"
                "YouTube demande une authentification.\n"
                "Le propriétaire du bot doit renouveler les cookies dans les variables d'environnement.",
                stderr
            )
        if "Private video" in stderr:
            raise YtdlpError("🔒 Cette vidéo est *privée* — impossible de la télécharger.", stderr)
        if "Video unavailable" in stderr or "This video is unavailable" in stderr:
            raise YtdlpError("❌ Vidéo *indisponible* dans ta région ou supprimée.", stderr)
        if "age-restricted" in stderr.lower() or "age restriction" in stderr.lower():
            raise YtdlpError("🔞 Vidéo avec *restriction d'âge* — des cookies valides sont nécessaires.", stderr)
        if "This live event will begin" in stderr:
            raise YtdlpError("⏳ Ce live n'a pas encore commencé.", stderr)

        if "Requested format is not available" in stderr or "not available" in stderr.lower():
            raise YtdlpError("__format_unavailable__", stderr)

        raise YtdlpError("❌ Échec du téléchargement. Réessaie ou vérifie le lien.", stderr)

    except subprocess.TimeoutExpired:
        raise YtdlpError("⏱️ Délai dépassé — la vidéo est peut-être trop longue ou la connexion trop lente.")
    except YtdlpError:
        raise
    except Exception as e:
        logger.error(f"yt-dlp exception: {e}")
        raise YtdlpError("❌ Erreur inattendue lors du téléchargement.")

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

# ─── Téléchargement ───────────────────────────────────────────────────────────

def _is_fatal_error(e: YtdlpError) -> bool:
    """Retourne True si l'erreur ne vaut pas la peine de retenter (cookies, accès, etc.)"""
    if e.user_msg == "__format_unavailable__":
        return False  # Format dispo → on retente avec fallback, silencieusement
    keywords = ["cookies", "privée", "indisponible", "restriction", "live", "valide"]
    return any(k in e.user_msg.lower() for k in keywords)

def dl_full_video(url: str, output_dir: Path, quality: str) -> Path:
    """Vidéo + audio fusionnés en un seul MP4. Lève YtdlpError en cas d'échec."""
    output_file = output_dir / "video_full.mp4"
    fmt = QUALITY_FORMATS.get(quality, QUALITY_FORMATS["1080p"])

    base_args = [
        "yt-dlp", "--no-playlist", "--no-warnings", "--no-check-formats",
        "--merge-output-format", "mp4",
        "-o", str(output_file),
    ] + get_cookies_args()

    # Tentative 1 : format qualité demandée
    try:
        run_ytdlp(base_args + ["-f", fmt] + [url])
        if output_file.exists():
            return output_file
    except YtdlpError as e:
        if _is_fatal_error(e):
            raise

    # Tentative 2 : meilleure qualité sans contrainte de codec (webm, mkv, etc.)
    try:
        run_ytdlp(base_args + ["-f", "bestvideo+bestaudio/best"] + [url])
        if output_file.exists():
            return output_file
    except YtdlpError as e:
        if _is_fatal_error(e):
            raise

    # Tentative 3 : format unique disponible (pas de merge)
    try:
        run_ytdlp(base_args + ["-f", "best"] + [url])
        if output_file.exists():
            return output_file
    except YtdlpError as e:
        if _is_fatal_error(e):
            raise

    # Tentative 4 : absolument n'importe quel format disponible
    run_ytdlp(["yt-dlp", "--no-playlist", "--no-warnings", "--no-check-formats",
               "-o", str(output_file)] + get_cookies_args() + [url])
    if output_file.exists():
        return output_file

    raise YtdlpError("❌ Impossible de télécharger cette vidéo.")

def dl_video_only(url: str, output_dir: Path, quality: str) -> Path:
    """Vidéo HD sans piste audio. Lève YtdlpError en cas d'échec."""
    output_file = output_dir / "video_only.mp4"
    fmt = QUALITY_VIDEO_ONLY.get(quality, QUALITY_VIDEO_ONLY["1080p"])

    base_args = [
        "yt-dlp", "--no-playlist", "--no-warnings", "--no-check-formats",
        "--merge-output-format", "mp4",
        "-o", str(output_file),
    ] + get_cookies_args()

    try:
        run_ytdlp(base_args + ["-f", fmt] + [url])
        if output_file.exists():
            return output_file
    except YtdlpError as e:
        if _is_fatal_error(e):
            raise

    # Fallback : n'importe quelle piste vidéo disponible
    run_ytdlp(base_args + ["-f", "bestvideo"] + [url])
    if output_file.exists():
        return output_file

    raise YtdlpError("❌ Impossible de télécharger la piste vidéo.")

def dl_audio_only(url: str, output_dir: Path) -> Path:
    """Audio MP3 haute qualité. Lève YtdlpError en cas d'échec."""
    output_mp3 = output_dir / "audio.mp3"
    cmd = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestaudio",
        "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
        "-o", str(output_mp3),
    ] + get_cookies_args() + [url]

    try:
        run_ytdlp(cmd)
        if output_mp3.exists():
            return output_mp3
    except YtdlpError as e:
        if "cookies" in e.user_msg.lower() or "privée" in e.user_msg or "indisponible" in e.user_msg:
            raise

    # Fallback m4a → mp3
    output_m4a = output_dir / "audio_raw.m4a"
    cmd2 = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-o", str(output_m4a),
    ] + get_cookies_args() + [url]

    run_ytdlp(cmd2)
    if output_m4a.exists():
        cmd_conv = [
            "ffmpeg", "-y", "-i", str(output_m4a),
            "-codec:a", "libmp3lame", "-qscale:a", "0", str(output_mp3)
        ]
        try:
            r = subprocess.run(cmd_conv, capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and output_mp3.exists():
                return output_mp3
        except Exception as e:
            logger.error(f"Conversion m4a→mp3 échouée : {e}")
        return output_m4a
    raise YtdlpError("❌ Impossible de télécharger l'audio.")

# ─── Envoi Telegram ───────────────────────────────────────────────────────────

MAX_SIZE_MB = 50

async def send_video(message, video_path: Path, caption: str):
    size_mb = video_path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_SIZE_MB:
        await message.reply_text(f"⚠️ Fichier trop volumineux ({size_mb:.1f} Mo > {MAX_SIZE_MB} Mo)")
        return
    with open(video_path, "rb") as f:
        await message.reply_video(
            video=f,
            caption=caption,
            parse_mode="Markdown",
            supports_streaming=True,
        )

async def send_audio(message, audio_path: Path, caption: str):
    size_mb = audio_path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_SIZE_MB:
        await message.reply_text(f"⚠️ Fichier trop volumineux ({size_mb:.1f} Mo > {MAX_SIZE_MB} Mo)")
        return
    with open(audio_path, "rb") as f:
        await message.reply_audio(audio=f, caption=caption, parse_mode="Markdown")

# ─── Handlers Telegram ────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *YouTube Downloader Bot*\n\n"
        "Envoie-moi un lien YouTube et je te propose :\n\n"
        "🎬 *Vidéo complète* — MP4 (vidéo + audio)\n"
        "🎥 *Vidéo HD seule* — MP4 sans audio\n"
        "🔊 *Audio seul* — MP3 haute qualité\n\n"
        "Tu pourras aussi choisir la qualité : *720p, 1080p ou 4K*",
        parse_mode="Markdown",
    )

async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not url.startswith("http"):
        await update.message.reply_text("❌ Envoie un lien valide commençant par `http`", parse_mode="Markdown")
        return

    if not is_youtube(url):
        await update.message.reply_text(
            "❌ *Lien non supporté*\n\nCe bot est optimisé pour *YouTube uniquement*.\nEnvoie un lien `youtube.com` ou `youtu.be`.",
            parse_mode="Markdown",
        )
        return

    user_id = update.message.from_user.id
    sessions[user_id] = {"url": url}

    keyboard = [
        [InlineKeyboardButton("🎬 Vidéo complète (MP4)", callback_data="fmt_full")],
        [InlineKeyboardButton("🎥 Vidéo HD seule (MP4, sans audio)", callback_data="fmt_video")],
        [InlineKeyboardButton("🔊 Audio seul (MP3)", callback_data="fmt_audio")],
    ]
    await update.message.reply_text(
        "🎬 *Lien YouTube détecté !*\n\nQue veux-tu télécharger ?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if user_id not in sessions:
        await query.edit_message_text("❌ Session expirée. Renvoie le lien.")
        return

    # ── Étape 1 : choix du format ────────────────────────────────────────────
    if data.startswith("fmt_"):
        fmt = data.replace("fmt_", "")
        sessions[user_id]["fmt"] = fmt

        # Audio : pas besoin de choisir la qualité
        if fmt == "audio":
            await _start_download(query, user_id)
            return

        # Vidéo : proposer la qualité
        keyboard = [
            [InlineKeyboardButton("⚡ Auto (meilleure dispo)", callback_data="q_Auto")],
            [InlineKeyboardButton("📱 720p (HD)", callback_data="q_720p")],
            [InlineKeyboardButton("🖥️ 1080p (Full HD)", callback_data="q_1080p")],
            [InlineKeyboardButton("🔥 4K (Ultra HD)", callback_data="q_4K")],
        ]
        labels = {"full": "Vidéo complète", "video": "Vidéo HD seule"}
        await query.edit_message_text(
            f"⚙️ *{labels.get(fmt)}* — Choisis la qualité :",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # ── Étape 2 : choix de la qualité ────────────────────────────────────────
    elif data.startswith("q_"):
        quality = data.replace("q_", "")
        sessions[user_id]["quality"] = quality
        await _start_download(query, user_id)

async def _start_download(query, user_id: int):
    """Lance le téléchargement une fois format + qualité choisis."""
    session = sessions.pop(user_id, None)
    if not session:
        await query.edit_message_text("❌ Session introuvable.")
        return

    url = session["url"]
    fmt = session["fmt"]
    quality = session.get("quality", "1080p")

    labels = {"full": "Vidéo complète MP4", "video": "Vidéo HD seule MP4", "audio": "Audio MP3"}
    quality_label = {"Auto": "Auto ⚡", "720p": "720p", "1080p": "1080p", "4K": "4K 🔥"}.get(quality, quality)
    await query.edit_message_text(f"⏳ Téléchargement *{labels[fmt]}*{' · ' + quality_label if fmt != 'audio' else ''}...", parse_mode="Markdown")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        message = query.message

        try:
            if fmt == "full":
                video = dl_full_video(url, tmp, quality)
                res = get_video_resolution(video)
                size_mb = video.stat().st_size / (1024 * 1024)
                await query.edit_message_text("📤 Envoi en cours...")
                await send_video(message, video, f"🎬 *Vidéo complète* · {res} · {size_mb:.1f} Mo")

            elif fmt == "video":
                video = dl_video_only(url, tmp, quality)
                res = get_video_resolution(video)
                size_mb = video.stat().st_size / (1024 * 1024)
                await query.edit_message_text("📤 Envoi en cours...")
                await send_video(message, video, f"🎥 *Vidéo HD seule* (sans audio) · {res} · {size_mb:.1f} Mo")

            elif fmt == "audio":
                audio = dl_audio_only(url, tmp)
                size_mb = audio.stat().st_size / (1024 * 1024)
                await query.edit_message_text("📤 Envoi en cours...")
                await send_audio(message, audio, f"🔊 *Audio MP3* · {size_mb:.1f} Mo")

            await query.delete_message()

        except YtdlpError as e:
            logger.error(f"YtdlpError: {e.technical[:200] if e.technical else str(e)}")
            await query.edit_message_text(e.user_msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Erreur inattendue: {e}")
            await query.edit_message_text("❌ Une erreur inattendue s'est produite. Réessaie.")

# ─── Main ──────────────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gère les erreurs globales — notamment les conflits d'instances multiples."""
    from telegram.error import Conflict, NetworkError, TimedOut
    err = context.error

    if isinstance(err, Conflict):
        logger.critical(
            "🚨 CONFLIT : Une autre instance du bot tourne déjà ! "
            "Arrête toutes les instances sauf une (Railway → redeploy unique)."
        )
        return  # Ne pas crasher, juste logguer

    if isinstance(err, TimedOut):
        logger.warning("⏱️ Timeout réseau Telegram — retente automatiquement.")
        return

    if isinstance(err, NetworkError):
        logger.warning(f"🌐 Erreur réseau : {err}")
        return

    logger.error(f"Erreur non gérée: {err}", exc_info=context.error)


def main():
    setup_cookies()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(120)
        .write_timeout(120)
        .pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(handle_choice))
    app.add_error_handler(error_handler)
    logger.info("🤖 YouTube Downloader Bot démarré !")
    app.run_polling(
        poll_interval=1.0,
        stop_signals=None,
        drop_pending_updates=True,   # Ignore les updates en attente au démarrage
        allowed_updates=["message", "callback_query"],
    )

if __name__ == "__main__":
    main()
