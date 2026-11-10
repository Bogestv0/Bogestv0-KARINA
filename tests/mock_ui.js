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
