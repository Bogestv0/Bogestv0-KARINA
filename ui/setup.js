/* Окно установки и удаления. Работает через window.pywebview.api (karina/setup_app.py).
   Режим берётся из api.setup_state().mode: install | update | uninstall. Для проверки в браузере: tests/mock_setup.js, адрес setup.html#uninstall. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const call = async (n, ...a) => { try { const f = api() && api()[n]; return f ? await f(...a) : null; } catch (e) { return null; } };

  const FLOWS = {
    install: ["i-welcome", "i-options", "i-profile", "progress", "done"],
    update: ["i-welcome", "progress", "done"],
    uninstall: ["u-welcome", "u-choose", "progress", "done"],
  };
  const ICONS = {
    admin: '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.500-7-10V6z"/><path d="M9.500 12l2 2 3.500-4"/>',
    vpn: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.500 2.700 3.500 5.700 3.500 9s-1 6.300-3.500 9c-2.500-2.700-3.500-5.700-3.500-9s1-6.300 3.500-9z"/>',
    auto: '<path d="M12 3v9"/><path d="M6.300 6.800a8 8 0 1 0 11.400 0"/>',
    island: '<rect x="3" y="4" width="18" height="6" rx="3"/><path d="M8 15h8M10 19h4"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.600 5.600l2.100 2.100M16.300 16.300l2.100 2.100M18.400 5.600l-2.100 2.100M7.700 16.300l-2.100 2.100"/>',
  };
  const svg = n => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ICONS.folder}</svg>`;

  const S = { mode: "install", flow: FLOWS.install, at: 0, st: {}, opts: {}, items: [], sel: new Set(), confirm: false, busy: false, timer: null };

  function fmt(n) {
    if (!n) return "—";
    const u = ["Б", "КБ", "МБ", "ГБ"]; let i = 0;
    while (n >= 1024 && i < 3) { n /= 1024; i++; }
    return (n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)).toString().replace(".", ",") + " " + u[i];
  }
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---------------------------------------------------------------- шаги
  function show(id) {
    S.at = Math.max(0, S.flow.indexOf(id));
    $$(".step").forEach(s => s.classList.toggle("on", s.dataset.step === id));
    $("#dots").innerHTML = S.flow.map((_, i) => `<i class="${i === S.at ? "on" : i < S.at ? "past" : ""}"></i>`).join("");
  }
  const go = id => { if (S.busy && id !== "done") return; show(id); };

  // ---------------------------------------------------------------- установка
  const OPTIONS = [
    { k: "admin", ic: "admin", t: "Запускать Карину с правами администратора", d: "Нужно для окон программ от администратора и VPN на весь компьютер. Windows спросит разрешение один раз.", on: false },
    { k: "vpn", ic: "vpn", t: "Сразу установить движок для VPN (sing-box)", d: "Около 12 МБ. Серверы или подписку добавите потом в настройках VPN.", on: true, only: "has_vpn" },
    { k: "background", ic: "auto", t: "Запускать Карину вместе с Windows", d: "Стартует тихо, в трее, окно не открывается: Карина просто ждёт своё имя.", on: false },
  ];
  function drawOptions() {
    const list = OPTIONS.filter(o => !o.only || S.st[o.only]);
    list.forEach(o => { if (!(o.k in S.opts)) S.opts[o.k] = o.on; });
    $("#optList").innerHTML = list.map(o => `<label class="opt${S.opts[o.k] ? " on" : ""}"><span class="ic">${svg(o.ic)}</span><span class="tx"><div class="t">${esc(o.t)}</div><div class="d">${esc(o.d)}</div></span><input type="checkbox" data-k="${o.k}"${S.opts[o.k] ? " checked" : ""}><span class="sw"></span></label>`).join("");
    $$("#optList input").forEach(i => i.addEventListener("change", () => { S.opts[i.dataset.k] = i.checked; i.closest(".opt").classList.toggle("on", i.checked); }));
  }
  function profile() {
    const v = id => $(id).value.trim(), n = x => (x === "" ? 0 : parseInt(x, 10));
    const p = { name: v("#pName"), city: v("#pCity"), birth_day: n(v("#pDay")), birth_month: n(v("#pMonth")), birth_year: n(v("#pYear")) };
    const bad = (v("#pDay") && !(p.birth_day >= 1 && p.birth_day <= 31)) || (v("#pMonth") && !(p.birth_month >= 1 && p.birth_month <= 12)) || (v("#pYear") && !(p.birth_year >= 1900 && p.birth_year <= 2100));
    return bad ? null : p;
  }
  async function runInstall() {
    const p = profile(), er = $("#pErr");
    if (!p) { er.hidden = false; er.textContent = "Проверьте дату рождения: день от 1 до 31, месяц от 1 до 12, год четырьмя цифрами. Или оставьте поля пустыми."; return; }
    er.hidden = true;
    startProgress(S.mode === "update" ? "Обновляю…" : "Устанавливаю…");
    const r = await call("setup_install", { ui: { admin: !!S.opts.admin, background: !!S.opts.background, island: false }, vpn: !!S.opts.vpn, profile: p });
    if (!r || r.ok === false) return finish({ ok: false, error: (r && r.error) || "Не удалось запустить установку." });
    poll();
  }

  // ---------------------------------------------------------------- удаление
  const GROUPS = { downloads: "Скачанное", data: "Ваши данные", system: "Система" };
  const IC = { llm: "gear", stt: "gear", imgmodels: "gear", voice: "gear", components: "gear", engine: "gear", vpn: "vpn", chats: "folder", memory: "user", island: "island", plugins: "folder", settings: "user", logs: "folder", system: "auto", program: "admin" };
  function drawChoose() {
    let html = "", last = "";
    S.items.forEach(it => {
      if (it.group !== last) { html += `<div class="grp">${GROUPS[it.group] || ""}</div>`; last = it.group; }
      const on = S.sel.has(it.id);
      html += `<label class="opt del${on ? " on" : ""}"><span class="ic">${svg(IC[it.id])}</span><span class="tx"><div class="t">${esc(it.title)}</div><div class="d">${esc(it.desc)}</div></span><span class="sz">${it.size ? fmt(it.size) : ""}</span><input type="checkbox" data-id="${it.id}"${on ? " checked" : ""}><span class="sw"></span></label>`;
    });
    $("#delList").innerHTML = html;
    $$("#delList input").forEach(i => i.addEventListener("change", () => { i.checked ? S.sel.add(i.dataset.id) : S.sel.delete(i.dataset.id); S.confirm = false; drawChoose(); }));
    const picked = S.items.filter(i => S.sel.has(i.id)), size = picked.reduce((a, i) => a + (i.size || 0), 0);
    const hasData = picked.some(i => i.group === "data");
    $("#uSum").innerHTML = picked.length ? `Выбрано <b>${picked.length}</b> · <b>${fmt(size)}</b>` : "Ничего не выбрано";
    $("#uWarn").hidden = !hasData;
    const b = $("#btnUDo"); b.disabled = !picked.length; b.textContent = S.confirm ? "Да, удалить" : "Удалить";
    const ids = [...S.sel], all = S.items.map(i => i.id), dl = S.items.filter(i => i.group === "downloads").map(i => i.id);
    const eq = a => a.length === ids.length && a.every(x => S.sel.has(x));
    $$("#uPreset button").forEach(x => x.classList.toggle("on", (x.dataset.p === "all" && eq(all)) || (x.dataset.p === "dl" && dl.length && eq(dl)) || (x.dataset.p === "none" && !ids.length)));
  }
  function preset(p) {
    S.sel = new Set(p === "all" ? S.items.map(i => i.id) : p === "dl" ? S.items.filter(i => i.group === "downloads").map(i => i.id) : []);
    S.confirm = false; drawChoose();
  }
  async function runUninstall(ids) {
    startProgress("Удаляю…");
    // uninstall_run возвращается, когда всё стёрто, и сам не сообщает ход: пока ждём, полоса плавно идёт к 90%, а в журнал по очереди выходят пункты
    const names = ids.map(i => (S.items.find(x => x.id === i) || {}).title).filter(Boolean);
    let pct = 4, shown = 0;
    clearInterval(S.timer);
    S.timer = setInterval(() => {
      pct += (90 - pct) * 0.08;
      const upto = Math.min(names.length, Math.floor((pct / 90) * names.length) + 1);
      const fresh = names.slice(shown, upto).map(t => ({ text: t })); shown = upto;
      paint({ pct, text: names[Math.max(0, upto - 1)] || "Удаляю", log: fresh });
    }, 220);
    const r = await call("uninstall_run", ids);
    clearInterval(S.timer);
    if (!r || r.ok === false) return finish({ ok: false, error: (r && r.error) || "Не удалось удалить." });
    paint({ pct: 100, text: "Готово", log: names.slice(shown).map(t => ({ text: t })) });
    setTimeout(() => finish(r), 500);
  }

  // ---------------------------------------------------------------- ход работы
  function startProgress(title) {
    S.busy = true; $("#progTitle").textContent = title; $("#meterFill").style.width = "0%"; $("#progPct").textContent = "0%";
    $("#progText").textContent = "Подготовка"; $("#log").innerHTML = ""; $("#meter").classList.remove("bad"); show("progress");
  }
  function paint(p) {
    const pct = Math.max(0, Math.min(100, Math.round(p.pct || 0)));
    $("#meterFill").style.width = pct + "%"; $("#progPct").textContent = pct + "%";
    if (p.text) $("#progText").textContent = p.text;
    const log = $("#log");
    (p.log || []).forEach(l => { const d = document.createElement("div"); d.className = l.bad ? "bad" : "ok"; d.textContent = l.text || l; log.appendChild(d); });
    while (log.children.length > 4) log.removeChild(log.firstChild);
  }
  function poll() {
    clearInterval(S.timer);
    S.timer = setInterval(async () => {
      const p = await call("setup_progress");
      if (!p) return;
      paint(p);
      if (p.done) { clearInterval(S.timer); setTimeout(() => finish(p), 450); }
    }, 300);
  }
  function finish(p) {
    S.busy = false; clearInterval(S.timer);
    const un = S.mode === "uninstall", ok = p.ok !== false;
    $("#doneTitle").textContent = ok ? (un ? "Карина удалена" : S.mode === "update" ? "Обновлено!" : "Готово!") : "Что-то пошло не так";
    $("#doneLead").textContent = ok
      ? (un ? "Выбранное убрано с компьютера. Спасибо, что были с нами." : "Карина установлена. Скажите её имя, и она отзовётся.")
      : (p.error || "Установка не завершена.");
    $("#finOpts").hidden = !(ok && !un);
    const rep = $("#doneReport"), prob = p.problems || [];
    rep.hidden = !prob.length; rep.innerHTML = prob.map(esc).join("<br>");
    $("#btnFinish").textContent = ok && !un ? "Готово" : "Закрыть";
    show("done");
  }

  // ---------------------------------------------------------------- запуск
  async function init() {
    const h = (location.hash || "").replace("#", "");
    const st = (await call("setup_state")) || { mode: h || "install", version: "1.23.0", has_vpn: true };
    S.st = st; S.mode = FLOWS[st.mode] ? st.mode : "install"; S.flow = FLOWS[S.mode];
    $("#ver").textContent = "v" + (st.version || "");
    if (S.mode === "update") {
      $("#iWelcomeLead").textContent = `Найдена версия ${st.installed_version || ""}. Настройки, профиль, модели и голос сохранятся.`;
      $("#btnStart").textContent = "Обновить"; $("#btnCustom").hidden = true;
    }
    if (S.mode === "uninstall") {
      const r = await call("uninstall_list");
      S.items = (r && r.items) || [];
      if (!S.items.length) $("#btnUPick").disabled = true;
      const dl = S.items.filter(i => i.group === "downloads").reduce((a, i) => a + (i.size || 0), 0);
      if (dl) $("#uWelcomeLead").textContent = `Скачанного на компьютере: ${fmt(dl)}. Его можно убрать отдельно, не трогая диалоги и настройки.`;
      S.sel = new Set(S.items.filter(i => i.group === "downloads").map(i => i.id));
    }
    drawOptions();
    show(S.flow[0]);
  }

  $("#btnStart").onclick = runInstall;
  $("#btnCustom").onclick = () => go("i-options");
  $("#btnInstall").onclick = runInstall;
  $("#btnUAll").onclick = () => runUninstall(S.items.map(i => i.id));
  $("#btnUPick").onclick = () => { drawChoose(); go("u-choose"); };
  $("#btnUDo").onclick = () => {
    const hasData = S.items.some(i => S.sel.has(i.id) && i.group === "data");
    if (hasData && !S.confirm) { S.confirm = true; drawChoose(); return; }
    runUninstall([...S.sel]);
  };
  $("#uPreset").addEventListener("click", e => { const b = e.target.closest("button"); if (b) preset(b.dataset.p); });
  $$("[data-go]").forEach(b => b.addEventListener("click", () => go(b.dataset.go)));
  $("#btnFinish").onclick = () => call("setup_finish", { launch: $("#fLaunch").checked && !$("#finOpts").hidden, desktop: $("#fDesk").checked && !$("#finOpts").hidden });
  $("#wcMin").onclick = () => call("win_minimize");
  $("#wcClose").onclick = () => (S.busy ? null : call("win_close"));
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !S.busy) call("win_close"); });

  if (window.pywebview) init(); else window.addEventListener("pywebviewready", init), setTimeout(() => { if (!S.flow || !$(".step.on")) init(); }, 400);
})();
