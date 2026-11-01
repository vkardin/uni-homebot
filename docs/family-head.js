// Apply saved appearance before the first paint.
try {
  const p = JSON.parse(localStorage.getItem('vkardin-site-preferences') || '{}') || {};
  if (['light','dark'].includes(p.theme)) document.documentElement.dataset.theme = p.theme;
  document.documentElement.dataset.motion = p.motion === 'reduce' ? 'reduce' : 'auto';
} catch {}
