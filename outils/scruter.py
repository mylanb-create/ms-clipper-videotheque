"""Va chercher tout seul, sur TikTok, des vidéos qui ont marché, pour alimenter la vidéothèque.

Étape 1, la liste :    python outils/scruter.py --mots "app,astuce" --max 30
    Interroge TikTok (via Apify, acteur clockworks/tiktok-scraper) par mots-clés, hashtags et comptes.
    Garde les vidéos récentes qui ont fait beaucoup de vues par rapport aux abonnés du compte (et des
    partages, des enregistrements), repère celles qui parlent d'une app ou d'un outil, écarte celles déjà
    dans la vidéothèque, puis écrit :
      travail/candidats/<date>.md         la liste à valider (titre, compte, chiffres, lien, pourquoi)
      travail/candidats/<date>-brut.json  toutes les données reçues (pour rejouer le tri sans repayer)
    Rien n'est téléchargé à cette étape.
Étape 2, Mylan lit le .md et choisit.
Étape 3, le téléchargement :    python outils/scruter.py --telecharger 7561234567890123456,7561234567890123457
    Récupère les MP4 (avec yt-dlp, gratuit) dans FICHIERS ANNEXES/VIDÉOS D'EXEMPLES, nommés
    tiktok-<id>-<compte>.mp4. Ensuite la chaîne habituelle : preparer.py → fiche → assembler.py → push.
    --ecarter <ids> : ne plus jamais reproposer ces vidéos.

Clé Apify : APIFY_TOKEN dans l'environnement ou dans le fichier .env à la racine de VIDEOTHEQUE
(ignoré par git ; modèle dans .env.exemple). Jamais dans le dépôt, jamais sur la ligne de commande.
--revue ouvre la vidéothèque complète (http://127.0.0.1:3110) : le site, avec en plus « Revue de vidéos » dans le menu de
gauche (/revue). On y approuve (A), refuse (R), met de côté (S) ou remet à revoir (Z) chaque vidéo ; une vidéo approuvée est téléchargée tout de suite dans FICHIERS ANNEXES/VIDÉOS D'EXEMPLES,
une vidéo approuvée puis refusée est retirée (tant qu'elle n'est pas analysée). La page permet aussi de coller la
clé Apify et de lancer une recherche.
--cle : saisir la clé Apify (cachée) ; elle est vérifiée puis écrite dans .env.
--estimer montre ce qui serait lancé et le coût, sans rien lancer.
--brut <fichier.json> rejoue le tri sur des données déjà reçues (gratuit).
Sans aucun argument : les mots par défaut + les comptes repérés (voir MOTS_DEFAUT, COMPTES_REPERES).

Lancer avec le Python de la vidéothèque Notion (le même que preparer.py, il a yt-dlp et ffmpeg) :
  "../../SCREEN MY CAR/CRÉATEURS/Briefs revclip/Vidéothèque/.venv/bin/python" outils/scruter.py ...
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import http.server
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import chaine  # la chaîne automatique : approuvée → préparée → analysée → en ligne

ICI = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ICI.parents[1] / "FICHIERS ANNEXES" / "VIDÉOS D'EXEMPLES"
FICHES, TRAVAIL = ICI / "outils" / "fiches", ICI / "travail"
CANDIDATS = TRAVAIL / "candidats"
VUS = CANDIDATS / "vus.json"  # la mémoire : proposées, téléchargées, écartées
APERCUS = CANDIDATS / "apercus"  # les vidéos récupérées pour les lire dans la page de revue
ENV = ICI / ".env"  # APIFY_TOKEN=… (ignoré par git)
SITE_PUBLIC = "https://mylanb-create.github.io/ms-clipper-videotheque/"
ORIGINE_PUBLIQUE = "https://mylanb-create.github.io"  # le site public lit /api/etat ici pour afficher la revue

API = "https://api.apify.com/v2"
ACTEUR = "clockworks~tiktok-scraper"
# Tarif de l'offre gratuite d'Apify, en $ par résultat (relevé le 21/10/2026 ; les offres payantes sont moins chères).
PRIX_RESULTAT, PRIX_FILTRE_DATE, PRIX_PAYS, PRIX_DEPART = 0.0037, 0.0013, 0.0013, 0.001
PERIODES = {"24h": "PAST_24_HOURS", "semaine": "PAST_WEEK", "mois": "PAST_MONTH",
            "3mois": "LAST_3_MONTHS", "6mois": "LAST_6_MONTHS", "tout": "ALL_TIME"}
MOTS_DEFAUT = ["app", "astuce", "IA", "side business"]
# Les deux marchés. Apify ne donne pas le pays d'une vidéo, seulement la langue de sa légende : fr → FR, en → US.
MARCHES = {
    "FR": {"drapeau": "🇫🇷", "nom": "France", "langue": "fr", "mots": ["application", "astuce", "IA", "argent en ligne"]},
    "US": {"drapeau": "🇺🇸", "nom": "US (anglais)", "langue": "en", "mots": ["app", "life hack", "ai tools", "side hustle"]},
}
_FR = set("le la les des du de un une et est je tu il elle on nous vous pas ça ca pour avec sur dans que qui mon ma mes ton ta tes "
          "c'est j'ai ce cette tout trop vraiment comment pourquoi quand faire fait voici voilà".split())
INDICES_FR = {"pourtoi", "pourtoii", "pourtoiii", "astuce", "astuces", "france", "francais", "français", "tiktokfrance",
              "apprendresurtiktok", "humourfr", "argentenligne", "businessenligne", "entrepreneurfrancais", "foryoufr"}
_EN = set("the and you your this that is are for with my it to of in on how why what when i'm it's don't just get got "
          "these those best try make made here".split())
COMPTES_REPERES = ["@emma_cst7", "@emma.viral80", "@ismael.viral", "@kamevo.ai", "@6min_ai", "@yanis.astuces.voy"]
# Ce qui dit « ça parle d'une app ou d'un outil » dans la légende ou les hashtags.
OUTIL = re.compile(
    r"(?<!\w)(apps?|applis?|applications?|sites?|outils?|logiciels?|ia|#ai|chat ?gpt|gpt|claude|gemini|notion|canva|"
    r"capcut|extensions?|t[ée]l[ée]charg\w*|gratuit\w*|lien en bio|automatis\w*|bots?|g[ée]n[ée]rateurs?|"
    r"productivit[ée]|intelligence artificielle|iphone|android|ordi|pc|macbook)(?!\w)", re.IGNORECASE)
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
UTC = dt.timezone.utc
VERROU = threading.RLock()  # la page de revue et les téléchargements en fond écrivent le même registre
JOURNAL = None  # pendant une recherche lancée depuis la page : les lignes à lui montrer


def dire(message, **_):
    print(message, flush=True)
    if JOURNAL is not None:
        JOURNAL.append(str(message))


# ---------- petits outils ----------

def nombre(n):
    if n is None:
        return "?"
    n = int(n or 0)
    if n >= 1_000_000:
        return (f"{n / 1_000_000:.1f}".replace(".", ",").replace(",0", "")) + " M"
    if n >= 10_000:
        return f"{round(n / 1000)} k"
    return f"{n:,}".replace(",", " ")


def pct(x):
    return f"{100 * x:.1f}".replace(".", ",").replace(",0", "") + " %"


def prix(x):
    return f"{x:.2f} $".replace(".", ",")


def date_fr(d):
    return f"{d.day} {MOIS[d.month - 1]} {d.year}"


def decouper(s, prefixe=""):
    return [m.strip().lstrip(prefixe).strip() for m in (s or "").split(",") if m.strip()]


def slug(s):
    return re.sub(r"[^\w.-]+", "-", (s or "").lstrip("@")).strip("-.")


def lire_env():
    if ENV.exists():
        for ligne in ENV.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if ligne.startswith("export "):
                ligne = ligne[7:]
            if not ligne or ligne.startswith("#") or "=" not in ligne:
                continue
            cle, val = ligne.split("=", 1)
            os.environ.setdefault(cle.strip(), val.strip().strip('"').strip("'"))


def jeton(obligatoire=True):
    lire_env()
    j = os.environ.get("APIFY_TOKEN", "").strip()
    if not j and obligatoire:
        sys.exit("Pas de clé Apify. Copie .env.exemple en .env (racine de VIDEOTHEQUE) et colle la clé après "
                 "APIFY_TOKEN= (Apify → Settings → API & Integrations). Ce fichier est ignoré par git.\n"
                 "Sans clé, tu peux déjà lire des comptes : python outils/scruter.py --sans-apify --comptes reperes")
    return j


def charger_registre():
    if VUS.exists():
        return json.loads(VUS.read_text(encoding="utf-8"))
    return {}


def sauver_registre(r):
    with VERROU:
        CANDIDATS.mkdir(parents=True, exist_ok=True)
        VUS.write_text(json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


# ---------- Apify ----------

def apify(jet, methode, chemin, corps=None, params=None, delai=90):
    url = f"{API}{chemin}" + (("?" + urllib.parse.urlencode(params)) if params else "")
    donnees = json.dumps(corps).encode("utf-8") if corps is not None else None
    req = urllib.request.Request(url, data=donnees, method=methode, headers={
        "Authorization": f"Bearer {jet}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=delai) as r:
            return json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        try:
            detail = json.loads(detail)["error"]["message"]
        except Exception:
            pass
        if e.code == 401:
            sys.exit(f"Apify refuse la clé (401) : {detail}")
        if e.code in (402, 403):
            sys.exit(f"Apify refuse ({e.code}) : crédit épuisé, ou acteur à activer une fois sur le compte ? {detail}")
        sys.exit(f"Erreur Apify {e.code} sur {chemin} : {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Impossible de joindre Apify ({e.reason}). Pas de réseau ?")


def en_hashtag(mot):
    """« side business » → « sidebusiness », « IA » → « ia »."""
    return re.sub(r"[^\w]+", "", mot.lower())


def plan(mots, hashtags, comptes, args):
    """Ce qu'on va demander à Apify : un run « recherche » (mots + hashtags) et un run « comptes ».
    Par défaut chaque mot est aussi cherché comme hashtag : la page d'un hashtag donne surtout des vidéos récentes,
    la recherche par mot surtout des anciennes (essais du 21/10/2026 : 12 sur 15 contre 4 sur 15 de moins de 60 jours)."""
    runs = []
    if mots and not getattr(args, "sans_hashtags_auto", False):
        deja = {h.lower() for h in hashtags}
        hashtags = hashtags + [h for h in dict.fromkeys(en_hashtag(m) for m in mots) if h and h not in deja]
    if mots or hashtags:
        e = {"resultsPerPage": args.max, "excludePinnedPosts": True,
             "shouldDownloadVideos": False, "shouldDownloadCovers": False}
        if mots:
            e["searchQueries"], e["searchSection"] = mots, "/video"
            if args.periode != "tout":
                e["videoSearchDateFilter"] = PERIODES[args.periode]
        if hashtags:
            e["hashtags"] = hashtags
        if args.pays:
            e["proxyCountryCode"] = args.pays
        n = (len(mots) + len(hashtags)) * args.max
        unite = PRIX_RESULTAT + (PRIX_PAYS if args.pays else 0) + (PRIX_FILTRE_DATE if "videoSearchDateFilter" in e else 0)
        runs.append(("recherche", e, n, PRIX_DEPART + n * unite))
    if comptes:
        e = {"profiles": [c.lstrip("@") for c in comptes], "resultsPerPage": args.max, "profileSorting": "latest",
             "profileScrapeSections": ["videos"], "excludePinnedPosts": True,
             "shouldDownloadVideos": False, "shouldDownloadCovers": False}
        n = len(comptes) * args.max
        runs.append(("comptes", e, n, PRIX_DEPART + n * PRIX_RESULTAT))
    return runs


def lancer_et_attendre(jet, runs):
    """Lance les runs en parallèle, attend la fin, renvoie les items (chacun marqué de son run)."""
    en_cours = {}
    for nom, entree, _, _ in runs:
        r = apify(jet, "POST", f"/acts/{ACTEUR}/runs", corps=entree, params={"timeout": 1500})["data"]
        en_cours[nom] = r
        dire(f"  {nom} : lancé (run {r['id']})", flush=True)
    finis, debut = {}, time.time()
    try:
        while en_cours:
            for nom, r in list(en_cours.items()):
                r = apify(jet, "GET", f"/actor-runs/{r['id']}", params={"waitForFinish": 60}, delai=130)["data"]
                if r["status"] in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                    finis[nom] = r
                    del en_cours[nom]
                    dire(f"  {nom} : {r['status'].lower()} après {int(time.time() - debut)} s", flush=True)
                else:
                    en_cours[nom] = r
            if en_cours and time.time() - debut > 1800:
                raise KeyboardInterrupt
    except KeyboardInterrupt:
        for nom, r in en_cours.items():
            apify(jet, "POST", f"/actor-runs/{r['id']}/abort")
            dire(f"  {nom} : arrêté")
        sys.exit("Arrêté avant la fin.")
    items = []
    for nom, r in finis.items():
        if r["status"] != "SUCCEEDED":
            dire(f"  ⚠️ le run « {nom} » a fini en {r['status']} : on garde ce qu'il a trouvé", flush=True)
        offset = 0
        while True:
            page = apify(jet, "GET", f"/datasets/{r['defaultDatasetId']}/items",
                         params={"clean": "true", "format": "json", "offset": offset, "limit": 1000}, delai=130)
            for it in page:
                it["_run"] = nom
            items.extend(page)
            if len(page) < 1000:
                break
            offset += 1000
    return items


# ---------- sans Apify : les comptes, avec yt-dlp ----------

def abonnes_de(compte):
    """Le nombre d'abonnés, lu sur la page du profil (sans clé). None si TikTok ne répond pas."""
    url = f"https://www.tiktok.com/@{compte.lstrip('@')}"
    try:
        try:
            from curl_cffi import requests as cffi
            html = cffi.get(url, impersonate="chrome", timeout=30).text
        except ImportError:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r'"followerCount":(\d+)', html)
    return int(m.group(1)) if m else None


def lire_comptes_gratuit(comptes, maxi):
    """Les dernières vidéos de chaque compte avec yt-dlp (gratuit, sans clé) : vues, likes, partages, commentaires,
    durée, date. Pas les enregistrements ; les abonnés viennent de la page du profil. Même forme que les résultats Apify."""
    try:
        import yt_dlp
    except ImportError:
        sys.exit("yt-dlp manque : « …/.venv/bin/python -m pip install yt-dlp » (dans le venv de la vidéothèque).")
    items = []
    for compte in comptes:
        nom = compte.lstrip("@")
        abonnes = abonnes_de(nom)
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": maxi}) as y:
                liste = y.extract_info(f"https://www.tiktok.com/@{nom}", download=False)
        except Exception as e:
            dire(f"  ✗ {compte} : {str(e).splitlines()[0][:160] if str(e) else type(e).__name__}", flush=True)
            continue
        entrees = (liste or {}).get("entries") or []
        for e in entrees:
            if not e.get("id"):
                continue
            items.append({
                "id": str(e["id"]), "text": e.get("description") or e.get("title") or "",
                "createTime": e.get("timestamp"),
                "authorMeta": {"name": e.get("uploader") or nom, "nickName": e.get("channel") or "", "fans": abonnes or 0},
                "webVideoUrl": e.get("url") or f"https://www.tiktok.com/@{nom}/video/{e['id']}",
                "videoMeta": {"duration": e.get("duration") or 0},
                "playCount": e.get("view_count"), "diggCount": e.get("like_count"), "shareCount": e.get("repost_count"),
                "commentCount": e.get("comment_count"), "collectCount": None,
                "isSlideshow": not e.get("duration"), "mediaUrls": [], "_run": "comptes", "_moteur": "yt-dlp"})
        dire(f"  {compte} : {len(entrees)} vidéos"
              + (f", {nombre(abonnes)} abonnés" if abonnes is not None else ", abonnés inconnus"), flush=True)
        time.sleep(1)
    return items


# ---------- lecture des résultats ----------

def champ(item, *cles, defaut=None):
    """Lit item["a"]["b"] ou item["a.b"] (Apify donne l'une ou l'autre forme selon l'export)."""
    v = item
    for c in cles:
        v = v.get(c) if isinstance(v, dict) else None
    if v is None:
        v = item.get(".".join(cles))
    return defaut if v is None else v


def lire_date(item):
    iso = champ(item, "createTimeISO")
    if iso:
        try:
            return dt.datetime.fromisoformat(str(iso).replace("Z", "")).replace(tzinfo=UTC)
        except ValueError:
            pass
    brut = champ(item, "createTime")
    if brut:
        try:
            return dt.datetime.fromtimestamp(int(brut), tz=UTC)
        except (ValueError, OverflowError, OSError):
            pass
    return None


def deviner_langue(texte):
    """fr, en ou None, d'après les petits mots de la légende (quand TikTok ne donne pas la langue)."""
    mots = re.findall(r"[a-zà-ÿ']+", re.sub(r"#\w+", " ", (texte or "").lower()))
    fr, en = sum(m in _FR for m in mots), sum(m in _EN for m in mots)
    if fr >= 2 and fr > en:
        return "fr"
    if en >= 2 and en > fr:
        return "en"
    return None


def marche_de(item, legende):
    """(marché, langue) : FR, US, « autre » (une autre langue) ou « ? » (impossible à dire)."""
    langue = str(champ(item, "textLanguage", defaut="") or "").lower()
    if langue in ("", "un", "und"):
        langue = deviner_langue(legende) or ""
        if not langue and {h.lower() for h in re.findall(r"#(\w+)", legende)} & INDICES_FR:
            langue = "fr"
    for m, d in MARCHES.items():
        if langue == d["langue"]:
            return m, langue
    if langue:
        return "autre", langue
    # Langue impossible à dire : on ne fait confiance au marché que pour les comptes qu'on suit (ils sont choisis à la main).
    if item.get("_run") == "comptes" and item.get("_marche"):
        return item["_marche"], "?"
    return "?", "?"


def normaliser(item):
    ident = str(champ(item, "id", defaut="") or "")
    lien = str(champ(item, "webVideoUrl", defaut="") or "")
    compte = str(champ(item, "authorMeta", "name", defaut="") or "")
    if not ident and lien:
        m = re.search(r"/video/(\d+)", lien)
        ident = m.group(1) if m else ""
    if not compte and lien:
        m = re.search(r"tiktok\.com/@([\w.-]+)", lien)
        compte = m.group(1) if m else ""
    if not lien and ident:
        lien = f"https://www.tiktok.com/@{compte}/video/{ident}"
    hashtags = []
    for h in champ(item, "hashtags", defaut=[]) or []:
        nom = h.get("name") if isinstance(h, dict) else str(h)
        if nom:
            hashtags.append(str(nom).lstrip("#"))
    if not hashtags:
        hashtags = re.findall(r"#(\w+)", str(champ(item, "text", defaut="") or ""))
    hashtags = list({h.lower(): h for h in reversed(hashtags)}.values())[::-1]  # sans doublon, dans l'ordre
    entree = item.get("searchQuery") or item.get("input") or champ(item, "searchHashtag", "name")
    if isinstance(entree, str) and (item.get("searchHashtag") or item.get("fromHashtag")) and not entree.startswith("#"):
        entree = "#" + entree
    if item.get("_run") == "comptes" or (isinstance(entree, str) and entree.lstrip("@").lower() == compte.lower()):
        via = "son compte"
    elif isinstance(entree, str) and entree.strip():
        via = entree if entree.startswith("#") else f"« {entree} »"
    else:
        via = "la recherche"
    date = lire_date(item)
    duree = int(champ(item, "videoMeta", "duration", defaut=0) or 0)
    legende = re.sub(r"\s+", " ", str(champ(item, "text", defaut="") or "")).strip()
    marche, langue = marche_de(item, legende)
    return {
        "marche": marche, "langue": langue,
        "id": ident, "lien": lien, "compte": f"@{compte}" if compte else "?",
        "nom": str(champ(item, "authorMeta", "nickName", defaut="") or ""),
        "abonnes": int(champ(item, "authorMeta", "fans", defaut=0) or 0),
        "vues": int(champ(item, "playCount", defaut=0) or 0),
        "likes": int(champ(item, "diggCount", defaut=0) or 0),
        "partages": int(champ(item, "shareCount", defaut=0) or 0),
        "enregistrements": None if champ(item, "collectCount") is None else int(champ(item, "collectCount") or 0),
        "commentaires": int(champ(item, "commentCount", defaut=0) or 0),
        "duree": duree, "date": date,
        "legende": re.sub(r"\s+", " ", str(champ(item, "text", defaut="") or "")).strip(),
        "hashtags": hashtags,
        "carrousel": bool(champ(item, "isSlideshow", defaut=False)) or (duree == 0 and not champ(item, "mediaUrls")),
        "pub": bool(champ(item, "isAd", defaut=False)) or bool(champ(item, "isSponsored", defaut=False)),
        "via": {via},
    }


def videotheque():
    """Ce qu'on a déjà : les ids TikTok connus (fichiers tiktok-<id>, champ « lien » des fiches) et, pour les fiches
    sans lien, (compte, durée, fiche) pour la comparaison approximative."""
    ids, couples, fiches = set(), [], {}  # fiches : id TikTok → id de la fiche (vidéo déjà analysée)
    for f in FICHES.glob("*.json"):
        v = json.loads(f.read_text(encoding="utf-8"))
        for c in ("fichier", "lien", "url", "source"):
            for i in re.findall(r"\d{15,20}", str(v.get(c, ""))):
                ids.add(i)
                fiches[i] = v["id"]
        infos = TRAVAIL / v["id"] / "infos.json"
        compte = str(v.get("compte", "")).lower()
        if compte.startswith("@") and infos.exists() and v.get("type") != "carrousel" and not v.get("lien"):
            couples.append((compte, float(json.loads(infos.read_text(encoding="utf-8"))["duree"]), v["id"]))
    if SOURCE.exists():
        for f in SOURCE.iterdir():
            ids.update(re.findall(r"tiktok-(\d{15,20})", f.name))
    return ids, couples, fiches


def proche(c, couples):
    """Même compte et même durée qu'une fiche. Un fichier enregistré depuis l'app TikTok dure quelques secondes
    de plus que la durée annoncée par TikTok (le petit générique de fin) : on tolère de -1 à +6 s."""
    for compte, duree, fiche in couples:
        if compte == c["compte"].lower() and c["duree"] and -1.0 <= duree - c["duree"] <= 6.0:
            return fiche
    return None


def note(c):
    n = math.log10(max(c["vues"], 1) / 1000)  # 100 k vues = 2, 1 M = 3
    n += math.log10(1 + (c["ratio"] or 0))  # 9× les abonnés = 1, 99× = 2
    n += min(2.0, 100 * (c["taux_enreg"] + c["taux_partage"]))  # 2 % d'enregistrements + partages = 2
    if c["outil"]:
        n += 0.5
    return round(n, 1)


def pourquoi(c):
    p = []
    if c["ratio"] is None:
        p.append("abonnés inconnus")
    elif c["ratio"] >= 10:
        p.append(f"{c['ratio']:.0f}× ses abonnés")
    else:
        p.append(f"{c['ratio']:.1f}× ses abonnés".replace(".", ","))
    if c["taux_enreg"] >= 0.005:
        p.append(f"{pct(c['taux_enreg'])} d'enregistrements")
    if c["taux_partage"] >= 0.003:
        p.append(f"{pct(c['taux_partage'])} de partages")
    if c["outil"]:
        p.append("parle d'un outil (" + ", ".join(c["mots_outil"]) + ")")
    if c["carrousel"]:
        p.append("carrousel de photos")
    if c["age"] is not None and c["age"] <= 7:
        p.append(f"toute fraîche ({c['age']} j)")
    p.append("trouvée par " + ", ".join(sorted(c["via"])))
    return " · ".join(p)


def trier(items, args, registre, pour_revue=False):
    """Fusionne les doublons, écarte (avec la raison), note et classe le reste.
    pour_revue : on ne tient pas compte des décisions déjà prises (elles deviennent le champ « statut »)."""
    ids_connus, couples, fiches = videotheque()
    par_id = {}
    for it in items:
        c = normaliser(it)
        if not c["id"]:
            continue
        if c["id"] in par_id:
            par_id[c["id"]]["via"] |= c["via"]
        else:
            par_id[c["id"]] = c
    maintenant = dt.datetime.now(UTC)
    gardees, ecartees = [], []
    for c in par_id.values():
        c["age"] = (maintenant - c["date"]).days if c["date"] else None
        c["ratio"] = c["vues"] / c["abonnes"] if c["abonnes"] else None
        c["taux_enreg"] = (c["enregistrements"] or 0) / c["vues"] if c["vues"] else 0
        c["taux_partage"] = c["partages"] / c["vues"] if c["vues"] else 0
        trouves = OUTIL.findall(c["legende"] + " " + " ".join("#" + h for h in c["hashtags"]))
        c["mots_outil"] = sorted({t.lower() for t in trouves})[:4]
        c["outil"] = bool(trouves)
        r = registre.get(c["id"])
        raison, fiche = None, None
        if pour_revue:
            # Déjà analysée (une fiche la cite) et rien décidé dans la page : plus rien à revoir.
            if c["id"] in fiches and (r or {}).get("statut") not in ("telecharge", "approuve"):
                raison = "déjà dans la vidéothèque"
        elif r and r.get("statut") in ("telecharge", "approuve"):
            raison = "déjà téléchargée"
        elif r and r.get("statut") == "ecarte":
            raison = f"écartée le {r.get('date')}"
        if raison:
            pass
        elif c["id"] in ids_connus and not pour_revue:  # en revue, c'est le statut du registre qui fait foi
            raison = "déjà dans la vidéothèque"
        elif c["pub"]:
            raison = "publicité"
        elif c["age"] is None:
            raison = "date inconnue"
        elif c["age"] > args.jours:
            raison = f"trop ancienne ({c['age']} j)"
        elif c["vues"] < args.vues_min:
            raison = "pas assez de vues"
        elif c["ratio"] is not None and c["ratio"] < args.ratio:
            raison = "trop peu de vues par rapport aux abonnés"
        elif args.outil and not c["outil"]:
            raison = "ne parle pas d'un outil"
        elif (getattr(args, "marche", "") or "").upper() in MARCHES and c["marche"] not in (args.marche.upper(), "?"):
            raison = f"pas le bon marché (langue {c['langue']})"
        elif (fiche := proche(c, couples)) and not pour_revue:  # en dernier : ne signaler que celles qui auraient été candidates
            raison = f"probablement déjà dans la vidéothèque (fiche {fiche}, même compte, même durée)"
        if raison:
            ecartees.append((c, raison))
            continue
        c["doublon"] = proche(c, couples) if pour_revue else None  # en revue : un avertissement, pas une exclusion
        c["deja_proposee"] = None
        if r and r.get("statut") == "propose" and r.get("date"):
            try:
                c["deja_proposee"] = date_fr(dt.date.fromisoformat(r["date"]))
            except ValueError:
                c["deja_proposee"] = r["date"]
        c["note"] = note(c)
        c["pourquoi"] = pourquoi(c)
        c["statut"] = (r or {}).get("statut") or "propose"
        c["fichier"] = (r or {}).get("fichier")
        c["erreur"] = (r or {}).get("erreur")
        c["remarque"] = (r or {}).get("note")
        c["fiche"] = fiches.get(c["id"]) or (
            (r or {}).get("fichier") and next((v["id"] for v in (json.loads(x.read_text(encoding="utf-8")) for x in FICHES.glob("*.json"))
                                              if v.get("fichier") == r.get("fichier")), None))
        gardees.append(c)
    gardees.sort(key=lambda c: (-c["note"], -c["vues"]))
    return gardees, ecartees


# ---------- la sortie ----------

def titre_court(c, n=70):
    t = re.sub(r"#\w+", "", c["legende"]).strip(" -·|")
    t = re.sub(r"\s+", " ", t)
    if not t:
        return "(sans légende)"
    return "« " + (t[:n].rstrip() + "…" if len(t) > n else t) + " »"


def ecrire_md(chemin, gardees, ecartees, decrit, nb_items, cout, brut_nom, args, moteur="Apify"):
    quand = dt.datetime.now()
    total = len(gardees)
    gardees = gardees[:args.garder]
    L = [f"# Candidats TikTok — {date_fr(quand)}, {quand:%H h %M}", "",
         f"Recherche : {decrit}.",
         f"Tri : moins de {args.jours} jours · au moins {nombre(args.vues_min)} vues · au moins "
         f"{str(args.ratio).replace('.', ',')}× les abonnés du compte"
         + (" · seulement les vidéos qui parlent d'un outil" if args.outil else "") + ".",
         f"{moteur} : {nb_items} résultats lus" + (f", ≈ {prix(cout)}" if cout else " (gratuit)")
         + f" · données complètes dans `{brut_nom}`.", "",
         "Pour télécharger celles que tu retiens (dans FICHIERS ANNEXES/VIDÉOS D'EXEMPLES) :",
         "`python outils/scruter.py --telecharger <id>,<id>` · pour ne plus jamais revoir les autres : "
         "`python outils/scruter.py --ecarter <id>,<id>`.",
         "« 47× ses abonnés » = la vidéo a fait 47 fois le nombre d'abonnés du compte : elle est sortie du cercle.", "",
         f"## {len(gardees)} candidates" + (f" (les {len(gardees)} meilleures sur {total})" if total > len(gardees) else ""), ""]
    for i, c in enumerate(gardees, 1):
        L.append(f"### {i}. {titre_court(c)} — {c['compte']} ({nombre(c['abonnes'])} abonnés)")
        L.append(f"- **{nombre(c['vues'])} vues** · {nombre(c['likes'])} likes · {nombre(c['partages'])} partages · "
                 + (f"{nombre(c['enregistrements'])} enregistrements · " if c["enregistrements"] is not None else "")
                 + f"{nombre(c['commentaires'])} commentaires · "
                 + (f"{c['duree']} s · " if c["duree"] else "")
                 + f"{date_fr(c['date'])} ({c['age']} j)")
        L.append(f"- Pourquoi : {c['pourquoi']}"
                 + (f" · ⚠️ déjà proposée le {c['deja_proposee']}" if c["deja_proposee"] else ""))
        if c["legende"]:
            L.append(f"- Légende : {c['legende'][:220]}" + ("…" if len(c["legende"]) > 220 else ""))
        if c["hashtags"]:
            L.append("- Hashtags : " + " ".join("#" + h for h in c["hashtags"]))
        L.append(f"- Lien : {c['lien']} · id `{c['id']}` · note {str(c['note']).replace('.', ',')}")
        L.append("")
    L.append(f"## Écartées : {len(ecartees)}")
    L.append("")
    motifs = collections.Counter(re.sub(r"\s*\(.*\)$", "", r).replace("écartée le", "écartée avant") for _, r in ecartees)
    if motifs:
        L.append("- " + " · ".join(f"{n} {m}" for m, n in motifs.most_common()))
    doutes = [(c, r) for c, r in ecartees if r.startswith("probablement")]
    if doutes:
        L.append("- À vérifier d'un coup d'œil (même compte et même durée qu'une fiche, c'est sans doute la même vidéo) :")
        for c, r in doutes:
            L.append(f"  - {c['compte']} · {c['duree']} s · {nombre(c['vues'])} vues · {c['lien']} · {r}")
    chemin.write_text("\n".join(L) + "\n", encoding="utf-8")


def afficher(gardees, args):
    for i, c in enumerate(gardees[:args.garder], 1):
        print(f"{i:>3}. {nombre(c['vues']):>7} vues · "
              + (f"{c['ratio']:.0f}×" if c["ratio"] is not None else "?×").rjust(5)
              + f" · {(nombre(c['enregistrements']) + ' enreg.') if c['enregistrements'] is not None else '':>13} · "
              f"{str(c['duree']) + ' s' if c['duree'] else 'photos':>5} · "
              f"{c['age']:>2} j · {c['compte']} · {titre_court(c, 50)}")
        print(f"     {c['id']}  {c['lien']}")


# ---------- téléchargement ----------

def identifiant(ref, registre):
    """« 7561… » ou une adresse TikTok → (id, compte ou None)."""
    ref = ref.strip()
    m = re.search(r"tiktok\.com/@([\w.-]*)/video/(\d{15,20})", ref)
    if m:
        return m.group(2), (m.group(1) or registre.get(m.group(2), {}).get("compte") or None)
    m = re.search(r"\d{15,20}", ref)
    if not m:
        sys.exit(f"Je ne reconnais pas « {ref} » : il faut l'id (les 19 chiffres) ou le lien TikTok.")
    return m.group(0), registre.get(m.group(0), {}).get("compte")


def media_url(ident):
    """L'adresse directe de la vidéo dans le dernier brut qui la contient (valable quelques heures)."""
    for brut in sorted(CANDIDATS.glob("*-brut.json"), reverse=True):
        try:
            d = json.loads(brut.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for it in d.get("items", d) if isinstance(d, dict) else d:
            if str(champ(it, "id", defaut="")) == ident:
                urls = champ(it, "mediaUrls", defaut=[]) or []
                return urls[0] if urls else None
    return None


def telecharger_direct(url, cible):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"})
    with urllib.request.urlopen(req, timeout=120) as r, open(cible, "wb") as f:
        while True:
            bloc = r.read(1 << 20)
            if not bloc:
                break
            f.write(bloc)


VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}


def vid_de(fichier, ff):
    """L'identifiant interne TikTok écrit dans les métadonnées du fichier (comment : vid:v26…), ou None."""
    sortie = subprocess.run([ff, "-hide_banner", "-i", str(fichier)], capture_output=True, text=True).stderr
    m = re.search(r"vid:(v\w{20,})", sortie)
    return m.group(1) if m else None


def telecharger(refs, registre):
    try:
        import yt_dlp
    except ImportError:
        sys.exit("yt-dlp manque : « …/.venv/bin/python -m pip install yt-dlp » (dans le venv de la vidéothèque).")
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    SOURCE.mkdir(parents=True, exist_ok=True)
    # Les vidéos déjà dans le dossier, par identifiant interne : pour reconnaître une vidéo déposée à la main.
    vids = {}
    for f in SOURCE.iterdir():
        if f.suffix.lower() in VIDEO_EXT and not f.name.startswith("."):
            v = vid_de(f, ff)
            if v:
                vids[v] = f
    faits = []
    for ref in refs:
        ident, compte = identifiant(ref, registre)
        url = f"https://www.tiktok.com/@{(compte or '').lstrip('@')}/video/{ident}"
        deja = [f for f in SOURCE.iterdir() if f"tiktok-{ident}" in f.name]
        if deja:
            print(f"  = {deja[0].name} est déjà là")
            registre[ident] = {**registre.get(ident, {}), "statut": "telecharge", "fichier": deja[0].name, "lien": url}
            registre[ident].pop("erreur", None)
            sauver_registre(registre)
            continue
        apercu_f, apercu_j = APERCUS / f"{ident}.mp4", APERCUS / f"{ident}.json"
        if apercu_f.exists():  # déjà récupérée pour la page de revue : on la copie, pas de second téléchargement
            infos_apercu = json.loads(apercu_j.read_text(encoding="utf-8")) if apercu_j.exists() else {}
            compte = compte or infos_apercu.get("compte") or ""
            nom = f"tiktok-{ident}-{slug(compte)}" if slug(compte) else f"tiktok-{ident}"
            cible = SOURCE / f"{nom}.mp4"
            shutil.copy2(apercu_f, cible)
            erreur = None
        else:
            info, erreur = None, None
            try:
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}) as y:
                    info = y.extract_info(url, download=False)
                compte = compte or info.get("uploader") or info.get("uploader_id") or ""
                nom = f"tiktok-{ident}-{slug(compte)}" if slug(compte) else f"tiktok-{ident}"
                cible = SOURCE / f"{nom}.mp4"
                with yt_dlp.YoutubeDL({"outtmpl": str(SOURCE / (nom + ".%(ext)s")), "format_sort": ["vcodec:h264", "res", "br"],
                                       "merge_output_format": "mp4", "ffmpeg_location": str(pathlib.Path(ff).parent),
                                       "quiet": True, "no_warnings": True, "noplaylist": True, "retries": 3}) as y:
                    res = y.extract_info(url, download=True)
                    fichier = pathlib.Path(y.prepare_filename(res))
                if fichier.exists() and fichier != cible:
                    subprocess.run([ff, "-v", "error", "-y", "-i", str(fichier), "-c", "copy", "-movflags", "+faststart", str(cible)], check=True)
                    fichier.unlink()
            except Exception as e:  # yt-dlp a sa propre famille d'erreurs
                erreur = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
                nom = f"tiktok-{ident}-{slug(compte)}" if slug(compte or "") else f"tiktok-{ident}"
                cible = SOURCE / f"{nom}.mp4"
                direct = media_url(ident)
                if direct:
                    try:
                        print(f"  … yt-dlp n'a pas pu ({erreur}) ; essai avec l'adresse directe reçue d'Apify")
                        telecharger_direct(direct, cible)
                        erreur = None
                    except Exception as e2:
                        erreur = f"{erreur} ; adresse directe : {str(e2)[:120]}"
                        if cible.exists():
                            cible.unlink()
        if erreur or not cible.exists():
            print(f"  ✗ {ident} : {erreur or 'pas de fichier'}. Solution de secours : enregistrer la vidéo depuis "
                  f"l'app TikTok et la déposer dans FICHIERS ANNEXES/VIDÉOS D'EXEMPLES ({url}).")
            registre[ident] = {**registre.get(ident, {}), "erreur": erreur or "pas de fichier"}
            sauver_registre(registre)
            continue
        vid = vid_de(cible, ff)
        if vid and vid in vids and vids[vid] != cible:
            cible.unlink()
            registre[ident] = {**registre.get(ident, {}), "statut": "telecharge", "date": dt.date.today().isoformat(),
                               "compte": f"@{compte.lstrip('@')}" if compte else None, "lien": url, "fichier": vids[vid].name,
                               "note": "était déjà dans le dossier"}
            sauver_registre(registre)
            print(f"  = {ident} était déjà dans le dossier : {vids[vid].name} (même vidéo). Rien à faire.")
            continue
        taille = cible.stat().st_size / 1024 / 1024
        registre[ident] = {**registre.get(ident, {}), "statut": "telecharge", "date": dt.date.today().isoformat(),
                           "compte": f"@{compte.lstrip('@')}" if compte else registre.get(ident, {}).get("compte"),
                           "lien": url, "fichier": cible.name}
        registre[ident].pop("erreur", None)
        sauver_registre(registre)
        faits.append(cible)
        print(f"  ✓ {cible.name} ({taille:.1f} Mo)")
    if faits:
        print(f"\n{len(faits)} vidéo(s) dans {SOURCE}\nEnsuite : lance preparer.py (voir LISEZ-MOI.md), écris la fiche, assembler.py, push.")


# ---------- la passe complète ----------

def cibles(args):
    """Les mots, hashtags et comptes demandés, avec les valeurs par défaut."""
    mots, hashtags = decouper(args.mots), decouper(args.hashtags, "#")
    c = (args.comptes or "").strip()
    m = (getattr(args, "marche", "") or "").upper()
    if m in MARCHES and not (mots or hashtags) and not c:
        mots, c = list(MARCHES[m]["mots"]), "aucun"
    if not c:
        comptes = COMPTES_REPERES if not (mots or hashtags) else []
    elif c.lower() in ("reperes", "repérés"):
        comptes = COMPTES_REPERES
    elif c.lower() == "fiches":
        comptes = sorted({json.loads(f.read_text(encoding="utf-8")).get("compte", "") for f in FICHES.glob("*.json")})
        comptes = [x for x in comptes if x.startswith("@")]
    elif c.lower() in ("aucun", "non", "0"):
        comptes = []
    else:
        comptes = ["@" + x.lstrip("@") for x in decouper(c)]
    if not (mots or hashtags or comptes) and not getattr(args, "brut", None):
        mots = MOTS_DEFAUT
    return mots, hashtags, comptes


def estimation(args):
    """Ce que la recherche ferait : (description, résultats au plus, coût en $, gratuit ?, runs Apify)."""
    mots, hashtags, comptes = cibles(args)
    jet = jeton(obligatoire=False)
    if args.sans_apify or (not jet and comptes and not (mots or hashtags)):
        if not comptes:
            return 'sans Apify il faut des comptes : « reperes », « fiches » ou @a, @b', 0, 0.0, True, []
        return (f"comptes {', '.join(comptes)} · {args.max} dernières vidéos par compte · lu avec yt-dlp, gratuit "
                f"(pas d'enregistrements)"), len(comptes) * args.max, 0.0, True, []
    runs = plan(mots, hashtags, comptes, args)
    tous_h = next((e.get("hashtags", []) for n, e, _, _ in runs if n == "recherche"), hashtags)
    marche = (getattr(args, "marche", "") or "").upper()
    decrit = " · ".join(filter(None, [
        f"marché {MARCHES[marche]['drapeau']} {marche}" if marche in MARCHES else "",
        "mots " + ", ".join(f"« {m} »" for m in mots) if mots else "",
        "hashtags " + ", ".join("#" + h for h in tous_h) if tous_h else "",
        "comptes " + ", ".join(comptes) if comptes else "",
        f"{args.max} résultats par requête",
        f"recherche sur {args.periode}" if mots and args.periode != "tout" else "",
        f"comme depuis {args.pays}" if args.pays and (mots or hashtags) else ""]))
    return decrit, sum(n for _, _, n, _ in runs), sum(c for _, _, _, c in runs), False, runs


def chercher(args, registre):
    """La passe complète : interroger (ou rejouer un brut), garder le brut, trier, écrire le .md, noter les proposées.
    Renvoie le chemin du .md (None avec --estimer)."""
    quand = dt.datetime.now()
    cout, moteur, runs = 0.0, "Apify", []
    if args.brut:
        d = json.loads(pathlib.Path(args.brut).read_text(encoding="utf-8"))
        items = d.get("items", []) if isinstance(d, dict) else d
        decrit = (d.get("decrit") if isinstance(d, dict) else None) or f"rejoué depuis {pathlib.Path(args.brut).name}"
        moteur = (d.get("moteur") if isinstance(d, dict) else None) or moteur
        brut_nom = pathlib.Path(args.brut).name
        dire(f"Tri rejoué sur {brut_nom} : {len(items)} résultats, sans rien relancer.")
    else:
        mots, hashtags, comptes = cibles(args)
        decrit, nb, cout, gratuit, runs = estimation(args)
        dire(f"Recherche : {decrit}.")
        if gratuit:
            moteur = "yt-dlp"
            if not comptes:
                raise SystemExit('Sans Apify il faut des comptes : --comptes reperes, --comptes fiches ou --comptes "@a,@b".')
            if mots or hashtags:
                dire("Sans Apify, pas de recherche par mots-clés ni par hashtags : je lis seulement les comptes.")
            if args.estimer:
                dire("Rien n'a été lancé (--estimer). Ce mode est gratuit.")
                return None
            if not jeton(obligatoire=False):
                dire("(pas de clé Apify : lecture des comptes avec yt-dlp)")
            items = lire_comptes_gratuit(comptes, args.max)
        else:
            dire(f"Au plus {nb} résultats, ≈ {prix(cout)} sur Apify (tarif de l'offre gratuite).")
            if args.estimer:
                for nom, entree, n, c in runs:
                    dire(f"  run « {nom} » ({n} résultats, ≈ {prix(c)}) : {json.dumps(entree, ensure_ascii=False)}")
                dire("Rien n'a été lancé (--estimer).")
                return None
            jet = jeton()
            dire("Lancement sur Apify (Ctrl-C pour arrêter et ne pas payer la suite)…")
            items = lancer_et_attendre(jet, runs)
        if (getattr(args, "marche", "") or "").upper() in MARCHES:
            for it in items:
                it["_marche"] = args.marche.upper()  # sert seulement quand la langue est impossible à deviner
        CANDIDATS.mkdir(parents=True, exist_ok=True)
        brut_nom = f"{quand:%Y-%m-%d-%H%M}-brut.json"
        (CANDIDATS / brut_nom).write_text(json.dumps(
            {"quand": quand.isoformat(timespec="minutes"), "decrit": decrit, "moteur": moteur, "marche": args.marche,
             "runs": [(n, e) for n, e, _, _ in runs], "cout_estime": round(cout, 2), "items": items},
            ensure_ascii=False), encoding="utf-8")
        dire(f"{len(items)} résultats reçus, gardés dans travail/candidats/{brut_nom}")
    with VERROU:
        gardees, ecartees = trier(items, args, registre)
        CANDIDATS.mkdir(parents=True, exist_ok=True)
        chemin = pathlib.Path(args.sortie) if args.sortie else CANDIDATS / f"{quand:%Y-%m-%d}.md"
        if not args.sortie and chemin.exists():
            chemin = CANDIDATS / f"{quand:%Y-%m-%d-%H%M}.md"
        ecrire_md(chemin, gardees, ecartees, decrit, len(items), cout, brut_nom, args, moteur)
        for c in gardees[:args.garder]:
            if c["id"] not in registre:
                registre[c["id"]] = {"statut": "propose", "date": dt.date.today().isoformat(), "compte": c["compte"],
                                     "lien": c["lien"], "vues": c["vues"], "legende": c["legende"][:120]}
        sauver_registre(registre)
    dire(f"\n{len(gardees)} candidates, {len(ecartees)} écartées → "
         f"{chemin.relative_to(ICI) if chemin.is_relative_to(ICI) else chemin}\n")
    afficher(gardees, args)
    if gardees:
        print("\nPour choisir : python outils/scruter.py --revue   ·   ou --telecharger <id>,<id>   ·   --ecarter <id>")
    return chemin


# ---------- quels hashtags font percer ----------

GENERIQUES = {"fyp", "foryou", "foryoupage", "foryourpage", "fy", "fypage", "fypシ", "fypシ゚viral", "pourtoi", "pourtoii",
              "pourtoiii", "pourtoiiii", "viral", "viralvideo", "viraltiktok", "tiktok", "trend", "trending", "xyzbca",
              "parati", "capcut", "fypviral", "tiktokfrance", "france"}
PERCEE_VUES, PERCEE_RATIO = 20000, 10  # « percer » = au moins 20 000 vues et 10 fois les abonnés du compte


def mediane(v):
    v = sorted(v)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def tous_les_items():
    """Toutes les vidéos de tous les bruts, une fois chacune (la version la plus récente)."""
    items, vus = [], set()
    for brut in sorted(CANDIDATS.glob("*-brut.json"), reverse=True):
        try:
            d = json.loads(brut.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for it in (d.get("items", []) if isinstance(d, dict) else d):
            i = str(champ(it, "id", defaut="") or "")
            if i and i not in vus:
                vus.add(i)
                items.append(it)
    return items


def stats_hashtags(items, jours=60, mini=3, marche=None):
    """Pour chaque hashtag : combien de vidéos, combien ont percé, vues et « × abonnés » médians.
    On ne garde que les vidéos de moins de `jours` jours dont on connaît les abonnés."""
    maintenant = dt.datetime.now(UTC)
    videos = []
    for it in items:
        c = normaliser(it)
        if not c["id"] or not c["abonnes"] or not c["date"] or (maintenant - c["date"]).days > jours or c["pub"]:
            continue
        if marche and c["marche"] != marche:
            continue
        ratio = c["vues"] / c["abonnes"]
        c["ratio"], c["percee"] = ratio, c["vues"] >= PERCEE_VUES and ratio >= PERCEE_RATIO
        videos.append(c)
    base_pct = sum(v["percee"] for v in videos) / len(videos) if videos else 0
    par = {}
    for v in videos:
        for h in {h.lower() for h in v["hashtags"]}:
            par.setdefault(h, []).append(v)
    lignes = []
    for h, vs in par.items():
        if len(vs) < mini:
            continue
        n_p = sum(v["percee"] for v in vs)
        lignes.append({"hashtag": h, "videos": len(vs), "percees": n_p, "pct": n_p / len(vs),
                       "vues_med": mediane([v["vues"] for v in vs]), "ratio_med": mediane([v["ratio"] for v in vs]),
                       "comptes": len({v["compte"] for v in vs}), "generique": h in GENERIQUES,
                       "exemples": [{"id": v["id"], "lien": v["lien"], "vues": v["vues"], "compte": v["compte"]}
                                    for v in sorted(vs, key=lambda v: -v["vues"])[:3]]})
    # Classement prudent : on part de la moyenne et on ne s'en éloigne qu'avec assez de vidéos
    # (1 sur 3 ne passe pas devant 5 sur 16).
    for l in lignes:
        l["score"] = (l["percees"] + 5 * base_pct) / (l["videos"] + 5)
    lignes.sort(key=lambda l: (-l["score"], -l["videos"], -(l["ratio_med"] or 0)))
    return {"marche": marche, "videos": len(videos), "percees": sum(v["percee"] for v in videos), "pct": base_pct, "jours": jours,
            "mini": mini, "seuil_vues": PERCEE_VUES, "seuil_ratio": PERCEE_RATIO, "hashtags": lignes}


# ---------- la page de revue ----------

RECHERCHE = {"en_cours": False, "journal": [], "erreur": None, "fini": None}


def candidats_revue(registre):
    """Toutes les vidéos de tous les bruts (la version la plus récente de chacune), triées, avec leur statut."""
    items = tous_les_items()
    args = parseur().parse_args([])
    gardees, _ = trier(items, args, registre, pour_revue=True)
    return gardees


def pour_json(c):
    d = dict(c)
    d["date"] = c["date"].isoformat() if c.get("date") else None
    d["date_fr"] = date_fr(c["date"]) if c.get("date") else ""
    d["via"] = sorted(c["via"])
    d["titre"] = titre_court(c, 90)
    return d


def analysee(nom):
    """Une fiche cite déjà ce fichier : la vidéo est dans la vidéothèque, on ne touche plus au fichier."""
    for f in FICHES.glob("*.json"):
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("fichier") == nom:
                return True
        except ValueError:
            pass
    return False


def retirer_fichier(ident, r):
    """Approuvée puis refusée : on enlève le MP4 téléchargé par l'outil, sauf s'il est déjà analysé ou déposé à la main."""
    nom = r.get("fichier")
    if not nom:
        return
    if analysee(nom):
        r["note"] = "déjà analysée : fichier gardé, à retirer à la main de la vidéothèque"
        return
    if nom.startswith(f"tiktok-{ident}") and (SOURCE / nom).exists():
        (SOURCE / nom).unlink()
    r.pop("fichier", None)


def telecharger_en_fond(ident, registre):
    try:
        telecharger([ident], registre)
    except BaseException as e:  # SystemExit compris (yt-dlp absent…)
        with VERROU:
            registre.setdefault(ident, {})["erreur"] = (str(e).splitlines()[0][:200] if str(e) else type(e).__name__)
            sauver_registre(registre)
        return
    if chaine.a_faire(registre.get(ident, {})):  # téléchargée : en route vers la vidéothèque
        chaine.ajouter(ident)


def infos_revue(ident, registre=None):
    """La vidéo telle que la revue la voit (marché, chiffres, légende…), pour l'analyse automatique."""
    registre = registre if registre is not None else charger_registre()
    return next((c for c in candidats_revue(registre) if c["id"] == ident), {})


APERCUS_EN_COURS = set()


def apercu(ident, compte=None):
    """Récupère la vidéo (même format h264 que pour la vidéothèque) dans travail/candidats/apercus/ pour la lire
    dans la page. Note l'identifiant interne et, s'il existe, le fichier du dossier d'exemples qui est la même vidéo."""
    cible, infos = APERCUS / f"{ident}.mp4", APERCUS / f"{ident}.json"
    if cible.exists():
        return cible
    try:
        import yt_dlp
        import imageio_ffmpeg
    except ImportError as e:
        infos.write_text(json.dumps({"erreur": f"il manque {e.name} dans le venv"}), encoding="utf-8")
        return None
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    APERCUS.mkdir(parents=True, exist_ok=True)
    url = f"https://www.tiktok.com/@{(compte or '').lstrip('@')}/video/{ident}"
    try:
        with yt_dlp.YoutubeDL({"outtmpl": str(APERCUS / f"{ident}.%(ext)s"), "format_sort": ["vcodec:h264", "res", "br"],
                               "merge_output_format": "mp4", "ffmpeg_location": str(pathlib.Path(ff).parent),
                               "quiet": True, "no_warnings": True, "noplaylist": True, "retries": 3}) as y:
            res = y.extract_info(url, download=True)
            fichier = pathlib.Path(y.prepare_filename(res))
        if fichier.exists() and fichier != cible:
            subprocess.run([ff, "-v", "error", "-y", "-i", str(fichier), "-c", "copy", "-movflags", "+faststart", str(cible)], check=True)
            fichier.unlink()
        vid, jumeau = vid_de(cible, ff), None
        if vid and SOURCE.exists():
            for f in SOURCE.iterdir():
                if f.suffix.lower() in VIDEO_EXT and not f.name.startswith(".") and vid_de(f, ff) == vid:
                    jumeau = f.name
                    break
        infos.write_text(json.dumps({"vid": vid, "jumeau": jumeau, "compte": res.get("uploader") or compte,
                                     "quand": dt.datetime.now().isoformat(timespec="minutes")}, ensure_ascii=False), encoding="utf-8")
        return cible
    except Exception as e:
        infos.write_text(json.dumps({"erreur": (str(e).splitlines()[0][:200] if str(e) else type(e).__name__)},
                                    ensure_ascii=False), encoding="utf-8")
        return None


def apercu_en_fond(ident, compte=None):
    with VERROU:
        if ident in APERCUS_EN_COURS or (APERCUS / f"{ident}.mp4").exists():
            return
        APERCUS_EN_COURS.add(ident)
        (APERCUS / f"{ident}.json").unlink(missing_ok=True)  # on retente après une erreur

    def tache():
        try:
            apercu(ident, compte)
        finally:
            with VERROU:
                APERCUS_EN_COURS.discard(ident)

    threading.Thread(target=tache, daemon=True).start()


def etat_apercu(ident):
    f, j = APERCUS / f"{ident}.mp4", APERCUS / f"{ident}.json"
    try:
        infos = json.loads(j.read_text(encoding="utf-8")) if j.exists() else {}
    except ValueError:
        infos = {}
    return {"pret": f.exists(), "url": f"/apercu/{ident}.mp4" if f.exists() else None, "jumeau": infos.get("jumeau"),
            "erreur": None if f.exists() else infos.get("erreur"), "en_cours": ident in APERCUS_EN_COURS}


def decision(corps, registre):
    ident, quoi = str(corps.get("id") or ""), corps.get("decision")
    statuts = {"refuser": "ecarte", "plus_tard": "plus_tard", "annuler": "propose"}
    if not re.fullmatch(r"\d{15,20}", ident) or quoi not in ("approuver", *statuts):
        return 400, {"erreur": "demande incomplète"}
    with VERROU:
        r = registre.setdefault(ident, {})
        r.pop("erreur", None)
        r["date"] = dt.date.today().isoformat()
        if quoi == "approuver":
            if not (r.get("statut") == "telecharge" and r.get("fichier") and (SOURCE / r["fichier"]).exists()):
                r["statut"] = "approuve"
                threading.Thread(target=telecharger_en_fond, args=(ident, registre), daemon=True).start()
        else:
            retirer_fichier(ident, r)
            r["statut"] = statuts[quoi]
            if quoi == "refuser":
                for f in (APERCUS / f"{ident}.mp4", APERCUS / f"{ident}.json"):
                    f.unlink(missing_ok=True)
        sauver_registre(registre)
        return 200, {"ok": True, "statut": r["statut"]}


def ecrire_env(cle):
    lignes = [l for l in (ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else [])
              if not l.strip().replace("export ", "").startswith("APIFY_TOKEN=")]
    lignes.append(f"APIFY_TOKEN={cle}")
    ENV.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    os.environ["APIFY_TOKEN"] = cle


def enregistrer_cle(cle):
    """Vérifie la clé auprès d'Apify puis l'écrit dans .env. La clé n'est jamais affichée."""
    cle = (cle or "").strip()
    if not cle:
        return 400, {"erreur": "clé vide"}
    try:
        d = apify(cle, "GET", "/users/me", delai=30)
    except SystemExit as e:
        return 400, {"erreur": str(e)}
    nom = ((d or {}).get("data") or {}).get("username") or "?"
    ecrire_env(cle)
    return 200, {"ok": True, "utilisateur": nom}


def lancer_recherche(corps, registre):
    args = parseur().parse_args([])
    for k in ("mots", "hashtags", "comptes"):
        setattr(args, k, str(corps.get(k) or ""))
    try:
        args.max = max(1, min(200, int(corps.get("max") or 30)))
    except (TypeError, ValueError):
        args.max = 30
    args.sans_apify = bool(corps.get("sans_apify"))
    args.marche = str(corps.get("marche") or "").upper() if str(corps.get("marche") or "").upper() in MARCHES else ""
    if corps.get("estimer"):
        decrit, nb, cout, gratuit, _ = estimation(args)
        return 200, {"decrit": decrit, "resultats": nb, "cout": round(cout, 2), "gratuit": gratuit}
    if RECHERCHE["en_cours"]:
        return 409, {"erreur": "une recherche est déjà en cours"}
    RECHERCHE.update({"en_cours": True, "journal": [], "erreur": None, "fini": None})

    def tache():
        global JOURNAL
        JOURNAL = RECHERCHE["journal"]
        try:
            chemin = chercher(args, registre)
            RECHERCHE["fini"] = chemin.name if chemin else "rien à lancer"
        except BaseException as e:
            RECHERCHE["erreur"] = (str(e).splitlines()[0][:300] if str(e) else type(e).__name__)
        finally:
            RECHERCHE["en_cours"] = False
            JOURNAL = None

    threading.Thread(target=tache, daemon=True).start()
    return 200, {"ok": True}


def revue(args, registre):
    page = ICI / "outils" / "revue.html"
    site = (ICI / "site").resolve()
    types = {".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".html": "text/html; charset=utf-8",
             ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
             ".mp4": "video/mp4"}

    class Page(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def repondre(self, code, corps, type_="application/json; charset=utf-8"):
            if isinstance(corps, (dict, list)):
                corps = json.dumps(corps, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            if self.headers.get("Origin") == ORIGINE_PUBLIQUE and self.path.startswith("/api/etat"):
                self.send_header("Access-Control-Allow-Origin", ORIGINE_PUBLIQUE)  # le site public lit les compteurs
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", type_)
            self.send_header("Content-Length", str(len(corps)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corps)

        def envoyer_fichier(self, f, type_):
            """Envoie un fichier en acceptant les demandes partielles (Range), pour que le lecteur puisse avancer."""
            taille = f.stat().st_size
            debut, fin, code = 0, taille - 1, 200
            plage = self.headers.get("Range", "")
            if plage.startswith("bytes="):
                a, _, b = plage[6:].partition("-")
                try:
                    if a:
                        debut, fin = int(a), (int(b) if b else fin)
                    elif b:
                        debut = max(0, taille - int(b))
                    code = 206
                except ValueError:
                    debut, fin, code = 0, taille - 1, 200
                fin = min(fin, taille - 1)
            self.send_response(code)
            self.send_header("Content-Type", type_)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(fin - debut + 1))
            if code == 206:
                self.send_header("Content-Range", f"bytes {debut}-{fin}/{taille}")
            self.end_headers()
            try:
                with open(f, "rb") as h:
                    h.seek(debut)
                    reste = fin - debut + 1
                    while reste > 0:
                        bloc = h.read(min(1 << 20, reste))
                        if not bloc:
                            break
                        self.wfile.write(bloc)
                        reste -= len(bloc)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def relire(self):
            with VERROU:
                frais = charger_registre()
                for i, r in frais.items():  # le disque gagne, sauf pour un téléchargement en cours ici
                    if registre.get(i, {}).get("statut") != "approuve":
                        registre[i] = r

        def do_GET(self):
            self.relire()
            decoupe = urllib.parse.urlparse(self.path)
            chemin, requete = decoupe.path, urllib.parse.parse_qs(decoupe.query)
            if chemin in ("/revue", "/revue.html"):
                return self.repondre(200, page.read_bytes(), "text/html; charset=utf-8")
            if chemin == "/api/etat":
                with VERROU:
                    cands = [pour_json(c) for c in candidats_revue(registre)]
                for c in cands:
                    c["apercu"] = etat_apercu(c["id"])
                    c["chaine"] = registre.get(c["id"], {}).get("chaine")
                return self.repondre(200, {"candidats": cands, "cle": bool(jeton(obligatoire=False)), "marches": MARCHES,
                                           "recherche": RECHERCHE, "source": str(SOURCE)})
            if chemin == "/api/hashtags":
                m = (requete.get("marche") or [""])[0].upper()
                return self.repondre(200, stats_hashtags(tous_les_items(), marche=m if m in MARCHES else None))
            if chemin == "/api/apercu":
                ident = (requete.get("id") or [""])[0]
                if not re.fullmatch(r"\d{15,20}", ident):
                    return self.repondre(400, {"erreur": "id ?"})
                e = etat_apercu(ident)
                if not e["pret"] and not e["en_cours"]:
                    apercu_en_fond(ident, registre.get(ident, {}).get("compte"))
                    e = etat_apercu(ident)
                return self.repondre(200, e)
            m = re.fullmatch(r"/apercu/(\d{15,20})\.mp4", chemin)
            if m and (APERCUS / f"{m.group(1)}.mp4").exists():
                return self.envoyer_fichier(APERCUS / f"{m.group(1)}.mp4", "video/mp4")
            # Le reste : le site de la vidéothèque (le même que sur GitHub Pages, mais ici la revue s'affiche dans le menu).
            f = (site / (urllib.parse.unquote(chemin).lstrip("/") or "index.html")).resolve()
            if f.is_dir():
                f = f / "index.html"
            if f.is_file() and str(f).startswith(str(site) + os.sep):
                if f.suffix == ".mp4":
                    return self.envoyer_fichier(f, "video/mp4")
                return self.repondre(200, f.read_bytes(), types.get(f.suffix, "application/octet-stream"))
            self.repondre(404, {"erreur": "introuvable"})

        def do_OPTIONS(self):
            # Le site public (GitHub Pages) demande la permission de lire /api/etat sur ce Mac (Chrome : réseau local).
            if self.headers.get("Origin") != ORIGINE_PUBLIQUE:
                return self.repondre(403, {"erreur": "origine refusée"})
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", ORIGINE_PUBLIQUE)
            self.send_header("Access-Control-Allow-Methods", "GET")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Local-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_POST(self):
            # Les actions (approuver, chercher, clé…) ne viennent que de la page servie ici : un autre site ne peut pas
            # en déclencher (dépenser le crédit Apify, changer la clé).
            origine = self.headers.get("Origin")
            if origine and not re.fullmatch(r"http://(127\.0\.0\.1|localhost):\d+", origine):
                return self.repondre(403, {"erreur": "origine refusée"})
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self.repondre(415, {"erreur": "JSON attendu"})
            self.relire()
            chemin = urllib.parse.urlparse(self.path).path
            try:
                n = int(self.headers.get("Content-Length") or 0)
                corps = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            except ValueError:
                return self.repondre(400, {"erreur": "JSON illisible"})
            if chemin == "/api/decision":
                return self.repondre(*decision(corps, registre))
            if chemin == "/api/cle":
                return self.repondre(*enregistrer_cle(corps.get("cle", "")))
            if chemin == "/api/recherche":
                return self.repondre(*lancer_recherche(corps, registre))
            if chemin == "/api/relancer":  # la chaîne automatique a échoué : on recommence
                ident = str(corps.get("id") or "")
                with VERROU:
                    r = registre.get(ident)
                    if not r or not re.fullmatch(r"\d{15,20}", ident):
                        return self.repondre(400, {"erreur": "id ?"})
                    r.pop("chaine", None)
                    sauver_registre(registre)
                    pret = chaine.a_faire(r)
                if pret:
                    chaine.ajouter(ident)
                return self.repondre(200, {"ok": pret})
            self.repondre(404, {"erreur": "introuvable"})

    # La chaîne automatique, et le rattrapage : les vidéos approuvées et téléchargées qui ne sont pas encore en ligne.
    chaine.demarrer(registre, VERROU, sauver_registre, lambda i: infos_revue(i, registre))
    for i, r in list(registre.items()):
        if chaine.a_faire(r):
            chaine.ajouter(i)
    if APERCUS.exists():  # ménage : les aperçus de plus de 14 jours
        limite = time.time() - 14 * 86400
        for f in APERCUS.iterdir():
            if f.is_file() and f.stat().st_mtime < limite:
                f.unlink()
    url = f"http://127.0.0.1:{args.port}/"
    ouvrir = url + "#revue/fr"
    try:
        serveur = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Page)
    except OSError:
        print(f"La page de revue tourne déjà : {url}", flush=True)
        if not args.sans_navigateur:
            webbrowser.open(ouvrir)
        return
    print(f"Vidéothèque + revue : {url}   (Ctrl-C pour arrêter)", flush=True)
    if not args.sans_navigateur:
        webbrowser.open(ouvrir)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêté.")


# ---------- programme ----------

def parseur():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mots", help='mots-clés cherchés sur TikTok, séparés par des virgules (ex. "app,astuce,IA")')
    p.add_argument("--hashtags", help='hashtags, séparés par des virgules, avec ou sans # (ex. "astuce,ia")')
    p.add_argument("--comptes", help='comptes à lire : "@a,@b", ou "reperes" (les comptes repérés), ou "fiches" (tous les comptes '
                                     'des fiches), ou "aucun"')
    p.add_argument("--marche", type=str.upper, choices=["", *MARCHES], default="",
                   help="FR ou US : sans --mots ni --hashtags, cherche les mots du marché (FR : application, astuce, IA, "
                        "argent en ligne ; US : app, life hack, ai tools, side hustle) et ne garde que les vidéos dans sa langue")
    p.add_argument("--max", type=int, default=30, help="résultats par mot, hashtag ou compte (défaut 30)")
    p.add_argument("--jours", type=int, default=60, help="âge maxi des vidéos, en jours (défaut 60)")
    p.add_argument("--vues-min", type=int, default=20000, help="vues minimum (défaut 20 000)")
    p.add_argument("--ratio", type=float, default=1.5, help="vues / abonnés minimum (défaut 1,5)")
    p.add_argument("--outil", action="store_true", help="garder seulement les vidéos qui parlent d'une app ou d'un outil")
    p.add_argument("--periode", choices=list(PERIODES), default="tout",
                   help="filtre de date de la recherche par mots côté TikTok (défaut : aucun). Attention : le 21/10/2026 ce "
                        "filtre faisait échouer Apify (0 résultat) ; l'âge est de toute façon trié ici (--jours)")
    p.add_argument("--pays", default="", help="chercher comme depuis ce pays, ex. FR (défaut : sans ; les mots français "
                                              "donnent déjà des vidéos françaises, et c'est +0,0013 $ par vidéo)")
    p.add_argument("--sans-hashtags-auto", action="store_true",
                   help="ne pas chercher aussi chaque mot comme hashtag (par défaut « side business » cherche aussi #sidebusiness)")
    p.add_argument("--garder", type=int, default=50, help="nombre maxi de candidates dans la liste (défaut 50)")
    p.add_argument("--sans-apify", action="store_true",
                   help="lire les comptes avec yt-dlp, gratuit et sans clé (pas de mots-clés ni de hashtags, pas les enregistrements) ; "
                        "c'est aussi ce qui se passe tout seul quand il n'y a pas de clé et seulement des comptes")
    p.add_argument("--estimer", action="store_true", help="montrer ce qui serait lancé et le coût, sans rien lancer")
    p.add_argument("--brut", help="rejouer le tri sur un fichier -brut.json déjà reçu (gratuit)")
    p.add_argument("--sortie", help="chemin du .md (défaut travail/candidats/<date>.md)")
    p.add_argument("--telecharger", help="ids ou liens TikTok à télécharger, séparés par des virgules")
    p.add_argument("--ecarter", help="ids à ne plus jamais proposer, séparés par des virgules")
    p.add_argument("--revue", action="store_true",
                   help="ouvrir la page de revue : approuver / refuser chaque vidéo, téléchargement à l'approbation, recherche, clé")
    p.add_argument("--port", type=int, default=3110, help="port de la page de revue (défaut 3110)")
    p.add_argument("--sans-navigateur", action="store_true", help="avec --revue : ne pas ouvrir le navigateur")
    p.add_argument("--cle", action="store_true", help="saisir la clé Apify (cachée), la vérifier et l'écrire dans .env")
    p.add_argument("--percer", action="store_true",
                   help="classer les hashtags qui font percer, d'après toutes les vidéos déjà lues (gratuit)")
    return p


def main():
    args = parseur().parse_args()
    if args.pays and args.pays.lower() in ("aucun", "none", "non"):
        args.pays = ""
    registre = charger_registre()

    if args.cle:
        import getpass
        code, rep = enregistrer_cle(getpass.getpass("Colle ta clé Apify (elle ne s'affiche pas) : "))
        if rep.get("ok"):
            print(f"✓ clé enregistrée dans {ENV} (compte Apify « {rep['utilisateur']} »)")
            return
        sys.exit(f"✗ {rep.get('erreur')}")
    if args.ecarter:
        for ref in decouper(args.ecarter):
            ident, compte = identifiant(ref, registre)
            registre[ident] = {**registre.get(ident, {}), "statut": "ecarte", "date": dt.date.today().isoformat(),
                               "compte": compte or registre.get(ident, {}).get("compte")}
            print(f"  écartée : {ident}")
        sauver_registre(registre)
        return
    if args.telecharger:
        telecharger(decouper(args.telecharger), registre)
        return
    if args.revue:
        revue(args, registre)
        return
    if args.percer:
        st = stats_hashtags(tous_les_items(), jours=args.jours, marche=args.marche.upper() or None)
        if st["marche"]:
            print(f"Marché {MARCHES[st['marche']]['drapeau']} {st['marche']}")
        print(f"{st['videos']} vidéos de moins de {st['jours']} jours, {st['percees']} ont percé ({pct(st['pct'])}) "
              f"· percer = au moins {nombre(st['seuil_vues'])} vues et {st['seuil_ratio']}× les abonnés · hashtags vus au moins {st['mini']} fois\n")
        for l in st["hashtags"]:
            r = l["ratio_med"]
            print(f"  #{l['hashtag']:<22} {l['percees']:>3}/{l['videos']:<3} percent ({pct(l['pct']):>7}) · vues médianes "
                  f"{nombre(round(l['vues_med'])):>7} · " + (f"{r:.0f}" if r >= 10 else f"{r:.1f}".replace(".", ","))
                  + f"× abonnés · {l['comptes']} compte" + ("s" if l["comptes"] > 1 else "")
                  + ("  (générique)" if l["generique"] else ""))
        return
    chercher(args, registre)


if __name__ == "__main__":
    main()
