// Подставляет фальшивый pywebview для проверки интерфейса в обычном браузере.
(() => {
  const cfg = {name:'Карина',name_aliases:['карина','карену'],wake_required:true,followup_seconds:8,ui:{theme:'auto'},
    stt:{model:'small',mic_device:null,vad_threshold:0.012},tts:{enabled:true,engine:'auto',rate:0},search_engine:'google',llm:{enabled:false,has_key:false,api_key:''},apps:[],commands:[{phrases:['включи запись'],do:{type:'keys',value:'ctrl+shift+r'},reply:'Запись включена'}]};
  const st = {online:true,model:'small',voice:'online',tts_warning:'',state:'idle',sleeping:false,listening:true,name:'Карина',plugin_errors:[]};
  window.pywebview = {api:{
    get_state: async()=>({status:st,config:cfg,mics:[{id:1,name:'Микрофон (USB Audio)'},{id:2,name:'Микрофон (Realtek)'}]}),
    get_status: async()=>st,
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
    chat_get: async()=>({id:'a1',messages:[{id:'1',role:'user',text:'Объясни, что такое Vulkan, коротко'},{id:'2',role:'assistant',text:'## Vulkan\nЭто **графический API** нового поколения.\n\n- работает ближе к железу\n- кроссплатформенный\n1. шаг один\n\n```\nvkCreateInstance()\n```\nПодробнее: [Khronos](https://www.khronos.org/vulkan/) или https://example.com/x'}]}),
    chat_new: async()=>({id:'c3',messages:[]}), chat_send: async()=>({ok:true,chat:'a1'}), chat_stop: async()=>true, chat_delete: async()=>true, open_url: async()=>true,
    list_apps: async()=>[{name:'Bogestv0 MIXER',aliases:['миксер','mixer'],src:'bogestv0',path:'',found:true},{name:'Bogestv0 REMOTE',aliases:['ремоут'],src:'bogestv0',path:'',found:false},{name:'Моя программа',aliases:['моя'],src:'user',path:'C:\\Tools\\my.exe',found:true},{name:'Блокнот',aliases:['блокнот'],src:'system',path:'',found:true}],
    send_text: async()=>true, set_listening: async()=>true, test_voice: async()=>true, wake_up: async()=>true, save_settings: async()=>cfg
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
