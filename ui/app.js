(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  let api = null, cfg = null, status = {}, logCount = 0, upd = null, version = '';

  const call = async (name, ...args) => { try { return await api[name](...args); } catch (e) { console.error(name, e); return null; } };
  const flash = (node, text, err) => { node.textContent = text; node.classList.toggle('err', !!err); clearTimeout(node._t); node._t = setTimeout(() => node.textContent = '', 4000); };

  // ---------- вкладки ----------
  $('#tabs').addEventListener('click', e => {
    const b = e.target.closest('button[data-tab]'); if (!b) return;
    $$('#tabs button').forEach(x => x.classList.toggle('on', x === b));
    $$('.tab').forEach(t => t.classList.toggle('on', t.id === 'tab-' + b.dataset.tab));
    if (b.dataset.tab === 'apps') loadApps();
    if (b.dataset.tab === 'commands') loadCommands();
    if (b.dataset.tab === 'models') { loadModels(); loadBrain(); loadCloud(); loadImg(); }
    if (b.dataset.tab === 'chat') openChatTab();
    if (b.dataset.tab === 'sound') loadOut(false);
    try { localStorage.setItem('karina.tab', b.dataset.tab); } catch (_) {}
  });

  // ---------- тема ----------
  function applyTheme(t) {
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  }

  // ---------- состояние ----------
  const STATES = {
    loading:  ['Запуск', 'Загружаюсь…', 'Подождите немного.'],
    idle:     ['Слушаю', null, 'Можно сказать, например: «открой блокнот», «громкость пятьдесят», «найди в интернете погоду».'],
    thinking: ['Обработка', 'Думаю…', 'Распознаю вашу фразу.'],
    speaking: ['Отвечаю', 'Говорю…', 'Пока я говорю, микрофон не слушает.'],
    paused:   ['Пауза', 'Микрофон выключен', 'Включите микрофон кнопкой ниже.'],
    error:    ['Ошибка', 'Что-то пошло не так', 'Подробности — в разговоре ниже.'],
  };
  function renderState(s, detail, sleeping, listening) {
    const hero = $('#hero');
    let key = s in STATES ? s : 'idle';
    const [label, title, hint] = STATES[key];
    hero.dataset.state = key;
    $('#stateLabel').textContent = sleeping && key === 'idle' ? 'Режим сна' : label;
    $('#stateTitle').textContent = sleeping && key === 'idle' ? 'Я сплю' : (title || `Скажите «${cfg ? cfg.name : 'Карина'}, …»`);
    $('#stateHint').textContent = sleeping && key === 'idle' ? `Скажите «${cfg ? cfg.name : 'Карина'}, проснись».` : (detail || hint);
    $('#btnListen').textContent = listening === false ? 'Включить микрофон' : 'Выключить микрофон';
    $('#kvMode').textContent = sleeping ? 'Сон' : listening === false ? 'Пауза' : 'Слушаю';
  }

  function renderStatus(st) {
    status = st;
    const chip = $('#chipNet');
    chip.className = 'chip ' + (st.online ? 'ok' : 'off');
    chip.lastElementChild.textContent = st.online ? 'Онлайн' : 'Офлайн';
    $('#kvNet').textContent = st.online ? 'есть' : 'нет — работаю автономно';
    const names = { tiny: 'минимальная', base: 'базовая', small: 'хорошая', medium: 'высокая' };
    $('#kvStt').textContent = (names[st.model] || st.model) + ' · офлайн';
    $('#kvVoice').textContent = st.voice === 'online' ? 'онлайн (нейросеть)' : st.voice === 'silero' ? 'локально (нейросеть Silero)' : st.voice === 'piper' ? 'офлайн (нейросеть «Ирина»)' : st.voice === 'offline' ? 'системный (офлайн)' : st.online ? 'онлайн' : 'системный';
    $('#kvMics').textContent = st.mics_text || '—'; renderMicStatus(st.mics);
    const w = $('#ttsWarn'); w.hidden = !st.tts_warning; w.textContent = st.tts_warning || '';
    const p = $('#pluginWarn'); p.hidden = !(st.plugin_errors && st.plugin_errors.length); p.textContent = (st.plugin_errors || []).join('\n');
    renderState(st.state, '', st.sleeping, st.listening);
  }

  // ---------- разговор ----------
  function addLog(kind, text, ts) {
    const log = $('#log');
    if (!logCount) log.textContent = '';
    logCount++;
    const row = el('div', 'msgline ' + kind);
    row.append(el('time', '', ts || ''), el('span', '', kind === 'ignored' ? text + '  · не ко мне' : text));
    log.append(row);
    while (log.children.length > 200) log.firstChild.remove();
    log.scrollTop = log.scrollHeight;
  }
  $('#btnClear').onclick = () => { logCount = 0; const l = $('#log'); l.textContent = ''; const d = el('div', 'empty'); d.textContent = 'Пока пусто.'; l.append(d); };
  $('#sendForm').addEventListener('submit', async e => {
    e.preventDefault();
    const i = $('#sendInput'); const t = i.value.trim(); if (!t) return;
    i.value = ''; await call('send_text', t);
  });
  $('#btnListen').onclick = async () => { await call('set_listening', status.listening === false); if (status.sleeping) await call('wake_up'); refresh(); };
  $('#btnVoice').onclick = () => call('test_voice');

  // ---------- своё окно: заголовок, перетаскивание, края ----------
  const root = document.documentElement;
  function setMaxed(m) { root.classList.toggle('maxed', !!m); const b = $('#wcMax'); if (b) b.title = m ? 'Свернуть в окно' : 'Развернуть'; }
  function setFrameless(on, maxed) {
    root.classList.toggle('frameless', !!on);
    $('#winctl').hidden = !on; $('#rz').hidden = !on;
    setMaxed(maxed);
  }
  $('#wcMin').onclick = () => call('win_minimize');
  $('#wcMax').onclick = async () => { const m = await call('win_toggle_maximize'); if (m !== null) setMaxed(m); };
  $('#wcClose').onclick = () => call('win_close');
  $('#topbar').addEventListener('mousedown', e => {
    if (!root.classList.contains('frameless') || e.button !== 0) return;
    if (e.target.closest('button,a,input,select,textarea,.tabs')) return;
    call('win_drag', 'move');
  });
  $('#topbar').addEventListener('dblclick', e => {
    if (!root.classList.contains('frameless') || e.target.closest('button,a,input,select,textarea,.tabs')) return;
    $('#wcMax').click();
  });
  $('#rz').addEventListener('mousedown', e => {
    const h = e.target.closest('i[data-e]'); if (!h || e.button !== 0) return;
    e.preventDefault(); call('win_drag', h.dataset.e);
  });

  // ---------- права администратора ----------
  let adm = null;
  function renderAdmin() {
    if (!adm || !adm.supported) return;
    $('#admRow').hidden = false;
    $('#admSwitch').checked = !!adm.enabled;
    const n = $('#admNote');
    n.className = 'note' + (adm.admin ? '' : ' warn');
    n.textContent = adm.admin
      ? 'Сейчас Карина работает с правами администратора.'
      : adm.enabled ? 'Включено, но сейчас Карина открыта с обычными правами — перезапустите её.' : 'Сейчас Карина работает с обычными правами.';
    $('#admRestart').hidden = !(adm.enabled !== adm.admin);
    $('#admBar').hidden = !(!adm.admin && !adm.enabled && !adm.asked);
  }
  async function loadAdmin() { adm = await call('admin_state'); renderAdmin(); }
  async function setAdmin(on) {
    const m = $('#admMsg'); m.textContent = on ? 'Жду подтверждения Windows…' : 'Отключаю…'; m.classList.remove('err');
    $('#admSwitch').disabled = true;
    const r = await call('admin_set', on);
    $('#admSwitch').disabled = false;
    if (r && r.state) adm = r.state;
    if (r && r.ok) flash(m, on ? 'Готово: права выданы, нужен перезапуск' : 'Права отключены, действует со следующего запуска');
    else flash(m, (r && r.error) || 'Не получилось', true);
    renderAdmin();
  }
  $('#admSwitch').addEventListener('change', e => setAdmin(e.target.checked));
  $('#admYes').onclick = async () => { $('#admBar').hidden = true; await setAdmin(true); };
  $('#admNo').onclick = async () => { $('#admBar').hidden = true; await call('admin_dismiss'); if (adm) adm.asked = true; };
  $('#admRestart').onclick = async () => { const r = await call('restart_app'); if (r && !r.ok) flash($('#admMsg'), r.error, true); };

  // события из Python
  window.karina = {
    event(ev) {
      if (ev.type === 'level') {
        document.documentElement.style.setProperty('--lvl', ev.v.toFixed(2));
        $$('.mic-row').forEach(r => { const v = (ev.mics || {})[r.dataset.slot]; $('.meter-bar', r).style.width = Math.round((v || 0) * 100) + '%'; });
      } else if (ev.type === 'mics') renderMicStatus(ev.mics);
      else if (ev.type === 'mic_pick') pickMic(ev.slot);
      else if (ev.type === 'out_test') { flash($('#outMsg'), ev.error || 'Звук проигран', !!ev.error); $('#outTest').disabled = false; }
      else if (ev.type === 'winstate') setMaxed(ev.max);
      else if (ev.type === 'log') addLog(ev.kind, ev.text, ev.ts);
      else if (ev.type === 'goto') { const b = $(`#tabs [data-tab="${ev.tab}"]`); if (b) b.click(); }
      else if (ev.type === 'chat_open') { chOpenId = ev.chat; const b = $('#tabs [data-tab="chat"]'); if (b) b.click(); }
      else if (ev.type === 'chat_start') chatStart(ev);
      else if (ev.type === 'chat_step') { if (ev.chat === chCur) setStep(ev.text); }
      else if (ev.type === 'chat_delta') { if (ev.chat === chCur && chBubble) renderMd(chBubble, ev.text); scrollChat(); }
      else if (ev.type === 'chat_image_plan') { if (ev.chat === chCur && chBubble) imgPlan(chBubble.parentElement, ev.items); }
      else if (ev.type === 'chat_image') { if (ev.chat === chCur && chBubble) imgEvent(chBubble.parentElement, ev); }
      else if (ev.type === 'chat_activity') actEvent(ev);
      else if (ev.type === 'chat_done') chatDone(ev);
      else if (ev.type === 'limg_progress') limgProgress(ev);
      else if (ev.type === 'limg_done') { if (ev.error && ev.error !== 'cancelled') flash($('#imgMsg'), ev.error, true); loadImg(); }
      else if (ev.type === 'images_test') { flash($('#imgMsg'), ev.text, !ev.ok); $('#imgTest').disabled = false; }
      else if (ev.type === 'search_test') { const b = $('#searchTest'); b.hidden = false; b.textContent = ev.lines.join('\n'); flash($('#searchMsg'), ev.ok ? 'Поиск работает' : 'Поиск не работает', !ev.ok); }
      else if (ev.type === 'cloud_test') { const r = $(`.cloud-row[data-pid="${ev.pid}"]`); if (r) { const o = $('.note', r); o.hidden = false; o.className = 'note ' + (ev.ok ? '' : 'bad'); o.textContent = ev.ok ? `Работает (${ev.sec} с): ${ev.text}` : ev.text; $$('button', r).forEach(b => b.disabled = false); } loadCloud(); loadBrain(); }
      else if (ev.type === 'brain_test') { const o = $('#brainTestOut'); o.hidden = false; o.className = 'note ' + (ev.ok ? '' : 'bad'); o.textContent = ev.ok ? `Claude отвечает: «${ev.text}» (${ev.sec} с)` : ev.text; $('#brainTest').disabled = false; }
      else if (ev.type === 'voice_dl') voiceDlProgress(ev);
      else if (ev.type === 'voice_dl_done') voiceDlDone(ev);
      else if (ev.type === 'google_done') googleDone(ev);
      else if (ev.type === 'silero_dl') silProgress(ev);
      else if (ev.type === 'silero_dl_done') silDone(ev);
      else if (ev.type === 'model_progress') modelProgress(ev);
      else if (ev.type === 'model_done') { if (ev.error && ev.error !== 'cancelled') flash($('#mdMsg'), ev.error, true); loadModels(); }
      else if (ev.type === 'engine') renderEngine(ev);
      else if (ev.type === 'update_progress') { const pb = $('#updPbar'); pb.hidden = false; $('i', pb).style.width = (ev.total ? ev.done / ev.total * 100 : 0).toFixed(1) + '%'; $('#updNotes').textContent = ev.total ? `Скачано ${fmtGb(ev.done)} из ${fmtGb(ev.total)}` : 'Скачиваю…'; }
      else if (ev.type === 'update_ready') { $('#updNotes').textContent = 'Скачано. Запускаю установку — Карина закроется и откроется заново.'; }
      else if (ev.type === 'update_done') { $('#updInstall').disabled = false; $('#updPbar').hidden = true; if (ev.error && ev.error !== 'cancelled') { $('#updNotes').textContent = ev.error; $('#updNotes').classList.add('err'); } }
      else if (ev.type === 'model_test') { const o = $('#mdTestOut'); o.hidden = false; o.className = 'note ' + (ev.ok ? '' : 'bad'); o.textContent = ev.ok ? `${ev.text}\n(${ev.sec} с)` : ev.text; $('#mdTest').disabled = false; }
      else if (ev.type === 'state') { status.state = ev.state; status.sleeping = ev.sleeping; status.listening = ev.listening; renderState(ev.state, ev.detail, ev.sleeping, ev.listening); }
    },
  };

  // ---------- команды ----------
  async function loadCommands() {
    const list = await call('get_commands') || [];
    const box = $('#cmdList'); box.textContent = '';
    for (const c of list) {
      const d = el('div', 'cmd'); d.append(el('b', '', c.desc));
      if (c.examples && c.examples.length) d.append(el('em', '', c.examples.map(x => '«' + x + '»').join('  ·  ')));
      box.append(d);
    }
    const cs = await call('list_custom') || [];
    const cl = $('#customList'); cl.textContent = '';
    if (!cs.length) cl.append(el('div', 'muted small', 'Своих команд пока нет.'));
    cs.forEach((c, i) => {
      const it = el('div', 'item ok'); const t = el('div', 't');
      t.append(el('b', '', c.phrases.join(' / ')), el('span', '', `${c.do.type}: ${c.do.value}`));
      const rm = el('button', 'btn danger', 'Удалить'); rm.onclick = async () => { await call('remove_custom', i); loadCommands(); };
      it.append(t, rm); cl.append(it);
    });
  }
  $('#customForm').addEventListener('submit', async e => {
    e.preventDefault(); const f = e.target;
    const r = await call('add_custom', f.phrases.value, f.type.value, f.value.value, f.reply.value);
    if (r && r.ok) { f.reset(); flash($('#customMsg'), 'Добавлено'); loadCommands(); } else flash($('#customMsg'), (r && r.error) || 'Ошибка', true);
  });

  // копирование: сначала через Python (буфер Windows), затем средствами браузера
  async function copyText(text) {
    try { if (await api.clipboard_set(text)) return true; } catch (_) {}
    try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
    try { const t = document.createElement('textarea'); t.value = text; t.style.position = 'fixed'; t.style.opacity = '0'; document.body.append(t); t.select(); const ok = document.execCommand('copy'); t.remove(); return ok; } catch (_) { return false; }
  }
  function flashBtn(b, ok) { const old = b.dataset.l || b.textContent; b.dataset.l = old; b.textContent = ok ? '✔ Скопировано' : 'Не удалось'; clearTimeout(b._t); b._t = setTimeout(() => b.textContent = old, 1600); }

  // ---------- чат ----------
  let chCur = null, chOpenId = null, chBubble = null, chBusy = false;
  const scrollChat = () => { const m = $('#chMsgs'); m.scrollTop = m.scrollHeight; };
  function setStep(t, ms) { const s = $('#chStep'); s.hidden = !t; s.textContent = t || ''; clearTimeout(s._t); if (t && ms) s._t = setTimeout(() => { if (!chBusy) setStep(''); }, ms); }
  function setBusy(b) { chBusy = b; $('#chSend').hidden = b; $('#chStop').hidden = !b; if (!b) setStep(''); }

  // Разметка ответа строим узлами DOM (не innerHTML): текст модели не может внедрить разметку или скрипт.
  function link(label, url) { const a = el('a', '', label); a.href = '#'; a.dataset.href = url; a.title = url; return a; }
  function inline(parent, text) {
    const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\(https?:\/\/[^)\s]+\))|(https?:\/\/[^\s)<]+)/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.append(text.slice(last, m.index));
      const t = m[0];
      if (m[1]) parent.append(el('code', '', t.slice(1, -1)));
      else if (m[2]) parent.append(el('strong', '', t.slice(2, -2)));
      else if (m[3]) { const i = t.indexOf(']('); parent.append(link(t.slice(1, i), t.slice(i + 2, -1))); }
      else if (m[4]) parent.append(link(t, t));
      last = m.index + t.length;
    }
    if (last < text.length) parent.append(text.slice(last));
  }
  function renderMd(box, text) {
    box.textContent = '';
    const lines = String(text).split('\n'); let i = 0, list = null, para = null;
    const endBlocks = () => { list = null; para = null; };
    while (i < lines.length) {
      const ln = lines[i];
      if (/^\s*```/.test(ln)) {
        endBlocks(); const code = []; const lang = (/^\s*```\s*([\w+#.-]*)/.exec(ln) || [])[1] || ''; i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++].replace(/^ {0,3}/, ''));
        i++; const src = code.join('\n'); const wrap = el('div', 'codebox'); const hd = el('div', 'codehead');
        const cp = el('button', 'codecopy', 'Копировать'); cp.type = 'button'; cp.onclick = async () => flashBtn(cp, await copyText(src));
        hd.append(el('span', '', lang || 'код'), cp); const pre = el('pre'); pre.append(el('code', '', src)); wrap.append(hd, pre); box.append(wrap); continue;
      }
      let m;
      if ((m = /^(#{1,4})\s+(.*)$/.exec(ln))) { endBlocks(); const h = el('h' + Math.min(4, m[1].length + 2)); inline(h, m[2]); box.append(h); }
      else if ((m = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(ln))) {
        const ord = /^\s*\d/.test(ln);
        if (!list || list.tagName !== (ord ? 'OL' : 'UL')) { list = el(ord ? 'ol' : 'ul'); box.append(list); para = null; }
        const li = el('li'); inline(li, m[1]); list.append(li);
      } else if (!ln.trim()) endBlocks();
      else { list = null; if (!para) { para = el('p'); box.append(para); } else para.append(document.createElement('br')); inline(para, ln); }
      i++;
    }
  }
  function attachSteps(row, steps) {
    if (!steps || !steps.length || row.querySelector('.steps')) return;
    const d = el('details', 'steps'); d.append(el('summary', '', 'Ход работы'));
    const ol = el('ol'); steps.forEach(s => ol.append(el('li', '', s))); d.append(ol);
    row.insertBefore(d, row.querySelector('.meta'));
  }
  const fmtSize = n => n < 1024 ? n + ' Б' : n < 1048576 ? Math.round(n / 1024) + ' КБ' : (n / 1048576).toFixed(1) + ' МБ';
  const KIND = { word: ['📄', 'Документ Word'], excel: ['📊', 'Таблица Excel'], pdf: ['📕', 'PDF'], svg: ['🖼', 'Схема SVG'], text: ['📝', 'Текстовый файл'], code: ['💻', 'Код'], image: ['🎨', 'Изображение'] };
  const PREVIEWABLE = /\.(html?|svg|png|jpe?g|webp|gif|md|txt|csv|json|py|js|mjs|ts|css|sql|xml|ya?ml|ini|toml)$/i;

  // Предпросмотр: картинка / HTML-шаблон / текст. HTML — в изолированной рамке: без доступа к сети и к самой программе.
  const CSP = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; font-src data:; script-src \'unsafe-inline\'">';
  function previewBox(r) {
    const box = el('div', 'pview');
    if (r.type === 'image') { const i = el('img', 'pimg'); i.src = r.src; i.alt = ''; box.append(i); }
    else if (r.type === 'html') {
      const f = document.createElement('iframe'); f.className = 'pframe'; f.setAttribute('sandbox', 'allow-scripts'); f.referrerPolicy = 'no-referrer';
      let h = String(r.html);
      if (/<head[^>]*>/i.test(h)) h = h.replace(/<head[^>]*>/i, m => m + CSP);
      else if (/<html[^>]*>/i.test(h)) h = h.replace(/<html[^>]*>/i, m => m + '<head>' + CSP + '</head>');
      else h = CSP + h;
      f.srcdoc = h; box.append(f);
    } else { const pre = el('pre', 'ptext'); pre.append(el('code', '', String(r.text).slice(0, 60000))); box.append(pre); }
    return box;
  }
  // ---------- правая панель просмотра: эскизы, HTML-шаблоны, картинки — на всю высоту, с вкладками ----------
  const NAV = '<script>document.addEventListener("click",function(e){var a=e.target.closest&&e.target.closest("a[href]");if(!a)return;var h=a.getAttribute("href");if(!h||h.charAt(0)==="#"||/^javascript:/i.test(h))return;e.preventDefault();parent.postMessage({karinaNav:h},"*")},true);<\/script>';
  let pnTabs = [], pnCur = -1;
  const pnFetch = t => t.project ? call('project_preview', t.project, t.path) : call('file_preview_full', t.name);
  function pnHtml(html) {
    let h = String(html);
    const inj = CSP + NAV;
    if (/<head[^>]*>/i.test(h)) h = h.replace(/<head[^>]*>/i, m => m + inj);
    else if (/<html[^>]*>/i.test(h)) h = h.replace(/<html[^>]*>/i, m => m + '<head>' + inj + '</head>');
    else h = inj + h;
    return h;
  }
  function pnRenderTabs() {
    const box = $('#pnTabs'); box.textContent = '';
    if (pnTabs.length < 2) return;
    pnTabs.forEach((t, i) => {
      const b = el('div', 'pn-tab' + (i === pnCur ? ' on' : '')); b.append(el('span', '', t.title));
      const x = el('b', '', '×'); x.title = 'Закрыть вкладку'; x.onclick = e => { e.stopPropagation(); pnClose(i); }; b.append(x);
      b.onclick = () => pnShow(i); box.append(b);
    });
  }
  async function pnShow(i, refetch) {
    pnCur = i; const t = pnTabs[i], body = $('#pnBody'); pnRenderTabs();
    $('#pnTitle').textContent = t.title; $('#pnSub').textContent = t.project ? 'проект · ' + t.project : 'файл';
    if (!t.res || refetch) { body.textContent = ''; body.append(el('div', 'pn-empty', 'Загружаю…')); const r = await pnFetch(t); if (pnCur !== i) return; if (!r || !r.ok) { body.textContent = ''; body.append(el('div', 'pn-empty', (r && r.error) || 'Не получилось показать файл')); return; } t.res = r.result; }
    body.textContent = ''; const r = t.res;
    if (r.type === 'image') { const im = el('img', 'pimg'); im.src = r.src; im.alt = t.title; body.append(im); body.style.background = 'var(--surface-2)'; }
    else if (r.type === 'html') { const f = document.createElement('iframe'); f.setAttribute('sandbox', 'allow-scripts'); f.referrerPolicy = 'no-referrer'; f.title = t.title; f.srcdoc = pnHtml(r.html); body.append(f); body.style.background = '#fff'; }
    else { const pre = el('pre'); pre.textContent = String(r.text).slice(0, 200000); body.append(pre); body.style.background = 'var(--bg)'; }
  }
  function pnOpen(item) {
    const k = (item.project || '') + '|' + (item.path || item.name);
    let i = pnTabs.findIndex(t => t.key === k);
    if (i < 0) { if (pnTabs.length >= 8) pnTabs.shift(); pnTabs.push({ ...item, key: k }); i = pnTabs.length - 1; }
    $('#chPanel').hidden = false; $('#chLayout').classList.add('has-panel'); pnShow(i);
  }
  function pnClose(i) {
    if (i == null) { pnTabs = []; pnCur = -1; } else { pnTabs.splice(i, 1); pnCur = Math.min(pnCur, pnTabs.length - 1); }
    if (!pnTabs.length) { $('#chPanel').hidden = true; $('#chLayout').classList.remove('has-panel', 'wide'); $('#pnBody').textContent = ''; $('#pnTabs').textContent = ''; }
    else pnShow(pnCur < 0 ? 0 : pnCur);
  }
  const pnRel = (base, h) => { const parts = base.split('/'); parts.pop(); for (const seg of h.split('?')[0].split('#')[0].split('/')) { if (seg === '..') parts.pop(); else if (seg && seg !== '.') parts.push(seg); } return parts.join('/'); };
  // клик по ссылке внутри эскиза: открываем нужную страницу проекта в этой же панели
  window.addEventListener('message', async e => {
    const f = $('#pnBody iframe'); if (!f || e.source !== f.contentWindow) return;
    const h = e.data && e.data.karinaNav; if (typeof h !== 'string') return;
    const t = pnTabs[pnCur]; const note = m => { const n = el('div', 'pn-note', m); $('#pnBody').append(n); setTimeout(() => n.remove(), 3500); };
    if (!t || !t.project || /^[a-z][a-z0-9+.-]*:|^\/\//i.test(h)) return note('Внешние ссылки в эскизе не открываются.');
    const path = pnRel(t.path, h), r = await call('project_preview', t.project, path);
    if (!r || !r.ok) return note('Страница «' + path + '» не найдена в проекте.');
    t.path = path; t.title = path; t.key = t.project + '|' + path; t.res = r.result; pnShow(pnCur);
  });
  $('#pnClose').onclick = () => pnClose();
  $('#pnWide').onclick = () => $('#chLayout').classList.toggle('wide');
  $('#pnReload').onclick = () => { if (pnCur >= 0) pnShow(pnCur, true); };
  $('#pnOpen').onclick = async () => { const t = pnTabs[pnCur]; if (!t) return; const r = t.project ? await call('project_open_file', t.project, t.path) : await call('file_open', t.name); if (r && !r.ok) { const n = el('div', 'pn-note', r.error); $('#pnBody').append(n); setTimeout(() => n.remove(), 3500); } };
  function viewButton(item, msg) { const b = el('button', 'btn', '👁 Просмотр'); b.onclick = () => pnOpen(item); return b; }

  // ---------- варианты картинок: плитки 2×2, каждая подписана моделью, которая рисовала; появляются по мере готовности ----------
  const TILE = { wait: 'в очереди', run: 'рисую…', fail: 'не получилось', skip: 'остановлено' };
  function variantGrid(row) {
    let g = row.querySelector('.igrid');
    if (!g) { g = el('div', 'files igrid'); row.querySelector('.bubble').after(g); }
    return g;
  }
  function variantTile(grid, n, label) {
    let t = grid.querySelector(`.itile[data-n="${n}"]`);
    if (!t) {
      t = el('div', 'itile wait'); t.dataset.n = n;
      const ph = el('div', 'iph', TILE.wait);
      const cap = el('div', 'icap'); cap.append(el('b', '', `${n} · ${label || ''}`), el('span', 'ist'));
      t.append(ph, cap);
      grid.append(t);
      [...grid.children].sort((a, b) => a.dataset.n - b.dataset.n).forEach(c => grid.append(c));
    }
    return t;
  }
  function tileState(t, state, text) {
    t.classList.remove('wait', 'run', 'done', 'fail', 'skip'); t.classList.add(state);
    const ph = $('.iph', t); if (ph && state !== 'done') ph.textContent = text || TILE[state] || '';
    const st = $('.ist', t); if (st) st.textContent = state === 'done' ? '' : (TILE[state] || '');
  }
  function tileDone(t, f) {
    tileState(t, 'done');
    const old = $('.iph', t); if (old) old.remove();
    const cap = $('.icap', t);
    const ph = el('div', 'iph'); t.insertBefore(ph, cap);
    call('file_preview_full', f.name).then(r => {
      if (r && r.ok && r.result.type === 'image') { const i = el('img', 'pimg'); i.src = r.result.src; i.alt = f.label || ''; i.title = 'Открыть в панели просмотра'; i.onclick = () => pnOpen({ title: f.name, name: f.name }); ph.replaceChildren(i); scrollChat(); }
      else ph.textContent = f.name;
    });
    if (!$('.iact', t)) {
      const act = el('div', 'iact');
      const b1 = el('button', 'btn', 'Открыть'); b1.onclick = () => call('file_open', f.name);
      const b2 = el('button', 'btn', 'В папке'); b2.onclick = () => call('file_reveal', f.name);
      act.append(b1, b2); t.append(act);
    }
  }
  function imgPlan(row, items) { const g = variantGrid(row); (items || []).forEach(i => variantTile(g, i.n, i.label)); scrollChat(); }
  function imgEvent(row, ev) {
    const t = variantTile(variantGrid(row), ev.n, ev.label);
    if (ev.status === 'start') { tileState(t, 'run', 'загружаю модель…'); const st = $('.ist', t); if (st) st.textContent = ''; }
    else if (ev.status === 'progress') {
      tileState(t, 'run', `рисую… шаг ${ev.step} из ${ev.steps}`);
      const st = $('.ist', t); if (st) st.textContent = ev.eta ? `≈ ${ev.eta >= 60 ? Math.round(ev.eta / 60) + ' мин' : ev.eta + ' с'}` : '';
    }
    else if (ev.status === 'fail') { tileState(t, 'fail', ev.error || ''); t.title = ev.error || ''; }
    else if (ev.status === 'done' && ev.file) tileDone(t, ev.file);
  }
  function finishGrid(row) { $$('.itile.wait, .itile.run', row).forEach(t => tileState(t, 'skip')); }
  function attachVariants(row, files) {
    const g = variantGrid(row);
    for (const f of files) tileDone(variantTile(g, f.variant, f.label), f);
  }

  function attachFiles(row, files) {
    if (!files || !files.length || row.querySelector('.files')) return;
    if (files.some(f => f.variant)) { attachVariants(row, files); return; }
    const box = el('div', 'files');
    for (const f of files) {
      const card = el('div', 'fcard'), k = KIND[f.kind] || KIND.text, msg = el('div', 'fmsg');
      const line = el('div', 'fline'), t = el('div', 'ft');
      t.append(el('b', '', f.name), el('span', '', `${k[1]} · ${fmtSize(f.size)}` + (f.warning ? ' · ⚠ ' + f.warning : '')));
      const act = (name, label, primary) => { const b = el('button', 'btn' + (primary ? ' primary' : ''), label); b.onclick = async () => { const r = await call(name, f.name); msg.textContent = r && !r.ok ? r.error : ''; }; return b; };
      line.append(el('span', 'ficon', k[0]), t);
      if (PREVIEWABLE.test(f.name)) line.append(viewButton({ title: f.name, name: f.name }, msg));
      line.append(act('file_open', 'Открыть', true), act('file_reveal', 'В папке'));
      card.append(line, msg);
      if (f.kind === 'image' || f.kind === 'svg') {          // картинки показываем сразу
        call('file_preview_full', f.name).then(r => { if (r && r.ok) { const pv = previewBox(r.result); pv.style.cursor = 'zoom-in'; pv.title = 'Открыть в панели просмотра'; pv.onclick = () => pnOpen({ title: f.name, name: f.name }); card.insertBefore(pv, line); } });
      }
      box.append(card);
    }
    row.querySelector('.bubble').after(box);
  }
  function setWho(row, brain) { const w = row.querySelector('.who'); if (w) w.textContent = brain ? 'Ответила: ' + brain : ''; }
  // Проект Кодера: кнопка папки + список файлов с предпросмотром (HTML-шаблон или картинку показываем сразу)
  function attachProject(row, pr, autoPreview) {
    if (!pr || !pr.name || row.querySelector('.pcard')) return;
    const box = el('div', 'pcard'); const top = el('div', 'ptop');
    const b = el('button', 'btn primary', '📁 Открыть папку проекта'); const msg = el('span', 'msg err');
    b.onclick = async () => { const r = await call('coder_open', pr.name); msg.textContent = r && !r.ok ? r.error : ''; };
    top.append(b, msg); box.append(top);
    let first = null;
    for (const f of (pr.files || [])) {
      const item = el('div', 'pfile'), line = el('div', 'fline'), t = el('div', 'ft'), m2 = el('div', 'fmsg');
      t.append(el('b', '', f.path), el('span', '', fmtSize(f.size)));
      line.append(el('span', 'ficon', /\.(html?|svg|png|jpe?g|webp|gif)$/i.test(f.path) ? '🎨' : '📄'), t);
      if (PREVIEWABLE.test(f.path)) {
        const it = { title: f.path, project: pr.name, path: f.path }; line.append(viewButton(it, m2));
        if (!first && /\.(html?|svg|png|jpe?g|webp|gif)$/i.test(f.path)) first = it;
      }
      const o = el('button', 'btn', 'Открыть'); o.onclick = async () => { const r = await call('project_open_file', pr.name, f.path); m2.textContent = r && !r.ok ? r.error : ''; };
      line.append(o); item.append(line, m2); box.append(item);
    }
    row.querySelector('.bubble').after(box);
    if (autoPreview && first) pnOpen(first);                  // готовый эскиз сразу показываем справа
  }

  // ---------- лента действий Кодера: что сказала → что записала / запустила → что получилось ----------
  let actNodes = [], actBox = null;
  const ACT_ICON = { write: '✏', read: '📖', find: '🔎', run: '▶', warn: '⚠', start: '📂' };
  function actNode(e) {
    if (e.k === 'think') {
      const d = el('div', 'a-think' + (e.live ? '' : ' done')); const h = el('div', 'th-h'); h.append(el('span', 'spin'), el('span', '', e.text || 'Думаю…')); d.append(h);
      if (e.say) d.append(el('div', 'th-say', e.say)); if (e.doing) d.append(el('div', 'th-do', e.doing));
      return d;
    }
    if (e.k === 'say') { const d = el('div', 'a-say'); renderMd(d, e.text || ''); return d; }
    if (e.k === 'read' || e.k === 'warn' || e.k === 'start') return el('div', 'a-line' + (e.k === 'warn' ? ' warn' : ''), ACT_ICON[e.k] + ' ' + (e.text || ''));
    const hasBody = e.k === 'write' ? !!e.preview : !!e.output;
    const d = el('details', 'a-tool ' + (e.ok === true ? 'ok' : e.ok === false ? 'bad' : ''));
    if (e.ok === false) d.open = true;
    const sm = el('summary'); sm.append(el('span', 'a-ico', ACT_ICON[e.k] || '•'));
    if (e.k === 'run') {
      sm.append(el('code', 'a-cmd', e.cmd || e.text));
      sm.append(el('span', 'a-state', e.ok === null || e.ok === undefined ? '⏳ выполняется…' : e.ok ? '✔ прошла' : '✖ не прошла' + (e.code != null ? ' (код ' + e.code + ')' : '')));
    } else if (e.k === 'write') { sm.append(el('span', '', 'Записала '), el('code', 'a-cmd', e.path || ''), el('span', 'a-state', (e.lines ? e.lines + ' стр.' : ''))); }
    else sm.append(el('span', '', e.text || ''));
    d.append(sm);
    if (hasBody) { const pre = el('pre', 'a-out'); pre.append(el('code', '', e.k === 'write' ? e.preview : e.output)); d.append(pre); }
    return d;
  }
  function actEnsure(row) {
    if (actBox && actBox.isConnected) return actBox;
    const d = el('details', 'work'); d.open = true;
    d.append(el('summary', '', '🛠 Ход работы'));
    d.append(el('div', 'work-body'));
    row.insertBefore(d, row.querySelector('.bubble')); actBox = d; return d;
  }
  function actCount(d, n) { d.querySelector('summary').textContent = '🛠 Ход работы · действий: ' + n; }
  function actEvent(ev) {
    if (ev.chat !== chCur || !chBubble) return;
    const row = chBubble.parentElement, d = actEnsure(row), body = d.querySelector('.work-body');
    const n = actNode(ev.entry);
    if (ev.op === 'add') { actNodes[ev.index] = n; body.append(n); }
    else if (actNodes[ev.index]) { actNodes[ev.index].replaceWith(n); actNodes[ev.index] = n; }
    actCount(d, actNodes.filter(Boolean).length); scrollChat();
  }
  function renderActivity(row, list, collapse) {
    if (!list || !list.length || row.querySelector('.work')) return;
    const d = el('details', 'work'); d.append(el('summary', '', '🛠 Ход работы · действий: ' + list.length));
    const body = el('div', 'work-body'); list.forEach(e => body.append(actNode(e))); d.append(body);
    d.open = !collapse; row.insertBefore(d, row.querySelector('.bubble'));
  }

  // «Сколько вариантов?»: кнопки-ответы под вопросом Карины; нажатие отправляет число как обычное сообщение
  function attachChoices(row, choices) {
    if (!choices || !choices.length || row.querySelector('.choices')) return;
    const box = el('div', 'choices');
    for (const c of choices) {
      const b = el('button', 'btn' + (c === '4' ? '' : ''), c); b.type = 'button';
      b.onclick = () => { if (chBusy) return; box.remove(); $('#chText').value = c; $('#chForm').requestSubmit(); };
      box.append(b);
    }
    row.querySelector('.bubble').after(box);
  }
  function addMsg(role, text, id, steps, files, brain, filesIn, project, activity, choices) {
    const m = $('#chMsgs'); const row = el('div', 'cmsg ' + role); if (id) row.dataset.id = id;
    const b = el('div', 'bubble');
    if (role === 'assistant') renderMd(b, text); else { b.textContent = text; if (filesIn && filesIn.length) b.append(el('div', 'attached', '📎 ' + filesIn.join(', '))); }
    row.append(b);
    if (role === 'assistant') {
      const meta = el('div', 'meta');
      const c = el('button', 'link copy', 'Копировать'); c.onclick = async () => flashBtn(c, await copyText(b.innerText));
      meta.append(c, el('span', 'who')); row.append(meta);
      renderActivity(row, activity, true); attachProject(row, project); attachFiles(row, files); attachSteps(row, steps); setWho(row, brain); attachChoices(row, choices);
    }
    m.append(row); scrollChat(); return b;
  }
  function showWelcome() {
    const m = $('#chMsgs'); m.textContent = '';
    const w = el('div', 'empty');
    w.append(el('b', '', 'Что будем делать?'));
    for (const t of ['Перетащите сюда файлы, папку или zip-архив с проектом и напишите, что сделать, — я прочитаю всё, напишу или перепишу код, проверю запуском и отдам готовые файлы. Ваши оригиналы не меняются.',
                     'Опишите программу или оформление (страница, макет, шаблон) — сделаю и покажу прямо здесь, в окне.',
                     'Попросите нарисовать: «нарисуй аниме-девушку с белыми волосами» — я предложу 4 варианта в стилях разных моделей (NoobAI, Pony, FLUX.2 Klein), а потом поправлю по вашей просьбе: «поверни её», «на втором варианте сделай волосы длиннее».',
                     'Переключатель «💻 Код» над полем ввода включён по умолчанию; выключите его, если нужен только разговор.'])
      w.append(el('p', '', t));
    m.append(w);
  }
  async function loadChatList() {
    const r = await call('chat_list'); if (!r) return r;
    const box = $('#chList'); box.textContent = '';
    for (const c of r.chats) {
      const it = el('div', 'citem' + (c.id === chCur ? ' on' : '')); it.append(el('span', 't', c.title || 'Новый чат'));
      const x = el('button', 'x', '×'); x.title = 'Удалить'; x.onclick = async e => { e.stopPropagation(); if (confirm('Удалить этот чат?')) { await call('chat_delete', c.id); if (chCur === c.id) { chCur = null; showWelcome(); } loadChatList(); } };
      it.append(x); it.onclick = () => openChat(c.id); box.append(it);
    }
    return r;
  }
  async function openChat(id) {
    const c = await call('chat_get', id); if (!c) return;
    chCur = id; chBubble = null; $('#chMsgs').textContent = '';
    if (!c.messages.length) showWelcome();
    c.messages.forEach((m, i) => addMsg(m.role, m.text, m.id, m.steps, m.files, m.brain, m.files_in, m.project, m.activity, i === c.messages.length - 1 ? (m.choices || null) : null));
    loadChatList(); scrollChat();
  }
  async function openChatTab() {
    const r = await loadChatList();
    const id = chOpenId || chCur || (r && r.chats[0] && r.chats[0].id); chOpenId = null;
    if (id) openChat(id); else showWelcome();
    if (r) setBusy(!!r.busy);
    loadCoderState();
    setTimeout(() => $('#chText').focus(), 50);
  }
  function chatStart(ev) {
    if (!chCur || ev.chat !== chCur) { chCur = ev.chat; $('#chMsgs').textContent = ''; }
    const w = $('#chMsgs .empty'); if (w) w.remove();
    actNodes = []; actBox = null;
    addMsg('user', ev.user.text, ev.user.id, null, null, null, ev.user.files_in); chBubble = addMsg('assistant', '', ev.reply_id); chBubble.classList.add('typing');
    setBusy(true); setStep('Думаю…'); loadChatList();
  }
  function chatDone(ev) {
    if (ev.chat === chCur && chBubble) { renderMd(chBubble, ev.text); chBubble.classList.remove('typing'); if (actBox && actNodes.length > 10) actBox.open = false; actBox = null; attachProject(chBubble.parentElement, ev.project, true); finishGrid(chBubble.parentElement); attachFiles(chBubble.parentElement, ev.files); attachSteps(chBubble.parentElement, ev.steps); setWho(chBubble.parentElement, ev.brain); attachChoices(chBubble.parentElement, ev.choices); if (ev.error) chBubble.classList.add('err'); chBubble = null; }
    if (ev.chat === chCur) setBusy(false);
    loadChatList(); scrollChat();
  }
  $('#chNew').onclick = async () => { const c = await call('chat_new'); if (c) { pnClose(); chCur = c.id; showWelcome(); loadChatList(); $('#chText').focus(); } };
  $('#chForm').addEventListener('submit', async e => {
    e.preventDefault(); const t = $('#chText'); const text = t.value.trim(); if (!text || chBusy || chUp) return;
    t.value = ''; t.style.height = ''; t.style.overflowY = 'hidden';
    const wm = webMode(); const paths = chPaths.slice();
    const r = await call('chat_send', chCur || '', text, wm === 'always' ? true : wm === 'off' ? false : null, paths, chatMode());
    if (r && !r.ok) { t.value = text; setStep(r.error); } else if (r) { chPaths = []; chMeta = {}; renderAttach(); }
  });
  $('#chText').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chForm').requestSubmit(); } });
  $('#chText').addEventListener('input', e => { const t = e.target; t.style.height = 'auto'; t.style.height = Math.min(220, t.scrollHeight) + 'px'; t.style.overflowY = t.scrollHeight > 220 ? 'auto' : 'hidden'; });
  $('#chStop').onclick = () => call('chat_stop');

  // приложенные файлы, переключатель «Код» и разрешение запуска
  let chPaths = [], chMeta = {}, chCodeOn = true, chUp = 0;
  try { if (localStorage.getItem('karina.code') === '0') chCodeOn = false; } catch (_) {}
  const baseName = p => p.split(/[\\/]/).pop();
  const chatMode = () => chCodeOn ? 'auto' : 'ask';
  function renderAttach() {
    const box = $('#chAttach'); box.textContent = ''; box.hidden = !chPaths.length && !chUp;
    chPaths.forEach((p, i) => {
      const meta = chMeta[p] || {};
      const c = el('div', 'chip'); c.title = p; const x = el('button', '', '×'); x.type = 'button'; x.title = 'Убрать';
      x.onclick = () => { if (meta.temp) call('upload_discard', p); chPaths.splice(i, 1); delete chMeta[p]; renderAttach(); };
      c.append(el('span', '', (meta.icon || '📎') + ' ' + (meta.label || baseName(p))), x); box.append(c);
    });
    if (chUp) { const c = el('div', 'chip busy'); c.append(el('span', '', '⏳ Загружаю: ' + chUpText)); box.append(c); }
  }
  let chUpText = '';
  async function pickAttach(folder) {
    const r = await call('coder_pick', !!folder);
    for (const p of (r || [])) if (!chPaths.includes(p)) { chPaths.push(p); chMeta[p] = { icon: folder ? '📂' : (/\.(zip|tar|tgz|gz)$/i.test(p) ? '📦' : '📎') }; }
    renderAttach(); $('#chText').focus();
  }
  $('#chPick').onclick = () => pickAttach(false);
  $('#chPickDir').onclick = () => pickAttach(true);

  // --- перетаскивание и вставка файлов: окно читает файл и передаёт байты кусками (путь на диске из браузера не узнать) ---
  const CHUNK = 700 * 1024, MAX_ONE = 80 * 1024 * 1024;
  const b64 = buf => { let s = '', a = new Uint8Array(buf); for (let i = 0; i < a.length; i += 0x8000) s += String.fromCharCode.apply(null, a.subarray(i, i + 0x8000)); return btoa(s); };
  async function entryFiles(entry, base) {
    if (entry.isFile) return [await new Promise((res, rej) => entry.file(f => res({ file: f, rel: base + f.name }), rej))];
    const out = [], rd = entry.createReader();
    for (;;) {
      const part = await new Promise((res, rej) => rd.readEntries(res, rej)); if (!part.length) break;
      for (const e of part) out.push(...await entryFiles(e, base + entry.name + '/'));
    }
    return out;
  }
  async function collectDrop(dt) {
    const items = [...(dt.items || [])].filter(i => i.kind === 'file');
    const entries = items.map(i => ({ entry: i.webkitGetAsEntry ? i.webkitGetAsEntry() : null, file: i.getAsFile() }));   // синхронно: после await данные пропадут
    const out = [];
    if (!entries.length) for (const f of (dt.files || [])) out.push({ file: f, rel: f.name, top: f.name });
    for (const { entry, file } of entries) {
      try {
        if (entry && entry.isDirectory) { const list = await entryFiles(entry, ''); list.forEach(x => x.top = entry.name); out.push(...list); }
        else if (file) out.push({ file, rel: file.name, top: file.name });
      } catch (_) { if (file) out.push({ file, rel: file.name, top: file.name }); }
    }
    return out;
  }
  async function uploadList(list) {
    if (!list.length) return;
    const batch = Math.random().toString(36).slice(2, 12), tops = new Map(); let done = 0, errs = [];
    chUp++; chUpText = list.length + ' файл.'; renderAttach();
    for (const { file, rel, top } of list) {
      chUpText = `${++done} из ${list.length} · ${file.name}`; renderAttach();
      if (file.size > MAX_ONE) { errs.push(`«${file.name}» больше ${MAX_ONE / 1048576} МБ`); continue; }
      const b = await call('upload_begin', rel, file.size, batch);
      if (!b || !b.ok) { if (b && !b.skipped) errs.push(b.error); continue; }
      let ok = true;
      for (let off = 0; off < file.size || off === 0; off += CHUNK) {
        const buf = await file.slice(off, off + CHUNK).arrayBuffer();
        const r = buf.byteLength ? await call('upload_chunk', b.token, b64(buf)) : { ok: true };
        if (!r || !r.ok) { ok = false; errs.push(r ? r.error : 'сбой передачи'); break; }
        if (file.size === 0) break;
      }
      const e = ok ? await call('upload_end', b.token) : null;
      if (ok && e && e.ok) { const t = tops.get(b.top) || { n: 0, dir: top !== rel || rel.includes('/') }; t.n++; tops.set(b.top, t); }
    }
    chUp--;
    for (const [p, t] of tops) if (!chPaths.includes(p)) {
      chPaths.push(p);
      const isArc = /\.(zip|tar|tgz|gz|bz2|xz)$/i.test(p);
      chMeta[p] = { temp: true, icon: t.dir ? '📂' : isArc ? '📦' : '📎', label: baseName(p) + (t.dir ? ` (${t.n} файл.)` : '') };
    }
    renderAttach();
    if (errs.length) setStep('⚠ ' + errs.slice(0, 2).join('; '), 8000);
    $('#chText').focus();
  }
  const hasFiles = e => e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files');
  let dragDepth = 0;
  // браузер по умолчанию «открывает» брошенный файл вместо страницы — запрещаем это во всём окне
  window.addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener('drop', e => { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener('dragenter', e => { if (hasFiles(e)) { dragDepth++; $('#chDrop').hidden = false; const b = $('#tabs [data-tab="chat"]'); if (b && !$('#tab-chat').classList.contains('on')) b.click(); } });
  document.addEventListener('dragleave', e => { if (hasFiles(e) && --dragDepth <= 0) { dragDepth = 0; $('#chDrop').hidden = true; } });
  document.addEventListener('drop', async e => {
    if (!hasFiles(e)) return; e.preventDefault(); dragDepth = 0; $('#chDrop').hidden = true;
    uploadList(await collectDrop(e.dataTransfer));
  });
  $('#chText').addEventListener('paste', e => {
    const fl = [...((e.clipboardData && e.clipboardData.files) || [])]; if (!fl.length) return;
    e.preventDefault(); uploadList(fl.map(f => ({ file: f, rel: f.name, top: f.name })));
  });

  function showCode() { const b = $('#chCode'); b.classList.toggle('on', chCodeOn); b.setAttribute('aria-checked', chCodeOn ? 'true' : 'false'); }
  $('#chCode').onclick = () => { chCodeOn = !chCodeOn; showCode(); try { localStorage.setItem('karina.code', chCodeOn ? '1' : '0'); } catch (_) {} };
  function showRun(on) { const b = $('#chRun'); b.classList.toggle('on', !!on); b.setAttribute('aria-checked', on ? 'true' : 'false'); b.dataset.on = on ? '1' : ''; }
  $('#chRun').onclick = async () => {
    const turnOn = !$('#chRun').dataset.on;
    if (turnOn && !confirm('Разрешить Карине запускать написанный ею код для проверки?\n\nЭто не песочница: программа выполняется с вашими правами. Разрешены только python, pytest и node, без сети, подпроцессов и удаления файлов (Карина проверяет код перед запуском). Нужен установленный Python или Node.js.')) return;
    const s = await call('coder_set', turnOn); if (s) showRun(s.run);
  };
  async function loadCoderState() { const s = await call('coder_state'); if (s) showRun(s.settings.run); }
  showCode(); showRun(true);
  $('#chFolder').onclick = async () => { const r = await call('files_open_folder'); if (r && !r.ok) setStep(r.error, 5000); };
  const WEB_LABEL = { auto: '🌐 Поиск: авто', always: '🌐 Поиск: вкл', off: '🌐 Поиск: выкл' };
  const webMode = () => ((cfg && cfg.search) || {}).mode || 'auto';
  function showWeb() { $('#chWeb').textContent = WEB_LABEL[webMode()] || WEB_LABEL.auto; }
  $('#chWeb').onclick = async () => {
    const order = ['auto', 'always', 'off']; const next = order[(order.indexOf(webMode()) + 1) % 3];
    const c = await call('save_settings', { search: { mode: next } }); if (c) { cfg = c; showWeb(); }
  };
  $('#btnSearchTest').onclick = async () => {
    const box = $('#searchTest'); box.hidden = false; box.textContent = 'Проверяю…'; call('search_test');
  };
  document.addEventListener('click', e => { const a = e.target.closest('a[data-href]'); if (a) { e.preventDefault(); call('open_url', a.dataset.href); } });

  // ---------- картинки ----------
  function renderImg(st) {
    if (!st) return;
    $('#imgProvider').value = st.provider; $('#imgUrl').value = st.url || ''; $('#imgModel').value = st.model || ''; $('#imgKey').value = '';
    $('#imgKeyClear').hidden = !st.has_key;
    $('#imgDirector').checked = st.director !== false; $('#imgAsk').checked = st.ask_count !== false; $('#imgVariants').value = String(st.variants || 4);
    renderPower(st);
    renderLimg(st);
  }
  // «Мощность рисования»: что за видеокарта, что выбрала «Авто», ручные настройки
  function renderPower(st) {
    const pw = st.power || {}, hw = st.hw || {};
    $('#imgPower').value = pw.power || 'auto'; $('#imgQuality').value = pw.quality || 'auto';
    const vg = String(pw.vram_gb || 0); $('#imgVramGb').value = [...$('#imgVramGb').options].some(o => o.value === vg) ? vg : '0';
    $('#imgThreads').value = pw.threads || 0; $('#imgUnload').value = pw.unload_llm || 'auto';
    const box = $('#imgHw'); box.hidden = false;
    let t;
    if (hw.detected && hw.vram_gb) t = `Видеокарта: ${hw.gpu || 'не определена'} · ${hw.vram_gb} ГБ видеопамяти${hw.source === 'manual' ? ' (указано вами)' : ''}`;
    else if (hw.detected && hw.integrated) t = `Видеокарта: ${hw.gpu || 'встроенная'} — встроенная, своей видеопамяти почти нет`;
    else t = 'Видеокарту и объём видеопамяти определить не удалось — укажите видеопамять вручную в «Мощности рисования» ниже';
    t += ` · оперативной памяти ${hw.ram_gb || '?'} ГБ · ядер процессора ${hw.cpu || '?'}.`;
    const rec = (st.local || []).filter(m => m.recommended).map(m => m.title);
    if (rec.length) t += ` Для вашего компьютера рекомендуются: ${rec.join(', ')}.`;
    box.textContent = t;
    const now = $('#imgPowerNow'); now.hidden = false;
    now.textContent = (pw.auto ? 'Сейчас «Авто» выбрала: ' : 'Сейчас выбрано: ') + (pw.now || '—') + (pw.unload_now ? ' · текстовая модель на время рисования выгружается из видеопамяти' : '');
  }
  function renderLimg(st) {
    const box = $('#limgList'); if (!box) return; box.textContent = '';
    (st.local || []).forEach(m => {
      const it = el('div', 'item model ' + (m.installed && m.selected ? 'ok' : '')); it.dataset.lid = m.id;
      const t = el('div', 't'); t.append(el('b', '', `${m.title} · ${m.size_gb} ГБ`), el('span', 'wrap', m.desc), el('span', 'wrap', `На вашем компьютере: ${m.fit_label} — ${m.eta}. Рисует так: ${m.plan}.`));
      const dl = st.dl && st.dl.id === m.id;
      if (dl) { const pb = el('div', 'pbar'); pb.append(el('i')); t.append(pb, el('span', 'plab', 'Подготовка…')); }
      it.append(t);
      if (m.recommended) it.append(el('span', 'tag', 'Рекомендуется'));
      if (m.fit === 'slow' || m.fit === 'heavy') it.append(el('span', 'tag warn', 'Медленно'));
      if (m.installed && m.selected) it.append(el('span', 'tag', 'Выбрана'));
      if (dl) { const b = el('button', 'btn', 'Отмена'); b.onclick = () => call('localimg_cancel'); it.append(b); }
      else if (m.installed) {
        if (!m.selected) { const b = el('button', 'btn primary', 'Выбрать'); b.onclick = async () => renderImg(await call('localimg_select', m.id)); it.append(b); }
        const d = el('button', 'btn danger', 'Удалить'); d.onclick = async () => { if (confirm('Удалить модель с диска?')) { await call('localimg_delete', m.id); loadImg(); } }; it.append(d);
      } else {
        const b = el('button', 'btn primary', 'Скачать'); b.onclick = async () => { b.disabled = true; await call('localimg_download', m.id); loadImg(); }; it.append(b);
      }
      box.append(it);
    });
  }
  function limgProgress(ev) {
    const row = $(`[data-lid="${ev.id}"]`); if (!row) return;
    const bar = $('.pbar i', row), lab = $('.plab', row); if (!bar || !lab) return;
    if (ev.stage === 'search') { lab.textContent = 'Ищу файлы…'; return; }
    const pct = ev.total ? Math.min(100, ev.done / ev.total * 100) : 0;
    bar.style.width = pct.toFixed(1) + '%';
    lab.textContent = (ev.stage === 'engine' ? 'Движок: ' : 'Модель: ') + (ev.total ? `${fmtGb(ev.done)} из ${fmtGb(ev.total)} · ${pct.toFixed(0)}%` : fmtGb(ev.done));
  }
  async function loadImg() { renderImg(await call('images_state')); }
  $('#imgSave').onclick = async () => { const st = await call('images_set', $('#imgProvider').value, $('#imgUrl').value, $('#imgModel').value, $('#imgKey').value, $('#imgDirector').checked, +$('#imgVariants').value, $('#imgAsk').checked, $('#imgPower').value, $('#imgQuality').value, +$('#imgVramGb').value, +$('#imgThreads').value || 0, $('#imgUnload').value); renderImg(st); flash($('#imgMsg'), 'Сохранено'); };
  $('#imgHwRefresh').onclick = async () => { $('#imgHwRefresh').disabled = true; renderImg(await call('images_hw_refresh')); $('#imgHwRefresh').disabled = false; flash($('#imgMsg'), 'Видеокарта определена заново'); };
  ['#imgPower', '#imgQuality', '#imgVramGb', '#imgThreads', '#imgUnload'].forEach(id => { $(id).onchange = () => $('#imgSave').onclick(); });   // мощность применяется сразу
  $('#imgKeyClear').onclick = async () => { renderImg(await call('images_clear_key')); flash($('#imgMsg'), 'Ключ удалён'); };
  $('#imgTest').onclick = async () => { await $('#imgSave').onclick(); $('#imgTest').disabled = true; $('#imgMsg').classList.remove('err'); $('#imgMsg').textContent = 'Рисую пробную картинку…'; call('images_test'); };

  // ---------- обновления ----------
  function showUpdate(u) {
    upd = u; const bar = $('#updBar');
    if (!u || !u.available) { bar.hidden = true; return; }
    bar.hidden = false; $('#updNotes').classList.remove('err'); $('#updPbar').hidden = true; $('#updInstall').disabled = false;
    $('#updTitle').textContent = `Доступна новая версия ${u.latest} (у вас ${u.current})`;
    const notes = (u.notes || '').replace(/[#*`]/g, '').split('\n').map(x => x.trim()).filter(Boolean).slice(0, 4).join(' · ');
    $('#updNotes').textContent = notes || 'Список изменений — на странице релиза.';
    $('#updInstall').textContent = u.can_install ? 'Скачать и установить' : 'Открыть страницу загрузки';
    $('#updLabel').textContent = 'Обновить ●';
  }
  async function checkUpdate(manual) {
    const lab = $('#updLabel'); if (manual) lab.textContent = 'Проверяю…';
    const u = await call('check_update', manual);
    lab.textContent = 'Обновить';
    if (!u) return;
    if (u.error) { if (manual) { flash($('#updMsg'), u.error, true); alert(u.error); } return; }
    if (u.available) showUpdate(u);
    else if (manual) { showUpdate(null); flash($('#updMsg'), `Установлена последняя версия (${u.current})`); alert(`У вас последняя версия: ${u.current}`); }
  }
  $('#btnUpdate').onclick = () => checkUpdate(true);
  $('#btnUpdate2').onclick = () => checkUpdate(true);
  $('#updLater').onclick = () => { $('#updBar').hidden = true; };
  $('#updInstall').onclick = async () => {
    $('#updInstall').disabled = true; $('#updNotes').classList.remove('err'); $('#updNotes').textContent = 'Подготовка…';
    const r = await call('install_update');
    if (r && r.opened) $('#updInstall').disabled = false;
    else if (!r || !r.ok) { $('#updInstall').disabled = false; $('#updNotes').textContent = (r && r.error) || 'Не удалось'; }
  };

  // ---------- бесплатные облачные модели ----------
  const CLOUD_STATE_LABEL = { ok: 'Готов', cooldown: 'Лимит', error: 'Ошибка', nokey: 'Нет ключа', off: 'Выключен' };
  let cloudLast = null;
  function cloudRow(p, idx, total) {
    const row = el('div', 'cloud-row ' + p.state); row.dataset.pid = p.id;
    const head = el('div', 'cloud-head');
    const title = el('div', 'cloud-title'); title.append(el('b', '', p.name), el('span', 'tag' + (p.state === 'ok' ? '' : ' warn'), CLOUD_STATE_LABEL[p.state] || p.state));
    const sw = el('label', 'switch'), cb = el('input'); cb.type = 'checkbox'; cb.checked = p.enabled; cb.disabled = !(p.has_key || p.nokey); cb.title = (p.has_key || p.nokey) ? '' : 'Сначала введите ключ';
    sw.append(cb, el('span', 'sw'), el('span', '', 'Использовать'));
    cb.onchange = async () => { renderCloud(await call('cloud_save', p.id, { enabled: cb.checked })); loadBrain(); };
    const mv = el('div', 'cloud-move');
    const up = el('button', 'btn', '↑'), dn = el('button', 'btn', '↓'); up.type = dn.type = 'button'; up.disabled = idx === 0; dn.disabled = idx === total - 1;
    up.title = 'Выше в очереди'; dn.title = 'Ниже в очереди';
    up.onclick = async () => renderCloud(await call('cloud_move', p.id, -1)); dn.onclick = async () => renderCloud(await call('cloud_move', p.id, 1));
    mv.append(up, dn);
    head.append(title, sw, mv);
    const body = el('div', 'cloud-body');
    body.append(el('div', 'muted small', p.note));
    const stat = el('div', 'small cloud-stat', p.text + (p.used_today ? ` · ответов сегодня: ${p.used_today}` : '')); body.append(stat);
    const f = el('div', 'form');
    let nameI, urlI;
    if (p.id === 'custom') {
      nameI = el('input'); nameI.placeholder = 'Название (любое)'; nameI.value = p.name === 'Свой сервер' ? '' : p.name;
      urlI = el('input'); urlI.placeholder = 'Адрес API, например https://api.example.com/v1'; urlI.value = p.url || '';
      f.append(nameI, urlI);
    }
    const keyI = el('input'); keyI.type = 'password'; keyI.autocomplete = 'off'; keyI.placeholder = p.has_key ? 'ключ сохранён (введите новый, чтобы заменить)' : p.nokey ? 'Ключ не нужен (если есть — можно вставить, лимиты выше)' : 'Вставьте ключ API';
    const modI = el('input'); modI.placeholder = p.model_auto ? `Модель: ${p.model_auto} (подобрана сама)` : 'Модель (можно оставить пустой — подберётся сама)'; modI.value = p.model || '';
    const dl = el('datalist'); dl.id = 'cm-' + p.id; modI.setAttribute('list', dl.id);
    f.append(keyI, modI, dl);
    const acts = el('div', 'form-actions');
    const save = el('button', 'btn primary', 'Сохранить'), test = el('button', 'btn', 'Проверить'), lst = el('button', 'btn', 'Список моделей');
    save.type = test.type = lst.type = 'button';
    acts.append(save, test, lst);
    if (p.signup) { const a = el('button', 'btn', 'Получить бесплатный ключ ↗'); a.type = 'button'; a.onclick = () => call('open_url', p.signup); acts.append(a); }
    if (p.has_key) { const d = el('button', 'btn danger', 'Удалить ключ'); d.type = 'button'; d.onclick = async () => { renderCloud(await call('cloud_clear_key', p.id)); loadBrain(); }; acts.append(d); }
    const msg = el('span', 'msg'); acts.append(msg);
    const out = el('div', 'note', ''); out.hidden = true;
    const data = () => { const o = { key: keyI.value.trim(), model: modI.value.trim() }; if (nameI) { o.name = nameI.value.trim(); o.url = urlI.value.trim(); } return o; };
    save.onclick = async () => {
      const d = data(); const hadKey = p.has_key;
      const st = await call('cloud_save', p.id, Object.assign({}, d, (!hadKey && d.key) ? { enabled: true } : {}));    // первый ключ сразу включает провайдера
      if (st) { renderCloud(st); loadBrain(); }
    };
    test.onclick = async () => { test.disabled = true; out.hidden = false; out.className = 'note'; out.textContent = 'Проверяю…'; const d = data(); if (d.key || d.model || d.url) await call('cloud_save', p.id, d); await call('cloud_test', p.id); };
    lst.onclick = async () => {
      lst.disabled = true; const d = data(); if (d.key || d.url) await call('cloud_save', p.id, d);
      const r = await call('cloud_models', p.id); lst.disabled = false;
      if (r && r.ok) { dl.textContent = ''; r.models.forEach(m => { const o = el('option'); o.value = m; dl.append(o); }); flash(msg, `Моделей: ${r.models.length}. Нажмите на поле «Модель», чтобы выбрать.`); modI.focus(); }
      else flash(msg, (r && r.error) || 'Не удалось получить список', true);
    };
    body.append(f, acts, out);
    row.append(head, body);
    return row;
  }
  function renderCloud(st) {
    if (!st) return; cloudLast = st;
    $('#cloudStrategy').value = st.strategy;
    const box = $('#cloudList'); box.textContent = '';
    st.providers.forEach((p, i) => box.append(cloudRow(p, i, st.providers.length)));
  }
  async function loadCloud() { renderCloud(await call('cloud_state')); }
  $('#cloudStrategy').onchange = async e => renderCloud(await call('cloud_strategy', e.target.value));
  // фоновое обновление статусов («лимит исчерпан, вернусь через…»), пока не печатают в полях
  setInterval(() => {
    const act = $('#tab-models.on'); if (!act || !api || $('#cloudList').contains(document.activeElement)) return;
    call('cloud_state').then(st => { if (st && JSON.stringify(st) !== JSON.stringify(cloudLast) && !$('#cloudList').contains(document.activeElement)) renderCloud(st); });
  }, 5000);

  // ---------- мозг ----------
  const BRAIN = {
    local: ['Локальная модель', 'Встроенная модель на этом компьютере: бесплатно и без интернета. На слабом компьютере отвечает медленнее.'],
    claude: ['Claude', 'Отвечает Claude по вашему ключу: быстрее и умнее, но нужен интернет, и запросы оплачиваются у Anthropic.'],
    cloud: ['Бесплатные облака', 'Отвечают бесплатные облачные модели (Gemini или свой сервер) по вашим ключам — быстро даже на слабом компьютере. Когда у одной кончается лимит, подхватывает следующая. Нужен интернет.'],
    auto: ['Авто', 'Простые вопросы — локальная модель, сложные (отчёты, код, файлы, глубокий поиск) — Claude или бесплатное облако. Если одного нет, отвечает другой, и в ответе это видно.'],
  };
  const BRAIN_SHORT = { local: 'локальный', claude: 'Claude', cloud: 'облако', auto: 'авто' };
  let brain = null;
  const brainWhy = (b, m) => m === 'local' ? (b.local.ok ? 'готова' : b.local.reason) : m === 'claude' ? (b.claude.ok ? 'готов' : b.claude.reason)
    : m === 'cloud' ? (b.cloud.ok ? `готово (подключено: ${b.cloud.count})` : b.cloud.reason)
    : `локальная: ${b.local.ok ? 'готова' : 'нет'} · Claude: ${b.claude.ok ? 'готов' : 'нет'} · облако: ${b.cloud.ok ? 'готово' : 'нет'}`;
  function renderBrain(b) {
    if (!b) return; brain = b;
    const sig = JSON.stringify(b); if (renderBrain.sig === sig) return; renderBrain.sig = sig;     // не перерисовываем без изменений (иначе закрывается открытый список)
    $('#chBrain').textContent = '🧠 Мозг: ' + BRAIN_SHORT[b.mode];
    $('#kvBrain').textContent = b.mode === 'claude' ? b.claude.label : b.mode === 'cloud' ? 'Бесплатные облака' : b.mode === 'auto' ? 'Авто (локально + облако + Claude)' : 'Локальная модель';
    const box = $('#brainList'); if (!box) return; box.textContent = '';
    for (const m of ['local', 'cloud', 'claude', 'auto']) {
      const ok = m === 'auto' ? (b.local.ok || b.claude.ok || b.cloud.ok) : m === 'local' ? b.local.ok : m === 'cloud' ? b.cloud.ok : b.claude.ok;
      const it = el('div', 'item model ' + (b.mode === m ? 'ok' : ''));
      const t = el('div', 't'); t.append(el('b', '', m === 'claude' ? b.claude.label : BRAIN[m][0]), el('span', 'wrap', BRAIN[m][1]));
      const st = el('span', 'wrap', (ok ? '✔ ' : '✖ ') + brainWhy(b, m)); st.style.color = ok ? 'var(--ok)' : 'var(--warn)'; t.append(st);
      it.append(t);
      if (b.mode === m) it.append(el('span', 'tag', 'Выбран'));
      else { const x = el('button', 'btn primary', 'Выбрать'); x.onclick = async () => { renderBrain(await call('brain_set', m)); }; it.append(x); }
      box.append(it);
    }
    const sel = $('#brainModel'); sel.textContent = '';
    for (const m of b.claude_models) { const o = el('option', '', m.title); o.value = m.id; sel.append(o); }
    sel.value = b.claude_model;
  }
  async function loadBrain() { renderBrain(await call('brain_state')); }
  $('#brainModel').onchange = async e => renderBrain(await call('brain_set', '', e.target.value));
  $('#brainKey').onclick = () => { const b = $('#tabs [data-tab="settings"]'); if (b) b.click(); setTimeout(() => $('#apiKey').focus(), 60); };
  $('#brainTest').onclick = async () => { $('#brainTest').disabled = true; const o = $('#brainTestOut'); o.hidden = false; o.className = 'note'; o.textContent = 'Проверяю…'; await call('brain_test_claude'); };
  $('#chBrain').onclick = async () => {
    if (!brain) await loadBrain(); if (!brain) return;
    const order = ['local', 'cloud', 'claude', 'auto']; const next = order[(order.indexOf(brain.mode) + 1) % 4];
    renderBrain(await call('brain_set', next));
    const ok = next === 'auto' ? (brain.local.ok || brain.claude.ok || brain.cloud.ok) : next === 'local' ? brain.local.ok : next === 'cloud' ? brain.cloud.ok : brain.claude.ok;
    if (!ok) setStep('⚠ ' + brainWhy(brain, next), 6000);
  };

  // ---------- модели ----------
  const fmtGb = b => (b / 2 ** 30).toFixed(1) + ' ГБ';
  const ENG = { stopped: 'остановлен (запустится при первом вопросе)', starting: 'запускается…', ready: 'работает', error: 'ошибка' };
  function renderEngine(e) {
    let t = ENG[e.state] || e.state;
    if (e.state === 'ready') t += e.flavor === 'vulkan' ? ' · на видеокарте' : ' · на процессоре';
    if (e.detail && e.state !== 'ready' && e.state !== 'stopped') t += ' — ' + e.detail;
    const n = $('#mdEngine'); if (n) n.textContent = t;
  }
  function modelProgress(ev) {
    const row = $(`[data-mid="${ev.id}"]`); if (!row) return;
    const bar = $('.pbar i', row), lab = $('.plab', row);
    if (ev.stage === 'verify') { lab.textContent = 'Проверяю целостность…'; bar.style.width = '100%'; return; }
    if (ev.stage === 'search') { lab.textContent = 'Ищу файл…'; return; }
    const pct = ev.total ? Math.min(100, ev.done / ev.total * 100) : 0;
    bar.style.width = pct.toFixed(1) + '%';
    lab.textContent = ev.total ? `${fmtGb(ev.done)} из ${fmtGb(ev.total)} · ${pct.toFixed(0)}%` : fmtGb(ev.done);
  }
  async function loadModels() {
    const s = await call('models_state'); if (!s) return;
    const hw = $('#mdHw'); hw.hidden = !s.ram;
    hw.textContent = `Оперативной памяти: ${s.ram} ГБ. Подходящая модель отмечена «Рекомендуется».`;
    $('#mdGpu').value = s.gpu; renderEngine(s.engine);
    const box = $('#mdList'); box.textContent = '';
    for (const m of s.models) {
      const it = el('div', 'item model ' + (m.active ? 'ok' : m.too_big ? 'miss' : '')); it.dataset.mid = m.id;
      const t = el('div', 't');
      const head = el('b', '', m.title);
      t.append(head, el('span', 'wrap', `${m.tier_label} · ${m.size_gb} ГБ · русский: ${m.ru} · память от ${m.ram} ГБ`), el('span', 'wrap', m.desc));
      if (m.dl) { const pb = el('div', 'pbar'); pb.append(el('i')); t.append(pb, el('span', 'plab', 'Подготовка…')); }
      it.append(t);
      if (m.id === s.recommended) it.append(el('span', 'tag', 'Рекомендуется'));
      if (m.active) it.append(el('span', 'tag', 'Выбрана'));
      if (m.dl) { const b = el('button', 'btn', 'Отмена'); b.onclick = () => call('model_cancel', m.id); it.append(b); }
      else if (m.installed) {
        if (!m.active) { const b = el('button', 'btn primary', 'Выбрать'); b.onclick = async () => { await call('model_select', m.id); loadModels(); }; it.append(b); }
        const d = el('button', 'btn danger', 'Удалить'); d.onclick = async () => { if (confirm('Удалить модель с диска?')) { await call('model_delete', m.id); loadModels(); } }; it.append(d);
      } else {
        const b = el('button', 'btn primary', 'Скачать'); b.onclick = async () => { b.disabled = true; await call('model_download', m.id); loadModels(); }; it.append(b);
      }
      box.append(it);
    }
  }
  $('#mdGpu').onchange = e => call('models_settings', { gpu: e.target.value });
  $('#mdTest').onclick = async () => { $('#mdTest').disabled = true; const o = $('#mdTestOut'); o.hidden = false; o.className = 'note'; o.textContent = 'Думаю… (первый запуск грузит модель в память, это может занять до минуты)'; await call('model_test'); };

  // ---------- программы ----------
  const SRC = { user: 'моя', bogestv0: 'Bogestv0', system: 'Windows' };
  async function loadApps() {
    const apps = await call('list_apps') || [];
    const box = $('#appList'); box.textContent = '';
    for (const a of apps) {
      const it = el('div', 'item ' + (a.found ? 'ok' : 'miss')); const t = el('div', 't');
      t.append(el('b', '', a.name), el('span', '', (a.aliases || []).join(', ') + (a.path ? ' · ' + a.path : '')));
      it.append(t, el('span', 'tag' + (a.found ? '' : ' warn'), a.found ? SRC[a.src] : 'не найдена'));
      const test = el('button', 'btn', 'Запустить'); test.onclick = async () => { test.textContent = '…'; await call('test_app', a.name); test.textContent = 'Запустить'; };
      it.append(test);
      if (a.src === 'user') { const rm = el('button', 'btn danger', 'Удалить'); rm.onclick = async () => { await call('remove_app', a.name); loadApps(); }; it.append(rm); }
      box.append(it);
    }
  }
  $('#btnPick').onclick = async () => { const p = await call('pick_file'); if (p) $('#appPath').value = p; };
  $('#appForm').addEventListener('submit', async e => {
    e.preventDefault(); const f = e.target;
    const r = await call('add_app', f.name.value, f.aliases.value, f.path.value, f.args.value);
    if (r && r.ok) { f.reset(); flash($('#appMsg'), 'Добавлено'); loadApps(); } else flash($('#appMsg'), (r && r.error) || 'Ошибка', true);
  });

  // ---------- офлайн-голос ----------
  const fmtMb = b => Math.round(b / 2 ** 20) + ' МБ';
  function voiceOfflineView(st) {
    $('#offStatus').textContent = st.downloading ? 'Скачиваю…' : st.ready ? `Скачан (${fmtMb(st.size)}). Без интернета Карина говорит им.` : 'Не скачан: без интернета будет говорить системный голос Windows.';
    $('#offDl').hidden = st.ready; $('#offDl').disabled = !!st.downloading; $('#offDel').hidden = !st.ready;
    if (!st.downloading) $('#offBar').hidden = true;
  }
  async function loadVoiceOffline() { const st = await call('voice_offline_status'); if (st) voiceOfflineView(st); }
  function voiceDlProgress(ev) {
    const pb = $('#offBar'); pb.hidden = false; $('i', pb).style.width = (ev.total ? ev.done / ev.total * 100 : 0).toFixed(1) + '%';
    $('#offStatus').textContent = ev.stage === 'verify' ? 'Проверяю файл…' : ev.total ? `Скачано ${fmtMb(ev.done)} из ${fmtMb(ev.total)}` : 'Ищу голос…';
  }
  function voiceDlDone(ev) {
    if (ev.error && ev.error !== 'cancelled') flash($('#setMsg'), ev.error, true);
    loadVoiceOffline();
  }
  $('#offDl').onclick = async () => { $('#offDl').disabled = true; $('#offStatus').textContent = 'Ищу голос…'; if (!(await call('voice_offline_download'))) loadVoiceOffline(); };
  $('#offDel').onclick = async () => { const st = await call('voice_offline_delete'); if (st) voiceOfflineView(st); };

  // ---------- профиль ----------
  let kinds = {};
  const pf = () => $('#profForm');
  function petRow(p) {
    const r = document.createElement('div'); r.className = 'row pet'; r.style.alignItems = 'center';
    const opts = Object.entries(kinds).map(([k, v]) => `<option value="${k}"${k === (p.kind || 'dog') ? ' selected' : ''}>${v}</option>`).join('');
    r.innerHTML = `<select class="pk" style="width:130px;flex:none">${opts}</select><input class="pn" maxlength="30" placeholder="Кличка" style="flex:1;width:auto;min-width:120px"><input class="pd" type="number" min="1" max="31" placeholder="день р." style="width:90px;flex:none"><select class="pm" style="width:120px;flex:none"><option value="0">месяц</option>${['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'].map((m, i) => `<option value="${i + 1}">${m}</option>`).join('')}</select><button type="button" class="btn danger pr">×</button>`;
    $('.pn', r).value = p.name || ''; $('.pd', r).value = p.birth_day || ''; $('.pm', r).value = p.birth_month || 0;
    $('.pr', r).onclick = () => r.remove();
    return r;
  }
  function fillProfile(d) {
    kinds = d.kinds; const p = d.profile, f = pf();
    f.name.value = p.name; f.birth_day.value = p.birth_day || ''; f.birth_month.value = p.birth_month || 0; f.birth_year.value = p.birth_year || '';
    f.city.value = p.city; f.share_with_llm.checked = p.share_with_llm !== false;
    const l = $('#petList'); l.innerHTML = ''; (p.pets || []).forEach(x => l.appendChild(petRow(x)));
    const g = p.google || {}, signed = !!g.email;
    $('#gSigned').hidden = !signed; $('#gUnsigned').hidden = signed;
    $('#gName').textContent = g.name || ''; $('#gMail').textContent = g.email || ''; if (g.picture) $('#gPic').src = g.picture; else $('#gPic').removeAttribute('src');
    $('#gClient').value = d.google.client_id || ''; $('#gSecret').placeholder = d.google.has_secret ? 'сохранён (введите новый, чтобы заменить)' : 'оставьте пустым, если Google не выдал';
    $('#gIn').hidden = !!d.signing_in; $('#gCancel').hidden = !d.signing_in;
  }
  async function loadProfile() { const d = await call('get_profile'); if (d) fillProfile(d); }
  $('#petAdd').onclick = () => { if (!Object.keys(kinds).length) return; $('#petList').appendChild(petRow({ kind: 'dog' })); };
  pf().addEventListener('submit', async e => {
    e.preventDefault(); const f = e.target;
    const pets = $$('#petList .pet').map(r => ({ kind: $('.pk', r).value, name: $('.pn', r).value.trim(), birth_day: Number($('.pd', r).value) || 0, birth_month: Number($('.pm', r).value) || 0 })).filter(x => x.name);
    const d = await call('save_profile', { name: f.name.value, birth_day: Number(f.birth_day.value) || 0, birth_month: Number(f.birth_month.value) || 0, birth_year: Number(f.birth_year.value) || 0,
      city: f.city.value, share_with_llm: f.share_with_llm.checked, pets });
    if (d) { fillProfile(d); flash($('#profMsg'), 'Сохранено'); } else flash($('#profMsg'), 'Не удалось сохранить', true);
  });
  const gMsg = (t, bad) => { const o = $('#gMsg'); o.hidden = !t; o.className = 'note' + (bad ? ' bad' : ''); o.textContent = t || ''; };
  $('#gSave').onclick = async () => { const d = await call('save_google_client', $('#gClient').value, $('#gSecret').value); if (d) { $('#gSecret').value = ''; fillProfile(d); gMsg('Сохранено. Теперь нажмите «Войти через Google».'); } };
  $('#gIn').onclick = async () => { gMsg('Открываю браузер… выберите аккаунт Google.'); $('#gIn').hidden = true; $('#gCancel').hidden = false; if (!(await call('google_login'))) loadProfile(); };
  $('#gCancel').onclick = () => call('google_cancel');
  $('#gOut').onclick = async () => { const d = await call('google_logout'); if (d) { fillProfile(d); gMsg(''); } };
  function googleDone(ev) { gMsg(ev.error || 'Готово: вы вошли.', !!ev.error); loadProfile(); }

  // ---------- голос Silero ----------
  function silView(st) {
    $('#silStatus').textContent = st.downloading ? 'Скачиваю…' : st.ready ? `Скачан (${fmtMb(st.size)})${st.torch ? '. Всё готово.' : '. ' + (st.hint || 'Не хватает пакета torch.')}` : 'Не скачан: пока Карина говорит обычным голосом.';
    $('#silDl').hidden = st.ready; $('#silDl').disabled = !!st.downloading; $('#silDel').hidden = !st.ready;
    if (!st.downloading) $('#silBar').hidden = true;
  }
  async function loadSil() { const st = await call('silero_status'); if (st) silView(st); }
  function silProgress(ev) {
    const pb = $('#silBar'); pb.hidden = false; $('i', pb).style.width = (ev.total ? ev.done / ev.total * 100 : 0).toFixed(1) + '%';
    $('#silStatus').textContent = ev.total ? `Скачано ${fmtMb(ev.done)} из ${fmtMb(ev.total)}` : 'Подключаюсь…';
  }
  function silDone(ev) { if (ev.error && ev.error !== 'cancelled') flash($('#setMsg'), ev.error, true); loadSil(); }
  $('#silDl').onclick = async () => { $('#silDl').disabled = true; $('#silStatus').textContent = 'Подключаюсь…'; if (!(await call('silero_download'))) loadSil(); };
  $('#silDel').onclick = async () => { const st = await call('silero_delete'); if (st) silView(st); };

  // ---------- микшер и VPN ----------
  async function loadVpnStatus() {
    const r = await call('integrations_status'); if (!r) return;
    const v = r.vpn; $('#vpnOut').className = 'note' + (v.browsers ? '' : ' bad');
    $('#vpnOut').textContent = !v.enabled ? 'Управление VPN выключено.' : v.error ? `Связь не работает: ${v.error}.` : v.browsers ? `Расширение подключено (браузеров: ${v.browsers}).` : 'Расширение пока не подключено: откройте браузер с Bogestv0 VPN 1.4.0+ (оно проверяет раз в полминуты).';
  }
  $('#mixTest').onclick = async () => {
    const o = $('#mixOut'); o.hidden = false; o.className = 'note'; o.textContent = 'Проверяю…'; $('#mixTest').disabled = true;
    const r = await call('mixer_test'); $('#mixTest').disabled = false;
    if (r) { o.className = 'note' + (r.ok ? '' : ' bad'); o.textContent = r.text; }
  };

  // ---------- микрофоны (до 4) ----------
  const MAX_MICS = 4;
  let micDevs = [], outDevs = [], micState = [];
  // те же правила, что в audio.mic_slots: пусто = старая настройка с одним микрофоном
  function slotsFromCfg(c) {
    let raw = (c.stt && c.stt.mics) || [];
    if (!raw.length) raw = [{ enabled: true, device: c.stt ? c.stt.mic_device : null }];
    const out = raw.slice(0, MAX_MICS).map(r => ({ enabled: !!r.enabled, device: r.device == null || r.device === '' ? null : Number(r.device) }));
    while (out.length < MAX_MICS) out.push({ enabled: false, device: null });
    return out;
  }
  function buildMics(slots) {
    const list = $('#micList'); list.textContent = '';
    slots.forEach((sl, i) => {
      const row = el('div', 'mic-row'); row.dataset.slot = i;
      const sw = el('label', 'switch'), cb = el('input'); cb.type = 'checkbox'; cb.className = 'mic-on'; cb.checked = sl.enabled;
      sw.append(cb, el('span', 'sw'), el('span', '', 'Микрофон ' + (i + 1)));
      const sel = el('select', 'mic-dev'); sel.setAttribute('aria-label', 'Устройство для микрофона ' + (i + 1));
      const o0 = el('option', '', i === 0 ? 'Системный по умолчанию' : '— выберите микрофон —'); o0.value = ''; sel.append(o0);
      micDevs.forEach(m => { const o = el('option', '', m.label || m.name); o.value = m.id; sel.append(o); });
      if (sl.device != null && !micDevs.some(m => m.id === sl.device)) { const o = el('option', '', 'Устройство №' + sl.device + ' (не найдено)'); o.value = sl.device; sel.append(o); }
      sel.value = sl.device == null ? '' : String(sl.device);
      const meter = el('div', 'meter'); meter.append(el('div', 'meter-bar'));
      row.append(sw, sel, meter, el('div', 'mic-state'));
      list.append(row);
    });
    syncMics(); renderMicStatus(micState);
  }
  function syncMics() {
    const rows = $$('.mic-row');
    const taken = rows.map(r => $('.mic-dev', r).value);
    rows.forEach((r, i) => {
      r.classList.toggle('off', !$('.mic-on', r).checked);
      $$('option', $('.mic-dev', r)).forEach(o => { o.disabled = o.value !== '' && taken.some((t, j) => j !== i && t === o.value); });
    });
  }
  function renderMicStatus(list) {
    if (list) micState = list;
    $$('.mic-row').forEach(r => {
      const st = (micState || []).find(x => x.slot === Number(r.dataset.slot)); const box = $('.mic-state', r);
      const on = $('.mic-on', r).checked;
      box.className = 'mic-state small';
      if (!on) $('.meter-bar', r).style.width = '0';
      if (!st || !on || st.state === 'off') { box.textContent = ''; return; }
      if (st.state === 'ok') { box.textContent = 'Слушает'; box.classList.add('ok'); }
      else { box.textContent = st.text; box.classList.add('bad'); }
    });
  }
  function pickMic(slot) {
    const r = $(`.mic-row[data-slot="${slot}"]`); if (!r) return;
    r.classList.add('picked'); clearTimeout(r._t); r._t = setTimeout(() => r.classList.remove('picked'), 1400);
  }
  function readMics() {
    return $$('.mic-row').map(r => { const v = $('.mic-dev', r).value; return { enabled: $('.mic-on', r).checked, device: v === '' ? null : Number(v) }; });
  }
  $('#micList').addEventListener('change', e => {
    const row = e.target.closest('.mic-row'); if (!row) return;
    if (e.target.classList.contains('mic-dev') && e.target.value !== '' && Number(row.dataset.slot) > 0) $('.mic-on', row).checked = true;
    syncMics(); renderMicStatus(null);
  });
  $('#btnMicsRefresh').onclick = async () => {
    const d = await call('audio_devices'); if (!d) return;
    const slots = readMics(); micDevs = d.mics || []; outDevs = d.outs || []; buildMics(slots);
    flash($('#micsMsg'), `Найдено микрофонов: ${micDevs.length}`);
  };

  // ---------- вкладка «Звук» (вывод) ----------
  function fillOut(o) {
    const sel = $('#outDev'); sel.textContent = '';
    const o0 = el('option', '', 'Устройство Windows по умолчанию'); o0.value = ''; sel.append(o0);
    outDevs.forEach(d => { const x = el('option', '', d.label || d.name); x.value = d.id; sel.append(x); });
    if (o.device != null && !outDevs.some(d => d.id === o.device)) { const x = el('option', '', (o.name || 'Устройство №' + o.device) + ' (не найдено)'); x.value = o.device; sel.append(x); }
    sel.value = o.device == null ? '' : String(o.device);
    $('#outVol').value = o.volume; $('#outVolVal').textContent = o.volume + ' %';
    const w = $('#outWarn'); const txt = !o.found ? 'Выбранное устройство не найдено — Карина говорит через устройство Windows по умолчанию. Подключите его или выберите другое.' : (o.warning || '');
    w.hidden = !txt; w.textContent = txt;
  }
  async function loadOut(refresh) {
    if (refresh) { const d = await call('audio_devices'); if (d) { outDevs = d.outs || []; micDevs = d.mics || micDevs; } }
    const o = await call('audio_out_state'); if (o) fillOut(o);
  }
  async function saveOut() {
    const v = $('#outDev').value; const o = await call('audio_out_set', v === '' ? null : Number(v), Number($('#outVol').value));
    if (o) { fillOut(o); flash($('#outMsg'), 'Сохранено'); }
  }
  $('#outDev').addEventListener('change', saveOut);
  $('#outVol').addEventListener('input', () => { $('#outVolVal').textContent = $('#outVol').value + ' %'; });
  $('#outVol').addEventListener('change', saveOut);
  $('#outTest').onclick = async () => { $('#outTest').disabled = true; if (!(await call('audio_out_test'))) $('#outTest').disabled = false; };
  $('#outVoice').onclick = () => call('test_voice');
  $('#outRefresh').onclick = async () => { await loadOut(true); flash($('#outMsg'), `Найдено устройств вывода: ${outDevs.length}`); };

  // ---------- настройки ----------
  const sf = () => $('#setForm');
  function fillSettings(c, mics) {
    cfg = c; const f = sf();
    if (mics && mics.length) micDevs = mics;
    f.name.value = c.name; f.aliases.value = (c.name_aliases || []).join(', ');
    f.wake_required.checked = !!c.wake_required; f.followup_seconds.value = c.followup_seconds;
    f.model.value = c.stt.model; f.vad_threshold.value = c.stt.vad_threshold; f.assist.value = c.stt.assist || 'smart';
    f.tts_enabled.checked = !!c.tts.enabled; f.engine.value = c.tts.engine; f.voice.value = c.tts.voice || 'svetlana_cute'; f.offline.value = c.tts.offline || 'auto'; f.background.checked = (c.ui || {}).background !== false; loadVoiceOffline(); loadSil(); f.rate.value = c.tts.rate;
    const mx = c.mixer || {}, vp = c.vpn || {};
    f.mixer_enabled.checked = mx.enabled !== false; f.mixer_pin.value = mx.pin || ''; f.mixer_port.value = mx.port || 0; f.mixer_step.value = mx.step_db || 3; f.vpn_enabled.checked = vp.enabled !== false; loadVpnStatus();
    f.upd_auto.checked = (c.updates || {}).auto !== false; f.search_engine.value = c.search_engine; f.llm_enabled.checked = !!c.llm.enabled; f.theme.value = (c.ui || {}).theme || 'auto';
    f.search_mode.value = (c.search || {}).mode || 'auto'; f.search_depth.value = (c.search || {}).depth || 'normal'; showWeb();
    $('#apiKey').placeholder = c.llm.has_key ? 'ключ сохранён (введите новый, чтобы заменить)' : 'sk-ant-…';
    buildMics(slotsFromCfg(c));
    sync(); applyTheme(f.theme.value);
    $$('.nm').forEach(n => n.textContent = c.name);
  }
  function sync() {
    const f = sf();
    $('#outFollow').textContent = f.followup_seconds.value + ' с';
    $('#outVad').textContent = Number(f.vad_threshold.value).toFixed(3);
    $('#outRate').textContent = f.rate.value > 0 ? '+' + f.rate.value : f.rate.value;
    $('#outStep').textContent = Number(f.mixer_step.value).toFixed(1) + ' дБ';
  }
  $('#setForm').addEventListener('input', sync);
  $('#setForm').addEventListener('change', e => { if (e.target.name === 'theme') applyTheme(e.target.value); });
  $('#setForm').addEventListener('submit', async e => {
    e.preventDefault(); const f = e.target;
    const mics = readMics();
    const patch = {
      name: f.name.value.trim() || 'Карина',
      name_aliases: f.aliases.value.split(',').map(s => s.trim()).filter(Boolean),
      wake_required: f.wake_required.checked, followup_seconds: Number(f.followup_seconds.value),
      stt: { model: f.model.value, mics, assist: f.assist.value, vad_threshold: Number(f.vad_threshold.value) },
      tts: { enabled: f.tts_enabled.checked, engine: f.engine.value, voice: f.voice.value, offline: f.offline.value, rate: Number(f.rate.value) },
      search: { mode: f.search_mode.value, depth: f.search_depth.value },
      mixer: { enabled: f.mixer_enabled.checked, pin: f.mixer_pin.value.trim(), port: Number(f.mixer_port.value) || 0, step_db: Number(f.mixer_step.value) },
      vpn: { enabled: f.vpn_enabled.checked },
      updates: { auto: f.upd_auto.checked }, search_engine: f.search_engine.value, ui: { theme: f.theme.value, background: f.background.checked },
      llm: { enabled: f.llm_enabled.checked, api_key: $('#apiKey').value.trim() },
    };
    const c = await call('save_settings', patch);
    $('#apiKey').value = '';
    if (c) { fillSettings(c, null); flash($('#setMsg'), 'Сохранено'); refresh(); loadBrain(); } else flash($('#setMsg'), 'Не удалось сохранить', true);
  });
  $('#btnClearKey').onclick = async () => { const c = await call('clear_api_key'); if (c) { fillSettings(c, null); flash($('#keyMsg'), 'Ключ удалён'); loadBrain(); } };
  $('#btnData').onclick = () => call('open_data_folder');

  async function refresh() {
    const s = await call('get_state'); if (!s) return;
    outDevs = s.outs || outDevs; fillSettings(s.config, s.mics);
    setFrameless(s.frameless, s.maximized);
    version = s.version || version; $('#kvVer').textContent = version; renderStatus(s.status);
  }

  async function init() {
    api = window.pywebview.api;
    await refresh(); loadBrain(); loadAdmin(); loadProfile();
    setTimeout(() => checkUpdate(false), 3000);
    setInterval(async () => { const s = await call('get_status'); if (s) renderStatus(s); loadBrain(); }, 5000);
    let t = null; try { t = localStorage.getItem('karina.tab'); } catch (_) {}
    if (t) { const b = $(`#tabs [data-tab="${t}"]`); if (b) b.click(); }
  }
  if (window.pywebview && window.pywebview.api) init(); else window.addEventListener('pywebviewready', init);
})();
