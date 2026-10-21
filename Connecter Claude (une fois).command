#!/bin/zsh
# À faire une seule fois : connecte le programme « claude » de ce Mac à ton compte Claude.
# C'est lui qui analyse tout seul les vidéos approuvées dans la revue (format, hook, chronologie…).
# Une page s'ouvre dans le navigateur : connecte-toi avec ton compte Claude, puis reviens ici.
claude auth login
echo ""
claude auth status
echo ""
echo "C'est bon : tu peux fermer cette fenêtre. Dans la revue, « Relancer » sur une vidéo en erreur."
read -k 1 "?Appuie sur une touche pour fermer."
