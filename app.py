# ============================================================
#  SOMRAT Downloader  |  Power by SOMRAT
#  Professional video/audio downloader - Flask + yt-dlp
# ============================================================
import os
import uuid
import shutil
import requests
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

BASE = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_FOLDER = os.path.join(BASE, "downloads")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    # winget install location fallback
    for pat in (
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg*\bin\ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ):
        hits = __import__("glob").glob(pat)
        if hits:
            os.environ["PATH"] = os.path.dirname(hits[0]) + os.pathsep + os.environ.get("PATH", "")
            return hits[0]
    return None


FFMPEG = find_ffmpeg()
HAS_FFMPEG = FFMPEG is not None

YDL_BASE = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "socket_timeout": 20,
}


def human_size(n):
    if not n:
        return ""
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------- Home page ----------------
@app.route("/")
def home():
    return send_file(os.path.join(BASE, "index.html"))


# ---------------- Video info + all formats ----------------
@app.route("/info", methods=["POST"])
def info():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "URL দিন"}), 400

    try:
        with yt_dlp.YoutubeDL(dict(YDL_BASE)) as ydl:
            data = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "Unsupported URL" in msg:
            msg = "এই লিংকটি সাপোর্টেড নয় — সঠিক ভিডিও লিংক দিন"
        elif "Sign in" in msg or "login" in msg.lower():
            msg = "এই ভিডিওটি প্রাইভেট/লগইন লাগে — ডাউনলোড করা যাচ্ছে না"
        elif "private" in msg.lower():
            msg = "ভিডিওটি প্রাইভেট"
        return jsonify({"error": msg[:300]}), 500
    except Exception as e:
        return jsonify({"error": f"ভিডিওর তথ্য আনা যায়নি: {str(e)[:200]}"}), 500

    videos, audios = [], []
    for f in data.get("formats", []):
        furl = f.get("url")
        if not furl:
            continue
        vc, ac = f.get("vcodec"), f.get("acodec")
        if vc and vc != "none":
            label = f"{f.get('height')}p" if f.get("height") else (f.get("format_note") or f.get("format_id"))
            videos.append({
                "format_id": f.get("format_id"),
                "label": label,
                "ext": f.get("ext"),
                "url": furl,
                "size": human_size(f.get("filesize") or f.get("filesize_approx")),
                "progressive": bool(ac and ac != "none"),
                "height": f.get("height") or 0,
            })
        elif ac and ac != "none":
            audios.append({
                "format_id": f.get("format_id"),
                "label": f.get("format_note") or str(f.get("ext", "audio")).upper(),
                "ext": f.get("ext"),
                "url": furl,
                "size": human_size(f.get("filesize") or f.get("filesize_approx")),
                "abr": f.get("abr") or 0,
            })

    videos.sort(key=lambda x: x["height"])
    audios.sort(key=lambda a: a["abr"])

    # best playable (video+audio in one file); fallback: best video-only
    play = None
    for v in reversed(videos):
        if v["progressive"]:
            play = v
            break
    if not play and videos:
        play = videos[-1]

    return jsonify({
        "title": data.get("title"),
        "uploader": data.get("uploader") or data.get("channel"),
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnail"),
        "videos": videos[-10:],
        "audios": audios[-4:],
        "play": play,
        "play_has_audio": bool(play and play["progressive"]),
        "has_ffmpeg": HAS_FFMPEG,
    })


# ---------------- Media proxy (playback in browser) ----------------
# Direct URLs (googlevideo etc.) are IP-bound, so the browser must
# stream them THROUGH this server, otherwise 403.
@app.route("/stream")
def stream():
    url = request.args.get("url", "")
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "bad url"}), 400

    upstream_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    if rng := request.headers.get("Range"):
        upstream_headers["Range"] = rng

    try:
        upstream = requests.get(url, headers=upstream_headers, stream=True, timeout=30)
    except Exception as e:
        return jsonify({"error": f"স্ট্রিম আনা যায়নি: {str(e)[:150]}"}), 502

    resp = Response(
        upstream.iter_content(chunk_size=64 * 1024),
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "video/mp4"),
    )
    for h in ("Content-Range", "Accept-Ranges", "Content-Length"):
        if h in upstream.headers:
            resp.headers[h] = upstream.headers[h]
    return resp


# ---------------- Download to server folder ----------------
@app.route("/download", methods=["POST"])
def download():
    body = request.json or {}
    url = body.get("url", "").strip()
    fid = body.get("format_id")  # optional: chosen format
    if not url:
        return jsonify({"error": "URL দিন"}), 400

    job = uuid.uuid4().hex[:8]
    if fid:
        spec = f"{fid}+bestaudio/{fid}/best" if HAS_FFMPEG else f"{fid}/best"
    else:
        spec = "best"

    opts = dict(YDL_BASE)
    opts.update({
        "outtmpl": os.path.join(DOWNLOAD_FOLDER, f"{job}.%(ext)s"),
        "format": spec,
    })
    if HAS_FFMPEG:
        opts["merge_output_format"] = "mp4"
        opts["ffmpeg_location"] = FFMPEG

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(data)
    except Exception as e:
        return jsonify({"error": f"ডাউনলোড ব্যর্থ: {str(e)[:250]}"}), 500

    return jsonify({
        "file": os.path.basename(filepath),
        "title": data.get("title"),
        "folder": DOWNLOAD_FOLDER,
    })


# ---------------- Serve a downloaded file ----------------
@app.route("/file/<name>")
def file(name):
    path = os.path.join(DOWNLOAD_FOLDER, os.path.basename(name))
    if not os.path.isfile(path):
        return jsonify({"error": "ফাইল পাওয়া যায়নি"}), 404
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    print("=" * 46)
    print("   SOMRAT Downloader  |  Power by SOMRAT")
    print("   Browser e khulo ->  http://127.0.0.1:5000")
    print("=" * 46)
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", 5000)),
    )
