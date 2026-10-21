# Vidéothèque MS Clipper

Un site public, dans la charte MS Clipper, où les créateurs regardent des vidéos qui ont marché et comprennent **comment elles sont faites** : le hook, ce qu'on voit, ce qu'on entend, pourquoi ça marche, et comment refaire pareil avec nos apps (Screen My Car, Revizio, MS Clipper).

- Site : `site/` (HTML, CSS, JavaScript, sans dépendance). Données : `site/data/videos.json` (généré) et `site/data/referentiel.json` (formats, apps, lexique, écrit à la main).
- Vidéos sources : `FICHIERS ANNEXES/VIDÉOS D'EXEMPLES` (Mylan les dépose là).
- Fiches écrites à la main : `outils/fiches/<id>.json` (une par vidéo). Le champ `ordre` règle l'ordre d'affichage.
- En ligne : GitHub Pages, dépôt `mylanb-create/ms-clipper-videotheque`, mise en ligne automatique à chaque push sur `main` (`.github/workflows/pages.yml`).

## Ajouter une vidéo

1. Déposer le fichier dans `FICHIERS ANNEXES/VIDÉOS D'EXEMPLES`.
2. `"../../SCREEN MY CAR/CRÉATEURS/Briefs revclip/Vidéothèque/.venv/bin/python" outils/preparer.py`
   → crée `site/videos/<id>.mp4`, `site/posters/<id>.jpg`, et dans `travail/<id>/` : `hook.jpg` (les 5 premières secondes), `planche.jpg` (toute la vidéo), `transcription.json`.
3. Regarder `hook.jpg`, `planche.jpg` et la transcription, puis écrire `outils/fiches/<id>.json` sur le modèle des autres (titre, sujet, hook dit / vu, texte à l'écran, étapes avec les secondes, pourquoi ça marche, à refaire chez nous, tags, note).
   Pour un carrousel (capture d'écran TikTok) : `"type": "carrousel"`, les images dans `site/carrousels/<id>/`, et des étapes avec `"photo": n`.
4. `python3 outils/assembler.py` → régénère `site/data/videos.json`.
5. Vérifier en local : `python3 -m http.server 3109 --directory site`, puis http://localhost:3109.
6. `git add -A && git commit -m "Ajout : <titre>" && git push` → en ligne 1 à 2 minutes plus tard.

## Les règles d'écriture des fiches

- On parle à quelqu'un de 8 ans : phrases courtes, pas de jargon (les mots techniques sont dans le lexique du site).
- On garde la **mécanique**, jamais le sujet : les exemples viennent d'autres niches.
- « À refaire chez nous » respecte le brief créateurs : le résultat d'abord, « faire un Screen My Car » avant 20 s, l'écran montré (téléphone en main ou incrustation), jamais « je te présente X, c'est un site qui… ».
- Une vidéo reprise (déjà virale) n'est autorisée que si c'est nous qui la donnons au créateur.
