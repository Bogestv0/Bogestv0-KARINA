(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  let api = null, cfg = null, status = {}, logCount = 0, upd = null, version = '';

  const call = async (name, ...args) => { try { return await api[name](...args); } catch (e) { console.error(name, e); return null; } };
  const flash = (node, text, err) => { node.textContent = text; node.classList.toggle('err', !!err); clearTimeout(node._t); node._t = setTimeout(() => node.textContent = '', 4000); };


  // ---------- свои окна подтверждения (вместо системных confirm/alert) ----------
  function ask(text, o = {}) {
    return new Promise(res => {
      const back = el('div', 'dlg-back'), box = el('div', 'dlg'); box.setAttribute('role', 'alertdialog'); box.setAttribute('aria-modal', 'true');
      if (o.title) box.append(el('h3', null, o.title));
      const p = el('p'); p.textContent = text; box.append(p);
      const row = el('div', 'dlg-btns');
      const done = v => { document.removeEventListener('keydown', key, true); back.classList.remove('on'); setTimeout(() => back.remove(), 160); res(v); };
      const no = el('button', 'btn', o.cancel || 'Отмена'); no.onclick = () => done(false);
      const ok = el('button', 'btn ' + (o.danger ? 'danger' : 'primary'), o.ok || 'ОК'); ok.onclick = () => done(true);
      if (o.info) row.append(ok); else row.append(no, ok);
      box.append(row); back.append(box); document.body.append(back);
      const key = e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); } else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); done(true); } };
      document.addEventListener('keydown', key, true);
      back.addEventListener('mousedown', e => { if (e.target === back) done(false); });
      requestAnimationFrame(() => { back.classList.add('on'); ok.focus(); });
    });
  }
  const note = (text, title) => ask(text, { info: true, title });

  // ---------- вкладки ----------
  $('#tabs').addEventListener('click', e => {
    const b = e.target.closest('button[data-tab]'); if (!b) return;
    $$('#tabs button').forEach(x => x.classList.toggle('on', x === b));
    $$('.tab').forEach(t => t.classList.toggle('on', t.id === 'tab-' + b.dataset.tab));
    if (b.dataset.tab === 'apps') loadApps();
    if (b.dataset.tab === 'commands') loadCommands();
    if (b.dataset.tab === 'models') { loadModels(); loadBrain(); loadImg(); }
    if (b.dataset.tab === 'cloud') { loadBrain(); loadCloud(); }
    if (b.dataset.tab === 'profile') loadProfile();
    if (b.dataset.tab === 'chat') openChatTab();
    if (b.dataset.tab === 'sound') loadOut(false);
    if (b.dataset.tab === 'widget') loadWidget();
    try { localStorage.setItem('karina.tab', b.dataset.tab); } catch (_) {}
  });

  // ---------- тема ----------
  // Только тёмная тема (голограмма Карины рассчитана на тёмный фон)
  function applyTheme() { document.documentElement.dataset.theme = 'dark'; }
  applyTheme();

  // ---------- состояние ----------
  const STATES = {
    loading:  ['Запуск', 'Загружаюсь…', 'Подождите немного.'],
    idle:     ['Жду имя', null, 'Начните с имени, например: «Карина, открой блокнот». Я отзываюсь только на своё имя. Прежде чем искать в интернете или спрашивать нейросеть, я переспрошу.'],
    called:   ['Позвали', 'Да, слушаю вас…', 'Я услышала своё имя. Говорите команду.'],
    confirm:  ['Жду ответ', 'Слушаю ваш ответ…', 'Скажите «да», «нет» или «стоп». Имя говорить не нужно.'],
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
    // голограмма: режим по состоянию приложения (выход из сна Holo сам проигрывает как «просыпается»)
    if (window.Holo) {
      const asleep = !!sleeping || key === 'paused' || listening === false;
      const prev = hero._holoPrev;
      let mode = asleep ? 'sleep' : key === 'thinking' ? 'think' : key === 'speaking' ? 'speak' : (key === 'called' || key === 'confirm') ? 'called' : 'idle';
      Holo.setState(mode);
      // улыбка: первое появление после запуска (приветствие) и конец ответа без ошибки
      if (!asleep && ((prev === 'loading' && key === 'idle') || (prev === 'speaking' && key === 'idle'))) Holo.smile();
      hero._holoPrev = key;
    }
    $('#stateLabel').textContent = sleeping && key === 'idle' ? 'Режим сна' : label;
    $('#stateTitle').textContent = sleeping && key === 'idle' ? 'Я сплю' : (title || `Скажите «${cfg ? cfg.name : 'Карина'}, …»`);
    $('#stateHint').textContent = sleeping && key === 'idle' ? `Скажите «${cfg ? cfg.name : 'Карина'}, проснись».` : (detail || hint);
    $('#btnListen').textContent = listening === false ? 'Включить микрофон' : 'Выключить микрофон';
    $('#kvMode').textContent = sleeping ? 'Сон' : listening === false ? 'Пауза' : key === 'confirm' ? 'Жду ответ' : (key === 'idle' && /^Слушаю дальше/.test(detail || '')) ? 'Слушаю дальше' : 'Жду имя';
    if (typeof renderPills === 'function') renderPills();
  }

  // ---------- главная: приветствие, плашки, точки состояния, волна ----------
  function setDot(id, kind) { const d = $('#' + id); if (d && d.previousElementSibling) d.previousElementSibling.dataset.dot = kind; }
  function greeting() {
    const h = new Date().getHours();
    const g = h < 5 ? 'Доброй ночи' : h < 12 ? 'Доброе утро' : h < 18 ? 'Добрый день' : 'Добрый вечер';
    const n = cfg && cfg.profile && cfg.profile.name ? ', ' + cfg.profile.name : '';
    return g + n;
  }
  function renderPills() {
    const wake = !cfg || cfg.wake_required !== false;
    $('#pillMode').textContent = 'Режим: ' + (status.sleeping ? 'сон' : status.listening === false ? 'пауза' : wake ? 'по имени' : 'всегда слушаю');
    const r = $('#pillReady'); let kind = 'ok', text = 'Всё готово';
    if (status.state === 'error') { kind = 'bad'; text = 'Есть ошибка'; }
    else if (status.state === 'loading') { kind = 'warn'; text = 'Загрузка…'; }
    else if (status.voice_error) { kind = 'bad'; text = 'Голос с ошибкой'; }
    else if (status.voice && status.voice !== 'silero') { kind = 'warn'; text = 'Голос не готов'; }
    r.className = 'pill ' + kind; r.textContent = text;
    if (!$('#tab-home').classList.contains('firstrun')) $('#homeTitle').textContent = greeting();
  }
  (() => {
    const hs = [10,16,26,18,34,44,30,20,38,52,40,24,14,28,46,36,22,12,30,42,54,38,26,18,34,48,32,20,14,24,40,50,36,22,16,28,44,34,20,12,22,32,26,16,10,18,12,8];
    const w = $('#wave'); if (!w) return;
    hs.forEach((h, i) => { const b = el('span'); b.style.cssText = `--h:${h}px;--o:${(h / 54).toFixed(2)};--i:${i}`; w.append(b); });
  })();

  function renderStatus(st) {
    status = st;
    const chip = $('#chipNet');
    chip.className = 'chip ' + (st.online ? 'ok' : 'off');
    chip.lastElementChild.textContent = st.online ? 'Онлайн' : 'Офлайн';
    $('#kvNet').textContent = st.online ? 'есть' : 'нет — работаю автономно';
    const names = { tiny: 'минимальная', base: 'базовая', small: 'хорошая', medium: 'высокая' };
    $('#kvStt').textContent = (names[st.model] || st.model) + ' · офлайн';
    const silero = st.voice === 'silero';
    $('#kvVoice').textContent = silero && !st.voice_error ? 'Карина' : silero ? 'Карина (есть ошибка)' : 'системный (голос Карины не загружен)';
    setDot('kvNet', st.online ? 'ok' : 'warn'); setDot('kvVoice', silero && !st.voice_error ? 'ok' : st.voice_error ? 'bad' : 'warn'); setDot('kvStt', 'ok'); setDot('kvMode', 'ok'); setDot('kvVer', 'muted');
    $('#kvMics').textContent = st.mics_text || '—'; renderMicStatus(st.mics);
    const w = $('#ttsWarn'); const wt = st.tts_warning || st.voice_error || ''; w.hidden = !wt; w.textContent = wt;
    const p = $('#pluginWarn'); p.hidden = !(st.plugin_errors && st.plugin_errors.length); p.textContent = (st.plugin_errors || []).join('\n');
    renderState(st.state, '', st.sleeping, st.listening);
    renderPills();
  }

  // ---------- разговор ----------
  function addLog(kind, text, ts) {
    const log = $('#log');
    if (!logCount) log.textContent = '';
    logCount++;
    const row = el('div', 'msgline ' + kind);
    row.append(el('time', '', ts || ''), el('span', '', text));
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
  // кнопки окна и перетаскивание нужны сразу, не дожидаясь ответа get_state (при долгой загрузке он мог не прийти, и окно нельзя было ни двигать, ни закрыть)
  try { if (/Windows/i.test(navigator.userAgent)) setFrameless(true, false); } catch (_) {}
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
        $$('.mic-row').forEach(r => { const v = (ev.mics || {})[r.dataset.slot]; $('.meter-bar', r).style.width = Math.round((v || 0) * 100) + '%'; });
      } else if (ev.type === 'mics') renderMicStatus(ev.mics);
      else if (ev.type === 'mic_pick') pickMic(ev.slot);
      else if (ev.type === 'out_test') { flash($('#outMsg'), ev.error || 'Звук проигран', !!ev.error); $('#outTest').disabled = false; }
      else if (ev.type === 'winstate') setMaxed(ev.max);
      else if (ev.type === 'log') addLog(ev.kind, ev.text, ev.ts);
      else if (ev.type === 'goto') { const b = $(`#tabs [data-tab="${ev.tab}"]`); if (b) b.click(); if (ev.focus === 'tnCard') openVpnSettings(); }
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
      else if (ev.type === 'google_done') googleDone(ev);
      else if (ev.type === 'sync_done') syncDone(ev);
      else if (ev.type === 'sync_applied') { loadProfile(); loadCloud(); loadBrain(); refresh(); }
      else if (ev.type === 'components') compEvent(ev);
      else if (ev.type === 'silero_dl') silProgress(ev);
      else if (ev.type === 'silero_dl_done') silDone(ev);
      else if (ev.type === 'tunnel_dl') tnProgress(ev);
      else if (ev.type === 'tunnel_dl_done') tnDlDone(ev);
      else if (ev.type === 'model_progress') modelProgress(ev);
      else if (ev.type === 'model_done') { if (ev.error && ev.error !== 'cancelled') flash($('#mdMsg'), ev.error, true); loadModels(); }
      else if (ev.type === 'engine') renderEngine(ev);
      else if (ev.type === 'update_progress') updProgress(ev);
      else if (ev.type === 'update_ready') { updProgress({ done: 1, total: 1, final: true }); updStatus('Скачано и проверено. Запускаю установку: Карина закроется и откроется заново.'); }
      else if (ev.type === 'update_done') { updBusy(false); if (ev.error && ev.error !== 'cancelled') updStatus(ev.error, true); else updStatus(ev.error === 'cancelled' ? 'Загрузка отменена.' : ''); }
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
    if ((pr.deliver || []).length) {                           // готовые файлы «как вы присылали»: такой же архив / такие же файлы с правками
      const dl = el('div', 'pdeliver'); dl.append(el('div', 'pdl-h', '📦 Готово для вас'));
      for (const f of pr.deliver) {
        const line = el('div', 'fline'), t = el('div', 'ft'), m3 = el('div', 'fmsg');
        t.append(el('b', '', f.name), el('span', '', fmtSize(f.size)));
        const sh = el('button', 'btn primary', 'Показать в папке'); sh.onclick = async () => { const r = await call('project_reveal_file', pr.name, f.path); m3.textContent = r && !r.ok ? r.error : ''; };
        line.append(el('span', 'ficon', /\.zip$/i.test(f.name) ? '🗜' : '📄'), t, sh); dl.append(line, m3);
      }
      box.append(dl);
    }
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
      const x = el('button', 'x', '×'); x.title = 'Удалить'; x.onclick = async e => { e.stopPropagation(); if (await ask('Удалить этот чат?', { ok: 'Удалить', danger: true })) { await call('chat_delete', c.id); if (chCur === c.id) { chCur = null; showWelcome(); } loadChatList(); } };
      it.append(x); it.onclick = () => openChat(c.id); box.append(it);
    }
    return r;
  }
  async function openChat(id) {
    const c = await call('chat_get', id); if (!c) return;
    chCur = id; chBubble = null; $('#chMsgs').textContent = '';
    if (!c.messages.length) showWelcome();
    c.messages.forEach((m, i) => addMsg(m.role, m.text, m.id, m.steps, m.files, m.brain, m.files_in, m.project, m.activity, i === c.messages.length - 1 ? (m.choices || null) : null));
    const lv = await call('chat_live', id);            // ответ ещё идёт: вернуть пузырь, плитки картинок и строку состояния
    if (lv && chCur === id) {
      actNodes = []; actBox = null;
      chBubble = addMsg('assistant', '', lv.id); chBubble.classList.add('typing');
      if (lv.text) renderMd(chBubble, lv.text);
      const row = chBubble.parentElement;
      if (lv.plan && lv.plan.length) imgPlan(row, lv.plan);
      Object.values(lv.tiles || {}).forEach(t => imgEvent(row, t));
      setBusy(true); setStep(lv.step || 'Работаю…');
    }
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
    if (turnOn && !await ask('Разрешить Карине запускать написанный ею код для проверки?\n\nЭто не песочница: программа выполняется с вашими правами. Разрешены только python, pytest и node, без сети, подпроцессов и удаления файлов (Карина проверяет код перед запуском). Нужен установленный Python или Node.js.')) return;
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
      const dl = st.dl && st.dl.id === m.id;
      const t = el('div', 't');
      t.append(mHead(m.title, [m.recommended && ['Рекомендуется'], (m.fit === 'slow' || m.fit === 'heavy') && ['Медленно', true], m.installed && m.selected && ['Выбрана']]),
        mChips([`${m.size_gb} ГБ`, m.fit_label, m.eta]), el('span', 'wrap desc', m.desc), el('span', 'wrap desc', `Рисует так: ${m.plan}.`));
      if (dl) { const pb = el('div', 'pbar'); pb.append(el('i')); t.append(pb, el('span', 'plab', 'Подготовка…')); }
      it.append(t);
      let btns = [];
      if (dl) { const x = el('button', 'btn', 'Отмена'); x.onclick = () => call('localimg_cancel'); btns = [x]; }
      else if (m.installed) {
        if (!m.selected) { const x = el('button', 'btn primary', 'Выбрать'); x.onclick = async () => renderImg(await call('localimg_select', m.id)); btns.push(x); }
        const d = el('button', 'btn danger', 'Удалить'); d.onclick = async () => { if (await ask('Удалить модель с диска?', { ok: 'Удалить', danger: true })) { await call('localimg_delete', m.id); loadImg(); } }; btns.push(d);
      } else {
        const x = el('button', 'btn primary', 'Скачать'); x.onclick = async () => { x.disabled = true; await call('localimg_download', m.id); loadImg(); }; btns = [x];
      }
      it.append(mActs(...btns));
      box.append(it);
    });
  }
  function limgProgress(ev) {
    const row = $(`[data-lid="${ev.id}"]`); if (!row) return;
    const bar = $('.pbar i', row), lab = $('.plab', row); if (!bar || !lab) return;
    if (ev.stage === 'search') { lab.textContent = 'Ищу файлы…'; return; }
    const pct = ev.total ? Math.min(100, ev.done / ev.total * 100) : 0;
    bar.style.width = pct.toFixed(1) + '%';
    lab.textContent = (ev.stage === 'engine' ? 'Движок: ' : 'Модель: ') + dlLine('i:' + ev.id + ev.stage, ev);
  }
  async function loadImg() { renderImg(await call('images_state')); }
  $('#imgSave').onclick = async () => { const st = await call('images_set', $('#imgProvider').value, $('#imgUrl').value, $('#imgModel').value, $('#imgKey').value, $('#imgDirector').checked, +$('#imgVariants').value, $('#imgAsk').checked, $('#imgPower').value, $('#imgQuality').value, +$('#imgVramGb').value, +$('#imgThreads').value || 0, $('#imgUnload').value); renderImg(st); flash($('#imgMsg'), 'Сохранено'); };
  $('#imgHwRefresh').onclick = async () => { $('#imgHwRefresh').disabled = true; renderImg(await call('images_hw_refresh')); $('#imgHwRefresh').disabled = false; flash($('#imgMsg'), 'Видеокарта определена заново'); };
  ['#imgPower', '#imgQuality', '#imgVramGb', '#imgThreads', '#imgUnload'].forEach(id => { $(id).onchange = () => $('#imgSave').onclick(); });   // мощность применяется сразу
  $('#imgKeyClear').onclick = async () => { renderImg(await call('images_clear_key')); flash($('#imgMsg'), 'Ключ удалён'); };
  $('#imgTest').onclick = async () => { await $('#imgSave').onclick(); $('#imgTest').disabled = true; $('#imgMsg').classList.remove('err'); $('#imgMsg').textContent = 'Рисую пробную картинку…'; call('images_test'); };

  // ---------- обновления ----------
  // заметки к релизу (Markdown из CHANGELOG): заголовки, пункты, **жирное** и `код`; весь текст экранируется
  function mdInline(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>'); }
  function renderNotes(md) {
    const out = []; let list = false;
    const close = () => { if (list) { out.push('</ul>'); list = false; } };
    String(md || '').split('\n').forEach(raw => {
      const l = raw.trim(); if (!l) return;
      let m;
      if ((m = l.match(/^#{1,6}\s+(.*)$/))) { close(); out.push('<h4>' + mdInline(m[1]) + '</h4>'); }
      else if ((m = l.match(/^[*\-•]\s+(.*)$/))) {
        if (!list) { out.push('<ul>'); list = true; }
        const cls = /^\*{0,2}\s*(исправлено|исправлен|fix)/i.test(m[1]) ? 'fix' : /^\*{0,2}\s*(новое|добавлен|new)/i.test(m[1]) ? 'new' : '';
        out.push(`<li class="${cls}">` + mdInline(m[1]) + '</li>');
      } else { close(); out.push('<p>' + mdInline(l) + '</p>'); }
    });
    close(); return out.join('');
  }
  function showUpdate(u, popup = true) {
    upd = u; const bar = $('#updBar');
    if (!u || !u.available) { bar.hidden = true; $('#updLabel').textContent = 'Проверить обновления'; return; }
    $('#updLabel').textContent = 'Доступно обновление ●';
    $('#updStatus').hidden = true; $('#updStatus').classList.remove('err'); updBusy(false);
    $('#updTitle').textContent = `Доступна новая версия ${u.latest}`;
    $('#updVerLine').innerHTML = `У вас <b>${u.current}</b> → будет <b>${u.latest}</b>` + (u.size ? ` · ${dlSize(u.size)}` : '');
    $('#updNotes').innerHTML = renderNotes(u.notes) || '<p class="muted">Подробный список изменений — на странице релиза.</p>';
    $('#updInstall').textContent = u.can_install ? 'Обновить сейчас' : 'Открыть страницу загрузки';
    if (popup) { bar.hidden = false; $('#updNotes').scrollTop = 0; }
  }
  let updGot = false;
  function updBusy(on) {                 // идёт загрузка: «Позже» превращается в «Отменить», кнопка установки блокируется
    $('#updInstall').disabled = on; $('#updLater').disabled = false; $('#updLater').textContent = on ? 'Отменить загрузку' : 'Позже';
    $('#updProg').hidden = !on; $('#updCard').classList.toggle('busy', on);
    if (on) { updGot = false; delete dlRates.upd; $('#updPct').textContent = '0%'; $('#updPbar i').style.width = '0%'; $('#updSize').textContent = 'Подключаюсь…'; $('#updSpeed').textContent = '—'; $('#updEta').textContent = '—'; }
  }
  function updProgress(ev) {
    $('#updProg').hidden = false;
    const pct = ev.total ? Math.min(100, ev.done / ev.total * 100) : 0, bar = $('#updPbar');
    bar.classList.toggle('busy', !ev.total);
    $('i', bar).style.width = pct.toFixed(1) + '%'; $('#updPct').textContent = ev.total ? Math.floor(pct) + '%' : '…';
    if (ev.final) { $('#updSpeed').textContent = '—'; $('#updEta').textContent = 'готово'; return; }
    const v = dlRate('upd', ev.done);
    $('#updSize').textContent = ev.total ? dlPair(ev.done, ev.total) : dlSize(ev.done);
    $('#updSpeed').textContent = v ? dlSpeed(v) : 'считаю…';
    $('#updEta').textContent = v && ev.total ? dlEta((ev.total - ev.done) / v) : '—';
    if (!updGot) { updGot = true; updStatus(''); }
  }
  function updStatus(text, err) { const s = $('#updStatus'); s.hidden = !text; s.textContent = text || ''; s.classList.toggle('err', !!err); }
  async function checkUpdate(manual) {
    const lab = $('#updLabel'); if (manual) lab.textContent = 'Проверяю…';
    const u = await call('check_update', manual);
    lab.textContent = 'Проверить обновления';
    if (!u) return;
    if (u.error) { if (manual) { flash($('#updMsg'), u.error, true); note(u.error); } return; }
    if (u.available) showUpdate(u);              // окно со списком изменений и кнопками «Обновить сейчас» / «Позже»
    else if (manual) { showUpdate(null); flash($('#updMsg'), `Установлена последняя версия (${u.current})`); note(`У вас последняя версия: ${u.current}`); }
  }
  $('#btnUpdate').onclick = () => checkUpdate(true);
  $('#btnUpdate2').onclick = () => checkUpdate(true);
  $('#updLater').onclick = () => { if (!$('#updProg').hidden && $('#updCard').classList.contains('busy')) { $('#updLater').disabled = true; call('cancel_update'); } else $('#updBar').hidden = true; };
  $('#updInstall').onclick = async () => {
    updBusy(true); updStatus('Подготовка…');
    const r = await call('install_update');
    if (r && r.opened) { updBusy(false); updStatus('Страница загрузки открыта в браузере.'); }
    else if (!r || !r.ok) { updBusy(false); updStatus((r && r.error) || 'Не удалось', true); }
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#updBar').hidden && !$('#updCard').classList.contains('busy')) $('#updBar').hidden = true; });

  // ---------- бесплатные облачные модели ----------
  const CLOUD_STATE_LABEL = { ok: 'Готов', cooldown: 'Лимит', error: 'Сбой', nokey: 'Нет ключа', off: 'Выключен' };
  let cloudLast = null;
  function cloudRow(p, idx, total) {
    const row = el('div', 'cloud-row ' + p.state); row.dataset.pid = p.id;
    const head = el('div', 'cloud-head');
    const title = el('div', 'cloud-title'); title.append(el('b', '', p.name), el('span', 'tag' + (p.state === 'ok' ? '' : ' warn'), CLOUD_STATE_LABEL[p.state] || p.state));
    const sw = el('label', 'switch'), cb = el('input'); cb.type = 'checkbox'; cb.checked = p.enabled; cb.disabled = !(p.has_key || p.nokey); cb.title = (p.has_key || p.nokey) ? '' : 'Сначала введите ключ';
    sw.append(cb, el('span', 'sw'), el('span', '', 'Использовать'));
    cb.onchange = async () => { renderCloud(await call('cloud_save', p.id, { enabled: cb.checked })); loadBrain(); };
    const mv = el('div', 'cloud-move'); mv.append(el('span', 'cloud-pos', '№ ' + (idx + 1)));
    const up = el('button', 'btn', '↑'), dn = el('button', 'btn', '↓'); up.type = dn.type = 'button'; up.disabled = idx === 0; dn.disabled = idx === total - 1;
    up.title = 'Выше в очереди'; dn.title = 'Ниже в очереди';
    up.onclick = async () => renderCloud(await call('cloud_move', p.id, -1)); dn.onclick = async () => renderCloud(await call('cloud_move', p.id, 1));
    mv.append(up, dn);
    head.append(title, sw, mv);
    const body = el('div', 'cloud-body');
    body.append(el('div', 'muted small', p.note));
    const stat = el('div', 'cloud-stat ' + p.state); stat.append(el('i'), el('span', '', p.text + (p.used_today ? ` · ответов сегодня: ${p.used_today}` : ''))); body.append(stat);
    const f = el('div', 'form cloud-form');
    let nameI, urlI;
    if (p.id === 'custom') {
      nameI = el('input'); nameI.placeholder = 'Название (любое)'; nameI.value = p.name === 'Свой сервер' ? '' : p.name;
      urlI = el('input'); urlI.placeholder = 'Адрес API, например https://api.example.com/v1'; urlI.value = p.url || '';
      f.append(nameI, urlI);
    }
    const keyI = el('input'); keyI.type = 'password'; keyI.autocomplete = 'off'; keyI.placeholder = p.has_key ? 'Ключ сохранён · введите новый для замены' : p.nokey ? 'Ключ не нужен (если есть — можно вставить, лимиты выше)' : 'Вставьте ключ API';
    const modI = el('input'); modI.placeholder = p.model_auto ? `Модель: ${p.model_auto} (подобрана сама)` : 'Модель (можно оставить пустой — подберётся сама)'; modI.value = p.model || '';
    const dl = el('datalist'); dl.id = 'cm-' + p.id; modI.setAttribute('list', dl.id);
    f.append(keyI, modI, dl);
    const acts = el('div', 'form-actions');
    const save = el('button', 'btn primary', 'Сохранить'), test = el('button', 'btn', 'Проверить'), lst = el('button', 'btn', 'Список моделей');
    save.type = test.type = lst.type = 'button';
    acts.append(save, test, lst);
    if (p.signup) { const a = el('button', 'btn', 'Получить ключ ↗'); a.type = 'button'; a.onclick = () => call('open_url', p.signup); acts.append(a); }
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
    const act = $('#tab-cloud.on'); if (!act || !api || $('#cloudList').contains(document.activeElement)) return;
    call('cloud_state').then(st => { if (st && JSON.stringify(st) !== JSON.stringify(cloudLast) && !$('#cloudList').contains(document.activeElement)) renderCloud(st); });
  }, 5000);

  // общие кирпичики карточек в «Моделях»: заголовок с метками, чипы-характеристики, блок кнопок
  const mHead = (title, tags = []) => { const h = el('div', 'mh'); h.append(el('b', '', title)); tags.filter(Boolean).forEach(([txt, warn]) => h.append(el('span', 'tag' + (warn ? ' warn' : ''), txt))); return h; };
  const mChips = list => { const c = el('div', 'mchips'); list.filter(Boolean).forEach(x => c.append(el('span', 'mchip', x))); return c; };
  const mActs = (...btns) => { const a = el('div', 'act'); btns.filter(Boolean).forEach(b => a.append(b)); return a; };

  // ---------- мозг ----------
  const BRAIN = {
    local: ['Локальная модель', 'Встроенная модель на этом компьютере: бесплатно и без интернета. На слабом компьютере отвечает медленнее.'],
    claude: ['Claude', 'Отвечает Claude по вашему ключу: быстрее и умнее, но нужен интернет, и запросы оплачиваются у Anthropic.'],
    cloud: ['Облачные модели', 'Отвечают облачные модели (Gemini, DeepSeek или свой сервер) по вашим ключам — быстро даже на слабом компьютере. Когда у одной кончается лимит, подхватывает следующая. Нужен интернет.'],
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
    setDot('kvBrain', b.mode === 'local' && b.local && !b.local.ok ? 'warn' : 'ok');
    $('#kvBrain').textContent = b.mode === 'claude' ? b.claude.label : b.mode === 'cloud' ? 'Облачные модели' : b.mode === 'auto' ? 'Авто (локально + облако + Claude)' : 'Локальная модель';
    const box = $('#brainList'); if (!box) return; box.textContent = '';
    for (const m of ['local', 'cloud', 'claude', 'auto']) {
      const ok = m === 'auto' ? (b.local.ok || b.claude.ok || b.cloud.ok) : m === 'local' ? b.local.ok : m === 'cloud' ? b.cloud.ok : b.claude.ok;
      const it = el('div', 'item model brain ' + (b.mode === m ? 'ok sel' : ''));
      const t = el('div', 't'); t.append(mHead(m === 'claude' ? b.claude.label : BRAIN[m][0], [b.mode === m && ['Выбран']]), el('span', 'wrap desc', BRAIN[m][1]));
      t.append(el('span', 'wrap stat ' + (ok ? 'ok' : 'warn'), (ok ? '✔ ' : '✖ ') + brainWhy(b, m)));
      it.append(t);
      if (b.mode !== m) { const x = el('button', 'btn primary', 'Выбрать'); x.onclick = async () => { renderBrain(await call('brain_set', m)); }; it.append(mActs(x)); }
      box.append(it);
    }
    const sel = $('#brainModel'); sel.textContent = '';
    for (const m of b.claude_models) { const o = el('option', '', m.title); o.value = m.id; sel.append(o); }
    sel.value = b.claude_model;
  }
  async function loadBrain() { renderBrain(await call('brain_state')); }
  $('#brainModel').onchange = async e => renderBrain(await call('brain_set', '', e.target.value));
  const goTab = t => { const b = $(`#tabs [data-tab="${t}"]`); if (b) b.click(); };
  $('#claudeSave').onclick = async () => {
    const k = $('#apiKey').value.trim(); if (!k) { flash($('#brainMsg'), 'Вставьте ключ в поле выше', true); return; }
    const c = await call('save_claude_key', k); $('#apiKey').value = '';
    if (c) { fillSettings(c, null); flash($('#brainMsg'), 'Ключ сохранён'); loadBrain(); } else flash($('#brainMsg'), 'Не удалось сохранить', true);
  };
  $('#claudeGet').onclick = () => call('open_url', 'https://console.anthropic.com/settings/keys');
  $('#brainToCloud').onclick = () => goTab('cloud');
  $('#cloudToProfile').onclick = () => goTab('profile');
  $('#setToCloud').onclick = () => goTab('cloud');
  $('#brainTest').onclick = async () => { $('#brainTest').disabled = true; const o = $('#brainTestOut'); o.hidden = false; o.className = 'note'; o.textContent = 'Проверяю…'; await call('brain_test_claude'); };
  $('#chBrain').onclick = async () => {
    if (!brain) await loadBrain(); if (!brain) return;
    const order = ['local', 'cloud', 'claude', 'auto']; const next = order[(order.indexOf(brain.mode) + 1) % 4];
    renderBrain(await call('brain_set', next));
    const ok = next === 'auto' ? (brain.local.ok || brain.claude.ok || brain.cloud.ok) : next === 'local' ? brain.local.ok : next === 'cloud' ? brain.cloud.ok : brain.claude.ok;
    if (!ok) setStep('⚠ ' + brainWhy(brain, next), 6000);
  };

  // ---------- модели ----------
  // размеры и скорость скачивания: сами выбирают КБ / МБ / ГБ
  const dlNum = (v, d) => v.toFixed(d).replace('.', ',');
  const dlUnit = ref => ref >= 2 ** 30 ? 2 ** 30 : ref >= 2 ** 20 ? 2 ** 20 : 1024;           // единица берётся по общему размеру, чтобы «412,5 из 980,0 МБ» было в одних единицах
  const dlName = u => u === 2 ** 30 ? 'ГБ' : u === 2 ** 20 ? 'МБ' : 'КБ';
  const dlSize = (b, ref) => { const u = dlUnit(ref == null ? b : ref); return dlNum(b / u, u === 2 ** 30 ? 2 : u === 2 ** 20 ? 1 : 0) + ' ' + dlName(u); };
  const dlPair = (done, total) => { const u = dlUnit(total || done), d = u === 2 ** 30 ? 2 : u === 2 ** 20 ? 1 : 0; return `${dlNum(done / u, d)} из ${dlNum(total / u, d)} ${dlName(u)}`; };
  const dlSpeed = v => v >= 2 ** 20 ? dlNum(v / 2 ** 20, 1) + ' МБ/с' : Math.max(1, Math.round(v / 1024)) + ' КБ/с';
  const dlEta = sec => !isFinite(sec) || sec <= 0 ? '—' : sec < 5 ? 'пара секунд' : sec < 60 ? `~${Math.round(sec)} с` : sec < 3600 ? `~${Math.floor(sec / 60)} мин ${String(Math.round(sec % 60)).padStart(2, '0')} с` : `~${Math.floor(sec / 3600)} ч ${Math.round(sec % 3600 / 60)} мин`;
  const dlRates = {};
  function dlRate(key, done) {          // скорость сглаживается: мгновенные скачки не прыгают в глаза
    const now = performance.now(), r = dlRates[key];
    if (!r || done < r.b) return (dlRates[key] = { t: now, b: done, v: 0 }).v;
    if (now - r.t >= 700) { const inst = (done - r.b) / ((now - r.t) / 1000); r.v = r.v ? r.v * .75 + inst * .25 : inst; r.t = now; r.b = done; }
    return r.v;
  }
  const dlLine = (key, ev) => {          // «412,5 из 980,0 МБ · 42% · 8,4 МБ/с · ~1 мин 05 с»
    if (!ev.total) return dlSize(ev.done);
    const v = dlRate(key, ev.done), pct = Math.min(100, ev.done / ev.total * 100);
    return `${dlPair(ev.done, ev.total)} · ${pct.toFixed(0)}%` + (v ? ` · ${dlSpeed(v)} · ${dlEta((ev.total - ev.done) / v)}` : '');
  };
  const fmtGb = b => dlSize(b);
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
    lab.textContent = dlLine('m:' + ev.id, ev);
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
      t.append(mHead(m.title, [m.id === s.recommended && ['Рекомендуется'], m.active && ['Выбрана']]), mChips([m.tier_label, `${m.size_gb} ГБ`, `русский: ${m.ru}`, `память от ${m.ram} ГБ`]), el('span', 'wrap desc', m.desc));
      if (m.dl) { const pb = el('div', 'pbar'); pb.append(el('i')); t.append(pb, el('span', 'plab', 'Подготовка…')); }
      it.append(t);
      let btns = [];
      if (m.dl) { const x = el('button', 'btn', 'Отмена'); x.onclick = () => call('model_cancel', m.id); btns = [x]; }
      else if (m.installed) {
        if (!m.active) { const x = el('button', 'btn primary', 'Выбрать'); x.onclick = async () => { await call('model_select', m.id); loadModels(); }; btns.push(x); }
        const d = el('button', 'btn danger', 'Удалить'); d.onclick = async () => { if (await ask('Удалить модель с диска?', { ok: 'Удалить', danger: true })) { await call('model_delete', m.id); loadModels(); } }; btns.push(d);
      } else {
        const x = el('button', 'btn primary', 'Скачать'); x.onclick = async () => { x.disabled = true; await call('model_download', m.id); loadModels(); }; btns = [x];
      }
      it.append(mActs(...btns));
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

  const fmtMb = b => Math.round(b / 2 ** 20) + ' МБ';

  // ---------- профиль ----------
  let kinds = {};
  const pf = () => $('#profForm');
  const TZ = [['Europe/Kaliningrad', 'Калининград (UTC+2)'], ['Europe/Moscow', 'Москва, Санкт-Петербург (UTC+3)'], ['Europe/Samara', 'Самара (UTC+4)'], ['Asia/Yekaterinburg', 'Екатеринбург (UTC+5)'],
    ['Asia/Omsk', 'Омск (UTC+6)'], ['Asia/Novosibirsk', 'Новосибирск (UTC+7)'], ['Asia/Krasnoyarsk', 'Красноярск (UTC+7)'], ['Asia/Irkutsk', 'Иркутск (UTC+8)'], ['Asia/Yakutsk', 'Якутск (UTC+9)'],
    ['Asia/Vladivostok', 'Владивосток (UTC+10)'], ['Asia/Magadan', 'Магадан (UTC+11)'], ['Asia/Kamchatka', 'Камчатка (UTC+12)'],
    ['Europe/Kyiv', 'Киев (UTC+2/+3)'], ['Europe/Minsk', 'Минск (UTC+3)'], ['Asia/Tbilisi', 'Тбилиси (UTC+4)'], ['Asia/Yerevan', 'Ереван (UTC+4)'], ['Asia/Baku', 'Баку (UTC+4)'],
    ['Asia/Tashkent', 'Ташкент (UTC+5)'], ['Asia/Almaty', 'Алматы (UTC+5)'], ['Asia/Bishkek', 'Бишкек (UTC+6)'], ['Europe/Istanbul', 'Стамбул (UTC+3)'], ['Asia/Dubai', 'Дубай (UTC+4)'],
    ['Asia/Kolkata', 'Индия (UTC+5:30)'], ['Asia/Bangkok', 'Бангкок (UTC+7)'], ['Asia/Shanghai', 'Китай (UTC+8)'], ['Asia/Tokyo', 'Токио (UTC+9)'], ['Asia/Seoul', 'Сеул (UTC+9)'],
    ['Australia/Sydney', 'Сидней (UTC+10/+11)'], ['Pacific/Auckland', 'Окленд (UTC+12/+13)'], ['Europe/London', 'Лондон (UTC+0/+1)'], ['Europe/Berlin', 'Берлин, Париж, Рим (UTC+1/+2)'],
    ['Europe/Warsaw', 'Варшава (UTC+1/+2)'], ['Africa/Cairo', 'Каир (UTC+2)'], ['America/Sao_Paulo', 'Сан-Паулу (UTC−3)'], ['America/New_York', 'Нью-Йорк (UTC−5/−4)'],
    ['America/Chicago', 'Чикаго (UTC−6/−5)'], ['America/Denver', 'Денвер (UTC−7/−6)'], ['America/Los_Angeles', 'Лос-Анджелес (UTC−8/−7)']];
  const browserTz = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) { return ''; } };
  function fillSelect(sel, pairs, val) { sel.textContent = ''; pairs.forEach(([k, v]) => { const o = el('option', '', v); o.value = k; sel.append(o); }); sel.value = val; }
  function fillTz(val) {
    const sel = pf().timezone, list = [['', 'Как на этом компьютере']].concat(TZ);
    if (val && !TZ.some(x => x[0] === val)) list.push([val, val]);
    fillSelect(sel, list, val || '');
  }
  function petRow(p) {
    const r = document.createElement('div'); r.className = 'row pet'; r.style.alignItems = 'center';
    const opts = Object.entries(kinds).map(([k, v]) => `<option value="${k}"${k === (p.kind || 'dog') ? ' selected' : ''}>${v}</option>`).join('');
    r.innerHTML = `<select class="pk" style="width:130px;flex:none">${opts}</select><input class="pn" maxlength="30" placeholder="Кличка" style="flex:1;width:auto;min-width:120px"><input class="pd" type="number" min="1" max="31" placeholder="день р." style="width:90px;flex:none"><select class="pm" style="width:120px;flex:none"><option value="0">месяц</option>${['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'].map((m, i) => `<option value="${i + 1}">${m}</option>`).join('')}</select><button type="button" class="btn danger pr">×</button>`;
    $('.pn', r).value = p.name || ''; $('.pd', r).value = p.birth_day || ''; $('.pm', r).value = p.birth_month || 0;
    $('.pr', r).onclick = () => r.remove();
    return r;
  }
  let profBusy = false;
  function fillProfile(d) {
    kinds = d.kinds; const p = d.profile, f = pf();
    // не затираем то, что человек сейчас печатает в форме
    if (!pf().contains(document.activeElement) || !profBusy) {
      fillSelect(f.gender, Object.entries(d.genders || {}), p.gender || '');
      fillSelect(f.partner_kind, Object.entries(d.partner_kinds || {}), (p.partner || {}).kind || 'none');
      f.name.value = p.name; f.full_name.value = p.full_name || ''; f.birth_day.value = p.birth_day || ''; f.birth_month.value = p.birth_month || 0; f.birth_year.value = p.birth_year || '';
      f.country.value = p.country || ''; f.city.value = p.city; fillTz(p.timezone || ''); f.occupation.value = p.occupation || ''; f.interests.value = p.interests || ''; f.about.value = p.about || '';
      const pr = p.partner || {}; f.partner_name.value = pr.name || ''; f.partner_day.value = pr.birth_day || ''; f.partner_month.value = pr.birth_month || 0;
      f.share_with_llm.checked = p.share_with_llm !== false;
      const l = $('#petList'); l.innerHTML = ''; (p.pets || []).forEach(x => l.appendChild(petRow(x)));
      partnerToggle();
    }
    const g = p.google || {}, signed = !!g.email;
    $('#gSigned').hidden = !signed; $('#gUnsigned').hidden = signed;
    $('#gName').textContent = g.name || ''; $('#gMail').textContent = g.email || ''; if (g.picture) $('#gPic').src = g.picture; else $('#gPic').removeAttribute('src');
    $('#gClient').value = d.google.client_id || ''; $('#gSecret').placeholder = d.google.has_secret ? 'сохранён (введите новый, чтобы заменить)' : 'оставьте пустым, если Google не выдал';
    $('#gSetup').hidden = !!d.google.bundled && !d.google.client_id;
    $('#gIn').hidden = !!d.signing_in; $('#gCancel').hidden = !d.signing_in;
    renderSync(d.sync);
  }
  function partnerToggle() { const none = pf().partner_kind.value === 'none'; ['partner_name', 'partner_day', 'partner_month'].forEach(n => { pf()[n].disabled = none; }); }
  async function loadProfile() { const d = await call('get_profile'); if (d) fillProfile(d); }
  $('#petAdd').onclick = () => { if (!Object.keys(kinds).length) return; $('#petList').appendChild(petRow({ kind: 'dog' })); };
  pf().addEventListener('input', () => { profBusy = true; });
  pf().partner_kind.addEventListener('change', partnerToggle);
  $('#tzAuto').onclick = () => { const z = browserTz(); if (z) { fillTz(z); flash($('#profMsg'), 'Часовой пояс: ' + z + '. Нажмите «Сохранить профиль».'); } else flash($('#profMsg'), 'Не удалось определить пояс', true); };
  pf().addEventListener('submit', async e => {
    e.preventDefault(); const f = e.target;
    const pets = $$('#petList .pet').map(r => ({ kind: $('.pk', r).value, name: $('.pn', r).value.trim(), birth_day: Number($('.pd', r).value) || 0, birth_month: Number($('.pm', r).value) || 0 })).filter(x => x.name);
    const d = await call('save_profile', { name: f.name.value, full_name: f.full_name.value, gender: f.gender.value, birth_day: Number(f.birth_day.value) || 0, birth_month: Number(f.birth_month.value) || 0, birth_year: Number(f.birth_year.value) || 0,
      country: f.country.value, city: f.city.value, timezone: f.timezone.value, occupation: f.occupation.value, interests: f.interests.value, about: f.about.value,
      partner: { kind: f.partner_kind.value, name: f.partner_name.value, birth_day: Number(f.partner_day.value) || 0, birth_month: Number(f.partner_month.value) || 0 },
      share_with_llm: f.share_with_llm.checked, pets });
    profBusy = false;
    if (d) { fillProfile(d); flash($('#profMsg'), 'Сохранено'); } else flash($('#profMsg'), 'Не удалось сохранить', true);
  });
  const gMsg = (t, bad) => { const o = $('#gMsg'); o.hidden = !t; o.className = 'note' + (bad ? ' bad' : ''); o.textContent = t || ''; if (t && bad) { flash($('#setMsg'), t, true); try { o.scrollIntoView({ block: 'center' }); } catch (e) {} } };
  $('#gSave').onclick = async () => { const d = await call('save_google_client', $('#gClient').value, $('#gSecret').value); if (d) { $('#gSecret').value = ''; fillProfile(d); gMsg('Сохранено. Теперь нажмите «Войти через Google».'); } };
  $('#gIn').onclick = async () => { gMsg('Открываю браузер… выберите аккаунт Google и разрешите доступ к данным приложения.'); $('#gIn').hidden = true; $('#gCancel').hidden = false; if (!(await call('google_login'))) loadProfile(); };
  $('#gCancel').onclick = () => call('google_cancel');
  $('#gOut').onclick = async () => { const d = await call('google_logout'); if (d) { profBusy = false; fillProfile(d); gMsg(''); } };
  function googleDone(ev) {
    profBusy = false;
    gMsg(ev.error || 'Готово: вы вошли.', !!ev.error); loadProfile();
    const k = ev.sync && ev.sync.kind;
    if (!ev.error && k === 'new_code') { syShow = true; note('Ваш персональный код:\n\n' + ev.sync.code + '\n\nЗапишите его в надёжное место. Он понадобится, чтобы войти в Карину с другого компьютера: после входа через тот же аккаунт Google введите код — и профиль, ключи облачных моделей и ключ Claude подтянутся. Увидеть код снова можно здесь же, в «Профиле».', 'Синхронизация включена'); }
    else if (!ev.error && k === 'need_code') { syMsg('В вашем Google Drive уже есть данные Карины. Введите персональный код ниже.'); }
    else if (!ev.error && k === 'resumed') { syMsg('Синхронизация продолжена.', 'ok'); }
  }

  // ---------- синхронизация между компьютерами ----------
  let syShow = false, syState = null;
  const syMsg = (t, kind) => { const o = $('#syMsg'); o.hidden = !t; o.className = 'note' + (kind === 'bad' ? ' bad' : kind === 'ok' ? ' ok' : ''); o.textContent = t || ''; };
  const syAgo = ts => { if (!ts) return 'ещё не было'; const s = Math.round(Date.now() / 1000 - ts); return s < 60 ? 'только что' : s < 3600 ? Math.round(s / 60) + ' мин назад' : s < 86400 ? Math.round(s / 3600) + ' ч назад' : new Date(ts * 1000).toLocaleString('ru-RU'); };
  const OSN = { Windows: 'Windows', Darwin: 'macOS', Linux: 'Linux' };
  function renderSync(st) {
    if (!st) return; syState = st;
    const act = st.enabled, pend = st.pending && st.signed && st.access;
    $('#syOff').hidden = st.signed; $('#syNoAccess').hidden = !(st.signed && !st.access && !st.enabled);
    $('#syPending').hidden = !pend; $('#syActive').hidden = !act;
    $('#syCode').textContent = act ? (syShow ? st.code : st.code.replace(/[A-Z0-9]/g, '•').replace(/^•••/, 'KRN')) : '';
    $('#syShow').textContent = syShow ? 'Скрыть' : 'Показать';
    const box = $('#syDevices'); box.textContent = '';
    const n = st.devices.length;
    $('#syDevHead').textContent = n > 1 ? `Авторизовано на ${n} устройствах` : 'Устройства';
    st.devices.forEach(d => {
      const it = el('div', 'item sy-dev ' + (d.this ? 'ok' : '')); const t = el('div', 't');
      t.append(el('b', '', d.name + (d.this ? ' · этот компьютер' : '')), el('span', '', (OSN[d.os] || d.os || '') + ' · синхронизация: ' + syAgo(d.seen)));
      it.append(t);
      if (!d.this) { const x = el('button', 'btn danger', 'Отключить'); x.type = 'button'; x.onclick = async () => { if (await ask(`Отключить «${d.name}»? Этот компьютер потеряет доступ к синхронизации, пока в нём снова не введут код.`, { title: 'Отключить устройство' })) { syMsg('Отключаю…'); await call('sync_remove_device', d.id); } }; it.append(x); }
      box.append(it);
    });
    $('#syLast').textContent = 'Последняя синхронизация: ' + syAgo(st.last_sync) + (st.busy ? ' · идёт…' : '');
    if (st.error && !$('#syMsg').textContent) syMsg(st.error, 'bad');
  }
  function syncDone(ev) {
    loadProfile();
    if (ev.error) { syMsg(ev.error, 'bad'); return; }
    if (ev.quiet) return;
    if (ev.label === 'code') syMsg('Готово: этот компьютер подключён, профиль и ключи загружены.', 'ok');
    else if (ev.label === 'reset') { syShow = true; syMsg(''); note('Новый персональный код:\n\n' + (ev.result || '') + '\n\nЗапишите его. Другие компьютеры теперь нужно подключить заново этим кодом.', 'Начато заново'); }
    else if (ev.label === 'now') syMsg('Синхронизировано.', 'ok');
    else if (ev.label === 'off') syMsg('Этот компьютер отключён от синхронизации.', 'ok');
    else if (ev.label === 'remove') syMsg('Устройство отключено.', 'ok');
  }
  $('#syShow').onclick = () => { syShow = !syShow; if (syState) renderSync(syState); };
  $('#syCopy').onclick = async () => { const c = syState && syState.code; if (!c) return; try { await navigator.clipboard.writeText(c); syMsg('Код скопирован.', 'ok'); } catch (_) { syShow = true; renderSync(syState); syMsg('Не удалось скопировать: код показан выше, скопируйте его вручную.', 'bad'); } };
  $('#syCodeOk').onclick = async () => { syMsg('Проверяю код…'); if (!(await call('sync_enter_code', $('#syCodeIn').value))) syMsg('Подождите: синхронизация уже идёт.', 'bad'); };
  $('#syCodeIn').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('#syCodeOk').click(); } });
  $('#syReset').onclick = async () => { if (await ask('Старые данные в Google Drive будут заменены данными этого компьютера, а прежний код перестанет работать. Остальные компьютеры придётся подключить заново.', { title: 'Начать заново?' })) { syMsg('Создаю…'); await call('sync_reset'); } };
  $('#syNow').onclick = async () => { syMsg('Синхронизирую…'); if (!(await call('sync_now'))) syMsg('Синхронизация уже идёт.', 'bad'); };
  $('#syOffBtn').onclick = async () => { if (await ask('Этот компьютер перестанет обмениваться данными. Профиль и ключи на нём останутся. Подключиться снова можно по персональному коду.', { title: 'Отключить синхронизацию?' })) { syMsg('Отключаю…'); await call('sync_disconnect'); } };

  // ---------- первый запуск: докачка компонентов ----------
  const fmtEta = sec => !sec ? 'Считаю скорость…' : sec < 60 ? 'Осталось меньше минуты' : sec < 3600 ? `Осталось около ${Math.round(sec / 60)} мин` : `Осталось около ${Math.floor(sec / 3600)} ч ${Math.round(sec % 3600 / 60)} мин`;
  const frSet = (step, st, label) => { const li = $(`#frSteps [data-step="${step}"]`); if (!li) return; li.dataset.st = st; $('em', li).textContent = label; };
  function frShow(on) {
    $('#frCard').hidden = !on; $('#tab-home').classList.toggle('firstrun', on);
    if (on) { $('#homeEyebrow').textContent = 'Главная'; $('#homeTitle').textContent = 'Первый запуск'; } else { renderPills(); }
  }
  function compEvent(ev) {
    const card = $('#frCard'), mb = b => Math.round(b / 2 ** 20), pct = ev.total ? Math.min(100, ev.done / ev.total * 100) : 0;
    if (ev.stage === 'done') { frShow(false); loadSil(); return; }
    frShow(true);
    card.classList.toggle('err', ev.stage === 'error'); card.classList.toggle('paused', ev.stage === 'paused');
    const bar = $('#frBar'); bar.classList.toggle('busy', ev.stage === 'offline' || (ev.stage === 'download' && !ev.total));
    $('i', bar).style.width = pct.toFixed(1) + '%'; $('#frPct').textContent = Math.round(pct) + '%';
    $('#frErr').hidden = ev.stage !== 'error'; $('#frRetry').hidden = ev.stage !== 'error'; $('#frPause').hidden = ev.stage === 'error';
    if (ev.stage !== 'paused') $('#frPause').textContent = 'Пауза';
    const L = $('#frLabel'), T = $('#frTitle'), H = $('#frHint');
    if (ev.stage === 'download') {
      L.textContent = 'Настраиваюсь'; T.textContent = 'Скачиваю компоненты'; H.textContent = 'Это нужно один раз. Дальше Карина запускается сразу и работает без загрузок.';
      $('#frNums').textContent = ev.total ? `${mb(ev.done)} из ${mb(ev.total)} МБ` : `${mb(ev.done)} МБ`;
      $('#frEta').textContent = ev.speed ? `${fmtEta(ev.eta)} · ${fmtSpeed(ev.speed)}` : 'Считаю скорость…';
      frSet('download', 'run', 'скачиваю'); frSet('verify', 'wait', 'в очереди'); frSet('extract', 'wait', 'в очереди');
    } else if (ev.stage === 'verify') {
      T.textContent = 'Проверяю файлы'; $('#frNums').textContent = 'Контрольная сумма SHA-256'; $('#frEta').textContent = 'Почти готово';
      frSet('download', 'done', 'готово'); frSet('verify', 'run', 'проверяю'); frSet('extract', 'wait', 'в очереди');
    } else if (ev.stage === 'extract') {
      T.textContent = 'Устанавливаю компоненты'; $('#frNums').textContent = 'Распаковка файлов'; $('#frEta').textContent = 'Ещё немного';
      frSet('download', 'done', 'готово'); frSet('verify', 'done', 'готово'); frSet('extract', 'run', 'устанавливаю');
    } else if (ev.stage === 'paused') {
      L.textContent = 'Пауза'; T.textContent = 'Загрузка на паузе'; $('#frEta').textContent = 'Нажмите «Продолжить» — докачаю с того же места'; $('#frPause').textContent = 'Продолжить';
    } else if (ev.stage === 'retry') {
      H.textContent = 'Связь прервалась. Пробую ещё раз, уже скачанное не пропадёт.'; $('#frEta').textContent = 'Переподключаюсь…';
    } else if (ev.stage === 'offline') {
      L.textContent = 'Жду интернет'; T.textContent = 'Нет подключения'; H.textContent = 'Как только появится интернет, продолжу загрузку. Если GitHub закрыт, включите VPN.'; $('#frNums').textContent = '—'; $('#frEta').textContent = 'Жду сеть…';
    } else if (ev.stage === 'error') {
      L.textContent = 'Ошибка'; T.textContent = 'Не удалось скачать компоненты'; H.textContent = 'Без них не работает голос Карины. Уже скачанное сохранено.';
      $('#frErr').textContent = ev.error || 'Неизвестная ошибка'; frSet('download', 'bad', 'ошибка'); $('#frEta').textContent = 'Нажмите «Повторить»';
    }
  }
  $('#frPause').onclick = async () => { const on = $('#frPause').textContent === 'Пауза'; const r = await call('components_pause', on); $('#frPause').textContent = r && r.paused ? 'Продолжить' : 'Пауза'; if (on) compEvent({ stage: 'paused', done: 0, total: 0 }); };
  $('#frRetry').onclick = async () => { $('#frRetry').hidden = true; $('#frErr').hidden = true; $('#frCard').classList.remove('err'); $('#frTitle').textContent = 'Подключаюсь…'; await call('components_retry'); };
  $('#frHide').onclick = () => call('win_minimize');

  // ---------- голос Silero ----------
  const fmtSpeed = v => v >= 1048576 ? (v / 1048576).toFixed(1).replace('.', ',') + ' МБ/с' : Math.max(1, Math.round(v / 1024)) + ' КБ/с';
  let silT = { t: 0, b: 0, v: 0 };
  function silView(st) {
    const box = $('#silBox'), err = st.error || '', comp = st.components || {};
    $('#silStatus').classList.remove('err');
    if (st.bundled) {                      // голос лежит в самой программе: ни скачивать, ни обновлять не нужно
      $('#silStatus').textContent = `Голос встроен в программу (${fmtMb(st.size)}). Работает без интернета${st.torch ? '.' : '. ' + (st.hint || 'Не хватает пакета torch.')}`;
      $('#silDl').hidden = $('#silDel').hidden = $('#silUpd').hidden = $('#silCancel').hidden = true; $('#silBar').hidden = true; box.classList.add('bundled'); box.classList.remove('err');
      return;
    }
    let text;
    if (st.downloading) text = 'Скачиваю…';
    else if (!st.ready && comp.running) text = 'Голос приходит в архиве компонентов вместе с torch и моделями речи. Прогресс — на главной' + (comp.paused ? ' (загрузка на паузе).' : '.');
    else if (err) text = (st.ready ? 'Голос есть, но не работает: ' : 'Голос не готов: ') + err;
    else if (st.ready) text = `Скачан (${fmtMb(st.size)})${st.torch ? '. Всё готово.' : '. ' + (st.hint || 'Не хватает пакета torch.')}`;
    else text = 'Не скачан: пока Карина говорит обычным голосом.';
    $('#silStatus').textContent = text;
    box.classList.toggle('err', !!err && !st.downloading);
    $('#silDl').textContent = err && !st.ready ? 'Повторить' : 'Скачать голос';
    $('#silDl').hidden = st.ready || !!st.downloading || !!comp.running;
    $('#silCancel').hidden = !st.downloading;
    $('#silDel').hidden = !st.ready || !!st.downloading; $('#silUpd').hidden = !st.ready || !!st.downloading;
    if (!st.downloading) $('#silBar').hidden = true;
  }
  async function loadSil() { const st = await call('silero_status'); if (st) silView(st); }
  function silProgress(ev) {
    const pb = $('#silBar'); pb.hidden = false; $('#silDl').hidden = $('#silUpd').hidden = $('#silDel').hidden = true; $('#silCancel').hidden = false;
    $('#silBox').classList.remove('err');
    const now = Date.now();
    if (ev.stage === 'download') {
      if (!silT.t || ev.done < silT.b) silT = { t: now, b: ev.done, v: 0 };
      else if (now - silT.t >= 1000) { const inst = (ev.done - silT.b) / ((now - silT.t) / 1000); silT = { t: now, b: ev.done, v: silT.v ? silT.v * .7 + inst * .3 : inst }; }
    }
    $('i', pb).style.width = (ev.total ? ev.done / ev.total * 100 : 0).toFixed(1) + '%';
    $('#silStatus').textContent = ev.stage === 'reconnect' ? `Связь прервалась, переподключаюсь… (скачано ${fmtMb(ev.done)}${ev.total ? ' из ' + fmtMb(ev.total) : ''})`
      : ev.total ? `Скачано ${fmtMb(ev.done)} из ${fmtMb(ev.total)}${silT.v ? ' · ' + fmtSpeed(silT.v) : ''}` : 'Подключаюсь…';
  }
  function silDone(ev) { silT = { t: 0, b: 0, v: 0 }; if (ev.error && ev.error !== 'cancelled') flash($('#setMsg'), ev.error, true); loadSil(); }
  $('#silDl').onclick = async () => { $('#silDl').disabled = true; $('#silStatus').textContent = 'Подключаюсь…'; if (!(await call('silero_download'))) loadSil(); };
  $('#silUpd').onclick = async () => { $('#silUpd').disabled = true; $('#silStatus').textContent = 'Подключаюсь…'; if (!(await call('silero_download', true))) loadSil(); };
  $('#silCancel').onclick = async () => { $('#silCancel').disabled = true; await call('silero_cancel'); setTimeout(() => { $('#silCancel').disabled = false; loadSil(); }, 600); };
  $('#silDel').onclick = async () => { const st = await call('silero_delete'); if (st) silView(st); };

  // ---------- свой VPN и прокси ----------
  let tnSt = null;
  const tnMsg = (t, bad) => { const o = $('#tnMsg'); o.hidden = !t; o.className = 'note' + (bad ? ' bad' : ''); o.textContent = t || ''; };
  function tnView(st) {
    if (!st) return; tnSt = st;
    const eng = $('#tnEngine');
    eng.className = 'note' + (st.win ? '' : ' bad');
    eng.textContent = !st.win ? 'Встроенный VPN работает только в Windows.' : st.downloading ? 'Скачиваю движок…' : st.engine ? `Движок sing-box ${st.version} установлен.` : 'Движок ещё не скачан. Нажмите «Скачать движок» (около 12 МБ, один раз).';
    $('#tnDl').hidden = st.engine || !st.win; $('#tnDl').disabled = !!st.downloading; $('#tnDel').hidden = !st.engine;
    if (!st.downloading) $('#tnBar').hidden = true;
    const sel = $('#tnServer'); sel.innerHTML = '';
    st.servers.forEach(s => { const o = document.createElement('option'); o.value = s.id; o.textContent = `${s.name} (${s.kind})`; sel.appendChild(o); });
    if (!st.servers.length) { const o = document.createElement('option'); o.textContent = 'Серверов нет: добавьте ссылку выше'; o.value = ''; sel.appendChild(o); }
    sel.value = st.active; sel.disabled = !st.servers.length;
    $('#tnMode').value = st.mode; $('#tnScope').value = st.scope; $('#tnApps').value = (st.apps || []).join(', ');
    $('#tnApps').disabled = st.scope !== 'apps'; $('#tnMode').disabled = st.scope === 'apps';
    $('#tnConfirm').checked = !!st.confirm; $('#tnDirectRu').checked = !!st.direct_ru; $('#tnCloud').checked = st.cloud !== false;
    $('#tnRefresh').hidden = !st.has_sub; $('#tnRemove').disabled = $('#tnClear').disabled = !st.servers.length;
    const run = st.status && st.status.running;
    $('#tnStart').disabled = !st.servers.length || !st.engine || !st.win; $('#tnStart').textContent = run ? 'Перезапустить' : 'Включить'; $('#tnStop').disabled = !run;
    hvView(st);
    $('#tnStatus').className = 'note' + (run ? ' ok' : ''); $('#tnStatus').textContent = st.text + (st.cloud_on && !run ? ' Для облачных моделей работает отдельный прокси (остальной трафик компьютера идёт напрямую).' : '') + ((!st.admin && (st.mode === 'vpn' || st.scope === 'apps')) ? ' Для режима VPN Карина должна быть запущена от имени администратора.' : '');
  }
  // ---------- VPN на главной ----------
  let hvBusy = false;
  function hvView(st) {
    if (!st) return;
    const run = !!(st.status && st.status.running), has = st.servers.length > 0;
    const stEl = $('#hvState'); stEl.dataset.on = hvBusy ? 'busy' : run ? '1' : '0';
    $('#hvTitle').textContent = hvBusy ? 'Подключаюсь…' : run ? 'VPN включён' : 'VPN выключен';
    $('#hvSub').textContent = run ? st.text : has ? 'Сервер выбран, можно включать' : 'Серверов пока нет';
    const sel = $('#hvServer');
    if (document.activeElement !== sel) {
      sel.innerHTML = '';
      st.servers.forEach(s => { const o = document.createElement('option'); o.value = s.id; o.textContent = s.name; sel.appendChild(o); });
      if (!has) { const o = document.createElement('option'); o.value = ''; o.textContent = 'Нет серверов'; sel.appendChild(o); }
      sel.value = st.active;
    }
    sel.disabled = !has;
    if (document.activeElement !== $('#hvScope')) $('#hvScope').value = st.scope;
    $('#hvScope').disabled = !has;
    const tg = $('#hvToggle'); tg.classList.toggle('on', run); tg.textContent = run ? 'Выключить VPN' : 'Включить VPN';
    tg.disabled = hvBusy || !has || !st.win || (!run && !st.engine);
    let note = '';
    if (!st.win) note = 'Встроенный VPN работает только в Windows.';
    else if (!has) note = 'Добавьте сервер или подписку в настройках VPN.';
    else if (!st.engine) note = 'Движок VPN ещё не скачан: откройте настройки VPN и нажмите «Скачать движок».';
    else if (st.scope === 'apps' && !(st.apps || []).length) note = 'Список приложений пуст. Скажите «Карина, добавь приложения в впн» или впишите их в настройках VPN.';
    else if (!st.admin && (st.mode === 'vpn' || st.scope === 'apps')) note = 'Для режима VPN Карина должна быть запущена от имени администратора (Настройки → «Окно и оформление»).';
    const n = $('#hvNote'); n.hidden = !note; n.className = 'note warn'; n.textContent = note;
  }
  async function hvLoad() { hvView(await call('tunnel_state')); }
  function openVpnSettings() {
    const b = $('#tabs [data-tab="settings"]'); if (b) b.click();
    setTimeout(() => { const c = $('#tnCard'); if (c) { c.scrollIntoView({ behavior: 'smooth', block: 'start' }); c.classList.add('flash'); setTimeout(() => c.classList.remove('flash'), 1600); } }, 80);
  }
  $('#hvSettings').onclick = openVpnSettings;
  $('#hvServer').addEventListener('change', async () => tnReply(await call('tunnel_set', { active: $('#hvServer').value })));
  $('#hvScope').addEventListener('change', async () => tnReply(await call('tunnel_set', { scope: $('#hvScope').value })));
  $('#hvToggle').onclick = async () => {
    const run = tnSt && tnSt.status && tnSt.status.running;
    hvBusy = true; $('#hvToggle').disabled = true; if (tnSt) hvView(tnSt);
    const r = await call(run ? 'tunnel_stop' : 'tunnel_start');
    hvBusy = false;
    if (r && r.state) { tnView(r.state); }
    const note = $('#hvNote');
    if (r && !r.ok) { note.hidden = false; note.className = 'note bad'; note.textContent = r.error; } else hvLoad();
  };
  async function loadTunnel() { tnView(await call('tunnel_state')); }
  const tnReply = r => { if (!r) return; if (r.state) tnView(r.state); tnMsg(r.ok ? '' : r.error, !r.ok); return r; };
  async function tnSave() {
    tnReply(await call('tunnel_set', { active: $('#tnServer').value, mode: $('#tnMode').value, scope: $('#tnScope').value, apps: $('#tnApps').value, confirm: $('#tnConfirm').checked, direct_ru: $('#tnDirectRu').checked, cloud: $('#tnCloud').checked }));
  }
  ['tnServer', 'tnMode', 'tnScope', 'tnConfirm', 'tnDirectRu', 'tnCloud'].forEach(id => $('#' + id).addEventListener('change', tnSave));
  $('#tnApps').addEventListener('change', tnSave);
  $('#tnAdd').onclick = async () => {
    $('#tnAdd').disabled = true; tnMsg('Добавляю…');
    const r = tnReply(await call('tunnel_add', $('#tnText').value)); $('#tnAdd').disabled = false;
    if (r && r.ok) { $('#tnText').value = ''; tnMsg(`Добавлено серверов: ${r.result.added}.` + (r.result.errors.length ? ` Не поняла ссылок: ${r.result.errors.length}.` : '')); }
  };
  $('#tnRefresh').onclick = async () => { tnMsg('Обновляю…'); const r = tnReply(await call('tunnel_refresh')); if (r && r.ok) tnMsg(`Серверов в подписке: ${r.result.added}.`); };
  $('#tnRemove').onclick = async () => tnReply(await call('tunnel_remove', $('#tnServer').value));
  $('#tnClear').onclick = async () => { if (await ask('Убрать все серверы и выключить VPN?', { ok: 'Убрать', danger: true })) tnReply(await call('tunnel_clear')); };
  $('#tnStart').onclick = async () => { $('#tnStart').disabled = true; tnMsg('Запускаю…'); const r = tnReply(await call('tunnel_start')); if (r && r.ok) tnMsg(''); };
  $('#tnStop').onclick = async () => tnReply(await call('tunnel_stop'));
  $('#tnDl').onclick = async () => { $('#tnDl').disabled = true; $('#tnEngine').textContent = 'Подключаюсь…'; if (!(await call('tunnel_download'))) loadTunnel(); };
  $('#tnDel').onclick = async () => { const st = await call('tunnel_engine_delete'); if (st) tnView(st); };
  function tnProgress(ev) {
    const pb = $('#tnBar'); pb.hidden = false; $('i', pb).style.width = (ev.total ? ev.done / ev.total * 100 : 0).toFixed(1) + '%';
    $('#tnEngine').textContent = ev.total ? `Скачано ${fmtMb(ev.done)} из ${fmtMb(ev.total)}` : 'Подключаюсь…';
  }
  function tnDlDone(ev) { if (ev.error && ev.error !== 'cancelled') tnMsg(ev.error, true); loadTunnel(); }

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

  // ---------- вкладка «Виджет» (динамический остров) ----------
  const W = { dock: 'top', pos: .5, mon: 0, pinned: false, running: false, loaded: false };
  const wgPill = () => $('#pill'), wgScr = () => $('#scr');
  function wgPlace() {                                          // рисуем плашку на схеме экрана
    const scr = wgScr(), pill = wgPill(); if (!scr || !pill || !scr.clientWidth) return;
    const Wd = scr.clientWidth, H = scr.clientHeight, vert = W.dock === 'left' || W.dock === 'right';
    pill.classList.toggle('v', vert);
    const pw = pill.offsetWidth, ph = pill.offsetHeight, m = 10;
    pill.style.left = (vert ? (W.dock === 'left' ? m : Wd - pw - m) : (Wd - pw) * W.pos) + 'px';
    pill.style.top = (vert ? (H - ph) * W.pos : (W.dock === 'top' ? m : H - ph - m)) + 'px';
    const g = $('#guide'); Object.assign(g.style, { left: 'auto', right: 'auto', top: 'auto', bottom: 'auto', width: 'auto', height: 'auto' },
      vert ? { [W.dock]: '2px', top: '6px', bottom: '6px', width: '30px' } : { [W.dock]: '2px', left: '6px', right: '6px', height: '30px' });
    const p = Math.round(W.pos * 100), d = Math.abs(p - 50) * 2;
    $('#oPos').textContent = p === 50 ? 'по центру' : (vert ? (p < 50 ? 'выше центра: ' : 'ниже центра: ') : (p < 50 ? 'левее центра: ' : 'правее центра: ')) + d + '%';
    $('#sPos').value = p;
    $$('#sDock button').forEach(b => b.classList.toggle('on', b.dataset.v === W.dock));
    $$('#sMon button').forEach(b => b.classList.toggle('on', Number(b.dataset.v) === W.mon));
    $('#wgPin').checked = W.pinned;
  }
  let wgT = 0;
  function wgSend(now) { clearTimeout(wgT); wgT = setTimeout(() => call('widget_place', W.dock, W.pos, W.mon), now ? 0 : 280); }
  function wgIdlePreview(s) {
    const sp = $$('#idle > *'); const on = { time: s.idle_time, bat: s.idle_battery, temp: s.idle_temp, music: s.idle_music };
    sp[1].hidden = !on.time; sp[2].hidden = !on.bat; sp[3].hidden = !on.temp; sp[4].hidden = !on.music;
  }
  function wgFill(d) {
    const s = d.settings || {}, w = d.where || {};
    Object.assign(W, { dock: w.dock || 'top', pos: w.pos ?? .5, mon: w.mon || 0, pinned: !!w.pinned, running: !!w.running, loaded: true });
    $('#wgEnable').checked = !!d.enabled; $('#wgAuto').checked = !!d.autostart;
    const mons = d.monitors || []; $('#rowMon').hidden = mons.length < 2;
    $('#sMon').replaceChildren(...mons.map((m, i) => { const b = el('button'); b.dataset.v = i; b.textContent = (m.primary ? 'Основной' : 'Монитор ' + (i + 1)) + ` · ${m.w}×${m.h}`; return b; }));
    $$('#sIdle button').forEach(b => b.classList.toggle('on', !!s[b.dataset.k])); wgIdlePreview(s);
    $$('#chars button').forEach(b => b.classList.toggle('on', b.dataset.c === s.character));
    $$('#sHol button').forEach(b => b.classList.toggle('on', b.dataset.v === s.holiday));
    $$('#sNotify button').forEach(b => b.classList.toggle('on', b.dataset.v === (s.notify === false ? '0' : '1')));
    $$('#sSnow button').forEach(b => b.classList.toggle('on', b.dataset.v === (s.snow ? '1' : '0')));
    $('#wgCity').value = s.city || '';
    wgKeys((d.hotkeys || {}).keys || {}, (d.hotkeys || {}).errors || []);
    wgPlace();
  }
  async function loadWidget() { const d = await call('widget_get'); if (d) wgFill(d); }
  const wgSave = patch => call('widget_set', patch);
  $('#sDock').onclick = e => { const b = e.target.closest('button'); if (!b) return; W.dock = b.dataset.v; wgPlace(); wgSend(true); };
  $('#sMon').onclick = e => { const b = e.target.closest('button'); if (!b) return; W.mon = Number(b.dataset.v); wgPlace(); wgSend(true); };
  $('#sPos').oninput = e => { W.pos = e.target.value / 100; wgPlace(); wgSend(false); };
  $('#scr').onclick = e => {                                    // клик по схеме: ближайший край и место вдоль него
    if (e.target.closest('.wg-pill')) return;
    const r = wgScr().getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height, d = { top: y, bottom: 1 - y, left: x, right: 1 - x };
    W.dock = Object.keys(d).sort((a, b) => d[a] - d[b])[0]; W.pos = Math.min(1, Math.max(0, W.dock === 'top' || W.dock === 'bottom' ? x : y)); wgPlace(); wgSend(true);
  };
  $('#wgPin').onchange = async e => { const r = await call('widget_pin', e.target.checked); if (r) { W.pinned = !!r.pinned; wgPlace(); } };
  $('#bReset').onclick = async () => { const r = await call('widget_reset'); if (r) { W.dock = r.dock; W.pos = r.pos; W.mon = r.mon; W.pinned = false; wgPlace(); } };
  $('#wgEnable').onchange = e => call('widget_enable', e.target.checked);
  $('#wgAuto').onchange = async e => { const r = await call('widget_autostart', e.target.checked); if (r && r.ok === false) { e.target.checked = !e.target.checked; } else if (r && r.island) $('#wgEnable').checked = true; };
  $('#sIdle').onclick = async e => { const b = e.target.closest('button'); if (!b) return; b.classList.toggle('on'); const s = {}; $$('#sIdle button').forEach(x => s[x.dataset.k] = x.classList.contains('on')); wgIdlePreview(s); wgSave({ [b.dataset.k]: s[b.dataset.k] }); };
  $('#chars').onclick = e => { const b = e.target.closest('button'); if (!b) return; $$('#chars button').forEach(x => x.classList.toggle('on', x === b)); wgSave({ character: b.dataset.c }); };
  const wgSeg = (id, key, conv) => $(id).onclick = e => { const b = e.target.closest('button'); if (!b) return; $$(id + ' button').forEach(x => x.classList.toggle('on', x === b)); wgSave({ [key]: conv ? conv(b.dataset.v) : b.dataset.v }); };
  wgSeg('#sHol', 'holiday'); wgSeg('#sSnow', 'snow', v => v === '1'); wgSeg('#sNotify', 'notify', v => v === '1');
  $('#wgCity').onchange = e => wgSave({ city: e.target.value.trim() });
  // горячие клавиши: нажать на сочетание и ввести новое
  const KEYNAME = { ctrl: 'Ctrl', alt: 'Alt', shift: 'Shift', win: 'Win' };
  function wgKeys(keys, errors) {
    $$('#wgKeys [data-hk]').forEach(row => {
      const b = $('.kbtn', row), combo = keys[row.dataset.hk] || ''; b.dataset.combo = combo; b.classList.remove('rec');
      b.replaceChildren(...(combo ? combo.split('+').map(k => { const x = el('kbd'); x.textContent = KEYNAME[k] || (k.length === 1 ? k.toUpperCase() : k.charAt(0).toUpperCase() + k.slice(1)); return x; }) : [Object.assign(el('span'), { className: 'empty', textContent: 'не задано' })]));
    });
    const m = $('#hkMsg'); m.classList.toggle('err', !!errors.length); m.textContent = errors.length ? errors.join(' ') : 'Нажмите на сочетание и введите новое. Backspace очищает, Esc отменяет.';
  }
  let hkRow = null;
  $('#wgKeys').addEventListener('click', e => {
    const row = e.target.closest('[data-hk]'); if (!row || hkRow === row) return;
    hkRow = row; const b = $('.kbtn', row); b.classList.add('rec'); b.replaceChildren(document.createTextNode('Нажмите сочетание…'));
  });
  document.addEventListener('keydown', async e => {
    if (!hkRow) return; e.preventDefault(); e.stopPropagation();
    const row = hkRow, name = row.dataset.hk;
    const done = async combo => { hkRow = null; const r = combo === null ? null : await call('widget_hotkey', name, combo); if (r && r.ok === false) { const m = $('#hkMsg'); m.textContent = r.error; m.classList.add('err'); loadWidget(); setTimeout(() => { const d = $('#hkMsg'); d.classList.remove('err'); }, 6000); } else loadWidget(); };
    if (e.key === 'Escape') return done(null);
    if (e.key === 'Backspace') return done('');
    if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) return;
    let k = e.code.startsWith('Key') ? e.code.slice(3).toLowerCase() : e.code.startsWith('Digit') ? e.code.slice(5) : /^F\d+$/.test(e.code) ? e.code.toLowerCase() : (e.key.length === 1 ? e.key.toLowerCase() : e.code.toLowerCase());
    const mods = [e.ctrlKey && 'ctrl', e.altKey && 'alt', e.shiftKey && 'shift', e.metaKey && 'win'].filter(Boolean);
    if (!mods.length) { const m = $('#hkMsg'); m.textContent = 'Нужна хотя бы одна из Ctrl, Alt, Shift, Win и обычная клавиша.'; m.classList.add('err'); return; }
    done([...mods, k].join('+'));
  }, true);
  window.addEventListener('resize', wgPlace);
  new MutationObserver(() => wgPlace()).observe($('#tab-widget'), { attributes: true, attributeFilter: ['class'] });

  // ---------- настройки ----------
  const sf = () => $('#setForm');
  function fillSettings(c, mics) {
    cfg = c; const f = sf();
    if (mics && mics.length) micDevs = mics;
    f.name.value = c.name; f.aliases.value = (c.name_aliases || []).join(', ');
    f.wake_required.checked = !!c.wake_required; f.followup_seconds.value = c.followup_seconds;
    f.model.value = c.stt.model; f.vad_threshold.value = c.stt.vad_threshold; f.assist.value = c.stt.assist || 'smart';
    f.tts_enabled.checked = !!c.tts.enabled; f.background.checked = (c.ui || {}).background !== false; loadSil(); f.rate.value = c.tts.rate;
    const mx = c.mixer || {}, vp = c.vpn || {};
    f.mixer_enabled.checked = mx.enabled !== false; f.mixer_prefer.checked = !!mx.prefer; f.mixer_pin.value = mx.pin || ''; f.mixer_port.value = mx.port || 0; f.mixer_step.value = mx.step_db || 3; f.vpn_enabled.checked = vp.enabled !== false; loadVpnStatus();
    f.persona_rude.checked = !!(c.persona || {}).rude; loadTunnel(); f.upd_auto.checked = (c.updates || {}).auto !== false; f.search_engine.value = c.search_engine; f.llm_enabled.checked = !!c.llm.enabled; f.theme.value = (c.ui || {}).theme || 'auto';
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
      tts: { enabled: f.tts_enabled.checked, voice: 'karina', rate: Number(f.rate.value) },
      search: { mode: f.search_mode.value, depth: f.search_depth.value },
      mixer: { enabled: f.mixer_enabled.checked, prefer: f.mixer_prefer.checked, pin: f.mixer_pin.value.trim(), port: Number(f.mixer_port.value) || 0, step_db: Number(f.mixer_step.value) },
      vpn: { enabled: f.vpn_enabled.checked },
      persona: { rude: f.persona_rude.checked },
      updates: { auto: f.upd_auto.checked }, search_engine: f.search_engine.value, ui: { theme: f.theme.value, background: f.background.checked },
      llm: { enabled: f.llm_enabled.checked, api_key: $('#apiKey').value.trim() },
    };
    const c = await call('save_settings', patch);
    $('#apiKey').value = '';
    if (c) { fillSettings(c, null); flash($('#setMsg'), 'Сохранено'); refresh(); loadBrain(); } else flash($('#setMsg'), 'Не удалось сохранить', true);
  });
  $('#btnClearKey').onclick = async () => { const c = await call('clear_api_key'); if (c) { fillSettings(c, null); flash($('#keyMsg'), 'Ключ удалён'); loadBrain(); } };
  $('#btnData').onclick = () => call('open_data_folder');

  // ---------- удаление по частям ----------
  const fmtBig = n => n >= 2 ** 30 ? (n / 2 ** 30).toFixed(1) + ' ГБ' : fmtSize(n);
  async function openUninstall() {
    const r = await call('uninstall_list'); if (!r) return;
    const items = r.items || [];
    if (!items.length) { note('Удалять нечего: данных и скачанных компонентов нет.', 'Удаление'); return; }
    const back = el('div', 'dlg-back'), box = el('div', 'dlg wide'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true');
    box.append(el('h3', null, 'Что удалить?'));
    const hint = el('p', null, 'Отметьте нужное. Скачанное можно загрузить заново, а настройки и диалоги вернуть нельзя.'); box.append(hint);
    const pre = el('div', 'un-pre'), list = el('div', 'un-list'), sum = el('div', 'un-sum');
    const checks = [];
    const groups = { downloads: 'Скачанное', data: 'Ваши данные', system: 'Система' };
    Object.keys(groups).forEach(g => {
      const its = items.filter(i => i.group === g); if (!its.length) return;
      list.append(el('div', 'un-g', groups[g]));
      its.forEach(i => {
        const row = el('label', 'un-row'), cb = document.createElement('input'); cb.type = 'checkbox'; cb.value = i.id; cb.dataset.group = i.group;
        row.append(cb, el('b', null, i.title), el('span', 'sz', i.size ? fmtBig(i.size) : ''), el('small', null, i.desc));
        list.append(row); checks.push(cb); cb.addEventListener('change', upd);
      });
    });
    const sel = fn => { checks.forEach(c => c.checked = fn(c)); upd(); };
    const bDl = el('button', 'btn', 'Только скачанное'), bAll = el('button', 'btn', 'Выбрать всё'), bNone = el('button', 'btn', 'Снять выбор');
    [bDl, bAll, bNone].forEach(b => b.type = 'button');
    bDl.onclick = () => sel(c => c.dataset.group === 'downloads'); bAll.onclick = () => sel(() => true); bNone.onclick = () => sel(() => false);
    pre.append(bDl, bAll, bNone); box.append(pre, list, sum);
    const row = el('div', 'dlg-btns'), no = el('button', 'btn', 'Отмена'), go = el('button', 'btn danger-solid', 'Удалить выбранное');
    no.type = go.type = 'button'; row.append(no, go); box.append(row); back.append(box); document.body.append(back);
    const close = () => { document.removeEventListener('keydown', key, true); back.classList.remove('on'); setTimeout(() => back.remove(), 160); };
    const key = e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } };
    document.addEventListener('keydown', key, true);
    no.onclick = close;
    function upd() {
      const on = checks.filter(c => c.checked), bytes = on.reduce((s, c) => s + ((items.find(i => i.id === c.value) || {}).size || 0), 0);
      go.disabled = !on.length;
      sum.textContent = on.length ? `Выбрано: ${on.length}` + (bytes ? ` · освободится ${fmtBig(bytes)}` : '') : 'Ничего не выбрано.';
    }
    upd();
    go.onclick = async () => {
      const ids = checks.filter(c => c.checked).map(c => c.value), names = ids.map(id => (items.find(i => i.id === id) || {}).title);
      const full = ids.includes('settings') || ids.includes('chats') || ids.includes('program');
      const okk = await ask('Будет удалено:\n• ' + names.join('\n• ') + (full ? '\n\nНастройки, диалоги и программу восстановить нельзя.' : '') + (ids.some(i => !['llm', 'engine', 'vpn', 'voice', 'system', 'plugins'].includes(i)) ? '\n\nКарина закроется, чтобы стереть занятые файлы.' : ''), { title: 'Удалить безвозвратно?', ok: 'Удалить', danger: true });
      if (!okk) return;
      go.disabled = no.disabled = true; sum.textContent = 'Удаляю…';
      const res = await call('uninstall_run', ids);
      close();
      if (!res) { note('Не удалось выполнить удаление.', 'Удаление'); return; }
      if (!res.ok) { note(res.error || 'Не удалось.', 'Удаление'); return; }
      const done = (res.done || []).map(id => (items.find(i => i.id === id) || {}).title).filter(Boolean);
      note((done.length ? 'Удалено:\n• ' + done.join('\n• ') : 'Готово.') + ((res.problems || []).length ? '\n\nНе получилось:\n• ' + res.problems.join('\n• ') : '') + (res.quit ? '\n\nКарина сейчас закроется и сотрёт остальное.' : ''), 'Удаление');
    };
    requestAnimationFrame(() => back.classList.add('on'));
  }
  $('#btnUninstall').onclick = openUninstall;

  async function refresh() {
    const s = await call('get_state'); if (!s) return;
    outDevs = s.outs || outDevs; fillSettings(s.config, s.mics);
    setFrameless(s.frameless, s.maximized);
    version = s.version || version; $('#kvVer').textContent = version; $('#updVer').textContent = version; renderStatus(s.status);
  }

  async function init() {
    api = window.pywebview.api;
    await refresh(); loadBrain(); loadAdmin(); loadProfile();
    setTimeout(() => checkUpdate(false), 3000);
    setInterval(async () => { const s = await call('get_status'); if (s) renderStatus(s); loadBrain(); if ($('#tab-home').classList.contains('on') && !hvBusy) hvLoad(); }, 5000);
    hvLoad();
    let t = null; try { t = localStorage.getItem('karina.tab'); } catch (_) {}
    if (t) { const b = $(`#tabs [data-tab="${t}"]`); if (b) b.click(); }
  }
  if (window.pywebview && window.pywebview.api) init(); else window.addEventListener('pywebviewready', init);
})();
