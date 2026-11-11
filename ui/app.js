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
    if (b.dataset.tab === 'models') { loadModels(); loadBrain(); }
    if (b.dataset.tab === 'chat') openChatTab();
    if (b.dataset.tab === 'coder') loadCoder();
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
    $('#kvVoice').textContent = st.voice === 'online' ? 'онлайн (нейросеть)' : st.voice === 'offline' ? 'системный (офлайн)' : st.online ? 'онлайн' : 'системный';
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

  // события из Python
  window.karina = {
    event(ev) {
      if (ev.type === 'level') {
        document.documentElement.style.setProperty('--lvl', ev.v.toFixed(2));
        $('#meter').style.width = Math.round(ev.v * 100) + '%';
      } else if (ev.type === 'log') addLog(ev.kind, ev.text, ev.ts);
      else if (ev.type === 'goto') { const b = $(`#tabs [data-tab="${ev.tab}"]`); if (b) b.click(); }
      else if (ev.type === 'chat_open') { chOpenId = ev.chat; const b = $('#tabs [data-tab="chat"]'); if (b) b.click(); }
      else if (ev.type === 'coder') coderEvent(ev);
      else if (ev.type === 'coder_done') coderDone(ev);
      else if (ev.type === 'chat_start') chatStart(ev);
      else if (ev.type === 'chat_step') { if (ev.chat === chCur) setStep(ev.text); }
      else if (ev.type === 'chat_delta') { if (ev.chat === chCur && chBubble) renderMd(chBubble, ev.text); scrollChat(); }
      else if (ev.type === 'chat_done') chatDone(ev);
      else if (ev.type === 'search_test') { const b = $('#searchTest'); b.hidden = false; b.textContent = ev.lines.join('\n'); flash($('#searchMsg'), ev.ok ? 'Поиск работает' : 'Поиск не работает', !ev.ok); }
      else if (ev.type === 'brain_test') { const o = $('#brainTestOut'); o.hidden = false; o.className = 'note ' + (ev.ok ? '' : 'bad'); o.textContent = ev.ok ? `Claude отвечает: «${ev.text}» (${ev.sec} с)` : ev.text; $('#brainTest').disabled = false; }
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
      if (/^```/.test(ln)) {
        endBlocks(); const code = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
        i++; const pre = el('pre'); pre.append(el('code', '', code.join('\n'))); box.append(pre); continue;
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
  const KIND = { word: ['📄', 'Документ Word'], excel: ['📊', 'Таблица Excel'], pdf: ['📕', 'PDF'], svg: ['🖼', 'Схема SVG'], text: ['📝', 'Текстовый файл'], code: ['💻', 'Код'] };
  function attachFiles(row, files) {
    if (!files || !files.length || row.querySelector('.files')) return;
    const box = el('div', 'files');
    for (const f of files) {
      const card = el('div', 'fcard'), k = KIND[f.kind] || KIND.text, msg = el('div', 'fmsg');
      if (f.kind === 'svg') {
        const img = el('img', 'fprev'); img.alt = f.name; card.append(img);
        call('file_preview', f.name).then(r => { if (r && r.ok) img.src = r.result; else img.remove(); });
      }
      const line = el('div', 'fline'), t = el('div', 'ft');
      t.append(el('b', '', f.name), el('span', '', `${k[1]} · ${fmtSize(f.size)}` + (f.warning ? ' · ⚠ ' + f.warning : '')));
      const act = (name, label, primary) => { const b = el('button', 'btn' + (primary ? ' primary' : ''), label); b.onclick = async () => { const r = await call(name, f.name); msg.textContent = r && !r.ok ? r.error : ''; }; return b; };
      line.append(el('span', 'ficon', k[0]), t, act('file_open', 'Открыть', true), act('file_reveal', 'В папке'));
      card.append(line, msg); box.append(card);
    }
    row.querySelector('.bubble').after(box);
  }
  function setWho(row, brain) { const w = row.querySelector('.who'); if (w) w.textContent = brain ? 'Ответила: ' + brain : ''; }
  function addMsg(role, text, id, steps, files, brain) {
    const m = $('#chMsgs'); const row = el('div', 'cmsg ' + role); if (id) row.dataset.id = id;
    const b = el('div', 'bubble');
    if (role === 'assistant') renderMd(b, text); else b.textContent = text;
    row.append(b);
    if (role === 'assistant') {
      const meta = el('div', 'meta');
      const c = el('button', 'link copy', 'Копировать'); c.onclick = () => { try { navigator.clipboard.writeText(b.innerText); c.textContent = 'Скопировано'; setTimeout(() => c.textContent = 'Копировать', 1500); } catch (_) {} };
      meta.append(c, el('span', 'who')); row.append(meta);
      attachFiles(row, files); attachSteps(row, steps); setWho(row, brain);
    }
    m.append(row); scrollChat(); return b;
  }
  function showWelcome() {
    const m = $('#chMsgs'); m.textContent = '';
    const w = el('div', 'empty'); w.textContent = 'Задайте вопрос, и я отвечу. Голосом: «Карина, объясни…» или «Карина, найди и напиши отчёт про…» — коротко отвечу вслух, подробности будут здесь.';
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
    for (const m of c.messages) addMsg(m.role, m.text, m.id, m.steps, m.files, m.brain);
    loadChatList(); scrollChat();
  }
  async function openChatTab() {
    const r = await loadChatList();
    const id = chOpenId || chCur || (r && r.chats[0] && r.chats[0].id); chOpenId = null;
    if (id) openChat(id); else showWelcome();
    if (r) setBusy(!!r.busy);
    setTimeout(() => $('#chText').focus(), 50);
  }
  function chatStart(ev) {
    if (!chCur || ev.chat !== chCur) { chCur = ev.chat; $('#chMsgs').textContent = ''; }
    const w = $('#chMsgs .empty'); if (w) w.remove();
    addMsg('user', ev.user.text, ev.user.id); chBubble = addMsg('assistant', '', ev.reply_id); chBubble.classList.add('typing');
    setBusy(true); setStep('Думаю…'); loadChatList();
  }
  function chatDone(ev) {
    if (ev.chat === chCur && chBubble) { renderMd(chBubble, ev.text); chBubble.classList.remove('typing'); attachFiles(chBubble.parentElement, ev.files); attachSteps(chBubble.parentElement, ev.steps); setWho(chBubble.parentElement, ev.brain); if (ev.error) chBubble.classList.add('err'); chBubble = null; }
    if (ev.chat === chCur) setBusy(false);
    loadChatList(); scrollChat();
  }
  $('#chNew').onclick = async () => { const c = await call('chat_new'); if (c) { chCur = c.id; showWelcome(); loadChatList(); $('#chText').focus(); } };
  $('#chForm').addEventListener('submit', async e => {
    e.preventDefault(); const t = $('#chText'); const text = t.value.trim(); if (!text || chBusy) return;
    t.value = ''; t.style.height = '';
    const wm = webMode(); const r = await call('chat_send', chCur || '', text, wm === 'always' ? true : wm === 'off' ? false : null);
    if (r && !r.ok) { t.value = text; setStep(r.error); }
  });
  $('#chText').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chForm').requestSubmit(); } });
  $('#chText').addEventListener('input', e => { const t = e.target; t.style.height = 'auto'; t.style.height = Math.min(160, t.scrollHeight) + 'px'; });
  $('#chStop').onclick = () => call('chat_stop');
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

  // ---------- кодер ----------
  let cdPaths = [], cdProject = '';
  const cdBase = p => p.split(/[\\/]/).pop();
  function cdRenderPicked() {
    const box = $('#cdPicked'); box.textContent = '';
    cdPaths.forEach((p, i) => {
      const it = el('div', 'item'); const t = el('div', 't'); t.append(el('b', '', cdBase(p)), el('span', '', p)); it.append(t);
      const x = el('button', 'btn', 'Убрать'); x.onclick = () => { cdPaths.splice(i, 1); cdRenderPicked(); }; it.append(x); box.append(it);
    });
    $('#cdClear').hidden = !cdPaths.length;
  }
  async function cdPick(folder) {
    const r = await call('coder_pick', !!folder);
    for (const p of (r || [])) if (!cdPaths.includes(p)) cdPaths.push(p);
    cdRenderPicked();
  }
  async function loadCoder() {
    const s = await call('coder_state'); if (!s) return;
    $('#cdRun').checked = !!s.settings.run; cdBusy(s.busy);
  }
  function cdBusy(b) { $('#cdStart').disabled = b; $('#cdStop').hidden = !b; }
  function cdLog(text, cls, detail) {
    $('#cdLogCard').hidden = false;
    const it = el('div', 'item ' + (cls || '')); const t = el('div', 't'); const b = el('b', '', text); t.append(b);
    if (detail) { const d = el('span', 'wrap', detail); d.style.whiteSpace = 'pre-wrap'; t.append(d); }
    it.append(t); const box = $('#cdLog'); box.append(it); it.scrollIntoView({ block: 'nearest' });
  }
  function coderEvent(ev) {
    if (ev.kind === 'start') { cdProject = ev.project; $('#cdLog').textContent = ''; $('#cdResult').hidden = true; cdBusy(true); cdLog(ev.text); }
    else if (ev.kind === 'result') cdLog((ev.ok ? '✔ ' : '✖ ') + ev.text, ev.ok ? 'ok' : 'miss', ev.output);
    else if (ev.kind === 'warn') cdLog('⚠ ' + ev.text, 'miss');
    else if (ev.kind === 'say') cdLog('Карина: ' + ev.text.slice(0, 600));
    else cdLog(ev.text);
  }
  function coderDone(ev) {
    cdBusy(false); cdProject = ev.project || cdProject; $('#cdLogCard').hidden = false; $('#cdResult').hidden = false;
    const v = $('#cdVerdict'); v.className = 'note ' + (ev.ok ? '' : 'bad'); v.textContent = ev.error ? '⚠ ' + ev.error : ev.verdict;
    $('#cdSummary').textContent = ev.summary || '';
    const box = $('#cdFilesOut'); box.textContent = '';
    for (const f of ev.files || []) { const it = el('div', 'item'); const t = el('div', 't'); t.append(el('b', '', f.path), el('span', '', f.size + ' байт')); it.append(t); box.append(it); }
    if (ev.zip) { const it = el('div', 'item ok'); const t = el('div', 't'); t.append(el('b', '', '📦 ' + ev.zip), el('span', '', 'Весь результат одним архивом — рядом с папкой проекта')); it.append(t); box.append(it); }
  }
  $('#cdFiles').onclick = () => cdPick(false);
  $('#cdFolder').onclick = () => cdPick(true);
  $('#cdClear').onclick = () => { cdPaths = []; cdRenderPicked(); };
  $('#cdRun').onchange = async e => { await call('coder_set', e.target.checked); };
  $('#cdStart').onclick = async () => {
    const r = await call('coder_start', $('#cdTask').value, cdPaths);
    if (!r) return flash($('#cdMsg'), 'Не удалось начать', true);
    if (!r.ok) return flash($('#cdMsg'), r.error, true);
    cdBusy(true);
  };
  $('#cdStop').onclick = () => call('coder_stop');
  $('#cdFolderOpen').onclick = () => call('coder_open', '');
  $('#cdOpenProject').onclick = () => call('coder_open', cdProject);

  // ---------- мозг ----------
  const BRAIN = {
    local: ['Локальная модель', 'Встроенная модель на этом компьютере: бесплатно и без интернета. На слабом компьютере отвечает медленнее.'],
    claude: ['Claude', 'Отвечает Claude по вашему ключу: быстрее и умнее, но нужен интернет, и запросы оплачиваются у Anthropic.'],
    auto: ['Авто', 'Простые вопросы — локальная модель, сложные (отчёты, код, файлы, глубокий поиск) — Claude. Если одного нет, отвечает другой, и в ответе это видно.'],
  };
  const BRAIN_SHORT = { local: 'локальный', claude: 'Claude', auto: 'авто' };
  let brain = null;
  const brainWhy = (b, m) => m === 'local' ? (b.local.ok ? 'готова' : b.local.reason) : m === 'claude' ? (b.claude.ok ? 'готов' : b.claude.reason)
    : `локальная: ${b.local.ok ? 'готова' : 'нет'} · Claude: ${b.claude.ok ? 'готов' : 'нет'}`;
  function renderBrain(b) {
    if (!b) return; brain = b;
    const sig = JSON.stringify(b); if (renderBrain.sig === sig) return; renderBrain.sig = sig;     // не перерисовываем без изменений (иначе закрывается открытый список)
    $('#chBrain').textContent = '🧠 Мозг: ' + BRAIN_SHORT[b.mode];
    $('#kvBrain').textContent = b.mode === 'claude' ? b.claude.label : b.mode === 'auto' ? 'Авто (локально + Claude)' : 'Локальная модель';
    const box = $('#brainList'); if (!box) return; box.textContent = '';
    for (const m of ['local', 'claude', 'auto']) {
      const ok = m === 'auto' ? (b.local.ok || b.claude.ok) : m === 'local' ? b.local.ok : b.claude.ok;
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
    const order = ['local', 'claude', 'auto']; const next = order[(order.indexOf(brain.mode) + 1) % 3];
    renderBrain(await call('brain_set', next));
    const ok = next === 'auto' ? (brain.local.ok || brain.claude.ok) : next === 'local' ? brain.local.ok : brain.claude.ok;
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

  // ---------- настройки ----------
  const sf = () => $('#setForm');
  function fillSettings(c, mics) {
    cfg = c; const f = sf();
    f.name.value = c.name; f.aliases.value = (c.name_aliases || []).join(', ');
    f.wake_required.checked = !!c.wake_required; f.followup_seconds.value = c.followup_seconds;
    f.model.value = c.stt.model; f.vad_threshold.value = c.stt.vad_threshold;
    f.tts_enabled.checked = !!c.tts.enabled; f.engine.value = c.tts.engine; f.rate.value = c.tts.rate;
    f.upd_auto.checked = (c.updates || {}).auto !== false; f.search_engine.value = c.search_engine; f.llm_enabled.checked = !!c.llm.enabled; f.theme.value = (c.ui || {}).theme || 'auto';
    f.search_mode.value = (c.search || {}).mode || 'auto'; f.search_depth.value = (c.search || {}).depth || 'normal'; showWeb();
    $('#apiKey').placeholder = c.llm.has_key ? 'ключ сохранён (введите новый, чтобы заменить)' : 'sk-ant-…';
    const ms = $('#micSelect'); ms.textContent = '';
    const o0 = el('option', '', 'По умолчанию (системный)'); o0.value = ''; ms.append(o0);
    (mics || []).forEach(m => { const o = el('option', '', m.name); o.value = m.id; ms.append(o); });
    ms.value = c.stt.mic_device == null ? '' : String(c.stt.mic_device);
    sync(); applyTheme(f.theme.value);
    $$('.nm').forEach(n => n.textContent = c.name);
  }
  function sync() {
    const f = sf();
    $('#outFollow').textContent = f.followup_seconds.value + ' с';
    $('#outVad').textContent = Number(f.vad_threshold.value).toFixed(3);
    $('#outRate').textContent = f.rate.value > 0 ? '+' + f.rate.value : f.rate.value;
  }
  $('#setForm').addEventListener('input', sync);
  $('#setForm').addEventListener('change', e => { if (e.target.name === 'theme') applyTheme(e.target.value); });
  $('#setForm').addEventListener('submit', async e => {
    e.preventDefault(); const f = e.target;
    const mic = f.mic_device.value;
    const patch = {
      name: f.name.value.trim() || 'Карина',
      name_aliases: f.aliases.value.split(',').map(s => s.trim()).filter(Boolean),
      wake_required: f.wake_required.checked, followup_seconds: Number(f.followup_seconds.value),
      stt: { model: f.model.value, mic_device: mic === '' ? null : Number(mic), vad_threshold: Number(f.vad_threshold.value) },
      tts: { enabled: f.tts_enabled.checked, engine: f.engine.value, rate: Number(f.rate.value) },
      search: { mode: f.search_mode.value, depth: f.search_depth.value },
      updates: { auto: f.upd_auto.checked }, search_engine: f.search_engine.value, ui: { theme: f.theme.value },
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
    if (s.mics && s.mics.length) fillSettings(s.config, s.mics); else { const keep = $('#micSelect').value; fillSettings(s.config, [...$('#micSelect').options].slice(1).map(o => ({ id: o.value, name: o.textContent }))); $('#micSelect').value = keep; }
    version = s.version || version; $('#kvVer').textContent = version; renderStatus(s.status);
  }

  async function init() {
    api = window.pywebview.api;
    await refresh(); loadBrain();
    setTimeout(() => checkUpdate(false), 3000);
    setInterval(async () => { const s = await call('get_status'); if (s) renderStatus(s); loadBrain(); }, 5000);
    let t = null; try { t = localStorage.getItem('karina.tab'); } catch (_) {}
    if (t) { const b = $(`#tabs [data-tab="${t}"]`); if (b) b.click(); }
  }
  if (window.pywebview && window.pywebview.api) init(); else window.addEventListener('pywebviewready', init);
})();
