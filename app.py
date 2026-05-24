import os
import uuid
import subprocess
from flask import Flask, render_template, request, send_file, jsonify
from gtts import gTTS

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

ALLOWED      = {".mp3", ".mp4", ".mov", ".m4a"}
PREVIEW_SECS = 75          # 1 minuto e 15 segundos

# Marcações fixas: (segundo, texto)
PLACEMENTS = [
    (25.0, "Essa é uma prévia"),
    (50.0, "pagamento pendente"),
    (70.0, "Essa é uma prévia, para ter ela completa faça o pagamento"),
]


def make_voice(text, path):
    tts = gTTS(text=text, lang="pt-br", slow=False)
    tts.save(path)


def process(input_path, output_path, ext, remove_watermark=False):
    uid = uuid.uuid4().hex[:6]

    # Gera os 3 arquivos de voz
    voice_paths = []
    for i, (_, text) in enumerate(PLACEMENTS):
        p = os.path.join(UPLOAD_DIR, f"{uid}_v{i}.mp3")
        make_voice(text, p)
        voice_paths.append(p)

    # Inputs ffmpeg: vídeo/áudio original + 3 vozes
    inputs = ["-i", input_path]
    for vp in voice_paths:
        inputs += ["-i", vp]

    # Filtros de áudio: cada voz com delay, mistura sem alterar original
    parts  = []
    labels = []
    for i, (t, _) in enumerate(PLACEMENTS):
        delay_ms = int(t * 1000)
        lbl = f"[vx{i}]"
        parts.append(f"[{i+1}:a]volume=2.2,adelay={delay_ms}|{delay_ms}{lbl}")
        labels.append(lbl)

    n = len(PLACEMENTS) + 1
    parts.append(f"[0:a]{''.join(labels)}amix=inputs={n}:duration=first:normalize=0[aout]")

    is_video = ext in {".mp4", ".mov"}

    if is_video and remove_watermark:
        parts += [
            "[0:v]split=2[vmain][vblur]",
            "[vblur]crop=iw:ih*0.28:0:ih*0.72,boxblur=luma_radius=18:luma_power=3[vblurred]",
            "[vmain][vblurred]overlay=0:main_h*0.72[vout]",
        ]
        fc = ";".join(parts)
        cmd = (
            ["ffmpeg", "-y"]
            + inputs
            + ["-filter_complex", fc,
               "-map", "[vout]", "-map", "[aout]",
               "-c:v", "libx264", "-crf", "22", "-preset", "ultrafast",
               "-c:a", "aac", "-b:a", "192k",
               "-t", str(PREVIEW_SECS),
               output_path]
        )
    elif is_video:
        fc = ";".join(parts)
        cmd = (
            ["ffmpeg", "-y"]
            + inputs
            + ["-filter_complex", fc,
               "-map", "0:v", "-map", "[aout]",
               "-c:v", "copy",
               "-c:a", "aac", "-b:a", "192k",
               "-t", str(PREVIEW_SECS),
               output_path]
        )
    else:
        fc = ";".join(parts)
        cmd = (
            ["ffmpeg", "-y"]
            + inputs
            + ["-filter_complex", fc,
               "-map", "[aout]",
               "-q:a", "2",
               "-t", str(PREVIEW_SECS),
               output_path]
        )

    r = subprocess.run(cmd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")

    for p in voice_paths:
        if os.path.exists(p):
            os.remove(p)

    stderr = r.stderr or ""
    return r.returncode == 0, stderr[-2000:]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process_route():
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Nome de arquivo inválido"}), 400

    name, ext = os.path.splitext(f.filename)
    ext = ext.lower()
    if ext not in ALLOWED:
        return jsonify({"error": f"Formato '{ext}' não suportado. Use MP3 ou MP4."}), 400

    uid     = uuid.uuid4().hex[:10]
    in_path  = os.path.join(UPLOAD_DIR, f"{uid}{ext}")
    out_path = os.path.join(OUTPUT_DIR,  f"{uid}_preview{ext}")

    f.save(in_path)

    remove_wm = request.form.get("remove_watermark") == "true"
    ok, err   = process(in_path, out_path, ext, remove_watermark=remove_wm)

    if os.path.exists(in_path):
        os.remove(in_path)

    if ok:
        out_name = f"{name}_preview{ext}"
        return jsonify({"uid": uid, "filename": out_name, "ext": ext})
    else:
        return jsonify({"error": f"Erro no processamento: {err}"}), 500


@app.route("/download/<uid>")
def download(uid):
    for fname in os.listdir(OUTPUT_DIR):
        if fname.startswith(uid):
            fpath = os.path.join(OUTPUT_DIR, fname)
            dname = request.args.get("name", fname)
            resp  = send_file(fpath, as_attachment=True, download_name=dname)

            @resp.call_on_close
            def _cleanup():
                try:
                    os.remove(fpath)
                except OSError:
                    pass
            return resp

    return jsonify({"error": "Arquivo não encontrado"}), 404


if __name__ == "__main__":
    print("Acesse: http://localhost:5000")
    app.run(debug=False, port=5000)
