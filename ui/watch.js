// Вкладка «Телефон»: телефон выбирается по сопряжённому Bluetooth, присутствие проверяется по Wi-Fi (karina/watchlock.py).
// Имена устройств приходят из Windows и могут быть любыми: в разметку их кладём только через textContent.
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const call = async (name, ...args) => {
    try { const a = window.pywebview && window.pywebview.api; return a ? await a[name](...args) : null; }
    catch (e) { console.error(name, e); return null; }
  };
  const flash = (node, text, err) => { node.textContent = text; node.classList.toggle('err', !!err); clearTimeout(node._t); node._t = setTimeout(() => node.textContent = '', 5000); };

  let targets = [], nk = 'none', hasToken = false, loaded = false, selected = null;

  // ---------- состояние ----------
  const STATE = {
    off:     ['Выключено', '', ''],
    unknown: ['Ищу телефон…', '', ''],
    present: ['Телефон в сети', 'ok', 'Всё в порядке, компьютер не блокируется.'],
    waiting: ['Телефон не отвечает', 'wait', ''],
    away:    ['Телефон ушёл — компьютер заблокирован', 'bad', 'Вернётесь — разблокируйте Windows как обычно.'],
  };

  function paintStatus(s) {
    if (!s) return;
    const [title, cls, sub] = STATE[s.state] || STATE.unknown;
    $('#wtState').textContent = title;
    $('#wtDot').className = 'wt-dot ' + cls;
    let text = sub;
    if (s.state === 'waiting') text = 'Если связь не вернётся, блокировка через ' + s.left + ' с.';
    else if (s.state === 'off') text = targets.length ? 'Включите «Следить за телефоном» ниже и сохраните.' : 'Сначала выберите телефон по Bluetooth и добавьте его IP в Wi‑Fi.';
    else if (s.state === 'unknown' && s.enabled) text = 'Пока ни разу не видела телефон в сети: блокировать не буду, пока он не появится.';
    $('#wtSub').textContent = text;

    const box = $('#wtReadings'); box.textContent = '';
    (s.readings || []).forEach(r => {
      const row = el('div', 'wt-row'), t = el('div', 'wt-t');
      t.append(el('b', '', r.name || r.id), el('span', '', ({ ping: 'Wi‑Fi', paired: 'Bluetooth', ble: 'Bluetooth' })[r.kind] + ' · ' + r.id));
      let st, c = '';
      if (r.present === true) { st = 'в сети'; c = 'ok'; }
      else if (r.present === false) { st = 'не видно'; c = 'bad'; }
      else st = 'нет данных';
      row.append(t, el('div', 'wt-s ' + c, st)); box.append(row);
    });

    const bat = $('#wtBat');
    bat.hidden = !(s.enabled && $('#wtBattOn') && $('#wtBattOn').checked);
    $('#wtBatV').textContent = s.battery == null ? 'нет данных' : s.battery + '%';
    const err = $('#wtErr'); err.hidden = !s.error; err.textContent = s.error || '';
    $('#wtLast').textContent = s.last_event ? 'Последнее событие: ' + s.last_event : '';
  }

  // ---------- цели ----------
  const KIND = { ping: 'Wi‑Fi', paired: 'Bluetooth', ble: 'Bluetooth' };
  function paintTargets() {
    const box = $('#wtTargets'); box.textContent = '';
    if (!targets.length) { box.append(el('p', 'muted small', 'Пока ни одного телефона не добавлено.')); return; }
    targets.forEach((t, i) => {
      const row = el('div', 'wt-row'), x = el('div', 'wt-t');
      x.append(el('b', '', t.name || t.id), el('span', '', KIND[t.kind] + ' · ' + t.id));
      const rm = el('button', 'btn', 'Убрать'); rm.type = 'button';
      rm.onclick = () => { targets.splice(i, 1); paintTargets(); };
      row.append(x, rm); box.append(row);
    });
  }
  function addTarget(t) {
    if (targets.some(x => x.kind === t.kind && x.id.toLowerCase() === t.id.toLowerCase())) return false;
    if (targets.length >= 4) { flash($('#wtScanMsg'), 'Не больше четырёх целей', true); return false; }
    targets.push(t); paintTargets(); return true;
  }

  // ---------- выбор телефона по Bluetooth ----------
  function paintSelected() {
    const box = $('#wtPhonePick'); box.hidden = !selected;
    if (!selected) return;
    $('#wtPhoneName').textContent = selected.name || 'Телефон';
    $('#wtPhoneId').textContent = selected.id;
  }
  function paintFound(r) {
    const box = $('#wtFound'); box.textContent = '';
    (r.errors || []).forEach(m => { const n = el('div', 'note warn', m); n.style.marginTop = '0'; box.append(n); });
    const paired = r.paired || [];
    if (!paired.length) {
      box.append(el('p', 'muted small', 'Сопряжённых устройств не найдено. В Windows → «Параметры» → «Bluetooth» подключите телефон и повторите поиск.'));
      return;
    }
    box.append(el('div', 'eyebrow', 'Сопряжено с Windows — выберите телефон'));
    paired.forEach(d => {
      const row = el('div', 'wt-row'), x = el('div', 'wt-t');
      x.append(el('b', '', d.name || '(без имени)'),
        el('span', '', (d.kind === 'le' ? 'Bluetooth LE' : 'Bluetooth') + ' · ' + (d.connected == null ? 'связь неизвестна' : d.connected ? 'на связи' : 'нет связи')));
      const b = el('button', 'btn', 'Выбрать'); b.type = 'button';
      b.onclick = () => {
        selected = { id: d.id, name: d.name || 'Телефон' };
        paintSelected(); $('#wtPhoneIp').focus();
      };
      row.append(x, b); box.append(row);
    });
  }
  async function scan() {
    const btn = $('#wtScan'); btn.disabled = true; $('#wtScanMsg').classList.remove('err'); $('#wtScanMsg').textContent = 'Смотрю сопряжённые устройства…';
    const r = await call('watch_scan'); btn.disabled = false; $('#wtScanMsg').textContent = '';
    if (!r) { flash($('#wtScanMsg'), 'Нет ответа от Карины', true); return; }
    paintFound(r);
  }
  function addPhone() {
    if (!selected) { flash($('#wtPhoneMsg'), 'Сначала выберите телефон по Bluetooth', true); return; }
    const ip = $('#wtPhoneIp').value.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9.\-]{0,62}$/.test(ip)) { flash($('#wtPhoneMsg'), 'Укажите IP телефона в домашней Wi‑Fi', true); return; }
    if (addTarget({ kind: 'ping', id: ip, name: selected.name || 'Телефон' })) flash($('#wtPhoneMsg'), 'Проверка по Wi‑Fi добавлена');
    else flash($('#wtPhoneMsg'), 'Этот IP уже есть в списке', true);
    $('#wtPhoneIp').value = '';
  }

  // ---------- форма ----------
  const seg = (sel, val) => $$(sel + ' button').forEach(b => b.classList.toggle('on', b.dataset.v === val));
  function paintNotifyKind() { seg('#wtNK', nk); $('#wtNtfy').hidden = nk !== 'ntfy'; $('#wtTg').hidden = nk !== 'telegram'; }
  const paintAfter = () => $('#wtAfterV').textContent = $('#wtAfter').value + ' с';
  const paintRssi = () => { $('#wtRssiV').textContent = $('#wtRssi').value + ' дБм'; $('#wtRssiRow').hidden = !$('#wtRssiOn').checked; };

  function fill(c) {
    const n = c.notify || {};
    targets = (c.targets || []).map(t => ({ ...t }));
    const first = targets[0];
    if (c.phone && c.phone.id) selected = { id: c.phone.id, name: c.phone.name || 'Телефон' };
    else if (first && !selected) selected = { id: first.id, name: first.name || 'Телефон' };
    $('#wtEnabled').checked = !!c.enabled; $('#wtLock').checked = c.lock !== false;
    $('#wtAfter').value = c.lock_after || 30; $('#wtRssiOn').checked = !!c.rssi_min; $('#wtRssi').value = c.rssi_min || -85;
    $('#wtBattOn').checked = c.battery !== false; $('#wtLow').value = c.low_battery == null ? 15 : c.low_battery;
    nk = n.kind || 'none'; hasToken = !!n.has_token;
    $('#wtTopic').value = n.topic || ''; $('#wtServer').value = n.server || 'https://ntfy.sh';
    $('#wtToken').value = ''; $('#wtToken').placeholder = hasToken ? 'токен сохранён (пусто — оставить)' : 'токен бота';
    $('#wtChat').value = n.chat_id || '';
    $('#wtOnLost').checked = n.on_lost !== false; $('#wtOnBack').checked = n.on_back !== false;
    paintTargets(); paintSelected(); paintNotifyKind(); paintAfter(); paintRssi();
  }

  function collect() {
    return {
      enabled: $('#wtEnabled').checked, lock: $('#wtLock').checked, lock_after: Number($('#wtAfter').value),
      rssi_min: $('#wtRssiOn').checked ? Number($('#wtRssi').value) : 0,
      battery: $('#wtBattOn').checked, low_battery: Number($('#wtLow').value) || 0, targets,
      phone: selected ? { id: selected.id, name: selected.name || 'Телефон' } : { id: '', name: '' },
      notify: { kind: nk, topic: $('#wtTopic').value.trim(), server: $('#wtServer').value.trim(), token: $('#wtToken').value.trim(),
                chat_id: $('#wtChat').value.trim(), on_lost: $('#wtOnLost').checked, on_back: $('#wtOnBack').checked },
    };
  }

  async function save(quiet) {
    const r = await call('watch_set', collect());
    if (!r) { if (!quiet) flash($('#wtMsg'), 'Не получилось сохранить', true); return false; }
    fill(r);
    if (!quiet) flash($('#wtMsg'), 'Сохранено');
    return true;
  }

  async function load() {
    loadCab();
    const r = await call('watch_get');
    if (!r) return;
    if (!loaded) { fill(r.conf); loaded = true; }
    paintStatus(r.status);
  }

  // ---------- кабинет и служба питания ----------
  function paintCab(r) {
    if (!r) return;
    const c = r.cabinet || {}, p = r.power || {};
    $('#cabOff').hidden = !!c.linked; $('#cabOn').hidden = !c.linked;
    $('#cabName').textContent = c.name || 'Подключено';
    const e = $('#cabErr'); e.hidden = !c.error; e.textContent = c.error || '';
    $('#wtNoteCab').hidden = !c.linked;
    const supported = p.supported !== false;
    $('#pwrCard').hidden = !supported;
    $('#pwrTitle').textContent = p.installed ? (p.running ? 'Работает' : 'Установлена, но не запущена') : 'Не установлена';
    $('#pwrSub').textContent = p.installed ? 'Запускается вместе с Windows.' : (c.linked ? 'Установка попросит подтверждение Windows (UAC).' : 'Сначала подключите кабинет выше.');
    const st = $('#pwrState'); st.textContent = p.installed ? (p.running ? 'работает' : 'остановлена') : ''; st.className = 'wt-s ' + (p.running ? 'ok' : 'bad');
    $('#pwrInstall').hidden = !!p.installed; $('#pwrInstall').disabled = !c.linked;
    $('#pwrRemove').hidden = !p.installed;
  }
  async function loadCab() { paintCab(await call('cabinet_get')); }
  $('#cabPair').onclick = async () => {
    const b = $('#cabPair'); const code = $('#cabCode').value.trim(); if (!code) return;
    b.disabled = true; $('#cabMsg').classList.remove('err'); $('#cabMsg').textContent = 'Подключаю…';
    const r = await call('cabinet_pair', code); b.disabled = false;
    if (r && r.ok) { $('#cabCode').value = ''; $('#cabMsg').textContent = ''; paintCab(r); }
    else flash($('#cabMsg'), (r && r.error) || 'Нет ответа от Карины', true);
  };
  $('#cabUnlink').onclick = async () => { paintCab(await call('cabinet_unlink')); };
  const pwr = async (name, busy) => {
    const b = $('#pwrInstall'), d = $('#pwrRemove'); b.disabled = d.disabled = true; $('#pwrMsg').classList.remove('err'); $('#pwrMsg').textContent = busy;
    const r = await call(name); b.disabled = d.disabled = false;
    if (r && r.ok) flash($('#pwrMsg'), name === 'power_install' ? 'Служба установлена и запущена' : 'Служба удалена');
    else flash($('#pwrMsg'), (r && r.error) || 'Нет ответа от Карины', true);
    paintCab(r);
  };
  $('#pwrInstall').onclick = () => pwr('power_install', 'Подтвердите окно Windows…');
  $('#pwrRemove').onclick = () => pwr('power_remove', 'Подтвердите окно Windows…');

  // ---------- события ----------
  $('#tabs').addEventListener('click', e => { const b = e.target.closest('button[data-tab]'); if (b && b.dataset.tab === 'watch') load(); });
  const prev = window.karina && window.karina.event;
  if (window.karina) window.karina.event = function (ev) {
    if (ev && ev.type === 'watch') { paintStatus(ev); return; }
    return prev ? prev.apply(this, arguments) : undefined;
  };

  $('#wtScan').onclick = scan;
  $('#wtPhoneChange').onclick = () => { selected = null; paintSelected(); scan(); };
  $('#wtPhoneAdd').onclick = addPhone;
  $('#wtPhoneIp').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addPhone(); } });
  $('#wtSave').onclick = () => save(false);
  $('#wtAfter').oninput = paintAfter;
  $('#wtRssi').oninput = paintRssi; $('#wtRssiOn').onchange = paintRssi;
  $('#wtNK').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { nk = b.dataset.v; paintNotifyKind(); } });
  $('#wtTopicNew').onclick = async () => { const t = await call('watch_topic'); if (t) $('#wtTopic').value = t; };
  $('#wtAdd').onclick = () => {
    const id = $('#wtId').value.trim(); if (!id) return;
    if (addTarget({ kind: 'ping', id, name: $('#wtName').value.trim() || 'Телефон' })) { $('#wtId').value = ''; $('#wtName').value = ''; }
  };
  $('#wtBatBtn').onclick = async () => { $('#wtBatBtn').disabled = true; await call('watch_battery'); $('#wtBatBtn').disabled = false; load(); };
  $('#wtTest').onclick = async () => {
    const b = $('#wtTest'); b.disabled = true;
    if (await save(true)) {
      const r = await call('watch_test_push');
      flash($('#wtMsg'), r && r.ok ? 'Отправлено: посмотрите на телефон' : 'Не отправилось: ' + ((r && r.error) || 'нет ответа'), !(r && r.ok));
    } else flash($('#wtMsg'), 'Не получилось сохранить', true);
    b.disabled = false;
  };
})();
