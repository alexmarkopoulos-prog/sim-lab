const state = {
  me: null,
  slates: [],
  slate: null,
  tab: "pool",
  sort: { key: "salary", dir: -1 },
  pos: new Set(),
  team: new Set(),
  simTimer: null,
  editing: null,
};

const $ = (id) => document.getElementById(id);

function toast(message) {
  const node = $("toast");
  node.hidden = false;
  node.textContent = message;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { node.hidden = true; }, 5000);
}

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  if (response.status === 401) {
    showLogin();
    throw new Error("Sign in required.");
  }
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function money(value) {
  return "$" + Math.round(value || 0).toLocaleString("en-US");
}
function num(value, digits = 1) {
  if (value === null || value === undefined || value === "") return "—";
  return Number(value).toFixed(digits);
}
function cls(value) {
  if (value === null || value === undefined) return "";
  if (value > 0.4) return "pos";
  if (value < -0.4) return "neg";
  return "";
}

function showLogin() {
  $("login-view").hidden = false;
  $("app-view").hidden = true;
  state.me = null;
}
function showApp() {
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  $("who").textContent = state.me.email;
}

async function boot() {
  try {
    state.me = await api("/api/me");
    showApp();
    await refreshSlates();
  } catch {
    showLogin();
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("auth-message").textContent = "";
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: { email: $("login-email").value, password: $("login-password").value },
    });
    state.me = await api("/api/me");
    showApp();
    await refreshSlates();
  } catch (error) {
    $("auth-message").textContent = error.message;
  }
});

$("magic-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("dev-link").hidden = true;
  try {
    const data = await api("/api/auth/magic", { method: "POST", body: { email: $("magic-email").value } });
    $("auth-message").textContent = data.message;
    if (data.dev_link) {
      $("dev-link").hidden = false;
      $("dev-link").innerHTML = `<a href="${data.dev_link}">Open the development sign-in link</a>`;
    }
  } catch (error) {
    $("auth-message").textContent = error.message;
  }
});

$("logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  showLogin();
});

async function refreshSlates(selectId) {
  state.slates = await api("/api/slates");
  const select = $("slate-select");
  select.innerHTML = state.slates.map((slate) => `<option value="${slate.id}">${escapeHtml(slate.name)}</option>`).join("");
  if (!state.slates.length) {
    state.slate = null;
    render();
    return;
  }
  const id = selectId || (state.slate && state.slate.id) || state.slates[0].id;
  select.value = String(id);
  await loadSlate(id);
}

$("slate-select").addEventListener("change", () => loadSlate($("slate-select").value));

async function loadSlate(id) {
  state.slate = await api(`/api/slates/${id}`);
  state.editing = null;
  fillRules();
  render();
}

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = new FormData();
  body.append("salary", $("salary-file").files[0]);
  body.append("projections", $("proj-file").files[0]);
  try {
    const slate = await api("/api/slates", { method: "POST", body });
    await refreshSlates(slate.id);
    toast("Slate imported.");
  } catch (error) {
    toast(error.message);
  }
});

$("load-sample").addEventListener("click", async () => {
  try {
    const slate = await api("/api/slates/sample", { method: "POST" });
    await refreshSlates(slate.id);
    toast("Loaded the IND@HOU sample.");
  } catch (error) {
    toast(error.message);
  }
});

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    render();
  });
});

function render() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.tab);
  });
  $("tab-pool").hidden = state.tab !== "pool";
  $("tab-builder").hidden = state.tab !== "builder";
  $("tab-sims").hidden = state.tab !== "sims";
  const slate = state.slate;
  $("format-pill").textContent = slate ? slate.format : "";
  const showdown = slate && slate.format === "showdown";
  document.querySelectorAll(".showdown-only").forEach((node) => { node.hidden = !showdown; });
  const note = $("slate-note");
  if (!slate) {
    note.hidden = false;
    note.textContent = "Import a DraftKings salary file and a projections file to start.";
    $("pool-table").querySelector("tbody").innerHTML = "";
    $("lineup-list").innerHTML = "";
    return;
  }
  const warnings = (slate.nfl && slate.nfl.warnings) || [];
  const pace = (slate.nfl && slate.nfl.pace) || [];
  const paceText = pace.map((row) => `${row.team} ${row.plays} plays, ${row.pass_rate}% pass`).join(" · ");
  const bits = [`${slate.game || "Slate"} ${slate.kickoff || ""}`.trim(), paceText, warnings[0]].filter(Boolean);
  note.hidden = bits.length === 0;
  note.textContent = bits.join(" — ");
  $("export-link").href = `/api/slates/${slate.id}/lineups/export`;
  renderPool();
  renderCaptains();
  renderLineups();
  renderSims();
}

function players() {
  return (state.slate && state.slate.players) || [];
}

function renderPool() {
  const slate = state.slate;
  const showdown = slate.format === "showdown";
  const columns = [
    ["lock", "Use"],
    ["name", "Player"],
    ["position", "Pos"],
    ["team", "Team"],
    ["status", "St"],
    ["salary", "Salary"],
    ["projection", "Proj"],
    ["ownership", "Own"],
    ["cpt_ownership", "CPT%"],
    ["value", "Val"],
    ["opt", "Opt%"],
    ["leverage", "Lev"],
    ["min_exposure", "Min"],
    ["max_exposure", "Max"],
    ["boost", "Boost"],
    ["snaps", "Snaps"],
  ].filter((col) => showdown || col[0] !== "cpt_ownership");
  $("pool-table").querySelector("thead").innerHTML = `<tr>${columns.map(([key, label]) => `<th data-key="${key}">${label}</th>`).join("")}</tr>`;
  const query = $("search").value.trim().toLowerCase();
  let rows = players().filter((player) => {
    if (query && !player.name.toLowerCase().includes(query)) return false;
    if (state.pos.size && !state.pos.has(player.position)) return false;
    if (state.team.size && !state.team.has(player.team)) return false;
    if ($("hide-out").checked && (player.status === "OUT" || player.status === "IR")) return false;
    if ($("hide-excluded").checked && player.excluded) return false;
    return true;
  });
  const { key, dir } = state.sort;
  rows.sort((a, b) => {
    const av = a[key] ?? -Infinity;
    const bv = b[key] ?? -Infinity;
    if (typeof av === "string") return av.localeCompare(bv) * dir;
    return (av - bv) * dir;
  });
  const outCount = players().filter((player) => player.status === "OUT" || player.status === "IR").length;
  const qCount = players().filter((player) => player.status === "Q" || player.status === "D").length;
  $("pool-meta").textContent = `${rows.length} shown · ${outCount} OUT/IR · ${qCount} Q/D`;
  $("pool-table").querySelector("tbody").innerHTML = rows.map((player) => poolRow(player, showdown)).join("");
  renderChips();
}

function poolRow(player, showdown) {
  const dim = player.excluded || player.status === "OUT" || player.status === "IR" ? "dim" : "";
  const status = player.status ? `<span class="badge ${player.status === "Q" || player.status === "D" ? "q" : "out"}">${player.status}</span>` : "";
  const injury = player.injury ? ` title="${escapeHtml(player.injury)}"` : "";
  const snaps = (player.snaps || []).map((value) => Math.round(value)).join(" ");
  const lock = showdown
    ? `<button type="button" class="mini ${player.lock === "cpt" ? "on" : ""}" data-act="lock" data-lock="cpt" data-id="${player.id}">CPT</button>
       <button type="button" class="mini ${player.lock === "flex" ? "on" : ""}" data-act="lock" data-lock="flex" data-id="${player.id}">FLX</button>`
    : `<button type="button" class="mini ${player.lock === "roster" ? "on" : ""}" data-act="lock" data-lock="roster" data-id="${player.id}">Lock</button>`;
  const cpt = showdown ? `<td class="num">${num(player.cpt_ownership)}</td>` : "";
  return `<tr class="${dim}"${injury}>
    <td>${lock}<button type="button" class="mini ${player.excluded ? "bad" : ""}" data-act="exclude" data-id="${player.id}">${player.excluded ? "Undo" : "Cut"}</button></td>
    <td class="left name">${escapeHtml(player.name)}</td>
    <td class="left">${player.position}</td>
    <td class="left">${player.team}</td>
    <td class="left">${status}</td>
    <td class="num">${money(player.salary)}</td>
    <td class="num">${num(player.projection)}</td>
    <td class="num">${num(player.ownership)}</td>
    ${cpt}
    <td class="num">${num(player.value, 2)}</td>
    <td class="num">${num(player.opt)}</td>
    <td class="num ${cls(player.leverage)}">${num(player.leverage)}</td>
    <td><input class="num" data-field="min_exposure" data-id="${player.id}" value="${player.min_exposure}" size="3" /></td>
    <td><input class="num" data-field="max_exposure" data-id="${player.id}" value="${player.max_exposure}" size="3" /></td>
    <td><input class="num" data-field="boost" data-id="${player.id}" value="${player.boost}" size="3" /></td>
    <td class="snaps left">${snaps}</td>
  </tr>`;
}

function renderChips() {
  const positions = [...new Set(players().map((player) => player.position))];
  const teams = [...new Set(players().map((player) => player.team))];
  $("pos-filters").innerHTML = positions.map((pos) => `<button type="button" class="chip ${state.pos.has(pos) ? "on" : ""}" data-pos="${pos}">${pos}</button>`).join("");
  $("team-filters").innerHTML = teams.map((team) => `<button type="button" class="chip ${state.team.has(team) ? "on" : ""}" data-team="${team}">${team}</button>`).join("");
}

$("pool-table").addEventListener("click", async (event) => {
  const th = event.target.closest("th");
  if (th && th.dataset.key && th.dataset.key !== "lock") {
    if (state.sort.key === th.dataset.key) state.sort.dir *= -1;
    else state.sort = { key: th.dataset.key, dir: th.dataset.key === "name" ? 1 : -1 };
    renderPool();
    return;
  }
  const button = event.target.closest("button");
  if (!button) return;
  const id = Number(button.dataset.id);
  const player = players().find((row) => row.id === id);
  if (!player) return;
  try {
    if (button.dataset.act === "exclude") {
      await api(`/api/slates/${state.slate.id}/players/${id}`, { method: "PATCH", body: { excluded: !player.excluded } });
    } else if (button.dataset.act === "lock") {
      const next = player.lock === button.dataset.lock ? "none" : button.dataset.lock;
      await api(`/api/slates/${state.slate.id}/players/${id}`, { method: "PATCH", body: { lock: next } });
    }
    await loadSlate(state.slate.id);
  } catch (error) {
    toast(error.message);
  }
});

$("pool-table").addEventListener("change", async (event) => {
  const input = event.target.closest("input[data-field]");
  if (!input) return;
  const body = { [input.dataset.field]: Number(input.value) };
  try {
    await api(`/api/slates/${state.slate.id}/players/${input.dataset.id}`, { method: "PATCH", body });
    const player = players().find((row) => row.id === Number(input.dataset.id));
    if (player) player[input.dataset.field] = Number(input.value);
  } catch (error) {
    toast(error.message);
  }
});

$("search").addEventListener("input", renderPool);
$("hide-out").addEventListener("change", renderPool);
$("hide-excluded").addEventListener("change", renderPool);
$("pos-filters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-pos]");
  if (!button) return;
  toggleSet(state.pos, button.dataset.pos);
  renderPool();
});
$("team-filters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-team]");
  if (!button) return;
  toggleSet(state.team, button.dataset.team);
  renderPool();
});

function toggleSet(set, value) {
  if (set.has(value)) set.delete(value);
  else set.add(value);
}

$("enrich").addEventListener("click", async () => {
  if (!state.slate) return;
  $("enrich").disabled = true;
  $("enrich").textContent = "Pulling nflverse…";
  try {
    state.slate = await api(`/api/slates/${state.slate.id}/enrich`, { method: "POST" });
    render();
    toast("Injuries, snaps, and pace updated.");
  } catch (error) {
    toast(error.message);
  } finally {
    $("enrich").disabled = false;
    $("enrich").textContent = "Pull injuries and snaps";
  }
});

function fillRules() {
  const rules = state.slate.rules || {};
  const form = $("rules-form");
  for (const [key, value] of Object.entries(rules)) {
    const field = form.elements[key];
    if (!field || key === "only_with" || key === "captain_ids") continue;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else if (value !== null && value !== undefined) field.value = value;
  }
  renderOnlyWith();
}

function renderCaptains() {
  if (!state.slate || state.slate.format !== "showdown") return;
  const selected = new Set((state.slate.rules.captain_ids || []).map(Number));
  const options = players()
    .filter((player) => player.status !== "OUT" && player.status !== "IR")
    .sort((a, b) => b.salary - a.salary);
  $("captain-pool").innerHTML = options.map((player) => `
    <label class="check"><input type="checkbox" value="${player.id}" ${selected.has(player.id) ? "checked" : ""} />
      ${escapeHtml(player.name)} <span class="meta">${player.position} ${player.team}</span></label>`).join("");
}

$("captain-pool").addEventListener("change", () => {
  if (!state.slate) return;
  state.slate.rules.captain_ids = [...$("captain-pool").querySelectorAll("input:checked")].map((node) => Number(node.value));
});

function renderOnlyWith() {
  const rules = state.slate.rules.only_with || [];
  const choices = players().filter((player) => player.status !== "OUT" && player.status !== "IR");
  const options = (selected) => choices.map((player) => `<option value="${player.id}" ${Number(selected) === player.id ? "selected" : ""}>${escapeHtml(player.name)}</option>`).join("");
  const qbs = choices.filter((player) => player.position === "QB");
  const qbOptions = (selected) => qbs.map((player) => `<option value="${player.id}" ${Number(selected) === player.id ? "selected" : ""}>${escapeHtml(player.name)}</option>`).join("");
  $("only-with").innerHTML = rules.map((rule, index) => `
    <div class="only-row">
      <select data-only="player" data-index="${index}">${options(rule.player_id)}</select>
      <select data-only="with" data-index="${index}">${qbOptions(rule.with_id)}</select>
      <button type="button" class="ghost" data-remove-only="${index}">×</button>
    </div>`).join("");
}

$("add-only-with").addEventListener("click", () => {
  const qbs = players().filter((player) => player.position === "QB");
  const skill = players().find((player) => player.position === "WR" || player.position === "TE");
  if (!qbs.length || !skill) return;
  state.slate.rules.only_with = state.slate.rules.only_with || [];
  state.slate.rules.only_with.push({ player_id: skill.id, with_id: qbs[0].id });
  renderOnlyWith();
});

$("only-with").addEventListener("change", (event) => {
  const select = event.target.closest("select");
  if (!select) return;
  const rule = state.slate.rules.only_with[Number(select.dataset.index)];
  if (select.dataset.only === "player") rule.player_id = Number(select.value);
  else rule.with_id = Number(select.value);
});
$("only-with").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-only]");
  if (!button) return;
  state.slate.rules.only_with.splice(Number(button.dataset.removeOnly), 1);
  renderOnlyWith();
});

function collectRules() {
  const form = $("rules-form");
  const data = { ...state.slate.rules };
  for (const field of form.elements) {
    if (!field.name) continue;
    if (field.type === "checkbox") data[field.name] = field.checked;
    else if (field.value === "") data[field.name] = null;
    else data[field.name] = Number(field.value);
  }
  data.captain_ids = [...$("captain-pool").querySelectorAll("input:checked")].map((node) => Number(node.value));
  data.only_with = state.slate.rules.only_with || [];
  data.no_naked_pass_catchers = form.elements.no_naked_pass_catchers.checked;
  return data;
}

$("rules-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("rules-note").textContent = "Generating…";
  try {
    const data = await api(`/api/slates/${state.slate.id}/lineups/generate`, { method: "POST", body: collectRules() });
    state.slate.rules = collectRules();
    state.slate.lineups = data.lineups;
    $("rules-note").textContent = data.warning || `Built ${data.lineups.filter((lineup) => lineup.source === "optimizer").length} lineups.`;
    renderLineups();
  } catch (error) {
    $("rules-note").textContent = error.message;
  }
});

function renderLineups() {
  const lineups = (state.slate && state.slate.lineups) || [];
  if (!lineups.length) {
    $("lineup-list").innerHTML = `<p class="hint">No lineups yet. Set the rules and generate, or the pool locks will be applied when you do.</p>`;
    return;
  }
  $("lineup-list").innerHTML = `<div class="cards">${lineups.map((lineup) => lineupCard(lineup)).join("")}</div>`;
}

function lineupCard(lineup) {
  const editing = state.editing === lineup.id;
  const rows = lineup.slots.map((slot, index) => {
    const control = editing
      ? `<select data-edit-slot="${index}" data-lineup="${lineup.id}">${slotOptions(slot)}</select>`
      : `<span>${escapeHtml(slot.name)}</span>`;
    return `<li class="${slot.slot === "CPT" ? "cpt" : ""}">
      <span class="slot-tag">${escapeHtml(slot.slot)}</span>
      ${control}
      <span class="num">${money(slot.salary)}</span>
      <span class="meta">${slot.team} ${slot.position}</span>
    </li>`;
  }).join("");
  return `<article class="lineup">
    <header>
      <strong>${lineup.source === "manual" ? "Edited" : "Optimizer"} ${lineup.id}</strong>
      <span class="num">${money(lineup.salary)}</span>
      <span class="num">${num(lineup.projection)} proj</span>
      <span class="num">${num(lineup.ownership)}% own</span>
      <button type="button" class="ghost" data-edit="${lineup.id}">${editing ? "Done" : "Edit"}</button>
      <button type="button" class="ghost" data-delete="${lineup.id}">Delete</button>
    </header>
    <ol>${rows}</ol>
  </article>`;
}

function slotOptions(slot) {
  return players()
    .filter((player) => player.status !== "OUT" && player.status !== "IR" && !player.excluded)
    .filter((player) => state.slate.format === "showdown" || canFill(player, slot.slot))
    .map((player) => `<option value="${player.id}" ${player.id === slot.player_id ? "selected" : ""}>${escapeHtml(player.name)} ${money(player.salary)}</option>`)
    .join("");
}

function canFill(player, slot) {
  if (slot === "FLEX") return ["RB", "WR", "TE"].includes(player.position);
  return player.position === slot;
}

$("lineup-list").addEventListener("click", async (event) => {
  const edit = event.target.closest("[data-edit]");
  const del = event.target.closest("[data-delete]");
  if (edit) {
    state.editing = state.editing === Number(edit.dataset.edit) ? null : Number(edit.dataset.edit);
    renderLineups();
  }
  if (del) {
    try {
      await api(`/api/slates/${state.slate.id}/lineups/${del.dataset.delete}`, { method: "DELETE" });
      state.slate.lineups = state.slate.lineups.filter((lineup) => lineup.id !== Number(del.dataset.delete));
      renderLineups();
    } catch (error) {
      toast(error.message);
    }
  }
});

$("lineup-list").addEventListener("change", async (event) => {
  const select = event.target.closest("[data-edit-slot]");
  if (!select) return;
  const lineup = state.slate.lineups.find((row) => row.id === Number(select.dataset.lineup));
  const slots = lineup.slots.map((slot, index) => ({
    player_id: index === Number(select.dataset.editSlot) ? Number(select.value) : slot.player_id,
    slot: slot.slot,
  }));
  try {
    const updated = await api(`/api/slates/${state.slate.id}/lineups/${lineup.id}`, { method: "PATCH", body: { slots } });
    const index = state.slate.lineups.findIndex((row) => row.id === lineup.id);
    state.slate.lineups[index] = updated;
    renderLineups();
  } catch (error) {
    toast(error.message);
    renderLineups();
  }
});

function renderSims() {
  const latest = state.slate && state.slate.latest_sim;
  if (!latest || !latest.result) {
    $("sim-summary").innerHTML = `<p class="hint">Run a sim to score the lineups in the builder. Showdown solves every legal lineup in the sim pool. Classic uses the optimizer's candidate set and says so.</p>`;
    $("result-table").querySelector("tbody").innerHTML = "";
    $("lev-table").querySelector("tbody").innerHTML = "";
    $("scripts").innerHTML = "";
    return;
  }
  const result = latest.result;
  const field = result.field || {};
  $("sim-summary").innerHTML = `
    <p class="disclaimer">${escapeHtml(result.disclaimer || "")}</p>
    <p class="meta">${result.n_sims.toLocaleString()} sims · ${result.optimal_method === "exact_enumeration" ? "Exact showdown optimal" : "Classic candidate optimal"} · field ${field.pct_salary_49k}% at $49k+ · ${field.legal_lineups.toLocaleString()} candidates · contest ${field.contest_size.toLocaleString()}</p>`;
  const lineups = result.lineups || [];
  $("result-table").querySelector("thead").innerHTML = `<tr>
    <th class="left">Lineup</th><th>Salary</th><th>Proj</th><th>Own</th><th>Top 0.1%</th><th>Top 1%</th><th>ITM</th><th>Sim ROI</th><th>Dupes</th><th></th></tr>`;
  $("result-table").querySelector("tbody").innerHTML = lineups.map((row) => {
    const label = (row.slots || []).map((slot) => `${slot.slot === "CPT" ? "CPT " : ""}${slot.name}`).join(", ");
    const yours = String(row.key || "").startsWith("saved") ? "Yours" : "Completion";
    return `<tr>
      <td class="left">${yours} · ${escapeHtml(label)}</td>
      <td class="num">${money(row.salary)}</td>
      <td class="num">${num(row.projection)}</td>
      <td class="num">${num(row.ownership)}</td>
      <td class="num">${num(row.top01)}%</td>
      <td class="num">${num(row.top1)}%</td>
      <td class="num">${num(row.itm)}%</td>
      <td class="num ${cls((row.roi || 0) * 100)}">${num((row.roi || 0) * 100)}%</td>
      <td class="num">${num(row.dupes)}</td>
      <td></td>
    </tr>`;
  }).join("");
  const scripts = result.scripts || [];
  $("scripts").innerHTML = scripts.length
    ? scripts.map((script) => `<div class="script"><span>${escapeHtml(script.label)}</span><span class="num">${num(script.pct)}%</span></div>`).join("")
    : `<p class="hint">Captain scripts are reported for two-team showdowns.</p>`;
  const lev = result.players || [];
  $("lev-table").querySelector("thead").innerHTML = `<tr>
    <th class="left">Player</th><th>Opt%</th><th>CPT opt%</th><th>Own</th><th>CPT own</th><th>Leverage</th><th>CPT lev</th></tr>`;
  $("lev-table").querySelector("tbody").innerHTML = lev.map((row) => `<tr>
    <td class="left">${escapeHtml(row.name)} <span class="meta">${row.position} ${row.team}</span></td>
    <td class="num">${num(row.opt)}</td>
    <td class="num">${num(row.cpt_opt)}</td>
    <td class="num">${num(row.ownership)}</td>
    <td class="num">${num(row.cpt_ownership)}</td>
    <td class="num ${cls(row.leverage)}">${num(row.leverage)}</td>
    <td class="num ${cls(row.cpt_leverage)}">${num(row.cpt_leverage)}</td>
  </tr>`).join("");
}

$("sim-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const body = {
    n_sims: Number(form.n_sims.value),
    contest_size: Number(form.contest_size.value),
    entry_fee: Number(form.entry_fee.value),
    max_entries: Number(form.max_entries.value),
    cash_pct: Number(form.cash_pct.value) / 100,
    rake: Number(form.rake.value) / 100,
    extra_lineups: Number(form.extra_lineups.value),
    pool_limit: Number(form.pool_limit.value),
    payout_text: form.payout_text.value,
  };
  try {
    const job = await api(`/api/slates/${state.slate.id}/sims`, { method: "POST", body });
    state.jobId = job.id;
    watchSim(job.id);
  } catch (error) {
    toast(error.message);
  }
});

function watchSim(id) {
  clearTimeout(state.simTimer);
  $("run-sim").disabled = true;
  $("cancel-sim").hidden = false;
  $("sim-progress").hidden = false;
  const tick = async () => {
    try {
      const job = await api(`/api/sims/${id}`);
      $("sim-bar").style.width = `${job.progress || 0}%`;
      $("sim-message").textContent = job.message || job.status;
      if (job.status === "queued" || job.status === "running") {
        state.simTimer = setTimeout(tick, 800);
        return;
      }
      $("run-sim").disabled = false;
      $("cancel-sim").hidden = true;
      if (job.status === "error") {
        toast(job.error || "Sim failed.");
        return;
      }
      await loadSlate(state.slate.id);
      state.tab = "sims";
      render();
    } catch (error) {
      $("run-sim").disabled = false;
      toast(error.message);
    }
  };
  tick();
}

$("cancel-sim").addEventListener("click", async () => {
  if (state.jobId) await api(`/api/sims/${state.jobId}/cancel`, { method: "POST" });
});

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

boot();
