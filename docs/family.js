// Localised links and project descriptions; no external requests or tracking.
(() => {
  const words = {"ru": {"title": "Другие инструменты от vkardin", "sub": "Один автор. Пять проектов для видео, скриншотов и домашнего сервера.", "site": "Открыть сайт", "code": "Исходный код", "download": "Релизы", "nav": "Проекты", "author": "Независимые инструменты · открытый код", "skip": "Перейти к содержимому"}, "uk": {"title": "Інші інструменти від vkardin", "sub": "Один автор. П’ять проєктів для відео, знімків екрана й домашнього сервера.", "site": "Відкрити сайт", "code": "Вихідний код", "download": "Релізи", "nav": "Проєкти", "author": "Незалежні інструменти · відкритий код", "skip": "Перейти до вмісту"}, "en": {"title": "More tools by vkardin", "sub": "One developer. Five projects for video, screenshots and your home server.", "site": "Visit website", "code": "Source code", "download": "Releases", "nav": "Projects", "author": "Independent tools · open source", "skip": "Skip to content"}};
  const projects = {"video-glow": {"type": {"ru": "Расширение", "uk": "Розширення", "en": "Extension"}, "desc": {"ru": "Подсветка вокруг видео в цвет кадра. Мягкое свечение, LED-лента и цвет комнаты.", "uk": "Підсвічування навколо відео в колір кадру. М’яке світло, LED-стрічка й колір кімнати.", "en": "Ambient light around web videos. Soft glow, LED strips and room colour."}}, "leveler": {"type": {"ru": "Расширение", "uk": "Розширення", "en": "Extension"}, "desc": {"ru": "Ровная громкость, разборчивые диалоги, эквалайзер и управление скоростью.", "uk": "Рівна гучність, розбірливі діалоги, еквалайзер і керування швидкістю.", "en": "Even loudness, clearer dialogue, an equalizer and playback speed controls."}}, "video-drift": {"type": {"ru": "Расширение", "uk": "Розширення", "en": "Extension"}, "desc": {"ru": "Видео поверх других окон, субтитры, сохранение позиции и заметки к моментам.", "uk": "Відео поверх інших вікон, субтитри, збереження позиції й нотатки до моментів.", "en": "Floating video with subtitles, saved playback positions and timestamped notes."}}, "snapt": {"type": {"ru": "Приложение и расширение", "uk": "Застосунок і розширення", "en": "App & extension"}, "desc": {"ru": "Скриншоты, стрелки и подписи. Приложение для Windows, macOS и Linux и расширение браузера.", "uk": "Знімки екрана, стрілки й підписи. Застосунок для Windows, macOS та Linux і розширення браузера.", "en": "Capture, annotate and export. A Windows, macOS and Linux app, plus a browser extension."}}, "uni-homebot": {"type": {"ru": "Telegram · свой сервер", "uk": "Telegram · власний сервер", "en": "Telegram · self-hosted"}, "desc": {"ru": "Домашний сервер в Telegram: приложения, мониторинг, уведомления и мини-приложение.", "uk": "Домашній сервер у Telegram: застосунки, моніторинг, сповіщення й мінізастосунок.", "en": "Your home server in Telegram: apps, monitoring, notifications and a Mini App."}}};
  let previous = '';
  function apply() {
    const code = document.documentElement.lang.slice(0,2), lang = words[code] ? code : 'en';
    if (lang === previous) return; previous = lang;
    document.querySelectorAll('[data-family-word]').forEach((el) => { el.textContent = words[lang][el.dataset.familyWord] + (el.dataset.familyWord === 'site' ? ' ↗' : ''); });
    document.querySelectorAll('[data-family-type]').forEach((el) => { el.textContent = projects[el.dataset.familyType].type[lang]; });
    document.querySelectorAll('[data-family-description]').forEach((el) => { el.textContent = projects[el.dataset.familyDescription].desc[lang]; });
    document.querySelectorAll('[data-family-site]').forEach((el) => { const id = el.dataset.familySite; el.href = 'https://vkardin.github.io/' + id + '/' + (id === 'snapt' && lang !== 'en' ? lang + '/' : ''); });
  }
  new MutationObserver(apply).observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });
  apply();
})();

// Shared site preferences on vkardin.github.io.
(() => {
  const key = 'vkardin-site-preferences';
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch {}
  function apply() {
    if (['light','dark'].includes(prefs.theme)) document.documentElement.dataset.theme = prefs.theme;
    else delete document.documentElement.dataset.theme;
    document.documentElement.dataset.motion = prefs.motion === 'reduce' ? 'reduce' : 'auto';
  }
  apply();
  const nav = document.querySelector('.family-nav-inner');
  if (!nav) return;
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'family-settings-button'; button.textContent = '⚙';
  const dialog = document.createElement('dialog'); dialog.className = 'family-settings-dialog';
  dialog.innerHTML = '<form method="dialog"><div class="family-settings-heading"><h2 id="family-settings-title"></h2><button class="family-close" value="close">×</button></div><label><span data-pref-label="theme"></span><select name="theme"><option value="auto"></option><option value="dark"></option><option value="light"></option></select></label><label><span data-pref-label="motion"></span><select name="motion"><option value="auto"></option><option value="reduce"></option></select></label><p></p></form>';
  dialog.setAttribute('aria-labelledby','family-settings-title');
  document.body.append(dialog); nav.append(button);
  const theme = dialog.querySelector('[name=theme]'), motion = dialog.querySelector('[name=motion]');
  theme.value = ['dark','light'].includes(prefs.theme) ? prefs.theme : 'auto';
  motion.value = prefs.motion === 'reduce' ? 'reduce' : 'auto';
  const copy = {
    ru:['Настройки сайта','Тема','Анимация','Как в системе','Тёмная','Светлая','Как в системе','Уменьшить','Общие настройки для всех сайтов vkardin в этом браузере.','Закрыть'],
    uk:['Налаштування сайту','Тема','Анімація','Як у системі','Темна','Світла','Як у системі','Зменшити','Спільні налаштування всіх сайтів vkardin у цьому браузері.','Закрити'],
    en:['Site settings','Theme','Motion','System','Dark','Light','System','Reduce','Shared preferences for all vkardin sites in this browser.','Close']
  };
  function localize(){
    const t=copy[document.documentElement.lang.slice(0,2)] || copy.en;
    button.setAttribute('aria-label',t[0]); button.title=t[0];
    dialog.querySelector('h2').textContent=t[0];
    dialog.querySelector('[data-pref-label=theme]').textContent=t[1];
    dialog.querySelector('[data-pref-label=motion]').textContent=t[2];
    [...theme.options].forEach((o,i)=>o.textContent=t[3+i]);
    [...motion.options].forEach((o,i)=>o.textContent=t[6+i]);
    dialog.querySelector('p').textContent=t[8]; dialog.querySelector('.family-close').setAttribute('aria-label',t[9]);
  }
  localize(); new MutationObserver(localize).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  button.addEventListener('click',()=>dialog.showModal());
  dialog.addEventListener('change',()=>{prefs={theme:theme.value,motion:motion.value};apply();try{localStorage.setItem(key,JSON.stringify(prefs));}catch{}});
  addEventListener('storage',(event)=>{if(event.key!==key)return;try{prefs=JSON.parse(event.newValue||'{}')||{};}catch{prefs={};}apply();theme.value=prefs.theme||'auto';motion.value=prefs.motion||'auto';});
})();

(() => {
const tags={"en": {"video-glow": ["Ambient light", "LED", "Room colour"], "leveler": ["Loudness", "EQ", "Speed"], "video-drift": ["Floating video", "Subtitles", "Notes"], "snapt": ["Capture", "Annotate", "Export"], "uni-homebot": ["Telegram", "Monitoring", "Self-hosted"]}, "ru": {"video-glow": ["Подсветка", "LED", "Цвет комнаты"], "leveler": ["Громкость", "Эквалайзер", "Скорость"], "video-drift": ["Плавающее видео", "Субтитры", "Заметки"], "snapt": ["Снимай", "Размечай", "Сохраняй"], "uni-homebot": ["Telegram", "Мониторинг", "Свой сервер"]}, "uk": {"video-glow": ["Підсвічування", "LED", "Колір кімнати"], "leveler": ["Гучність", "Еквалайзер", "Швидкість"], "video-drift": ["Плаваюче відео", "Субтитри", "Нотатки"], "snapt": ["Знімай", "Позначай", "Зберігай"], "uni-homebot": ["Telegram", "Моніторинг", "Свій сервер"]}};
function applyTags(){const lang=document.documentElement.lang.slice(0,2);document.querySelectorAll('[data-family-chip]').forEach(el=>{const [id,index]=el.dataset.familyChip.split(':');el.textContent=(tags[lang]||tags.en)[id][Number(index)];});}
new MutationObserver(applyTags).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});applyTags();
})();

(() => {
 const labels={ru:{try:'Попробовать',features:'Возможности',install:'Установить',download:'Скачать',donate:'Поддержать',support:'Поддержка',code:'GitHub'},uk:{try:'Спробувати',features:'Можливості',install:'Встановити',download:'Завантажити',donate:'Підтримати',support:'Підтримка',code:'GitHub'},en:{try:'Try it',features:'Features',install:'Install',download:'Download',donate:'Donate',support:'Support',code:'GitHub'}};
 function applyLanguage(){
  const lang=['ru','uk','en'].includes(document.documentElement.lang)?document.documentElement.lang:'ru';
  document.querySelectorAll('[data-family-label]').forEach(el=>el.textContent=labels[lang][el.dataset.familyLabel]);
  document.querySelectorAll('[data-lang]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.lang===lang)));
  try{localStorage.setItem('vkardin-site-language',lang);for(const k of ['vg-lang','lv-lang','video-drift-lang','lang'])localStorage.setItem(k,lang);}catch{}
 }
 document.addEventListener('click',event=>{const link=event.target.closest('a[hreflang]');if(link&&['ru','uk','en'].includes(link.hreflang)){try{localStorage.setItem('vkardin-site-language',link.hreflang);}catch{}}},true);
 new MutationObserver(applyLanguage).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
 applyLanguage();
})();
