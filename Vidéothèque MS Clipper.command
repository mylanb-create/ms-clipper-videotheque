#!/bin/zsh
# Double-clic : ouvre la vidéothèque complète sur ce Mac (http://127.0.0.1:3110) : les vidéos FR / US, le guide,
# et la revue des vidéos TikTok (FR / US, approuver, refuser) dans le menu de gauche.
# Fermer cette fenêtre (ou Ctrl-C) arrête la page.
cd "$(dirname "$0")" || exit 1
exec "../../SCREEN MY CAR/CRÉATEURS/Briefs revclip/Vidéothèque/.venv/bin/python" outils/scruter.py --revue
