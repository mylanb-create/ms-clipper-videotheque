"""La chaîne automatique : une vidéo approuvée dans la revue arrive toute seule dans la vidéothèque.

Pour chaque vidéo approuvée et téléchargée (FICHIERS ANNEXES/VIDÉOS D'EXEMPLES/tiktok-<id>-….mp4) :
  1. préparation : preparer.py --seul <fichier> (version web, image d'aperçu, images clés, sous-titres) ;
  2. analyse : Claude (le CLI « claude », avec l'abonnement de Mylan) regarde les images clés et lit les sous-titres, puis
     écrit la fiche (format, hooks, chronologie, chez nous…) ; la fiche est vérifiée ici avant d'être enregistrée ;
  3. mise en ligne : assembler.py, puis commit et push → GitHub Pages publie en 1 à 2 minutes.
L'état de chaque vidéo est gardé dans le registre (travail/candidats/vus.json), champ « chaine » :
{"etape": "attente" | "preparation" | "analyse" | "publication" | "fini", "erreur": …, "fiche": …}.
Utilisé par scruter.py (page de revue) ; à la main : python outils/chaine.py <id TikTok>.
"""
from __future__ import annotations

import json
import os
import pathlib
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback

ICI = pathlib.Path(__file__).resolve().parents[1]
FICHES, TRAVAIL, SITE = ICI / "outils" / "fiches", ICI / "travail", ICI / "site"
REFERENTIEL = SITE / "data" / "referentiel.json"
EXEMPLE = FICHES / "db8h1un.json"  # une fiche complète, montrée à Claude comme modèle
ETAPES = ("attente", "preparation", "analyse", "publication", "fini")

_file: "queue.Queue[str]" = queue.Queue()
_dans_la_file: set[str] = set()
_lance = False
_contexte: dict = {}


def id_fiche(ident: str) -> str:
    """Même règle que preparer.ident pour un fichier tiktok-<id>-… : « t » + les 8 derniers chiffres."""
    return "t" + ident[-8:]


def demarrer(registre, verrou, sauver, infos_de):
    """Branche la chaîne sur le registre de scruter.py. infos_de(ident) → la vidéo telle que la revue la voit (chiffres…)."""
    global _lance
    _contexte.update(registre=registre, verrou=verrou, sauver=sauver, infos_de=infos_de)
    if not _lance:
        threading.Thread(target=_travailleur, daemon=True).start()
        _lance = True


def a_faire(r: dict) -> bool:
    """Approuvée, téléchargée par l'outil, pas encore dans la vidéothèque, pas en erreur."""
    nom = r.get("fichier") or ""
    ch = r.get("chaine") or {}
    return (r.get("statut") == "telecharge" and nom.startswith("tiktok-") and not ch.get("erreur")
            and ch.get("etape") != "fini" and not _deja_fiche(nom))


def _deja_fiche(nom: str) -> bool:
    for f in FICHES.glob("*.json"):
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("fichier") == nom:
                return True
        except ValueError:
            pass
    return False


def ajouter(ident: str):
    """Met une vidéo dans la file (une seule à la fois est traitée)."""
    reg, verrou = _contexte["registre"], _contexte["verrou"]
    with verrou:
        if ident in _dans_la_file:
            return
        r = reg.setdefault(ident, {})
        r["chaine"] = {"etape": "attente"}
        _contexte["sauver"](reg)
        _dans_la_file.add(ident)
    _file.put(ident)


def _etape(ident, etape, **plus):
    reg, verrou = _contexte["registre"], _contexte["verrou"]
    with verrou:
        r = reg.setdefault(ident, {})
        r["chaine"] = {"etape": etape, **plus}
        _contexte["sauver"](reg)


def _travailleur():
    while True:
        ident = _file.get()
        etape = "preparation"
        try:
            r = _contexte["registre"].get(ident, {})
            nom = r.get("fichier")
            if not nom:
                raise RuntimeError("pas de fichier téléchargé")
            _etape(ident, "preparation")
            fid = id_fiche(ident)
            if not ((TRAVAIL / fid / "transcription.json").exists() and (SITE / "videos" / f"{fid}.mp4").exists()):
                preparer(nom)  # déjà préparée (une relance après une erreur) : on ne refait pas
            etape = "analyse"
            _etape(ident, "analyse")
            infos = _contexte["infos_de"](ident) or {}
            fiche = analyser(ident, nom, {**r, **infos})
            etape = "publication"
            _etape(ident, "publication", fiche=fiche["id"])
            publier(fiche)
            _etape(ident, "fini", fiche=fiche["id"])
            print(f"✓ dans la vidéothèque : {fiche['titre']} (fiche {fiche['id']})", flush=True)
        except Exception as e:  # on garde la raison pour la page de revue, et on passe à la suivante
            traceback.print_exc()
            _etape(ident, etape, erreur=(str(e).strip().splitlines() or [type(e).__name__])[-1][:300])
        finally:
            with _contexte["verrou"]:
                _dans_la_file.discard(ident)


# ---------- 1. préparation ----------

def preparer(nom: str):
    p = subprocess.run([sys.executable, str(ICI / "outils" / "preparer.py"), "--seul", nom], cwd=ICI,
                       capture_output=True, text=True, timeout=3600)
    if p.returncode:
        raise RuntimeError("préparation : " + (p.stderr.strip().splitlines() or ["échec"])[-1])


# ---------- 2. analyse par Claude ----------

def _claude():
    for c in (shutil.which("claude"), "/usr/local/bin/claude", str(pathlib.Path.home() / ".local/bin/claude"),
              str(pathlib.Path.home() / ".claude/local/claude")):
        if c and pathlib.Path(c).exists():
            return c
    raise RuntimeError("le programme « claude » (Claude Code) est introuvable sur ce Mac")


def consigne(fid: str, ident: str, infos: dict) -> str:
    ref = json.loads(REFERENTIEL.read_text(encoding="utf-8"))
    exemple = json.loads(EXEMPLE.read_text(encoding="utf-8"))
    for cle in ("id", "fichier", "compte", "plateforme", "lien", "marche", "ordre", "transcription", "duree"):
        exemple.pop(cle, None)
    t = TRAVAIL / fid
    duree = json.loads((t / "infos.json").read_text(encoding="utf-8"))
    sous_titres = json.loads((t / "transcription.json").read_text(encoding="utf-8"))
    texte = "\n".join(f"[{l['debut']:>6.1f} s] {l['texte']}" for l in sous_titres) or "(aucune parole : musique seulement)"
    marche = infos.get("marche") if infos.get("marche") in ("FR", "US") else "FR"
    chiffres = {k: infos.get(k) for k in ("compte", "nom", "abonnes", "vues", "likes", "partages", "enregistrements",
                                          "commentaires", "legende", "hashtags", "lien") if infos.get(k) not in (None, "", [])}
    formats = {k: {"nom": f["nom"], "def": f["def"], "reconnaitre": f.get("reconnaitre", "")} for k, f in ref["formats"].items()}
    hooks = {k: h["def"] for k, h in ref["hooks"].items()}
    apps = {k: a["desc"] for k, a in ref["apps"].items()}
    return f"""Tu analyses une vidéo TikTok qui a marché, pour la vidéothèque MS Clipper : des créateurs français la regardent pour comprendre comment elle est faite et la refaire avec nos apps.

LA VIDÉO
- Durée : {duree['duree']} s. Marché : {marche} ({'vidéo en français' if marche == 'FR' else 'vidéo en anglais, US'}).
- Ce que TikTok dit d'elle : {json.dumps(chiffres, ensure_ascii=False)}
- Les images : lis avec l'outil Read ces deux fichiers (chemins relatifs au dossier courant) :
  • travail/{fid}/hook.jpg : les 5 premières secondes, une image toutes les 0,5 s (10 images, de gauche à droite puis ligne du bas) ;
  • travail/{fid}/planche.jpg : toute la vidéo, une image toutes les {duree['pas_planche']} s (6 par ligne, de gauche à droite, ligne par ligne).
- Tout ce qui est dit (sous-titres automatiques, quelques mots peuvent être mal entendus) :
{texte}

LES RÈGLES DE CLASSEMENT (fixées par Mylan, à respecter)
- Toute vidéo qui contient une autre vidéo, ou l'écran / la vidéo de quelqu'un d'autre repris dedans (créateur en incrustation, réaction, vidéo virale en fond) = format "fond-vert" avec "reprise": true et "reprise_quoi" qui dit ce qui est repris. Jamais "duo" ni "troisieme-personne" dans ce cas.
- Une tête choquée, une grimace ou un filtre déformant en ouverture, avant la découverte de l'outil = "choque" (pas "flow").
- Une liste d'apps (« 3 apps… »), en vidéo ou en photos = "top-apps".
- "flow" = l'outil en action, téléphone ou écran filmé à la main presque toute la vidéo ; "face-camera" = la personne parle seule à la caméra.
- Formats possibles : {json.dumps(formats, ensure_ascii=False)}
- Types de hooks possibles (clés de "hooks", 1 à 3) : {json.dumps(hooks, ensure_ascii=False)}
- Apps pour « à refaire » (clés de "refaire[].app", 2 ou 3) : {json.dumps(apps, ensure_ascii=False)}

LES RÈGLES D'ÉCRITURE
- En français, pour quelqu'un de 8 ans : phrases courtes, pas de jargon. Ne rien inventer : seulement ce qu'on voit et entend.
- "hook.dit" : les mots exacts des 3 premières secondes{' (en anglais, suivis de la traduction française entre parenthèses)' if marche == 'US' else ''} ; s'il n'y a pas de parole, l'écrire entre parenthèses. "texte_ecran" : les textes écrits sur la vidéo, tels quels.
- "chrono" : les secondes exactes ("t", nombre ou null si ça n'arrive jamais) et "quoi" pour : hook, probleme, outil_nomme, outil_montre, preuve, fin.
- "etapes" : 4 à 7 étapes qui couvrent toute la vidéo, avec "debut" et "fin" en secondes, "voit" et "entend".
- "pourquoi" : 5 à 7 ingrédients {{"emoji", "titre", "texte"}}.
- "chez_nous" : 3 à 5 points {{"ok": "oui" | "non" | "bof", "texte"}} comparés à nos règles : le résultat d'abord ; l'outil nommé avant 20 s (« j'ai fait un Screen My Car », jamais « je te présente X, c'est un site qui… ») ; le vrai écran montré (téléphone en main ou incrustation en bas à gauche, jamais une capture plein écran du début à la fin) ; 30 à 60 s ; vidéo reprise seulement si Screen My Car la fournit ; au moins une vanne.
- "refaire" : 2 ou 3 idées {{"app", "hook", "comment"}} qui gardent la mécanique et changent le sujet.
- "pour_refaire" : {{"materiel", "temps", "niveau": "facile" | "moyen" | "costaud"}}.
- "titre" : court et parlant, comme les autres fiches. "niche" : 1 à 3 mots. "tags" : 4 à 7 mots.

LE MODÈLE (une fiche existante, même structure, mêmes clés) :
{json.dumps(exemple, ensure_ascii=False, indent=1)}

Réponds UNIQUEMENT par le JSON de la fiche (mêmes clés que le modèle ; "note" facultative, en HTML simple), sans texte avant ni après, sans bloc de code."""


OBLIGATOIRES = ("titre", "sujet", "niche", "format", "hooks", "point_sensible", "mise_en_scene", "chrono", "montre_comment",
                "cta", "montage", "chez_nous", "pour_refaire", "hook", "texte_ecran", "etapes", "pourquoi", "refaire", "tags")


def verifier(f: dict, ref: dict, duree: float) -> list[str]:
    """Ce qui ne va pas dans la fiche proposée (liste vide = bonne)."""
    pb = [f"« {k} » manque" for k in OBLIGATOIRES if not f.get(k) and f.get(k) != []]
    if f.get("format") not in ref["formats"]:
        pb.append(f"format inconnu : {f.get('format')!r} (au choix : {', '.join(ref['formats'])})")
    if f.get("format") == "fond-vert" and not f.get("reprise"):
        pb.append("un fond vert est toujours une reprise : « reprise »: true et « reprise_quoi »")
    for h in f.get("hooks") or []:
        if h not in ref["hooks"]:
            pb.append(f"type de hook inconnu : {h!r}")
    for r in f.get("refaire") or []:
        if r.get("app") not in ref["apps"]:
            pb.append(f"app inconnue dans « refaire » : {r.get('app')!r}")
    for c in f.get("chez_nous") or []:
        if c.get("ok") not in ("oui", "non", "bof"):
            pb.append("« chez_nous[].ok » doit valoir oui, non ou bof")
    ch = f.get("chrono") or {}
    for k in ("hook", "probleme", "outil_nomme", "outil_montre", "preuve", "fin"):
        if not isinstance(ch.get(k), dict) or "quoi" not in ch[k]:
            pb.append(f"« chrono.{k} » doit être {{\"t\": secondes ou null, \"quoi\": …}}")
        elif ch[k].get("t") is not None and not isinstance(ch[k]["t"], (int, float)):
            pb.append(f"« chrono.{k}.t » doit être un nombre de secondes")
    for e in f.get("etapes") or []:
        if not all(isinstance(e.get(k), (int, float)) for k in ("debut", "fin")) or e["debut"] > duree + 2:
            pb.append("chaque étape a « debut » et « fin » en secondes, dans la durée de la vidéo")
            break
    if not isinstance(f.get("hook"), dict) or not f["hook"].get("dit") or not f["hook"].get("voit"):
        pb.append("« hook » doit avoir « dit » et « voit »")
    return pb


def _json_de(texte: str) -> dict:
    texte = re.sub(r"^```(?:json)?|```$", "", texte.strip(), flags=re.M).strip()
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut < 0 or fin < 0:
        raise ValueError("pas de JSON dans la réponse")
    return json.loads(texte[debut:fin + 1])


def demander(consigne_: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}  # une session neuve, pas celle d'un parent
    p = subprocess.run([_claude(), "-p", "--output-format", "json", "--permission-mode", "dontAsk", "--allowedTools", "Read"],
                       input=consigne_, cwd=ICI, env=env, capture_output=True, text=True, timeout=1200)
    try:
        sortie = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError("Claude : " + ((p.stderr or p.stdout).strip().splitlines() or ["pas de réponse"])[-1][:200])
    if sortie.get("is_error"):
        raison = str(sortie.get("result") or sortie.get("subtype"))
        if "login" in raison.lower() or "logged in" in raison.lower():
            raise RuntimeError("Claude n'est pas connecté sur ce Mac : double-clique une fois sur « Connecter Claude (une fois).command » "
                               "(dossier VIDEOTHEQUE), connecte-toi, puis clique sur « Relancer »")
        raise RuntimeError("Claude : " + raison[:200])
    return sortie.get("result") or ""


def analyser(ident: str, nom: str, infos: dict) -> dict:
    fid = id_fiche(ident)
    ref = json.loads(REFERENTIEL.read_text(encoding="utf-8"))
    duree = json.loads((TRAVAIL / fid / "infos.json").read_text(encoding="utf-8"))["duree"]
    texte = consigne(fid, ident, infos)
    fiche, problemes = None, []
    for essai in range(2):  # une seconde chance si la fiche ne passe pas la vérification
        demande = texte if not problemes else (texte + "\n\nTa réponse précédente avait ces problèmes, corrige-les :\n- "
                                               + "\n- ".join(problemes) + "\n\nTa réponse précédente :\n" + json.dumps(fiche, ensure_ascii=False))
        try:
            fiche = _json_de(demander(demande))
        except ValueError as e:
            fiche, problemes = {}, [f"réponse illisible ({e}) : réponds seulement par le JSON"]
            continue
        problemes = verifier(fiche, ref, duree)
        if not problemes:
            break
    if problemes:
        raise RuntimeError("fiche refusée : " + " ; ".join(problemes[:3]))
    return enregistrer(ident, nom, infos, fiche)


def enregistrer(ident: str, nom: str, infos: dict, fiche: dict) -> dict:
    """Complète la fiche vérifiée (id, fichier, compte, lien, marché, chiffres) et l'écrit dans outils/fiches/."""
    fid = id_fiche(ident)
    marche = infos.get("marche") if infos.get("marche") in ("FR", "US") else "FR"
    stats = {k: infos.get(k) for k in ("vues", "likes", "partages", "enregistrements", "commentaires", "abonnes") if infos.get(k) is not None}
    fiche = {"id": fid, "fichier": nom, "compte": infos.get("compte") or "?", "plateforme": "TikTok", "marche": marche,
             "lien": infos.get("lien") or f"https://www.tiktok.com/video/{ident}", **fiche, "stats": stats, "ordre": 0,
             "ajout": "automatique"}
    if fiche["format"] != "fond-vert":
        fiche.pop("reprise_quoi", None)
        fiche["reprise"] = bool(fiche.get("reprise"))
    (FICHES / f"{fid}.json").write_text(json.dumps(fiche, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return fiche


# ---------- 3. mise en ligne ----------

def _git(*args, verifier=True):
    p = subprocess.run(["git", *args], cwd=ICI, capture_output=True, text=True, timeout=300)
    if verifier and p.returncode:
        raise RuntimeError(f"git {args[0]} : " + ((p.stderr or p.stdout).strip().splitlines() or ["échec"])[-1])
    return p


def publier(fiche: dict):
    p = subprocess.run([sys.executable, str(ICI / "outils" / "assembler.py")], cwd=ICI, capture_output=True, text=True, timeout=300)
    if p.returncode:
        raise RuntimeError("assembler : " + (p.stderr.strip().splitlines() or ["échec"])[-1])
    fid = fiche["id"]
    chemins = [f"outils/fiches/{fid}.json", "site/data/videos.json", f"site/videos/{fid}.mp4", f"site/posters/{fid}.jpg"]
    _git("add", *[c for c in chemins if (ICI / c).exists()])
    _git("commit", "-m", f"Vidéothèque : {fiche['titre']} (ajout automatique, {fiche['marche']})")
    if _git("push", "-q", "origin", "main", verifier=False).returncode:
        _git("pull", "--rebase", "-q", "origin", "main")
        _git("push", "-q", "origin", "main")


if __name__ == "__main__":  # à la main : python outils/chaine.py <id TikTok>
    sys.path.insert(0, str(ICI / "outils"))
    import scruter

    reg = scruter.charger_registre()
    ident = sys.argv[1]
    demarrer(reg, scruter.VERROU, scruter.sauver_registre, scruter.infos_revue)
    ajouter(ident)
    while ident in _dans_la_file:
        threading.Event().wait(2)
    print(json.dumps(reg.get(ident, {}).get("chaine"), ensure_ascii=False))
