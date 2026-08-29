const $ = (id) => document.getElementById(id);

let state = null;
let selectedEnemy = null;

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || "request failed");
  }
  return data;
}

function show(id) {
  for (const section of document.querySelectorAll("main > section")) {
    section.classList.toggle("hidden", section.id !== id);
  }
}

function render(next) {
  state = next;
  const phase = state.phase;
  $("health-label").textContent = `Health: ${state.hp}`;
  if (phase === "stance_select") {
    show("screen-stance");
    return;
  }
  if (phase === "combat") {
    show("screen-combat");
    renderCombat(state.combat);
    return;
  }
  if (phase === "reward" || phase === "relic" || phase === "upgrade") {
    show("screen-pick");
    renderPick(phase);
    return;
  }
  show("screen-end");
  $("end-title").textContent = phase === "victory" ? "I DEMAND NAPTIME!" : "Lights Out";
  $("end-blurb").textContent =
    phase === "victory"
      ? "Colander Kid saved the city, then asked for juice."
      : "The Bedtime Committee wins this round.";
}

function renderCombat(combat) {
  const hero = combat.hero;
  $("hero-hp").textContent = `${hero.hp}/${hero.max_hp}`;
  $("hero-block").textContent = String(hero.shield);
  $("hero-ap").textContent = `${hero.ap}/${hero.max_ap}`;
  $("hero-stance").textContent = `${hero.stance || "—"} Mode`;
  $("hero-hp-fill").style.width = `${Math.max(0, (100 * hero.hp) / hero.max_hp)}%`;
  $("tantrum-count").textContent = `${hero.tantrum}/5`;
  const pips = $("tantrum-pips");
  pips.innerHTML = "";
  for (let i = 0; i < 5; i += 1) {
    const pip = document.createElement("div");
    pip.className = "pip" + (i < hero.tantrum ? " on" : "") + (hero.tantrum >= 5 ? " melt" : "");
    pips.appendChild(pip);
  }
  $("hero-statuses").innerHTML = Object.entries(hero.statuses)
    .map(([k, v]) => `<span class="chip">${k.replace("status_", "").replace("power_", "")} ${v}</span>`)
    .join("");
  $("relic-list").innerHTML = (state.relics || [])
    .map((r) => `<span class="chip relic" title="${r.text}">${r.name}</span>`)
    .join("");
  $("pile-counts").textContent =
    `Draw ${hero.piles.draw} · Discard ${hero.piles.discard} · Exile ${hero.piles.exile}`;

  const enemies = combat.enemies;
  if (!selectedEnemy || !enemies.some((e) => e.id === selectedEnemy && e.alive)) {
    selectedEnemy = (enemies.find((e) => e.alive) || {}).id || null;
  }
  $("enemy-row").innerHTML = enemies
    .map((e) => {
      const intent = e.intent
        ? `${e.intent.label || e.intent.kind}${e.intent.value ? ` ${e.intent.value}` : ""}${e.intent.times > 1 ? ` x${e.intent.times}` : ""}`
        : "…";
      return `<article class="enemy${e.id === selectedEnemy ? " selected" : ""}${e.alive ? "" : " dead"}" data-id="${e.id}">
        <h3>${e.name}</h3>
        <div class="bar hp"><span style="width:${(100 * e.hp) / e.max_hp}%"></span></div>
        <div>${e.hp}/${e.max_hp} · Block ${e.shield}</div>
        <div class="intent">${intent}</div>
      </article>`;
    })
    .join("");

  $("hand").innerHTML = combat.hand
    .map((c) => {
      const cls = ["card", c.color, c.playable ? "" : "unplayable"].filter(Boolean).join(" ");
      return `<article class="${cls}" data-uid="${c.uid}">
        <div class="cost">${c.cost}</div>
        <h4>${c.name}</h4>
        <div class="type">${c.type}</div>
        <p class="text">${c.text}</p>
      </article>`;
    })
    .join("");

  $("log").innerHTML = combat.log.map((line) => `<div>${line}</div>`).join("");
  $("log").scrollTop = $("log").scrollHeight;
}

function renderPick(phase) {
  const skip = $("pick-skip");
  if (phase === "reward") {
    $("pick-title").textContent = "Add a card";
    $("pick-blurb").textContent = "A new page for the origin comic.";
    $("pick-grid").innerHTML = state.rewards
      .map(
        (c) => `<article class="card ${c.color}" data-kind="reward" data-id="${c.id}">
          <div class="cost">${c.cost}</div><h4>${c.name}</h4><div class="type">${c.type}</div>
          <p class="text">${c.text}</p></article>`
      )
      .join("");
    skip.textContent = "Skip card";
  } else if (phase === "relic") {
    $("pick-title").textContent = "A relic from the toybox";
    $("pick-blurb").textContent = "The Floor Manager dropped something shiny.";
    $("pick-grid").innerHTML = state.relic_choices
      .map(
        (r) => `<article class="card yellow" data-kind="relic" data-id="${r.id}">
          <h4>${r.name}</h4><div class="type">${r.rarity}</div><p class="text">${r.text}</p></article>`
      )
      .join("");
    skip.textContent = "Skip relic";
  } else {
    $("pick-title").textContent = "Upgrade a card";
    $("pick-blurb").textContent = "Ink a plus on one page.";
    $("pick-grid").innerHTML = state.upgrades
      .map(
        (c) => `<article class="card starter" data-kind="upgrade" data-id="${c.id}">
          <h4>${c.name}</h4><p class="text">${c.text}<br><em>${c.upgrade}</em></p></article>`
      )
      .join("");
    skip.textContent = "Skip upgrade";
  }
}

async function refresh() {
  render(await api("/api/state"));
}

$("new-run").addEventListener("click", async () => {
  const seed = Number($("seed").value || 1);
  render(await api("/api/new", { seed }));
});

document.querySelectorAll("[data-stance]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const seed = Number($("seed").value || 1);
    await api("/api/new", { seed });
    render(await api("/api/stance", { stance: btn.dataset.stance }));
  });
});

$("enemy-row").addEventListener("click", (ev) => {
  const card = ev.target.closest(".enemy");
  if (!card) return;
  selectedEnemy = card.dataset.id;
  if (state && state.combat) renderCombat(state.combat);
});

$("hand").addEventListener("click", async (ev) => {
  const card = ev.target.closest(".card");
  if (!card || card.classList.contains("unplayable")) return;
  try {
    render(await api("/api/play", { uid: card.dataset.uid, target_id: selectedEnemy }));
  } catch (err) {
    alert(err.message);
  }
});

$("end-turn").addEventListener("click", async () => {
  render(await api("/api/end_turn", {}));
});

$("pick-grid").addEventListener("click", async (ev) => {
  const card = ev.target.closest("[data-kind]");
  if (!card) return;
  const kind = card.dataset.kind;
  const id = card.dataset.id;
  if (kind === "reward") render(await api("/api/reward", { card_id: id }));
  if (kind === "relic") render(await api("/api/relic", { relic_id: id }));
  if (kind === "upgrade") render(await api("/api/upgrade", { card_id: id }));
});

$("pick-skip").addEventListener("click", async () => {
  if (state.phase === "reward") render(await api("/api/reward", {}));
  else if (state.phase === "relic") render(await api("/api/relic", {}));
  else render(await api("/api/upgrade", {}));
});

$("play-again").addEventListener("click", async () => {
  render(await api("/api/new", { seed: Number($("seed").value || 1) }));
});

refresh().catch((err) => {
  document.body.insertAdjacentHTML("beforeend", `<p>${err.message}</p>`);
});
