import os
import subprocess
import threading
import uuid
import base64
from pathlib import Path
from flask import Flask, request, jsonify, render_template, Response, send_file
import queue
import json
import re

app = Flask(__name__, template_folder='../templates')

UPLOAD_DIR = Path('/tmp/uploads')
OUTPUT_DIR = Path(os.environ.get('OUTPUT_DIR', '/output'))
ACTIVATION_BYTES = os.environ.get('ACTIVATION_BYTES', '')

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# job_id -> queue of log lines
job_logs    = {}
# job_id -> output file path (for browser download)
job_outputs = {}


def sanitize(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or 'Unknown'


def get_metadata(filepath: str, activation_bytes: str) -> dict:
    """Extract tags + embedded cover from the AAX file."""
    result = subprocess.run(
        [
            'ffprobe', '-v', 'quiet',
            '-activation_bytes', activation_bytes,
            '-print_format', 'json',
            '-show_format', '-show_streams',
            filepath
        ],
        capture_output=True, text=True, timeout=30
    )
    try:
        data = json.loads(result.stdout)
    except Exception:
        return {}

    tags = data.get('format', {}).get('tags', {})
    meta = {
        'title':    tags.get('title')        or tags.get('album') or '',
        'author':   tags.get('artist')       or tags.get('author') or tags.get('album_artist') or '',
        'narrator': tags.get('composer')     or '',
        'duration': tags.get('duration')     or '',
        'year':     tags.get('date', '')[:4] or '',
    }

    # Duration from format block
    dur_sec = float(data.get('format', {}).get('duration', 0) or 0)
    if dur_sec:
        h, rem = divmod(int(dur_sec), 3600)
        m, s   = divmod(rem, 60)
        meta['duration'] = f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"

    # Extract cover to base64
    try:
        cover_proc = subprocess.run(
            [
                'ffmpeg', '-v', 'quiet',
                '-activation_bytes', activation_bytes,
                '-i', filepath,
                '-an', '-vcodec', 'copy',
                '-f', 'image2', 'pipe:1'
            ],
            capture_output=True, timeout=20
        )
        if cover_proc.returncode == 0 and cover_proc.stdout:
            meta['cover'] = 'data:image/jpeg;base64,' + base64.b64encode(cover_proc.stdout).decode()
    except Exception:
        pass

    return meta


def convert_job(job_id: str, aax_path: str, stem: str, fmt: str, destination: str):
    log_queue = job_logs[job_id]

    def emit(line: str):
        log_queue.put(line)

    try:
        if not ACTIVATION_BYTES:
            emit("[error] ACTIVATION_BYTES nicht konfiguriert. Bitte in der .env Datei setzen.")
            return

        # ── Metadata & output path ────────────────────────────────────────
        emit(f"[info] Lese Metadaten …")
        meta   = get_metadata(aax_path, ACTIVATION_BYTES)
        author = sanitize(meta.get('author') or 'Unknown Author')
        title  = sanitize(meta.get('title')  or stem)
        emit(f"[info] Autor: {author}")
        emit(f"[info] Titel: {title}")

        if destination == 'download':
            out_dir = UPLOAD_DIR / job_id
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            folder_name = f"{author} - {title}"
            out_dir = OUTPUT_DIR / folder_name
            if out_dir.exists():
                emit(f"[error] Zielordner existiert bereits: {out_dir}")
                emit(f"[error] Bitte vorhandene Dateien prüfen oder Ordner umbenennen.")
                return
            out_dir.mkdir(parents=True, exist_ok=True)
            emit(f"[info] Ordner erstellt: {folder_name}")

        out_path = out_dir / f"{stem}.{fmt}"
        emit(f"[info] Zieldatei: {out_path}")

        # ── Build ffmpeg command ──────────────────────────────────────────
        if fmt == 'm4b':
            # Fast: remux only, no re-encode
            codec_args = ['-map', '0:a', '-codec:a', 'copy', '-map_metadata', '0']
        else:
            # MP3: decode audio stream only, re-encode
            # -map 0:a  → skip video/cover streams (avoids stalling on cover stream)
            codec_args = ['-map', '0:a', '-codec:a', 'libmp3lame', '-q:a', '2', '-map_metadata', '0']

        cmd = [
            'ffmpeg', '-v', 'info', '-stats', '-y',
            '-activation_bytes', ACTIVATION_BYTES,
            '-i', aax_path,
            *codec_args,
            str(out_path)
        ]

        emit(f"[info] Starte Konvertierung ({fmt.upper()}) …")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        for line in process.stdout:
            line = line.rstrip()
            if line:
                emit(f"[ffmpeg] {line}")

        process.wait()

        if process.returncode == 0:
            emit(f"[success] Konvertierung abgeschlossen: {out_path}")
            if destination == 'download':
                job_outputs[job_id] = str(out_path)
                emit(f"[download] {stem}.{fmt}")
        else:
            emit(f"[error] ffmpeg beendet mit Code {process.returncode}")

    except Exception as e:
        emit(f"[error] Unerwarteter Fehler: {e}")
    finally:
        try:
            os.remove(aax_path)
        except Exception:
            pass
        log_queue.put(None)  # sentinel


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/metadata', methods=['POST'])
def metadata():
    """Extract and return metadata + cover from uploaded AAX without converting."""
    if 'file' not in request.files:
        return jsonify({'error': 'Keine Datei'}), 400
    if not ACTIVATION_BYTES:
        return jsonify({'error': 'ACTIVATION_BYTES nicht konfiguriert'}), 500

    f    = request.files['file']
    stem = sanitize(Path(f.filename).stem)
    tmp  = str(UPLOAD_DIR / f"meta_{uuid.uuid4()}.aax")
    f.save(tmp)

    try:
        meta = get_metadata(tmp, ACTIVATION_BYTES)
        meta['stem'] = stem
        return jsonify(meta)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


@app.route('/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({'error': 'Keine Datei übermittelt'}), 400

    f = request.files['file']
    if not f.filename.lower().endswith('.aax'):
        return jsonify({'error': 'Nur .aax Dateien werden akzeptiert'}), 400
    if not ACTIVATION_BYTES:
        return jsonify({'error': 'ACTIVATION_BYTES nicht in .env konfiguriert'}), 500

    fmt         = request.form.get('format', 'm4b').lower()
    destination = request.form.get('destination', 'server').lower()

    if fmt not in ('m4b', 'mp3'):
        return jsonify({'error': 'Ungültiges Format'}), 400

    stem     = sanitize(Path(f.filename).stem)
    job_id   = str(uuid.uuid4())
    tmp_path = str(UPLOAD_DIR / f"{job_id}.aax")
    f.save(tmp_path)

    log_queue: queue.Queue = queue.Queue()
    job_logs[job_id] = log_queue

    threading.Thread(
        target=convert_job,
        args=(job_id, tmp_path, stem, fmt, destination),
        daemon=True
    ).start()

    return jsonify({'job_id': job_id})


@app.route('/stream/<job_id>')
def stream(job_id: str):
    if job_id not in job_logs:
        return jsonify({'error': 'Job nicht gefunden'}), 404

    def generate():
        q = job_logs[job_id]
        while True:
            line = q.get()
            if line is None:
                yield "data: __done__\n\n"
                break
            yield f"data: {line}\n\n"
        job_logs.pop(job_id, None)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/download/<job_id>')
def download(job_id: str):
    path = job_outputs.pop(job_id, None)
    if not path or not Path(path).exists():
        return jsonify({'error': 'Datei nicht gefunden oder bereits heruntergeladen'}), 404

    filename = Path(path).name

    def cleanup():
        import time; time.sleep(10)
        try:
            os.remove(path)
            Path(path).parent.rmdir()
        except Exception:
            pass

    threading.Thread(target=cleanup, daemon=True).start()
    return send_file(path, as_attachment=True, download_name=filename)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
