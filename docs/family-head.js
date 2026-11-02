document.documentElement.dataset.theme = 'dark';
// Apply saved appearance before the first paint.
try {
  const p = JSON.parse(localStorage.getItem('vkardin-site-preferences') || '{}') || {};
  document.documentElement.dataset.theme = 'dark';
  document.documentElement.dataset.motion = p.motion === 'reduce' ? 'reduce' : 'auto';
} catch {}

// One language preference for the project websites.
try {
 const language=localStorage.getItem('vkardin-site-language')||'ru';
 for(const key of ['vg-lang','lv-lang','video-drift-lang','lang'])localStorage.setItem(key,language);
 if(document.documentElement.dataset.siteProject==='uni-homebot')document.documentElement.lang=language;
} catch {}
