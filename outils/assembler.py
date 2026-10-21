"""Assemble site/data/videos.json : une fiche écrite à la main (outils/fiches/<id>.json)
+ la durée et la transcription produites par preparer.py (travail/<id>/).

Lancer : python3 outils/assembler.py
Ordre d'affichage : le champ « ordre » de la fiche (petit = en premier, 50 par défaut), puis l'id.
"""
import json, pathlib

ICI = pathlib.Path(__file__).resolve().parents[1]
FICHES, TRAVAIL = ICI / "outils" / "fiches", ICI / "travail"
SORTIE = ICI / "site" / "data" / "videos.json"
# Phrases que Whisper invente sur les silences et les musiques.
PARASITES = ("sous-titrage", "radio-canada", "abonnez-vous", "merci d'avoir regardé", "amara.org")

def transcription(t):
    f = t / "transcription.json"
    if not f.exists():
        return []
    lignes, precedente = [], None
    for l in json.loads(f.read_text(encoding="utf-8")):
        texte = l["texte"].strip()
        if len(texte) < 2 or any(p in texte.lower() for p in PARASITES) or texte == precedente:
            continue
        lignes.append({"debut": l["debut"], "fin": l["fin"], "texte": texte})
        precedente = texte
    return lignes

videos = []
for f in sorted(FICHES.glob("*.json")):
    v = json.loads(f.read_text(encoding="utf-8"))
    t = TRAVAIL / v["id"]
    if (t / "infos.json").exists():
        v["duree"] = json.loads((t / "infos.json").read_text(encoding="utf-8"))["duree"]
    if v.get("type") != "carrousel":
        v["transcription"] = transcription(t)
    videos.append(v)
videos.sort(key=lambda v: (v.get("ordre", 50), v["id"]))
SORTIE.write_text(json.dumps(videos, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{len(videos)} fiches → {SORTIE.relative_to(ICI)}")
