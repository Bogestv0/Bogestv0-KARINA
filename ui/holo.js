/* Голограмма Карины. Снаружи одна функция: Holo.setState(name) (+ Holo.smile()).
   Режимы: idle, called, think, speak, smile, sleep, wake.
   Устройство: внизу неподвижная картинка покоя (holo/f/base.webp). Каждое действие это короткий ролик (holo/v/*.webm,
   без звука), который плавно проявляется поверх картинки и растворяется обратно. Все ролики начинаются и заканчиваются
   в базовой позе, поэтому картинка не «плывёт».
   ВАЖНО: проекторы закреплены. Качается только персонаж и лучи; два основания проекторов рисуются отдельным
   неподвижным слоем (.h-proj) и вырезаются из качающегося слоя маской. */
(function () {
  'use strict';

  /* ===================== НАСТРОЙКИ (правятся здесь) ===================== */
  const CFG = {
    bright: 0.9, sat: 0.92,         // яркость и насыщенность; если свечения много, уменьшать bright
    sway: { x: 0.4, y: 0.7, rot: 0.25, scale: 1.01, scaleAmp: 0.004,   // % / % / градусы
            sx: 0.7, sy: 0.9, sr: 0.5 },                               // скорости покачивания
    // ролики (файлы в ui/holo/v/<имя>.webm)
    clips: {
      idle:  [['idle_base', 5], ['idle_left', 2], ['idle_right', 2], ['idle_up', 1]],   // [имя, вес]: случайно в покое
      speak: ['speak_a', 'speak_b'],        // по очереди, пока Карина говорит
      think: 'think', smile: 'smile', called: 'called',
      sleep: 'sleep_in',                    // засыпание; в конце кадр остаётся (Карина спит)
      wake:  'wake',                        // пробуждение
      gapMin: 2500, gapMax: 6000,           // пауза между роликами покоя, мс
      fadeIn: 200, fadeOut: 400,            // появление / растворение ролика, мс
      overlap: 350                          // стык подряд идущих роликов (речь, «думает»), мс
    },
    // сон: дополнительное приглушение и «дыхание» яркости
    sleep: { dim: 0.12, dimRate: 1.2, breath: 0.025, breathSpeed: 0.8 },
    // зоны проекторов в долях кадра (центр и радиусы овала, который остаётся НЕПОДВИЖНЫМ)
    projectors: [ { x: 0.068, y: 0.915, rx: 0.078, ry: 0.088 },
                  { x: 0.932, y: 0.915, rx: 0.078, ry: 0.088 } ],
    projFeather: 0.62               // с какой доли радиуса начинается мягкий край
  };
  const DIR = 'holo/';
  /* ======================================================================= */

  const wrap = document.getElementById('holoWrap');
  if (!wrap) { window.Holo = { setState() {}, smile() {} }; return; }
  const stage = wrap.parentElement, C = CFG.clips;
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const proj = stage.querySelector('.h-proj');
  const sleepMs = ms => new Promise(r => setTimeout(r, ms));
  const setHF = (b) => stage.style.setProperty('--hf', `brightness(${b.toFixed(3)}) saturate(${CFG.sat})`);
  setHF(CFG.bright);

  // --- маска: вырезать основания из качающегося слоя, показать их в неподвижном ---
  (function maskSetup() {
    const f = Math.round(CFG.projFeather * 100);
    const g = (inv) => CFG.projectors.map(p =>
      `radial-gradient(ellipse ${p.rx * 100}% ${p.ry * 100}% at ${p.x * 100}% ${p.y * 100}%,` +
      (inv ? `#000 ${f}%,transparent 100%)` : `transparent ${f}%,#000 100%)`));
    wrap.style.webkitMaskImage = wrap.style.maskImage = g(false).join(',');
    wrap.style.webkitMaskComposite = 'source-in'; wrap.style.maskComposite = 'intersect';
    if (proj) proj.style.webkitMaskImage = proj.style.maskImage = g(true).join(',');
  })();

  // --- ролики ---
  const names = new Set();
  const addN = x => { if (Array.isArray(x)) x.forEach(addN); else if (typeof x === 'string') names.add(x); };
  addN(C.idle.map(a => a[0])); addN(C.speak); addN([C.think, C.smile, C.called, C.sleep, C.wake]);
  const VID = {};
  names.forEach(n => {
    const v = document.createElement('video');
    v.className = 'h-vid'; v.muted = true; v.defaultMuted = true; v.playsInline = true; v.preload = 'auto'; v.loop = false;
    v.setAttribute('muted', ''); v.src = DIR + 'v/' + n + '.webm'; v.style.opacity = 0;
    wrap.appendChild(v); VID[n] = v;
  });
  let shown = null;                                  // ролик, который сейчас виден
  const fade = (v, ms, to) => { v.style.transition = `opacity ${ms}ms ease`; v.style.opacity = to; };
  const hide = (v, ms) => { fade(v, ms, 0); setTimeout(() => { if (v.style.opacity === '0') v.pause(); }, ms + 80); };
  const release = (ms) => { if (shown) { hide(shown, ms == null ? C.fadeOut : ms); shown = null; } };

  let ep = 0, cur = null;                            // ep: номер «эпохи»: смена режима прерывает прежние циклы
  // играет ролик; возвращает true, когда он дошёл до конца (минус overlap) и режим не сменился
  async function play(e, n, opt) {
    opt = opt || {};
    const v = VID[n]; if (!v) return false;
    const fi = opt.fadeIn != null ? opt.fadeIn : C.fadeIn;
    try {
      v.pause(); v.currentTime = 0; await v.play();
      // ждём, пока на экране появится именно первый кадр ролика (иначе на миг виден старый последний кадр: «скачок»)
      await new Promise(r => { let d = 0; const f = () => { if (!d) { d = 1; r(); } };
        if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(f); setTimeout(f, 250); });
    } catch (_) { return false; }
    if (e !== ep) { v.pause(); return false; }
    const prev = shown; shown = v; fade(v, fi, 1);
    if (prev && prev !== v) hide(prev, fi);
    const ov = opt.hold ? 0 : (opt.overlap || 0) / 1000;
    await new Promise(r => {
      const t = setInterval(() => {
        if (e !== ep || v.ended || v.paused || (ov && v.duration - v.currentTime <= ov)) { clearInterval(t); r(); }
      }, 40);
    });
    return e === ep;
  }

  // --- главный цикл: покачивание всей картинки (проекторы не качаются) и яркость во сне ---
  const SW = CFG.sway, SL = CFG.sleep;
  let dim = 0, dimT = 0, last = performance.now(), t0 = last;
  function tick(now) {
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    dim += (dimT - dim) * (1 - Math.exp(-SL.dimRate * dt)); if (Math.abs(dimT - dim) < .002) dim = dimT;
    const s = (now - t0) / 1000, k = reduce ? 0 : 1;
    wrap.style.transform = `translate(${Math.sin(s * SW.sx) * SW.x * k}%,${Math.sin(s * SW.sy) * SW.y * k}%) rotate(${Math.sin(s * SW.sr) * SW.rot * k}deg) scale(${1 + (SW.scale - 1) * k + Math.sin(s * SW.sy) * SW.scaleAmp * k})`;
    if (dim > .01 || dimT > 0) setHF(CFG.bright - SL.dim * dim + SL.breath * dim * Math.sin(s * SL.breathSpeed * 2 * Math.PI / 2));
    else if (dim === 0 && stage._dimOn) setHF(CFG.bright);
    stage._dimOn = dim > .01 || dimT > 0;
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  // --- режимы ---
  let lastIdle = '';
  function pickIdle() {
    const L = C.idle.filter(a => a[0] !== lastIdle), sum = L.reduce((x, a) => x + a[1], 0);
    let r = Math.random() * sum; for (const a of L) { if ((r -= a[1]) < 0) { lastIdle = a[0]; return a[0]; } }
    return L[0][0];
  }
  async function idle(e, first) {
    if (first === 'called' && !reduce) { if (await play(e, C.called)) release(); if (e !== ep) return; }
    while (e === ep) {
      await sleepMs(C.gapMin + Math.random() * (C.gapMax - C.gapMin)); if (e !== ep) return;
      if (reduce) continue;
      if (await play(e, pickIdle())) release();
    }
  }
  async function loopClips(e, list) {                  // речь и «думает»: ролики подряд с плавным стыком
    let i = Math.random() * list.length | 0;
    while (e === ep) { if (!(await play(e, list[i++ % list.length], { overlap: C.overlap }))) return; }
  }
  const speak = e => loopClips(e, C.speak);
  const think = e => loopClips(e, [C.think]);
  async function smile(e) { if (await play(e, C.smile)) { release(); if (e === ep) go('idle', true); } }
  async function sleepM(e) { dimT = 0; if (await play(e, C.sleep, { hold: true })) dimT = 1; }
  async function wake(e) {
    dimT = 0;
    if (await play(e, C.wake)) release();
    if (e === ep) { cur = 'idle'; idle(e); }
  }
  const MODES = { idle: e => idle(e), called: e => idle(e, 'called'), think, speak, smile, sleep: sleepM, wake };

  function go(k, force) {
    if (!MODES[k] || (k === cur && !force)) return;
    const wasSleep = cur === 'sleep';
    cur = k; ep++; const e = ep;
    if (wasSleep && k !== 'sleep') {
      if (k === 'idle' || k === 'called') k = 'wake';                // из сна в покой: полное пробуждение
      else { dimT = 0; setTimeout(() => { if (e === ep) MODES[cur](e); }, 0); return; }   // сразу говорит/думает: ролик проявится поверх спящей Карины
    } else if (k !== 'wake') release();
    if (k !== 'sleep' && k !== 'wake') dimT = 0;
    MODES[k](e);
  }

  // приложение -> голограмма
  const ALIAS = { idle: 'idle', called: 'called', confirm: 'called', thinking: 'think', think: 'think', speaking: 'speak', speak: 'speak',
                  paused: 'sleep', sleeping: 'sleep', sleep: 'sleep', wake: 'wake', smile: 'smile', loading: 'idle', error: 'idle' };
  window.Holo = {
    setState(name) { const m = ALIAS[name] || 'idle'; if (cur === 'smile' && m === 'idle') return; go(m); },
    smile() { if (cur !== 'sleep' && cur !== 'speak') go('smile', true); },
    clip(n) { if (VID[n]) { ep++; const e = ep; play(e, n).then(ok => { if (ok) release(); }); } },   // показать ролик сразу (для проверки)
    config: CFG
  };
  go('idle');
})();
