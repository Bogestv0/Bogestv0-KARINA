// Подставляет фальшивый pywebview для проверки интерфейса в обычном браузере.
(() => {
  const cfg = {name:'Карина',name_aliases:['карина','карену'],wake_required:true,followup_seconds:8,ui:{background:true,theme:'auto'},
    stt:{model:'small',mic_device:null,vad_threshold:0.012},tts:{enabled:true,engine:'auto',voice:'svetlana_cute',offline:'auto',rate:0},search_engine:'google',llm:{enabled:false,has_key:false,api_key:''},apps:[],commands:[{phrases:['включи запись'],do:{type:'keys',value:'ctrl+shift+r'},reply:'Запись включена'}]};
  const st = {online:true,model:'small',voice:'online',tts_warning:'',state:'idle',sleeping:false,listening:true,name:'Карина',plugin_errors:[]};
  const brainState = {mode:'auto',label:'Авто',claude_model:'claude-sonnet-5-5',claude_models:[{id:'claude-haiku-5-5',title:'Claude Haiku — самый быстрый и дешёвый'},{id:'claude-sonnet-5-5',title:'Claude Sonnet — баланс скорости и качества (рекомендуется)'},{id:'claude-opus-5-5',title:'Claude Opus — самый сильный, дороже'}],local:{ok:true,reason:''},claude:{ok:false,reason:'Ключ Claude не задан. Введите его в «Настройки» (раздел «Claude»).',has_key:false,label:'Claude Sonnet'}};
  window.pywebview = {api:{
    get_state: async()=>({frameless:true,maximized:false,status:st,config:cfg,mics:[{id:1,name:'Микрофон (USB Audio)'},{id:2,name:'Микрофон (Realtek)'}]}),
    admin_state: async()=>({supported:true,admin:false,enabled:false,task:false,asked:false}), admin_set: async(on)=>({ok:true,state:{supported:true,admin:false,enabled:on,task:on,asked:true}}), admin_dismiss: async()=>true, restart_app: async()=>({ok:true}),
    get_status: async()=>st, win_minimize: async()=>true, win_toggle_maximize: async()=>{window.__max=!window.__max;return window.__max}, win_close: async()=>true, win_drag: async()=>true,
    get_commands: async()=>[{name:'open_app',desc:'Открыть программу или сайт',examples:['открой миксер','запусти блокнот']},{name:'volume_set',desc:'Установить громкость в процентах',examples:['громкость пятьдесят процентов']},{name:'time',desc:'Сказать время',examples:['который час']}],
    list_custom: async()=>cfg.commands,
    models_state: async()=>({ram:32,recommended:'gemma4-26b',gpu:'auto',engine:{state:'ready',flavor:'vulkan',detail:'Готово'},models:[
      {id:'qwen35-4b',title:'Qwen 3.5 · 4B',tier_label:'Слабый ПК',size_gb:2.7,ram:8,ru:'хороший',desc:'Для слабых компьютеров и ноутбуков (от 8 ГБ памяти). Быстрая, подходит и для процессора.',installed:true,active:false,dl:null},
      {id:'gemma4-e4b',title:'Gemma 4 · E4B',tier_label:'Средний ПК',size_gb:5.0,ram:12,ru:'очень хороший',desc:'Золотая середина: от 12 ГБ памяти.',installed:false,active:false,dl:{done:2.1*2**30,total:5*2**30,stage:'download'}},
      {id:'qwen35-9b',title:'Qwen 3.5 · 9B',tier_label:'Мощный ПК',size_gb:5.7,ram:16,ru:'отличный',desc:'Для мощного компьютера.',installed:true,active:true,dl:null},
      {id:'gemma4-26b',title:'Gemma 4 · 26B-A4B (MoE)',tier_label:'Максимум',size_gb:16.9,ram:32,ru:'отличный',desc:'Самая умная из встроенных.',installed:false,active:false,dl:null}]}),
    model_download: async()=>true, model_cancel: async()=>true, model_select: async()=>({ok:true}), models_settings: async()=>true, model_test: async()=>true,
    check_update: async()=>({available:true,current:'1.1.0',latest:'1.2.0',notes:'## Что нового\n* Окно «Чат»\n* Поиск в интернете',can_install:true}), install_update: async()=>({ok:true}),
    chat_list: async()=>({chats:[{id:'a1',title:'Отчёт про солнечные панели',updated:2,count:2},{id:'b2',title:'Что такое Vulkan',updated:1,count:2}],busy:false,current:null}),
    chat_get: async()=>({id:'a1',messages:[{id:'f1',role:'user',text:'Создай документ Word с отчётом и схему сети'},{id:'f2',role:'assistant',text:'Готово: отчёт и схема ниже.',brain:'Claude Sonnet',files:[{name:'отчёт.docx',kind:'word',size:37268,warning:''},{name:'схема.svg',kind:'svg',size:1240,warning:''},{name:'скрипт.py',kind:'code',size:210,warning:'в файле есть синтаксическая ошибка (строка 1)'}]},{id:'1',role:'user',text:'Объясни, что такое Vulkan, коротко'},{id:'2',role:'assistant',text:'## Vulkan\nЭто **графический API** нового поколения.\n\n- работает ближе к железу\n- кроссплатформенный\n1. шаг один\n\n```\nvkCreateInstance()\n```\nПодробнее: [Khronos](https://www.khronos.org/vulkan/) или https://example.com/x'}]}),
    coder_state: async()=>({busy:false,settings:{run:false,max_steps:12},last:{}}), coder_set: async(r)=>({run:!!r,max_steps:12}), coder_pick: async(f)=>f?['C:\\Users\\me\\myproject']:['C:\\Users\\me\\main.py','C:\\Users\\me\\utils.py'], coder_open: async()=>({ok:true}),
    chat_new: async()=>({id:'c3',messages:[]}), chat_send: async()=>({ok:true,chat:'a1'}), chat_stop: async()=>true, search_test: async()=>true, chat_delete: async()=>true, open_url: async()=>true,
    list_apps: async()=>[{name:'Bogestv0 MIXER',aliases:['миксер','mixer'],src:'bogestv0',path:'',found:true},{name:'Bogestv0 REMOTE',aliases:['ремоут'],src:'bogestv0',path:'',found:false},{name:'Моя программа',aliases:['моя'],src:'user',path:'C:\\Tools\\my.exe',found:true},{name:'Блокнот',aliases:['блокнот'],src:'system',path:'',found:true}],
    brain_state: async()=>brainState, brain_set: async(m,cm)=>{ if(m) brainState.mode=m; if(cm) brainState.claude_model=cm; return brainState; }, brain_test_claude: async()=>{ setTimeout(()=>window.karina.event({type:'brain_test',ok:true,text:'работаю',sec:1.2}),300); return true; },
    file_open: async(n)=>n==='скрипт.py'?{ok:false,error:'Файл не найден — возможно, его переместили или удалили.'}:{ok:true}, file_reveal: async()=>({ok:true}), files_open_folder: async()=>({ok:true}),
    file_preview: async()=>({ok:true,result:'data:image/svg+xml;utf8,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 120"><rect x="10" y="30" width="110" height="50" rx="8" fill="#cfe3f6" stroke="#1f77bd"/><text x="65" y="60" text-anchor="middle" font-family="sans-serif" font-size="14">Клиент</text><path d="M120 55H190" stroke="#1f77bd" stroke-width="2"/><rect x="190" y="30" width="110" height="50" rx="8" fill="#cfe3f6" stroke="#1f77bd"/><text x="245" y="60" text-anchor="middle" font-family="sans-serif" font-size="14">Сервер</text></svg>')}),
    send_text: async()=>true, voice_offline_status: async()=>({ready:false,size:0,downloading:false}), voice_offline_download: async()=>true, voice_offline_delete: async()=>({ready:false,size:0,downloading:false}), set_listening: async()=>true, test_voice: async()=>true, wake_up: async()=>true, save_settings: async()=>cfg
  }};
  window.__demo = () => {
    const e = window.karina.event;
    e({type:'log',kind:'you',text:'Карина, открой миксер',ts:'10:31:02'});
    e({type:'log',kind:'karina',text:'Запускаю Bogestv0 MIXER.',ts:'10:31:03'});
    e({type:'log',kind:'ignored',text:'да ладно, потом посмотрим',ts:'10:31:20'});
    e({type:'log',kind:'you',text:'сделай громче',ts:'10:31:26'});
    e({type:'log',kind:'karina',text:'Громкость 60.',ts:'10:31:27'});
    e({type:'log',kind:'error',text:'Не удалось озвучить: пример ошибки',ts:'10:31:30'});
    e({type:'level',v:0.5});
  };
})();
