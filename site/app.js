/* Vidéothèque MS Clipper — tout en JavaScript simple, sans dépendance.
   Les données : data/referentiel.json (formats, apps, lexique) et data/videos.json (une fiche par vidéo). */

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

let REF = { formats: {}, apps: {}, lexique: [] };
let VIDEOS = [];
let filtre = { format: "tous", q: "" };
let ouverte = null;

async function init() {
  const [ref, videos] = await Promise.all([
    fetch("data/referentiel.json").then((r) => r.json()),
    fetch("data/videos.json").then((r) => r.json()),
  ]);
  REF = ref;
  VIDEOS = videos.filter((v) => v.titre);
  rendreStats();
  rendreChips();
  rendreLexique();
  rendreGrille();
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

/* ——— Accueil ——— */

function rendreStats() {
  const formats = new Set(VIDEOS.map((v) => v.format));
  const secondes = VIDEOS.reduce((a, v) => a + (v.duree || 0), 0);
  $("#stats").innerHTML = [
    `<b>${VIDEOS.length}</b> vidéos`,
    `<b>${formats.size}</b> façons de filmer`,
    `<b>${Math.round(secondes / 60)}</b> min à regarder`,
  ].map((t) => `<span class="stat">${t}</span>`).join("");
}

function rendreChips() {
  const compte = {};
  VIDEOS.forEach((v) => { compte[v.format] = (compte[v.format] || 0) + 1; });
  const ordre = Object.keys(REF.formats).filter((k) => compte[k]);
  const chips = [["tous", "Tout", VIDEOS.length], ...ordre.map((k) => [k, `${REF.formats[k].emoji} ${REF.formats[k].nom}`, compte[k]])];
  $("#chips-formats").innerHTML = chips
    .map(([k, nom, n]) => `<button class="chip${filtre.format === k ? " actif" : ""}" data-f="${k}">${nom}<span class="n">${n}</span></button>`)
    .join("");
  $("#chips-formats").querySelectorAll(".chip").forEach((b) =>
    b.addEventListener("click", () => { filtre.format = b.dataset.f; rendreChips(); rendreGrille(); })
  );
}

function rendreLexique() {
  $("#defs").innerHTML = (REF.lexique || [])
    .map((d) => `<div class="def"><div class="mot"><span>${d.emoji}</span>${esc(d.mot)}</div><p>${esc(d.def)}</p></div>`)
    .join("");
}

function texteRecherche(v) {
  return [v.titre, v.sujet, v.compte, v.niche, v.hook?.dit, v.hook?.voit, (v.texte_ecran || []).join(" "),
    (v.pourquoi || []).map((p) => `${p.titre} ${p.texte}`).join(" "), (v.tags || []).join(" "),
    (v.etapes || []).map((e) => `${e.voit} ${e.entend}`).join(" "), REF.formats[v.format]?.nom]
    .join(" ").toLowerCase();
}

function filtrees() {
  return VIDEOS.filter((v) => (filtre.format === "tous" || v.format === filtre.format) && (!filtre.q || texteRecherche(v).includes(filtre.q)));
}

function rendreGrille() {
  const liste = filtrees();
  $("#vide").hidden = liste.length > 0;
  $("#grille").innerHTML = liste.map((v) => {
    const f = REF.formats[v.format] || { nom: v.format, emoji: "" };
    const poster = v.type === "carrousel" ? v.images?.[0] : `posters/${v.id}.jpg`;
    return `<a class="carte" href="#v/${v.id}">
      <div class="visuel">
        <span class="badge">${f.emoji} ${esc(f.nom)}</span>
        ${v.reprise ? `<span class="badge gris" style="left:auto;right:10px">reprise</span>` : ""}
        <img src="${poster}" alt="" loading="lazy">
        <span class="hook">${esc(v.hook?.dit || v.titre)}</span>
        <span class="duree">${v.type === "carrousel" ? `${v.images?.length || 0} photos` : mmss(v.duree)}</span>
      </div>
      <div class="infos">
        <div class="titre">${esc(v.titre)}</div>
        <div class="compte"><span>${esc(v.compte)}</span><span>${esc(v.niche || "")}</span></div>
      </div>
    </a>`;
  }).join("");
}

/* ——— Fiche ——— */

function router() {
  const m = location.hash.match(/^#v\/([\w-]+)/);
  const v = m && VIDEOS.find((x) => x.id === m[1]);
  if (v) ouvrir(v);
  else fermer();
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

function ouvrir(v) {
  ouverte = v;
  const liste = filtrees();
  const i = liste.findIndex((x) => x.id === v.id);
  $("#compteur").textContent = i >= 0 ? `${i + 1} / ${liste.length}` : "";
  $("#prec").disabled = i <= 0;
  $("#suiv").disabled = i < 0 || i >= liste.length - 1;
  document.title = `${v.titre} · Vidéothèque MS Clipper`;

  const f = REF.formats[v.format] || { nom: v.format, emoji: "", def: "" };
  const lecteur = v.type === "carrousel"
    ? `<div class="carrousel">${(v.images || []).map((src, k) => `<img id="photo-${k + 1}" src="${src}" alt="Photo ${k + 1}">`).join("")}</div>`
    : `<video src="videos/${v.id}.mp4" poster="posters/${v.id}.jpg" controls playsinline preload="metadata"></video>`;

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

  const refaire = (v.refaire || []).map((r) => {
    const app = REF.apps[r.app] || { nom: r.app };
    return `<div class="appli"><div class="nom">${esc(app.nom)}</div><div class="h">${esc(r.hook)}</div><p>${esc(r.comment)}</p></div>`;
  }).join("");

  const transcription = (v.transcription || []).map((l, k) => `
    <div class="ligne-t" data-debut="${l.debut}" data-k="${k}"><span class="t">${mmss(l.debut)}</span><span>${esc(l.texte)}</span></div>`).join("");

  $("#fiche-corps").innerHTML = `
    <div class="lecteur">
      ${lecteur}
      <div class="source"><span><b>${esc(v.compte)}</b> · ${esc(v.plateforme || "TikTok")}</span><span>${esc(v.niche || "")}</span>${v.type === "carrousel" ? "" : `<span>${mmss(v.duree)}</span>`}</div>
    </div>
    <div class="analyse">
      <header>
        <div class="eyebrow">${f.emoji} ${esc(f.nom)} · ${esc(f.def)}</div>
        <h2>${esc(v.titre)}</h2>
        <p class="sous">${esc(v.sujet)}</p>
        ${v.tags?.length ? `<div class="tags">${v.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
      </header>

      ${v.hook ? `<section class="bloc">
        <h3>Le hook <small>Les 3 premières secondes. C'est là que les gens décident de rester ou de partir.</small></h3>
        <div class="hookbox">
          <p class="dit">${esc(v.hook.dit)}</p>
          <p class="voit"><b>Ce qu'on voit :</b> ${esc(v.hook.voit)}</p>
        </div>
      </section>` : ""}

      ${etapes ? `<section class="bloc">
        <h3>Comment c'est ${v.type === "carrousel" ? "fait" : "tourné"} <small>${v.type === "carrousel" ? "Une photo après l'autre. Clique sur une étape pour voir la photo." : "Clique sur une étape : la vidéo va à ce moment-là."}</small></h3>
        <div class="etapes">${etapes}</div>
      </section>` : ""}

      ${pourquoi ? `<section class="bloc">
        <h3>Pourquoi ça marche</h3>
        <div class="ingredients">${pourquoi}</div>
      </section>` : ""}

      ${v.texte_ecran?.length ? `<section class="bloc">
        <h3>Le texte à l'écran <small>Ce qui est écrit sur la vidéo, en gros, pour ceux qui regardent sans le son.</small></h3>
        <div class="ecran">${v.texte_ecran.map((t) => `<span>${esc(t)}</span>`).join("")}</div>
      </section>` : ""}

      ${refaire ? `<section class="bloc">
        <h3>À refaire chez nous <small>La même mécanique, avec nos apps. On garde la recette, on change le sujet.</small></h3>
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
      etapesEl.forEach((el, k) => {
        const e = v.etapes[k];
        el.classList.toggle("actif", t >= e.debut && t < e.fin);
      });
      lignesEl.forEach((el, k) => {
        const l = v.transcription[k];
        el.classList.toggle("actif", t >= l.debut && t < l.fin);
      });
    });
  }
}

init().catch((e) => {
  $("#grille").innerHTML = `<p class="vide">Impossible de charger les vidéos (${esc(e.message)}).</p>`;
});
