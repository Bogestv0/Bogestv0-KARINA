// Фальшивый pywebview для проверки окна установки/удаления в обычном браузере (setup.html#install | #update | #uninstall).
(() => {
  const mode = (location.hash || '#install').slice(1);
  const GB = 1024 ** 3, MB = 1024 ** 2;
  const items = [
    {id:'llm',group:'downloads',title:'Модели чата',desc:'Скачанные нейросети для разговора (файлы .gguf).',size:5.7*GB},
    {id:'stt',group:'downloads',title:'Распознавание речи',desc:'Модели Whisper (small, tiny): по ним Карина слышит вас.',size:480*MB},
    {id:'voice',group:'downloads',title:'Голос Карины',desc:'Скачанная копия голоса Silero.',size:62*MB},
    {id:'components',group:'downloads',title:'Компоненты (torch)',desc:'Библиотека torch, которую Карина докачала при первом запуске.',size:210*MB},
    {id:'engine',group:'downloads',title:'Движок llama.cpp',desc:'Программа, которая запускает модели чата.',size:38*MB},
    {id:'chats',group:'data',title:'Диалоги и вложения',desc:'История чатов и файлы, которые вы прикладывали.',size:23*MB},
    {id:'settings',group:'data',title:'Настройки, ключи и профиль',desc:'Ключи Claude и Gemini, профиль, ссылки на VPN-серверы.',size:12*1024},
    {id:'system',group:'system',title:'Автозапуск и ярлыки',desc:'Убрать: автозапуск вместе с Windows, ярлык на рабочем столе.',size:0},
    {id:'program',group:'system',title:'Сама программа',desc:'Файлы программы, ярлыки, меню «Пуск». Выполняется последним.',size:0},
  ];
  let t0 = 0;
  const steps = [[8,'Копирую файлы программы','Файлы программы'],[46,'Распаковываю голос','Голос Silero'],[70,'Движок VPN (sing-box)','sing-box'],[88,'Ярлыки и автозапуск','Ярлык в меню «Пуск»'],[100,'Готово','']];
  let shown = 0;
  window.pywebview = {api:{
    setup_state: async()=>({mode, version:'1.21.2', installed_version:'1.20.4', has_vpn:true}),
    uninstall_list: async()=>({items}),
    uninstall_run: async()=>new Promise(r=>setTimeout(()=>r({ok:true,done:[],problems:[],quit:true}), window.__slow?60000:2600)),
    setup_install: async()=>{t0=Date.now();shown=0;return {ok:true}},
    setup_progress: async()=>{
      const pct = Math.min(100,(Date.now()-t0)/30), log=[];
      while (shown<steps.length && pct>=steps[shown][0]) { if (steps[shown][2]) log.push({text:steps[shown][2]}); shown++; }
      const cur = steps.find(s=>pct<s[0]) || steps[steps.length-1];
      return {pct, text:cur[1], log, done:pct>=100, ok:true};
    },
    setup_finish: async()=>true, win_minimize: async()=>true, win_close: async()=>true,
  }};
})();
