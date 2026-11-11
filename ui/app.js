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
    if (b.dataset.tab === 'models') { loadModels(); loadBrain(); loadCloud(); }
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
    $('#kvVoice').textContent = st.voice === 'online' ? 'онлайн (нейросеть)' : st.voice === 'piper' ? 'офлайн (нейросеть «Ирина»)' : st.voice === 'offline' ? 'системный (офлайн)' : st.online ? 'онлайн' : 'системный';
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
      else if (ev.type === 'chat_done') chatDone(ev);
      else if (ev.type === 'search_test') { const b = $('#searchTest'); b.hidden = false; b.textContent = ev.lines.join('\n'); flash($('#searchMsg'), ev.ok ? 'Поиск работает' : 'Поиск не работает', !ev.ok); }
      else if (ev.type === 'cloud_test') { const r = $(`.cloud-row[data-pid="${ev.pid}"]`); if (r) { const o = $('.note', r); o.hidden = false; o.className = 'note ' + (ev.ok ? '' : 'bad'); o.textContent = ev.ok ? `Работает (${ev.sec} с): ${ev.text}` : ev.text; $$('button', r).forEach(b => b.disabled = false); } loadCloud(); loadBrain(); }
      else if (ev.type === 'brain_test') { const o = $('#brainTestOut'); o.hidden = false; o.className = 'note ' + (ev.ok ? '' : 'bad'); o.textContent = ev.ok ? `Claude отвечает: «${ev.text}» (${ev.sec} с)` : ev.text; $('#brainTest').disabled = false; }
      else if (ev.type === 'voice_dl') voiceDlProgress(ev);
      else if (ev.type === 'voice_dl_done') voiceDlDone(ev);
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
  function attachProject(row, pr) {
    if (!pr || !pr.name || row.querySelector('.pcard')) return;
    const box = el('div', 'pcard'); const b = el('button', 'btn primary', '📁 Открыть папку проекта'); const msg = el('span', 'msg err');
    b.onclick = async () => { const r = await call('coder_open', pr.name); msg.textContent = r && !r.ok ? r.error : ''; };
    box.append(b, msg); row.querySelector('.bubble').after(box);
  }
  function addMsg(role, text, id, steps, files, brain, filesIn, project) {
    const m = $('#chMsgs'); const row = el('div', 'cmsg ' + role); if (id) row.dataset.id = id;
    const b = el('div', 'bubble');
    if (role === 'assistant') renderMd(b, text); else { b.textContent = text; if (filesIn && filesIn.length) b.append(el('div', 'attached', '📎 ' + filesIn.join(', '))); }
    row.append(b);
    if (role === 'assistant') {
      const meta = el('div', 'meta');
      const c = el('button', 'link copy', 'Копировать'); c.onclick = () => { try { navigator.clipboard.writeText(b.innerText); c.textContent = 'Скопировано'; setTimeout(() => c.textContent = 'Копировать', 1500); } catch (_) {} };
      meta.append(c, el('span', 'who')); row.append(meta);
      attachProject(row, project); attachFiles(row, files); attachSteps(row, steps); setWho(row, brain);
    }
    m.append(row); scrollChat(); return b;
  }
  function showWelcome() {
    const m = $('#chMsgs'); m.textContent = '';
    const w = el('div', 'empty'); w.textContent = 'Задайте вопрос или опишите задачу. Нужно посмотреть или изменить код — приложите файлы кнопкой 📎 и напишите, что сделать: я отвечу здесь же (разберу код или напишу и проверю его в папке проекта, ваши файлы не меняются). Голосом: «Карина, объясни…» или «Карина, найди и напиши отчёт про…» — коротко отвечу вслух, подробности будут здесь.';
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
    for (const m of c.messages) addMsg(m.role, m.text, m.id, m.steps, m.files, m.brain, m.files_in, m.project);
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
    addMsg('user', ev.user.text, ev.user.id, null, null, null, ev.user.files_in); chBubble = addMsg('assistant', '', ev.reply_id); chBubble.classList.add('typing');
    setBusy(true); setStep('Думаю…'); loadChatList();
  }
  function chatDone(ev) {
    if (ev.chat === chCur && chBubble) { renderMd(chBubble, ev.text); chBubble.classList.remove('typing'); attachProject(chBubble.parentElement, ev.project); attachFiles(chBubble.parentElement, ev.files); attachSteps(chBubble.parentElement, ev.steps); setWho(chBubble.parentElement, ev.brain); if (ev.error) chBubble.classList.add('err'); chBubble = null; }
    if (ev.chat === chCur) setBusy(false);
    loadChatList(); scrollChat();
  }
  $('#chNew').onclick = async () => { const c = await call('chat_new'); if (c) { chCur = c.id; showWelcome(); loadChatList(); $('#chText').focus(); } };
  $('#chForm').addEventListener('submit', async e => {
    e.preventDefault(); const t = $('#chText'); const text = t.value.trim(); if (!text || chBusy) return;
    t.value = ''; t.style.height = '';
    const wm = webMode(); const paths = chPaths.slice();
    const r = await call('chat_send', chCur || '', text, wm === 'always' ? true : wm === 'off' ? false : null, paths, chCodeMode);
    if (r && !r.ok) { t.value = text; setStep(r.error); } else if (r) { chPaths = []; renderAttach(); }
  });
  $('#chText').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chForm').requestSubmit(); } });
  $('#chText').addEventListener('input', e => { const t = e.target; t.style.height = 'auto'; t.style.height = Math.min(160, t.scrollHeight) + 'px'; });
  $('#chStop').onclick = () => call('chat_stop');

  // приложенные файлы, режим «Код» и разрешение запуска
  let chPaths = [], chCodeMode = 'auto';
  try { const v = localStorage.getItem('karina.codeMode'); if (['auto', 'code', 'ask'].includes(v)) chCodeMode = v; } catch (_) {}
  const baseName = p => p.split(/[\\/]/).pop();
  function renderAttach() {
    const box = $('#chAttach'); box.textContent = ''; box.hidden = !chPaths.length;
    chPaths.forEach((p, i) => {
      const c = el('div', 'chip'); c.title = p; const x = el('button', '', '×'); x.type = 'button'; x.title = 'Убрать';
      x.onclick = () => { chPaths.splice(i, 1); renderAttach(); }; c.append(el('span', '', baseName(p)), x); box.append(c);
    });
  }
  async function pickAttach(folder) {
    const r = await call('coder_pick', !!folder);
    for (const p of (r || [])) if (!chPaths.includes(p)) chPaths.push(p);
    renderAttach(); $('#chText').focus();
  }
  $('#chPick').onclick = () => pickAttach(false);
  $('#chPickDir').onclick = () => pickAttach(true);
  const CODE_LABEL = { auto: '💻 Код: авто', code: '💻 Код: всегда', ask: '💻 Код: только чат' };
  function showCode() { const b = $('#chCode'); b.textContent = CODE_LABEL[chCodeMode]; b.classList.toggle('on', chCodeMode === 'code'); }
  $('#chCode').onclick = () => {
    const order = ['auto', 'code', 'ask']; chCodeMode = order[(order.indexOf(chCodeMode) + 1) % 3]; showCode();
    try { localStorage.setItem('karina.codeMode', chCodeMode); } catch (_) {}
  };
  function showRun(on) { const b = $('#chRun'); b.textContent = on ? '▶ Запуск кода: вкл' : '▶ Запуск кода: выкл'; b.classList.toggle('on', !!on); b.dataset.on = on ? '1' : ''; }
  $('#chRun').onclick = async () => {
    const turnOn = !$('#chRun').dataset.on;
    if (turnOn && !confirm('Разрешить Карине запускать написанный ею код для проверки?\n\nЭто не песочница: программа выполняется с вашими правами. Разрешены только python, pytest и node, без сети, подпроцессов и удаления файлов (Карина проверяет код перед запуском). Нужен установленный Python или Node.js.')) return;
    const s = await call('coder_set', turnOn); if (s) showRun(s.run);
  };
  async function loadCoderState() { const s = await call('coder_state'); if (s) showRun(s.settings.run); }
  showCode();
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

  // ---------- бесплатные облачные модели ----------
  const CLOUD_STATE_LABEL = { ok: 'Готов', cooldown: 'Лимит', error: 'Ошибка', nokey: 'Нет ключа', off: 'Выключен' };
  let cloudLast = null;
  function cloudRow(p, idx, total) {
    const row = el('div', 'cloud-row ' + p.state); row.dataset.pid = p.id;
    const head = el('div', 'cloud-head');
    const title = el('div', 'cloud-title'); title.append(el('b', '', p.name), el('span', 'tag' + (p.state === 'ok' ? '' : ' warn'), CLOUD_STATE_LABEL[p.state] || p.state));
    const sw = el('label', 'switch'), cb = el('input'); cb.type = 'checkbox'; cb.checked = p.enabled; cb.disabled = !p.has_key; cb.title = p.has_key ? '' : 'Сначала введите ключ';
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
    const keyI = el('input'); keyI.type = 'password'; keyI.autocomplete = 'off'; keyI.placeholder = p.has_key ? 'ключ сохранён (введите новый, чтобы заменить)' : 'Вставьте ключ API';
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
    cloud: ['Бесплатные облака', 'Отвечают бесплатные облачные модели (Groq, Gemini и др.) по вашим ключам — быстро даже на слабом компьютере. Когда у одной кончается лимит, подхватывает следующая. Нужен интернет.'],
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
    f.tts_enabled.checked = !!c.tts.enabled; f.engine.value = c.tts.engine; f.voice.value = c.tts.voice || 'svetlana_cute'; f.offline.value = c.tts.offline || 'auto'; f.background.checked = (c.ui || {}).background !== false; loadVoiceOffline(); f.rate.value = c.tts.rate;
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
    await refresh(); loadBrain(); loadAdmin();
    setTimeout(() => checkUpdate(false), 3000);
    setInterval(async () => { const s = await call('get_status'); if (s) renderStatus(s); loadBrain(); }, 5000);
    let t = null; try { t = localStorage.getItem('karina.tab'); } catch (_) {}
    if (t) { const b = $(`#tabs [data-tab="${t}"]`); if (b) b.click(); }
  }
  if (window.pywebview && window.pywebview.api) init(); else window.addEventListener('pywebviewready', init);
})();
