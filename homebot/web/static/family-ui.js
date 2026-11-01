// vkardin UI 1.0.0 — packaged locally with each application.
(() => {
  function fillRange(input) {
    const min = input.min === '' ? 0 : Number(input.min);
    const max = input.max === '' ? 100 : Number(input.max);
    const value = Number(input.value);
    const percentage = max > min ? Math.max(0, Math.min(100, (value - min) / (max - min) * 100)) : 0;
    input.style.setProperty('--p', percentage + '%');
  }
  self.VkUI = Object.freeze({ version: '1.0.0', fillRange });
  document.addEventListener('input', event => { if (event.target.type === 'range') fillRange(event.target); }, true);
  const init = () => document.querySelectorAll('input[type=range]').forEach(fillRange);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true}); else init();
})();
