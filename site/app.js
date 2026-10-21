/* Vidéothèque MS Clipper — tout en JavaScript simple, sans dépendance.
   Les données : data/referentiel.json (formats, hooks, apps, règles, lexique) et data/videos.json (une fiche par vidéo).
   Le même site tourne à deux endroits : sur GitHub Pages (public, pour les créateurs) et sur le Mac de Mylan, servi par
   outils/scruter.py --revue. Là, /api/etat répond et le menu montre en plus la revue des vidéos TikTok (iframe /revue).
   Sur le site public, le navigateur de Mylan (marqué une fois avec #admin) voit aussi « Revue de vidéos » : ces liens
   ouvrent le même site sur son Mac (http://127.0.0.1:3110). Chrome interdit à une page publique de lire le réseau local
   (fetch ou iframe), mais pas d'y naviguer. Les créateurs ne voient jamais la revue. */
const LOCAL = "http://127.0.0.1:3110";

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

let REF = { formats: {}, hooks: {}, apps: {}, regles: [], lexique: [] };
let VIDEOS = [];
let filtre = { format: "tous", q: "" };
let ouverte = null;
let marche = "FR"; // le marché affiché dans la vidéothèque : FR ou US
let local = false; // vrai quand la revue est disponible (site servi par scruter.py, ou le Mac de Mylan joignable)
let admin = false; // le navigateur de Mylan
let nbFiches = null;
let aRevoir = { FR: 0, US: 0 };
let enCours = []; // vidéos approuvées dans la revue pas encore dans la vidéothèque (sur le Mac seulement)
const MARCHES = { FR: "🇫🇷 Vidéos FR", US: "🇺🇸 Vidéos US" };

async function init() {
  const [ref, videos] = await Promise.all([
    // « no-cache » : le navigateur revérifie à chaque visite, une vidéo ajoutée apparaît tout de suite.
    fetch("data/referentiel.json", { cache: "no-cache" }).then((r) => r.json()),
    fetch("data/videos.json", { cache: "no-cache" }).then((r) => r.json()),
  ]);
  REF = ref;
  VIDEOS = videos.filter((v) => v.titre).map((v) => ({ ...v, marche: v.marche || "FR" }));
  $("#n-fr").textContent = VIDEOS.filter((v) => v.marche === "FR").length;
  $("#n-us").textContent = VIDEOS.filter((v) => v.marche === "US").length || "";
  rendreGuide();
  brancherMenu();
  await detecterRevue();
  $("#q").addEventListener("input", (e) => { filtre.q = e.target.value.trim().toLowerCase(); rendreGrille(); });
  $("#prec").addEventListener("click", () => voisine(-1));
  $("#suiv").addEventListener("click", () => voisine(1));
  document.addEventListener("keydown", (e) => {
    if (!ouverte || e.target.tagName === "INPUT") return;
    if (e.key === "Escape") location.hash = "";
    if (e.key === "ArrowLeft") voisine(-1);
    if (e.key === "ArrowRight") voisine(1);
    if (e.key === " " && e.target.tagName !== "VIDEO" && e.target.tagName !== "BUTTON") { e.preventDefault(); const v = $("#fiche video"); if (v) v.paused ? v.play() : v.pause(); }
  });
  window.addEventListener("hashchange", router);
  router();
}

/* ——— Menu de gauche ——— */

function brancherMenu() {
  const fermerMenu = () => { document.body.classList.remove("menu-ouvert"); $("#voile").hidden = true; };
  $("#menu").addEventListener("click", () => { document.body.classList.add("menu-ouvert"); $("#voile").hidden = false; });
  $("#voile").addEventListener("click", fermerMenu);
  document.querySelectorAll(".side-lien, .side-marque").forEach((a) => a.addEventListener("click", fermerMenu));
}

function surligner(route) {
  document.querySelectorAll(".side-lien").forEach((a) => a.classList.toggle("actif", a.dataset.route === route));
  const lien = document.querySelector(`.side-lien[data-route="${route}"] span`);
  $("#barre-titre").textContent = lien ? lien.textContent : "";
}

// La revue n'existe que sur le Mac de Mylan : sur ce serveur, ou (site public) en lien vers son Mac.
async function detecterRevue() {
  try {
    if (/^#admin/.test(location.hash)) { localStorage.setItem("vt-admin", "1"); history.replaceState(null, "", "#fr"); }
    admin = localStorage.getItem("vt-admin") === "1";
  } catch (e) {}
  const etat = await lireEtat();
  if (!etat) {
    if (admin) {
      // Site public, navigateur de Mylan : les liens de la revue ouvrent le site de son Mac.
      $("#side-revue").hidden = false;
      document.querySelectorAll("#side-revue .side-lien").forEach((a) => {
        a.href = `${LOCAL}/${a.getAttribute("href")}`;
        a.removeAttribute("data-route");
      });
      $("#side-revue-aide").hidden = false;
    }
    return;
  }
  local = true;
  $("#side-revue").hidden = false;
  $("#lien-public").hidden = false;
  compterRevue(etat.candidats);
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin) return;
    if (e.data?.type === "revue") {
      aRevoir = e.data.aRevoir;
      afficherRevue();
      if (nbFiches !== null && e.data.fiches > nbFiches) rechargerVideos();
      nbFiches = e.data.fiches;
    }
    if (e.data?.type === "ouvrir") location.hash = `#v/${e.data.id}`;
  });
  setInterval(async () => {
    const e = await lireEtat();
    if (e) compterRevue(e.candidats);
  }, 10000);
}

async function lireEtat() {
  try {
    const r = await fetch("api/etat", { cache: "no-store" });
    if (!r.ok) return null;
    const e = await r.json();
    return Array.isArray(e.candidats) ? e : null;
  } catch (e) {
    return null;
  }
}

// Une fiche vient d'arriver (chaîne automatique) : on relit les vidéos. Sur le site public, GitHub Pages met 1 à 2 min
// à publier : on relit encore un peu plus tard.
async function rechargerVideos() {
  const relire = async () => {
    try {
      const videos = await (await fetch("data/videos.json", { cache: "no-store" })).json();
      VIDEOS = videos.filter((v) => v.titre).map((v) => ({ ...v, marche: v.marche || "FR" }));
      $("#n-fr").textContent = VIDEOS.filter((v) => v.marche === "FR").length;
      $("#n-us").textContent = VIDEOS.filter((v) => v.marche === "US").length || "";
      if (!$("#accueil").hidden) { rendreChips(); rendreGrille(); }
    } catch (e) {}
  };
  await relire();
}

function compterRevue(candidats) {
  const n = (m) => candidats.filter((c) => (!c.statut || c.statut === "propose") && c.marche === m).length;
  aRevoir = { FR: n("FR"), US: n("US") };
  nbFiches = candidats.filter((c) => c.fiche && (c.chaine || {}).etape !== "publication").length;
  afficherRevue();
  // Approuvées mais pas encore dans la liste des vidéos : on les montre tout de suite, avec leur étape.
  const connues = new Set(VIDEOS.map((v) => v.id));
  const avant = enCours.map((c) => c.id).join();
  enCours = candidats
    .filter((c) => (c.statut === "approuve" || c.statut === "telecharge") && (c.chaine || {}).etape !== "fini"
      && !connues.has(c.fiche || "t" + c.id.slice(-8)))
    .sort((a, b) => String(b.approuve_le || "").localeCompare(String(a.approuve_le || "")));
  const fini = avant.split(",").filter((id) => id && !enCours.some((c) => c.id === id)).length > 0;
  if (fini) rechargerVideos(); // une vidéo vient d'arriver : la vraie fiche remplace la carte « en cours »
  else if (!$("#accueil").hidden && enCours.map((c) => c.id).join() !== avant) rendreGrille();
}

const ETAPES = {
  attente: ["⏳ en file d'attente", "Arrive dans quelques minutes"],
  preparation: ["⏳ 1/3 préparation", "Arrive dans 3 à 5 min"],
  analyse: ["🧠 2/3 Claude l'analyse", "Arrive dans 1 à 3 min"],
  publication: ["🚀 3/3 mise en ligne", "Arrive dans quelques secondes"],
};

function carteEnCours(c) {
  const ch = c.chaine || {};
  const [etape, quand] = ch.erreur ? ["✗ erreur", "Relance-la dans la revue"]
    : ETAPES[ch.etape] || [c.statut === "approuve" ? "⏳ téléchargement" : "⏳ en file d'attente", "Arrive dans quelques minutes"];
  const apercu = c.apercu && c.apercu.pret ? `<video src="${esc(c.apercu.url)}#t=1" muted playsinline preload="metadata"></video>` : "";
  return `<a class="carte en-cours" href="#revue/${c.marche.toLowerCase()}" title="Tu viens de l'approuver : Claude l'analyse, puis sa fiche remplace cette carte toute seule">
    <div class="visuel">
      <span class="badge">${esc(etape)}</span>
      ${apercu}
      <span class="hook">${esc(c.legende || c.titre || "")}</span>
      <span class="duree">${c.duree ? mmss(c.duree) : ""}</span>
    </div>
    <div class="infos">
      <div class="titre">${esc(quand)}</div>
      <div class="compte"><span>${esc(c.compte)}</span><span>tu viens de l'approuver</span></div>
    </div>
  </a>`;
}

function afficherRevue() {
  $("#r-fr").textContent = aRevoir.FR || "";
  $("#r-us").textContent = aRevoir.US || "";
}

const poster = (v) => (v.type === "carrousel" ? v.images?.[0] : `posters/${v.id}.jpg`);
const dureeTexte = (v) => (v.type === "carrousel" ? `${v.images?.length || 0} photos` : mmss(v.duree));
const formatDe = (v) => REF.formats[v.format] || { nom: v.format, emoji: "", def: "" };

/* ——— Accueil ——— */

const duMarche = () => VIDEOS.filter((v) => v.marche === marche);

function rendreChips() {
  const compte = {};
  const videos = duMarche();
  videos.forEach((v) => { compte[v.format] = (compte[v.format] || 0) + 1; });
  const ordre = Object.keys(REF.formats).filter((k) => compte[k]);
  if (filtre.format !== "tous" && !compte[filtre.format]) filtre.format = "tous";
  const chips = videos.length ? [["tous", "Tout", videos.length], ...ordre.map((k) => [k, `${REF.formats[k].emoji} ${REF.formats[k].nom}`, compte[k]])] : [];
  $("#chips-formats").innerHTML = chips
    .map(([k, nom, n]) => `<button class="chip${filtre.format === k ? " actif" : ""}" data-f="${k}">${nom}<span class="n">${n}</span></button>`)
    .join("");
  $("#chips-formats").querySelectorAll(".chip").forEach((b) =>
    b.addEventListener("click", () => { filtre.format = b.dataset.f; rendreChips(); rendreGrille(); })
  );
}

function texteRecherche(v) {
  return [v.titre, v.sujet, v.compte, v.niche, v.hook?.dit, v.hook?.voit, (v.texte_ecran || []).join(" "), v.point_sensible, v.mise_en_scene, v.reprise_quoi,
    (v.pourquoi || []).map((p) => `${p.titre} ${p.texte}`).join(" "), (v.tags || []).join(" "),
    (v.etapes || []).map((e) => `${e.voit} ${e.entend}`).join(" "), formatDe(v).nom,
    (v.hooks || []).map((h) => REF.hooks[h]?.nom).join(" "), v.reprise ? "reprise" : ""]
    .join(" ").toLowerCase();
}

function filtrees() {
  return duMarche().filter((v) => (filtre.format === "tous" || v.format === filtre.format) && (!filtre.q || texteRecherche(v).includes(filtre.q)));
}

function carte(v) {
  const f = formatDe(v);
  return `<a class="carte" href="#v/${v.id}">
    <div class="visuel">
      <span class="badge">${f.emoji} ${esc(f.nom)}</span>
      ${v.reprise ? `<span class="badge gris badge-droite">🔁 reprise</span>` : ""}
      <img src="${poster(v)}" alt="" loading="lazy">
      <span class="hook">${esc(v.hook?.dit || v.titre)}</span>
      <span class="duree">${dureeTexte(v)}</span>
    </div>
    <div class="infos">
      <div class="titre">${esc(v.titre)}</div>
      <div class="compte"><span>${esc(v.compte)}</span><span>${esc(v.niche || "")}</span></div>
    </div>
  </a>`;
}

function rendreGrille() {
  const liste = filtrees();
  const rien = !duMarche().length;
  $("#vide").hidden = liste.length > 0;
  $("#vide").innerHTML = !rien
    ? "Rien avec ce mot. Essaie un autre, ou enlève le filtre."
    : local
      ? `<b>Pas encore de vidéo ${marche} ici.</b><br>Approuve des vidéos dans la <a href="#revue/${marche.toLowerCase()}">revue ${marche}</a> : une fois analysées, elles arrivent ici, rangées par format.`
      : `<b>Les vidéos ${marche} arrivent bientôt.</b><br>En attendant, regarde les <a href="#fr">vidéos FR</a> : la mécanique est la même.`;
  const attente = filtre.format === "tous" && !filtre.q ? enCours.filter((c) => c.marche === marche) : [];
  if (attente.length) $("#vide").hidden = true;
  $("#grille").innerHTML = attente.map(carteEnCours).join("") + liste.map(carte).join("");
}

function afficherVideotheque(m) {
  if (m !== marche) { marche = m; filtre = { format: "tous", q: "" }; $("#q").value = ""; }
  $("#hero-marche").textContent = MARCHES[marche];
  rendreChips();
  rendreGrille();
  $("#accueil").hidden = false;
  $("#revue").hidden = true;
  surligner(marche.toLowerCase());
  document.title = `${MARCHES[marche].slice(5)} · Vidéothèque MS Clipper`;
}

function afficherRevuePage(m) {
  const cadre = $("#revue-cadre");
  $("#revue-eteinte").hidden = local;
  cadre.hidden = !local;
  $("#ouvrir-mac").href = `${LOCAL}/#revue/${m.toLowerCase()}`;
  const src = `revue?embed=1&marche=${m}`;
  if (local && cadre.getAttribute("src") !== src) cadre.setAttribute("src", src);
  $("#accueil").hidden = true;
  $("#revue").hidden = false;
  surligner(`revue/${m.toLowerCase()}`);
  document.title = `Revue ${m} · Vidéothèque MS Clipper`;
  cadre.addEventListener("load", () => cadre.contentWindow?.focus(), { once: true });
}

/* ——— Guide ——— */

function miniCarte(v) {
  return `<a class="mini" href="#v/${v.id}"><img src="${poster(v)}" alt="" loading="lazy"><span>${esc(v.titre)}</span></a>`;
}

function rendreGuide() {
  $("#g-formats").innerHTML = Object.entries(REF.formats).map(([k, f]) => {
    const ex = VIDEOS.filter((v) => v.format === k);
    return `<article class="g-format">
      <div class="g-format-tete"><span class="g-emoji">${f.emoji}</span><h3>${esc(f.nom)}</h3><span class="g-n">${ex.length ? `${ex.length} exemple${ex.length > 1 ? "s" : ""}` : "pas encore d'exemple"}</span></div>
      <p class="g-def">${esc(f.def)}</p>
      <div class="g-lignes">
        <p><b>👀 Comment le reconnaître</b>${esc(f.reconnaitre)}</p>
        <p><b>📏 La règle chez nous</b>${esc(f.regle)}</p>
      </div>
      ${ex.length ? `<div class="minis">${ex.map(miniCarte).join("")}</div>` : ""}
    </article>`;
  }).join("");

  $("#g-hooks").innerHTML = Object.entries(REF.hooks).map(([k, h]) => {
    const ex = VIDEOS.filter((v) => (v.hooks || []).includes(k));
    return `<article class="g-hook">
      <div class="g-hook-tete"><span class="g-emoji">${h.emoji}</span><h3>${esc(h.nom)}</h3></div>
      <p class="g-def">${esc(h.def)}</p>
      ${ex.length ? `<ul class="g-ex">${ex.map((v) => `<li><a href="#v/${v.id}">« ${esc((v.hook?.dit || "").slice(0, 90))}${(v.hook?.dit || "").length > 90 ? "…" : ""} »<small>${esc(v.compte)}</small></a></li>`).join("")}</ul>` : ""}
    </article>`;
  }).join("");

  $("#g-regles").innerHTML = (REF.regles || []).map((r) => `<div class="g-regle"><span class="g-emoji">${r.emoji}</span><div><b>${esc(r.titre)}</b><p>${esc(r.texte)}</p></div></div>`).join("");
  $("#defs").innerHTML = (REF.lexique || []).map((d) => `<div class="def"><div class="mot"><span>${d.emoji}</span>${esc(d.mot)}</div><p>${esc(d.def)}</p></div>`).join("");
}

/* ——— Navigation ——— */

function router() {
  const h = location.hash;
  const m = h.match(/^#v\/([\w-]+)/);
  const v = m && VIDEOS.find((x) => x.id === m[1]);
  if (v) {
    fermerGuide();
    if (v.marche !== marche || $("#accueil").hidden) afficherVideotheque(v.marche);
    ouvrir(v);
    return;
  }
  fermer();
  if (h.startsWith("#guide")) {
    ouvrirGuide();
    surligner("guide");
    const cible = h.length > 6 ? document.getElementById(h.slice(1)) : null;
    if (cible) cible.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  fermerGuide();
  const r = h.match(/^#revue\/(fr|us)$/);
  if (r && (local || admin)) return afficherRevuePage(r[1].toUpperCase());
  afficherVideotheque(h === "#us" ? "US" : "FR");
}

function ouvrirGuide() {
  $("#retour-guide").setAttribute("href", `#${marche.toLowerCase()}`);
  if (!$("#guide").hidden) return;
  $("#guide").hidden = false;
  document.body.classList.add("fige");
  $("#guide").scrollTo(0, 0);
  document.title = "Le guide · Vidéothèque MS Clipper";
}
function fermerGuide() {
  if ($("#guide").hidden) return;
  $("#guide").hidden = true;
  document.body.classList.remove("fige");
  document.title = "Vidéothèque MS Clipper";
}

function voisine(sens) {
  const liste = filtrees();
  const i = liste.findIndex((v) => v.id === ouverte?.id);
  const cible = liste[i + sens];
  if (cible) location.hash = `#v/${cible.id}`;
}

function fermer() {
  if (!ouverte) return;
  const vid = $("#fiche video");
  if (vid) vid.pause();
  ouverte = null;
  $("#fiche").hidden = true;
  document.body.classList.remove("fige");
  document.title = "Vidéothèque MS Clipper";
}

/* ——— Fiche ——— */

const CHRONO = [
  ["hook", "🪝", "Le hook"],
  ["probleme", "🩹", "Le point sensible"],
  ["outil_nomme", "🗣️", "L'outil nommé"],
  ["outil_montre", "📱", "L'outil montré"],
  ["preuve", "🧾", "La preuve"],
  ["fin", "🏁", "La fin"],
];
const OK = { oui: ["✅", "on garde"], non: ["❌", "pas chez nous"], bof: ["⚠️", "à ajuster"] };

function ligneChrono(v, cle, emoji, nom) {
  const c = v.chrono?.[cle];
  if (!c) return "";
  const photo = v.type === "carrousel";
  const pos = photo ? c.photo : c.t;
  const label = pos == null ? "—" : photo ? `Photo ${pos}` : mmss(pos);
  const attr = pos == null ? "" : photo ? `data-photo="${pos}"` : `data-debut="${pos}"`;
  return `<button class="chrono-ligne${pos == null ? " sans" : ""}" ${attr}>
    <span class="chrono-t">${label}</span>
    <span class="chrono-nom">${emoji} ${nom}</span>
    <span class="chrono-quoi">${esc(c.quoi)}</span>
  </button>`;
}

function barreChrono(v) {
  if (v.type === "carrousel" || !v.chrono || !v.duree) return "";
  // Deux moments trop proches : le second est décalé vers le haut pour rester lisible.
  let precedent = -100;
  const points = CHRONO.filter(([k]) => v.chrono[k] && v.chrono[k].t != null)
    .sort((a, b) => v.chrono[a[0]].t - v.chrono[b[0]].t)
    .map(([k, emoji, nom]) => {
      const gauche = Math.min(98, (v.chrono[k].t / v.duree) * 100);
      const haut = gauche - precedent < 5;
      precedent = gauche;
      return `<span class="pt${haut ? " haut" : ""}" style="left:${gauche}%" title="${esc(nom)} · ${mmss(v.chrono[k].t)}" data-debut="${v.chrono[k].t}">${emoji}</span>`;
    })
    .join("");
  return `<div class="chrono-barre"><div class="piste"></div>${points}<span class="fin-t">${mmss(v.duree)}</span></div>`;
}

const compact = (n) => new Intl.NumberFormat("fr-FR", { notation: n >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(n);

function chiffres(st) {
  const morceaux = [["vues", "vues"], ["likes", "likes"], ["partages", "partages"], ["enregistrements", "enregistrements"], ["commentaires", "commentaires"]]
    .filter(([k]) => st[k] != null).map(([k, nom]) => `<b>${compact(st[k])}</b> ${nom}`);
  if (st.vues && st.abonnes) morceaux.splice(1, 0, `<b>${Math.round(st.vues / st.abonnes)}×</b> ses abonnés`);
  return morceaux.join(" · ");
}

function ouvrir(v) {
  ouverte = v;
  const liste = filtrees();
  const i = liste.findIndex((x) => x.id === v.id);
  $("#compteur").textContent = i >= 0 ? `${i + 1} / ${liste.length}` : "";
  $("#prec").disabled = i <= 0;
  $("#suiv").disabled = i < 0 || i >= liste.length - 1;
  document.title = `${v.titre} · Vidéothèque MS Clipper`;
  $("#retour").setAttribute("href", `#${v.marche.toLowerCase()}`);
  surligner(v.marche.toLowerCase());

  const f = formatDe(v);
  const carrousel = v.type === "carrousel";
  const lecteur = carrousel
    ? `<div class="carrousel">${(v.images || []).map((src, k) => `<img id="photo-${k + 1}" src="${src}" alt="Photo ${k + 1}">`).join("")}</div>`
    : `<video src="videos/${v.id}.mp4" poster="posters/${v.id}.jpg" controls playsinline preload="metadata"></video>`;

  const hooks = (v.hooks || []).map((h) => REF.hooks[h]).filter(Boolean)
    .map((h) => `<a class="tag tag-hook" href="#guide-hooks" title="${esc(h.def)}">${h.emoji} ${esc(h.nom)}</a>`).join("");

  const t = (c) => (c && c.t != null ? `à ${mmss(c.t)}` : c && c.photo != null ? `photo ${c.photo}` : "jamais");
  const tech = [
    ["Format", `<b>${f.emoji} ${esc(f.nom)}</b>${v.reprise ? ` <span class="tag">🔁 reprise</span>` : ""}<br><span class="tech-sous">${esc(v.mise_en_scene || "")}</span>`],
    v.reprise ? ["Vidéo reprise", esc(v.reprise_quoi || "")] : null,
    ["Durée", esc(dureeTexte(v))],
    v.stats && Object.keys(v.stats).length ? ["Les chiffres", chiffres(v.stats)] : null,
    ["Hook", hooks ? `<span class="tags">${hooks}</span>` : "—"],
    ["Point sensible", esc(v.point_sensible || "—")],
    ["L'outil", `nommé <b>${t(v.chrono?.outil_nomme)}</b> · montré <b>${t(v.chrono?.outil_montre)}</b><br><span class="tech-sous">${esc(v.montre_comment || "")}</span>`],
    ["La preuve", v.chrono?.preuve ? `<b>${t(v.chrono.preuve)}</b> · ${esc(v.chrono.preuve.quoi)}` : "—"],
    ["CTA", esc(v.cta || "aucun")],
    ["Montage", esc(v.montage || "—")],
    v.pour_refaire ? ["Pour le refaire", `<span class="tag niveau-${esc(v.pour_refaire.niveau)}">${esc(v.pour_refaire.niveau)}</span> ${esc(v.pour_refaire.temps)} · ${esc(v.pour_refaire.materiel)}`] : null,
  ].filter(Boolean).map(([k, val]) => `<div class="tech-k">${k}</div><div class="tech-v">${val}</div>`).join("");

  const chrono = CHRONO.map(([k, emoji, nom]) => ligneChrono(v, k, emoji, nom)).join("");

  const etapes = (v.etapes || []).map((e, k) => `
    <button class="etape" data-k="${k}" ${e.photo ? `data-photo="${e.photo}"` : `data-debut="${e.debut}"`}>
      <span class="t">${e.photo ? `Photo ${e.photo}` : `${mmss(e.debut)}<small>→ ${mmss(e.fin)}</small>`}</span>
      <span>
        ${e.voit ? `<span class="ligne voit"><b>On voit</b>${esc(e.voit)}</span>` : ""}
        ${e.entend ? `<span class="ligne entend"><b>On entend</b>${esc(e.entend)}</span>` : ""}
      </span>
    </button>`).join("");

  const pourquoi = (v.pourquoi || []).map((p) => `
    <div class="ingredient"><div class="e">${p.emoji || "✨"}</div><div class="n">${esc(p.titre)}</div><p>${esc(p.texte)}</p></div>`).join("");

  const chezNous = (v.chez_nous || []).map((c) => {
    const [ico, lab] = OK[c.ok] || OK.bof;
    return `<li class="check check-${esc(c.ok)}"><span class="check-ico">${ico}</span><span><small>${lab}</small>${esc(c.texte)}</span></li>`;
  }).join("");

  const refaire = (v.refaire || []).map((r) => {
    const app = REF.apps[r.app] || { nom: r.app };
    return `<div class="appli"><div class="nom">${esc(app.nom)}</div><div class="h">${esc(r.hook)}</div><p>${esc(r.comment)}</p></div>`;
  }).join("");

  const transcription = (v.transcription || []).map((l, k) => `
    <div class="ligne-t" data-debut="${l.debut}" data-k="${k}"><span class="t">${mmss(l.debut)}</span><span>${esc(l.texte)}</span></div>`).join("");

  $("#fiche-corps").innerHTML = `
    <div class="lecteur">
      ${lecteur}
      <div class="source"><span><b>${esc(v.compte)}</b> · ${esc(v.plateforme || "TikTok")}</span><span>${esc(v.niche || "")}</span><span>${esc(dureeTexte(v))}</span>${v.lien ? `<a href="${esc(v.lien)}" target="_blank" rel="noopener">Voir sur TikTok ↗</a>` : ""}</div>
    </div>
    <div class="analyse">
      <header>
        <div class="eyebrow">${f.emoji} ${esc(f.nom)}${v.reprise ? " · vidéo reprise" : ""}</div>
        <h2>${esc(v.titre)}</h2>
        <p class="sous">${esc(v.sujet)}</p>
      </header>

      <section class="bloc">
        <h3>La fiche technique</h3>
        <div class="tech">${tech}</div>
      </section>

      <section class="bloc">
        <h3>La chronologie <small>Les 6 moments qui comptent. ${carrousel ? "Clique pour voir la photo." : "Clique : la vidéo saute à ce moment-là."}</small></h3>
        ${barreChrono(v)}
        <div class="chrono">${chrono}</div>
      </section>

      ${v.hook ? `<section class="bloc">
        <h3>Le hook <small>Les 3 premières secondes, mot pour mot.</small></h3>
        <div class="hookbox">
          <p class="dit">${esc(v.hook.dit)}</p>
          <p class="voit"><b>Ce qu'on voit :</b> ${esc(v.hook.voit)}</p>
        </div>
      </section>` : ""}

      ${etapes ? `<section class="bloc">
        <h3>Comment c'est ${carrousel ? "fait" : "tourné"} <small>${carrousel ? "Une photo après l'autre." : "Ce qu'on voit, ce qu'on entend, seconde par seconde."}</small></h3>
        <div class="etapes">${etapes}</div>
      </section>` : ""}

      ${pourquoi ? `<section class="bloc">
        <h3>Pourquoi ça marche</h3>
        <div class="ingredients">${pourquoi}</div>
      </section>` : ""}

      ${v.texte_ecran?.length ? `<section class="bloc">
        <h3>Le texte à l'écran <small>Ce qui est écrit sur la vidéo, pour ceux qui regardent sans le son.</small></h3>
        <div class="ecran">${v.texte_ecran.map((x) => `<span>${esc(x)}</span>`).join("")}</div>
      </section>` : ""}

      ${chezNous ? `<section class="bloc">
        <h3>Chez nous <small>Ce qu'on garde, ce qu'on ajuste, ce qu'on ne fait pas.</small></h3>
        <ul class="checks">${chezNous}</ul>
      </section>` : ""}

      ${refaire ? `<section class="bloc">
        <h3>À refaire avec nos apps <small>La même mécanique, le sujet change.</small></h3>
        <div class="refaire">${refaire}</div>
      </section>` : ""}

      ${v.note ? `<div class="note">${v.note}</div>` : ""}

      ${transcription ? `<section class="bloc">
        <details class="transcription"><summary>Tout ce qui est dit, mot pour mot</summary><div class="lignes">${transcription}</div></details>
      </section>` : ""}

      <div class="fiche-suiv">
        <button class="btn" id="prec2" ${i <= 0 ? "disabled" : ""}>← Vidéo précédente</button>
        <button class="btn" id="suiv2" ${i < 0 || i >= liste.length - 1 ? "disabled" : ""}>Vidéo suivante →</button>
      </div>
    </div>`;

  $("#fiche").hidden = false;
  document.body.classList.add("fige");
  $("#fiche").scrollTo(0, 0);
  $("#prec2")?.addEventListener("click", () => voisine(-1));
  $("#suiv2")?.addEventListener("click", () => voisine(1));

  const video = $("#fiche video");
  // Avant que les métadonnées soient chargées, un réglage de currentTime est ignoré : on attend.
  const aller = (t) => {
    if (!video) return;
    const sauter = () => { video.currentTime = t; video.play().catch(() => {}); };
    if (video.readyState >= 1) sauter();
    else video.addEventListener("loadedmetadata", sauter, { once: true });
  };
  $("#fiche-corps").querySelectorAll("[data-debut]").forEach((el) => el.addEventListener("click", () => aller(Number(el.dataset.debut))));
  $("#fiche-corps").querySelectorAll("[data-photo]").forEach((el) =>
    el.addEventListener("click", () => document.getElementById(`photo-${el.dataset.photo}`)?.scrollIntoView({ behavior: "smooth", block: "center" }))
  );
  if (video) {
    const etapesEl = [...$("#fiche-corps").querySelectorAll(".etape")];
    const lignesEl = [...$("#fiche-corps").querySelectorAll(".ligne-t")];
    video.addEventListener("timeupdate", () => {
      const t = video.currentTime;
      etapesEl.forEach((el, k) => { const e = v.etapes[k]; el.classList.toggle("actif", t >= e.debut && t < e.fin); });
      lignesEl.forEach((el, k) => { const l = v.transcription[k]; el.classList.toggle("actif", t >= l.debut && t < l.fin); });
    });
  }
}

init().catch((e) => {
  $("#grille").innerHTML = `<p class="vide">Impossible de charger les vidéos (${esc(e.message)}).</p>`;
});
