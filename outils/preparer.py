"""Prépare les vidéos d'exemples pour la vidéothèque publique (site/).

Source : FICHIERS ANNEXES/VIDÉOS D'EXEMPLES. Pour chaque vidéo absente de site/data/videos.json
(ou toutes avec --tout) :
  - site/videos/<id>.mp4 : version web (H.264, 576 px de large, lecture immédiate) ;
  - site/posters/<id>.jpg : l'image d'aperçu (à 1 s) ;
  - travail/<id>/hook.jpg : les 5 premières secondes, une image toutes les 0,5 s (le hook) ;
  - travail/<id>/planche.jpg : toute la vidéo en 30 images maximum (le déroulé) ;
  - travail/<id>/transcription.json : ce qui est dit, avec le temps.
Lancer avec le Python de la vidéothèque Notion (il a ffmpeg et faster-whisper) :
  "../../SCREEN MY CAR/CRÉATEURS/Briefs revclip/Vidéothèque/.venv/bin/python" outils/preparer.py
"""
import json, pathlib, re, subprocess, sys
import imageio_ffmpeg

ICI = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ICI.parents[1] / "FICHIERS ANNEXES" / "VIDÉOS D'EXEMPLES"
SITE, TRAVAIL = ICI / "site", ICI / "travail"
FF = imageio_ffmpeg.get_ffmpeg_exe()

def ident(f):
    m = re.search(r"gc000[01](\w{7})", f.stem)
    if m:
        return m.group(1)
    m = re.search(r"tiktok-(\d{15,20})", f.stem)  # téléchargée par scruter.py : « t » + les 8 derniers chiffres de l'id TikTok
    if m:
        return "t" + m.group(1)[-8:]
    m = re.search(r"(\d\d)-(\d\d)-\d\d_\d", f.stem)
    return f"ecran-{m.group(1)}{m.group(2)}" if m else re.sub(r"\W+", "-", f.stem.lower())

def duree(f):
    sortie = subprocess.run([FF, "-i", str(f)], capture_output=True, text=True).stderr
    h, m, s = sortie.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

def ff(*args):
    subprocess.run([FF, "-v", "error", "-y", *map(str, args)], check=True)

def preparer(f, modele):
    i, d = ident(f), duree(f)
    t = TRAVAIL / i
    t.mkdir(parents=True, exist_ok=True)
    for dossier in ("videos", "posters"):
        (SITE / dossier).mkdir(parents=True, exist_ok=True)
    ff("-i", f, "-vf", "scale=576:-2,fps=30", "-c:v", "libx264", "-preset", "slow", "-crf", 27,
       "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
       SITE / "videos" / f"{i}.mp4")
    ff("-ss", min(1.0, d / 3), "-i", f, "-vf", "scale=576:-2", "-frames:v", 1, "-q:v", 4, SITE / "posters" / f"{i}.jpg")
    ff("-t", 5, "-i", f, "-vf", "fps=2,scale=320:-2,tile=5x2", "-frames:v", 1, t / "hook.jpg")
    pas = max(2.0, d / 30)
    ff("-i", f, "-vf", f"fps=1/{pas:.2f},scale=240:-2,tile=6x5", "-frames:v", 1, t / "planche.jpg")
    ff("-i", f, "-ac", 1, "-ar", 16000, t / "audio.wav")
    segs, _ = modele.transcribe(str(t / "audio.wav"), language="fr", vad_filter=True)
    texte = [{"debut": round(s.start, 1), "fin": round(s.end, 1), "texte": s.text.strip()} for s in segs]
    (t / "transcription.json").write_text(json.dumps(texte, ensure_ascii=False, indent=1), encoding="utf-8")
    (t / "infos.json").write_text(json.dumps({"id": i, "fichier": f.name, "duree": round(d, 1), "pas_planche": round(pas, 2)}, ensure_ascii=False), encoding="utf-8")
    (t / "audio.wav").unlink()
    taille = (SITE / "videos" / f"{i}.mp4").stat().st_size / 1024 / 1024
    print(f"✓ {i} ({f.name}) : {d:.0f} s, {taille:.1f} Mo, {len(texte)} phrases", flush=True)

if __name__ == "__main__":
    donnees = SITE / "data" / "videos.json"
    connues = {v["fichier"] for v in json.loads(donnees.read_text(encoding="utf-8"))} if donnees.exists() else set()
    videos = sorted(p for p in SOURCE.iterdir() if p.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"})
    nouvelles = videos if "--tout" in sys.argv else [v for v in videos if v.name not in connues]
    if not nouvelles:
        print("Aucune nouvelle vidéo.")
        sys.exit(0)
    from faster_whisper import WhisperModel
    modele = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", cpu_threads=8)
    for v in nouvelles:
        preparer(v, modele)
