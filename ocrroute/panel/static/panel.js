/* Shared panel helpers: API calls through the session, toasts, dialogs, charts, live feed. */
(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  async function api(method, path, body, opts = {}) {
    const headers = { "X-Requested-With": "OcrRoute" };
    let payload = body;
    if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
    const res = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
    const ct = res.headers.get("content-type") || "";
    const data = ct.includes("json") ? await res.json() : await res.text();
    if (!res.ok && !opts.raw) {
      const msg = (data && (data.error_message || data.detail)) ? (data.error_message || JSON.stringify(data.detail)) : res.statusText;
      throw Object.assign(new Error(msg), { status: res.status, data });
    }
    return data;
  }

  const T = (k) => (window.I18N && window.I18N[k]) || k;
  function toast(msg, bad = false) {
    msg = T(msg);
    let wrap = $("#toasts");
    if (!wrap) { wrap = document.createElement("div"); wrap.id = "toasts"; wrap.className = "toast-container position-fixed bottom-0 end-0 p-3"; document.body.appendChild(wrap); }
    const el = document.createElement("div");
    el.className = "toast align-items-center border-0 " + (bad ? "text-bg-danger" : "text-bg-dark");
    el.setAttribute("role", "status");
    el.innerHTML = `<div class="d-flex"><div class="toast-body">${msg}</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>`;
    wrap.appendChild(el);
    if (window.bootstrap) { const t = new bootstrap.Toast(el, { delay: bad ? 6000 : 3000 }); el.addEventListener("hidden.bs.toast", () => el.remove()); t.show(); }
    else { el.classList.add("show"); setTimeout(() => el.remove(), bad ? 6000 : 3000); }
  }

  function confirmDo(msg) { return window.confirm(msg); }

  function fmtMs(ms) { return ms == null ? "" : ms >= 1000 ? (ms / 1000).toFixed(2) + " s" : ms + " ms"; }
  function fmtCents(c) { return c == null ? "" : "$" + (c / 100).toFixed(4); }

  // ---- charts: palette derived from CSS tokens, every instance registered so a theme switch recolours it live
  const charts = [];
  function chartTheme() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n, d) => (cs.getPropertyValue(n).trim() || d);
    const dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
    return {
      dark, ink: v("--bs-secondary-color", "#64748B"), line: v("--bs-border-color", "#E2E8F0"), text: v("--bs-body-color", "#0F172A"),
      surface: v("--ocr-surface", "#FFFFFF"), font: v("--bs-font-sans-serif", "system-ui"),
      palette: dark ? ["#60A5FA", "#34D399", "#FBBF24", "#F87171", "#A78BFA", "#22D3EE", "#F472B6"]
                    : ["#2563EB", "#059669", "#D97706", "#DC2626", "#7C3AED", "#0891B2", "#DB2777"],
    };
  }
  // Colours are scriptable options evaluated at draw time: after a theme switch chart.update() repaints with the
  // new tokens. No option objects are replaced (Chart.js options are proxies).
  const T_ = () => chartTheme();
  const dsColor = (ctx) => { const d = ctx.chart.data.datasets[ctx.datasetIndex] || {}; return d._fixedColor || T_().palette[ctx.datasetIndex % 7]; };
  const axis = (showGrid) => ({ ticks: { color: () => T_().ink }, grid: { display: showGrid, color: () => T_().line }, border: { color: () => T_().line } });
  function chart(canvas, type, labels, datasets, extra = {}) {
    if (!window.Chart || !canvas) return null;
    datasets.forEach((d) => {
      if (typeof d.backgroundColor === "string" && !d._fixedColor) d._fixedColor = d.backgroundColor;
      d.borderColor = dsColor;
      d.backgroundColor = (ctx) => { const col = dsColor(ctx); return type === "line" ? col + "22" : type === "bar" ? col + "D9" : col; };
      d.pointBackgroundColor = dsColor; d.pointBorderColor = () => T_().surface; d.hoverBackgroundColor = dsColor;
      d.borderWidth = d.borderWidth ?? 2; d.tension = 0.35; d.pointRadius = 2.5;
      if (type === "bar") d.borderRadius = 6;
      if (type === "line") d.fill = true;
    });
    const scales = type === "doughnut" ? {} : { x: axis(false), y: Object.assign(axis(true), { beginAtZero: true }) };
    Object.entries(extra.scales || {}).forEach(([k, s]) => { const base = scales[k] || axis(true); scales[k] = Object.assign({}, base, s, { ticks: base.ticks, border: base.border, grid: Object.assign({}, base.grid, (s || {}).grid || {}, { color: base.grid.color }) }); });
    const options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: { duration: 500 } }, extra, {
      scales,
      plugins: {
        legend: { display: datasets.length > 1, labels: { color: () => T_().text, boxWidth: 10, usePointStyle: true } },
        tooltip: { backgroundColor: () => (T_().dark ? "#0F1724" : "#FFFFFF"), titleColor: () => T_().text, bodyColor: () => T_().text, borderColor: () => T_().line, borderWidth: 1, padding: 10, cornerRadius: 8 },
      },
    });
    Chart.defaults.font.family = T_().font;
    const c = new Chart(canvas, { type, data: { labels, datasets }, options });
    charts.push(c);
    return c;
  }
  function syncFavicon() {
    const dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
    document.querySelectorAll("link[data-favicon]").forEach((l) => { l.href = dark ? "/panel/static/favicon-dark.svg" : "/panel/static/favicon.svg"; });
  }
  function repaintCharts() { syncFavicon(); charts.forEach((c) => c.update()); }

  function liveFeed(listEl, onRun) {
    if (!listEl) return;
    const es = new EventSource("/panel/stream");
    es.addEventListener("run", (e) => {
      const p = JSON.parse(e.data);
      const li = document.createElement("li");
      li.innerHTML = `<span class="muted">${(p.created_at || "").slice(11, 19)}</span><a href="/panel/runs/${p.id}">${p.route || "-"} · ${p.engine || "-"}</a><span class="num">${fmtMs(p.duration_ms)}</span><span class="status ${p.status}">${p.status}</span>`;
      listEl.prepend(li);
      while (listEl.children.length > 30) listEl.lastChild.remove();
      if (onRun) onRun(p);
    });
    es.onerror = () => { es.close(); setTimeout(() => liveFeed(listEl, onRun), 5000); };
  }

  // theme: light / dark / system (persisted per browser; system follows prefers-color-scheme)
  function applyTheme(mode) {
    const effective = mode === "system" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : mode;
    document.documentElement.setAttribute("data-bs-theme", effective);
    document.documentElement.dataset.theme = mode;
    $$(".seg [data-theme]").forEach((b) => b.classList.toggle("active", b.dataset.theme === mode));
    try { localStorage.setItem("ocrroute-theme", mode); } catch {}
    requestAnimationFrame(repaintCharts);  // CSS tokens are updated; recolour every chart in place
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if ((document.documentElement.dataset.theme || "system") === "system") applyTheme("system"); });
  const savedTheme = (() => { try { return localStorage.getItem("ocrroute-theme") || "system"; } catch { return "system"; } })();
  applyTheme(savedTheme);
  $$(".seg [data-theme]").forEach((b) => b.addEventListener("click", () => applyTheme(b.dataset.theme)));
  const tt = $("#theme-toggle");
  if (tt) tt.addEventListener("click", () => {
    const effective = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light";
    applyTheme(effective === "dark" ? "light" : "dark");
  });

  // sidebar: collapsible groups (state per browser) + live filter
  $$(".ocr-group > button").forEach((btn) => {
    const g = btn.parentElement, key = "ocrroute-group-" + g.dataset.group;
    try { if (localStorage.getItem(key) === "0") g.classList.add("collapsed"); } catch {}
    btn.addEventListener("click", () => { g.classList.toggle("collapsed"); btn.setAttribute("aria-expanded", String(!g.classList.contains("collapsed"))); try { localStorage.setItem(key, g.classList.contains("collapsed") ? "0" : "1"); } catch {} });
  });
  $$("[data-nav-filter]").forEach((inp) => inp.addEventListener("input", () => {
    const q = inp.value.trim().toLowerCase();
    $$(".ocr-nav-item").forEach((a) => { a.style.display = !q || a.dataset.label.includes(q) ? "" : "none"; });
    $$(".ocr-group").forEach((g) => g.classList.toggle("collapsed", q ? ![...g.querySelectorAll(".ocr-nav-item")].some((a) => a.style.display !== "none") : (g.classList.contains("collapsed") && !q)));
  }));

  // server controls in the sidebar footer
  $$("[data-action='restart']").forEach((b) => b.addEventListener("click", async () => {
    if (!confirmDo(T("Restart the server process") + "?")) return;
    b.disabled = true; b.innerHTML = '<i class="bi bi-arrow-repeat spin me-1"></i>' + T("Restart");
    try { const r = await api("POST", "/v1/endpoints/server/restart"); toast(T("Restarting…"));
      const t0 = Date.now(); const poll = async () => { try { const h = await fetch("/v1/health", {cache: "no-store"}); if (h.ok && Date.now() - t0 > 1500) { location.reload(); return; } } catch {} if (Date.now() - t0 < 60000) setTimeout(poll, 1000); else location.reload(); };
      setTimeout(poll, 1500); }
    catch (e) { toast(e.message, true); b.disabled = false; b.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>' + T("Restart"); }
  }));
  $$("[data-action='shutdown']").forEach((b) => b.addEventListener("click", async () => {
    if (!confirmDo(T("Stop the server") + "?")) return;
    try { await api("POST", "/v1/endpoints/server/shutdown"); toast(T("Shutting down…")); document.body.insertAdjacentHTML("beforeend", `<div class="position-fixed top-0 start-0 w-100 h-100 d-grid align-content-center text-center" style="background:var(--bs-body-bg);z-index:2000"><div><i class="bi bi-power" style="font-size:3rem;color:var(--ocr-bad)"></i><h2 class="mt-3">${T("Server stopped")}</h2><p class="text-muted">${T("Start it again with")} <code>ocrroute serve</code></p></div></div>`); }
    catch (e) { toast(e.message, true); }
  }));
  const qn = $("#quicknav"); if (qn) qn.addEventListener("click", () => { window.showDialog($("#palette")); setTimeout(() => $("#palette input")?.focus(), 150); });
  // engines-ready pill in the sidebar footer
  const ers = $$("[data-engines-ready]"); if (ers.length) api("GET", "/v1/ready").then((r) => ers.forEach((er) => { er.textContent = (r.status === "ready" ? "● " : "○ ") + er.textContent; })).catch(() => {});

  // command palette (Ctrl/⌘+K) & search focus (/)
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); window.showDialog($("#palette")); setTimeout(() => $("#palette input")?.focus(), 150); }
    if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement.tagName)) { e.preventDefault(); ($(".ocr-sidebar.d-lg-flex .global-search") || $(".global-search") || $("#global-search"))?.focus(); }
  });
  const pal = $("#palette");
  if (pal) {
    const input = $("input", pal), list = $("ul", pal);
    const items = $$(".ocr-nav-item").map((a) => ({ label: a.querySelector(".t")?.textContent.trim() || a.textContent.trim(), href: a.href }));
    const renderList = (q = "") => { list.innerHTML = ""; items.filter((i) => i.label.toLowerCase().includes(q.toLowerCase())).forEach((i) => { const li = document.createElement("li"); li.innerHTML = `<a class="dropdown-item rounded-2" href="${i.href}"><i class="bi bi-arrow-return-right me-2 text-muted"></i>${i.label}</a>`; list.appendChild(li); }); };
    input.addEventListener("input", () => renderList(input.value)); renderList();
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { const a = $("a", list); if (a) location.href = a.href; } });
  }

    window.showDialog = (el) => { if (!el) return; if (el.tagName === "DIALOG") el.showModal(); else bootstrap.Modal.getOrCreateInstance(el).show(); };
  window.hideDialog = (el) => { if (!el) return; if (el.tagName === "DIALOG") el.close(); else bootstrap.Modal.getOrCreateInstance(el).hide(); };

  // Language picker: a <select> of the languages the chosen engine accepts ("auto" preselected when the engine can
  // detect the language, else "en"), plus an engine-variant <select> for engines whose languages depend on it
  // (OCR.Space engines 1-3). Routes list every language. Engines without a language setting show "automatic".
  // Model picker: a text box with the engine's known models as suggestions (datalist), a "live" refresh that asks the
  // provider's account for its current catalogue, and a note. Hidden for engines without a model setting.
  function modelPicker(cfg) {
    const {target, input, list, refresh, wrap, note, providerId} = cfg;  // target: select whose value is "engine:<id>", or engineId()
    const T = (k) => (window.I18N && window.I18N[k]) || k;
    const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
    let seq = 0, current = null;
    const setNote = (text, bad) => { if (note) { note.textContent = text || ""; note.className = "form-text " + (bad ? "text-danger" : "text-muted") + (text ? "" : " d-none"); } };
    const engineId = () => { if (typeof target === "function") return target(); const t = target.value || ""; return t.startsWith("engine:") ? t.slice(7) : ""; };
    function fill(d) {
      list.innerHTML = (d.models || []).map((m) => `<option value="${esc(m.id)}">${esc(m.label || m.id)}</option>`).join("");
      input.placeholder = d.default || "";
      if (!input.value || input.dataset.auto === "1") { input.value = ""; input.dataset.auto = "1"; }  // empty = the engine's default
    }
    async function load(live) {
      const id = engineId(), my = ++seq;
      if (!id) { if (wrap) wrap.classList.add("d-none"); current = null; return; }
      let q = "";
      const pid = typeof providerId === "function" ? providerId() : providerId;
      if (live && pid) q = "?live=1&provider_id=" + encodeURIComponent(pid);
      let d;
      try { d = await api("GET", "/v1/engines/" + encodeURIComponent(id) + "/models" + q); } catch (e) { d = null; }
      if (my !== seq) return;
      current = d;
      if (!d || !d.has_model) { if (wrap) wrap.classList.add("d-none"); return; }
      if (wrap) wrap.classList.remove("d-none");
      fill(d);
      if (live) setNote(d.source === "live" ? T("Live list from the provider") + " (" + (d.models || []).length + ")" : (d.error || T("Known models")), !!d.error && d.source !== "live");
      else setNote(d.available === false ? T("This engine is not installed here.") : T("Default:") + " " + (d.default || "-") + (pid ? ". " + T("Refresh asks the provider for its current models.") : ""), false);
    }
    input.addEventListener("input", () => { input.dataset.auto = input.value ? "0" : "1"; });
    if (refresh) refresh.onclick = () => load(true);
    if (typeof target !== "function") target.addEventListener("change", () => { input.value = ""; input.dataset.auto = "1"; load(false); });
    load(false);
    return {load, value: () => input.value.trim(), engineId};
  }

  function languagePicker(cfg) {
    const {target, lang, variant, variantWrap, note} = cfg;
    const T = (k) => (window.I18N && window.I18N[k]) || k;
    const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
    let all = null, seq = 0, picked = "";  // picked: a language the user chose (kept when the same target reloads, e.g. a variant change)
    let lastTarget = null;
    // several languages: the dropdown is the first one; "Also:" chips add more, within the engine's combination rules
    let multi = null, extra = [], names = {};
    const wrap = document.createElement("div");
    wrap.className = "d-none mt-1 small lang-multi";
    lang.insertAdjacentElement("afterend", wrap);
    const setNote = (text) => { if (note) { note.textContent = text || ""; note.classList.toggle("d-none", !text); } };
    function compatible(chosen) {  // mirrors OCRPlugin.compatibleLanguages
      const codes = multi.codes, universal = new Set(multi.universal || ["en"]);
      const specific = chosen.filter((c) => c !== "auto" && !universal.has(c));
      if (!multi.groups.length || !specific.length) return codes.filter((c) => !chosen.includes(c));
      const grouped = new Set(multi.groups.flat());
      const free = new Set(codes.filter((c) => !grouped.has(c) && !universal.has(c)));
      const allowed = new Set(universal);
      if (specific.every((c) => free.has(c))) free.forEach((c) => allowed.add(c));
      multi.groups.forEach((g) => { if (specific.every((c) => g.includes(c))) g.forEach((c) => allowed.add(c)); });
      return codes.filter((c) => allowed.has(c) && !chosen.includes(c));
    }
    function renderMulti() {
      if (!multi) { wrap.classList.add("d-none"); wrap.innerHTML = ""; return; }
      const first = lang.value, chosen = [first, ...extra];
      const compat = compatible(chosen);
      extra = extra.filter((c) => c !== first);  // the first language is not repeated
      const options = compat.map((c) => `<option value="${esc(c)}">${esc((names[c] || c) + " (" + c + ")")}</option>`).join("");
      wrap.innerHTML = `<span class="text-muted me-1">${esc(T("Also:"))}</span>` +
        extra.map((c) => `<span class="badge text-bg-secondary me-1 lang-chip" data-c="${esc(c)}">${esc(names[c] || c)} <a href="#" class="text-white text-decoration-none lang-x" data-c="${esc(c)}" title="${esc(T("Remove"))}">&times;</a></span>`).join("") +
        (options ? `<select class="form-select form-select-sm d-inline-block w-auto lang-add"><option value="">${esc(T("+ Add a language…"))}</option>${options}</select>` : "") +
        (multi.groups.length && extra.length ? `<div class="form-text">${esc(T("Only combinations this engine supports are offered."))}</div>` : "");
      wrap.classList.remove("d-none");
      wrap.querySelectorAll(".lang-x").forEach((a) => a.onclick = (e) => { e.preventDefault(); extra = extra.filter((c) => c !== a.dataset.c); renderMulti(); });
      const add = wrap.querySelector(".lang-add");
      if (add) add.onchange = () => { if (add.value) { extra.push(add.value); renderMulti(); } };
    }
    lang.addEventListener("change", () => {
      picked = lang.value;
      if (multi) {  // an incompatible extra is dropped when the first language changes
        const ok = new Set(compatible([lang.value]));
        extra = extra.filter((c) => ok.has(c) || c === "auto");
        renderMulti();
      }
    });
    function fill(list, def, keep) {
      lang.disabled = false;
      lang.innerHTML = list.map((l) => `<option value="${esc(l.code)}">${esc(l.code === "auto" ? T("Automatic (detect the language)") : l.name + " (" + l.code + ")")}</option>`).join("");
      const codes = list.map((l) => l.code);
      lang.value = codes.includes(keep) ? keep : (codes.includes(def) ? def : (codes[0] || "auto"));
      names = Object.fromEntries(list.map((l) => [l.code, l.name]));
    }
    function setMulti(d, list) {
      if (d && d.multiple) { multi = {codes: list.map((l) => l.code).filter((c) => c !== "auto"), groups: d.groups || [], universal: d.universal || ["en"]}; }
      else multi = null;
      renderMulti();
    }
    async function refresh(keepVariant) {
      const my = ++seq, t = (typeof target === "function" ? target() : (target.value || "")), same = (t === lastTarget), keep = same ? picked : null;
      if (!same) extra = [];
      lastTarget = t;
      if (t.startsWith("engine:")) {
        const id = t.slice(7), q = keepVariant && variant && variant.value ? "?engine=" + encodeURIComponent(variant.value) : "";
        let d;
        try { d = await api("GET", "/v1/engines/" + encodeURIComponent(id) + "/languages" + q); } catch (e) { d = null; }
        if (my !== seq) return;  // a newer choice is already loading
        if (!d) { fill([{code: "auto", name: "auto"}], "auto", keep); setMulti(null); setNote(""); return; }
        if (variant && variantWrap) {
          const vs = d.engines || [];
          if (vs.length) {
            if (!keepVariant) variant.innerHTML = vs.map((v) => `<option value="${esc(v.value)}">${esc(v.label)}</option>`).join("");
            variant.value = String(d.engine);
            variantWrap.classList.remove("d-none");
          } else { variantWrap.classList.add("d-none"); variant.innerHTML = ""; }
        }
        if (d.fixed) {
          const reads = (d.reads || []).map((x) => x.name).join(", ");
          lang.innerHTML = `<option value="auto">${esc(reads ? T("No language setting (reads") + " " + reads + ")" : T("Automatic (detected by the engine)"))}</option>`;
          lang.value = "auto"; lang.disabled = true; setMulti(null);
          setNote(d.available === false ? T("This engine is not installed here.")
            : reads ? T("This engine's models read") + " " + reads + T(". It has no language setting.")
            : T("This engine detects the language itself."));
        } else {
          fill(d.languages, d.default, keep); setMulti(d, d.languages);
          setNote(d.hint ? T("This engine detects the language itself; a chosen language is sent to the model as a hint (useful for ambiguous scripts).") : "");
        }
        return;
      }
      if (variantWrap) { variantWrap.classList.add("d-none"); if (variant) variant.innerHTML = ""; }
      if (!all) { try { all = (await api("GET", "/v1/languages")).languages; } catch (e) { all = [{code: "auto", name: "auto"}]; } }
      if (my !== seq) return;
      fill(all, "auto", keep); setMulti({multiple: true, groups: [], universal: ["en"]}, all); setNote("");  // a route: every engine takes what it supports
    }
    (cfg.listen || (typeof target === "function" ? null : target))?.addEventListener("change", () => refresh(false));
    if (variant) variant.addEventListener("change", () => refresh(true));
    refresh(false);
    return {
      refresh,
      // preselect a saved value ("fr" or "en,fr"): the first goes in the dropdown, the rest become chips
      set: (value) => { const codes = String(value || "").split(/[,+]/).map((c) => c.trim()).filter(Boolean); if (!codes.length) return;
        if ([...lang.options].some((o) => o.value === codes[0])) lang.value = codes[0]; picked = codes[0];
        extra = codes.slice(1); if (multi) renderMulti(); },
      variantValue: () => (variant && variantWrap && !variantWrap.classList.contains("d-none") ? variant.value : ""),
      // the chosen languages: one code, or a list when several are selected (an "auto" first language then steps aside)
      languages: () => { const list = [lang.value, ...extra].filter((c, i, a) => c && a.indexOf(c) === i); const real = list.filter((c) => c !== "auto"); return real.length > 1 ? real : (real[0] || list[0]); },
    };
  }

  window.OcrRoute = { api, toast, confirmDo, fmtMs, fmtCents, chart, liveFeed, repaintCharts, languagePicker, modelPicker, $, $$ };
})();
