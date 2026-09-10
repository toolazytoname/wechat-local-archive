/* Local dialogs only. No remote assets, inline handlers or HTML injection. */
(() => {
  const byId = id => document.getElementById(id);
  const narrow = window.matchMedia('(max-width: 800px)');
  let active = null;
  let returnTo = null;
  const focusable = root => [...root.querySelectorAll('button, input, select, textarea, a[href], [tabindex]')]
    .filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length && !node.closest('[inert]'));
  function close(restore = true) {
    if (!active) return;
    if (active === 'drawer') {
      byId('drawer').classList.add('hidden');
      byId('archive').inert = false;
      byId('setup').inert = false;
    } else {
      byId('rail').classList.remove('open');
      byId('rail').removeAttribute('role');
      byId('rail').removeAttribute('aria-modal');
      byId('stage').inert = false;
      byId('open-rail').setAttribute('aria-expanded', 'false');
    }
    const previous = returnTo;
    active = null;
    returnTo = null;
    if (restore && previous?.isConnected && previous.getClientRects().length) previous.focus();
  }
  function open(kind) {
    close(false);
    if (kind === 'rail' && !narrow.matches) return;
    active = kind;
    returnTo = document.activeElement;
    const root = byId(kind);
    if (kind === 'drawer') {
      root.classList.remove('hidden');
      byId('archive').inert = true;
      byId('setup').inert = true;
    } else {
      root.classList.add('open');
      root.setAttribute('role', 'dialog');
      root.setAttribute('aria-modal', 'true');
      byId('stage').inert = true;
      byId('open-rail').setAttribute('aria-expanded', 'true');
    }
    (kind === 'drawer' ? byId('ex-scope') : byId('conv-q')).focus();
  }
  document.addEventListener('keydown', event => {
    if (!active) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    else if (event.key === 'Tab') {
      const items = focusable(byId(active));
      const position = items.indexOf(document.activeElement);
      if (!items.length) { event.preventDefault(); return; }
      if (event.shiftKey && position <= 0) { event.preventDefault(); items.at(-1).focus(); }
      else if (!event.shiftKey && (position < 0 || position === items.length - 1)) {
        event.preventDefault(); items[0].focus();
      }
    }
  });
  byId('drawer').addEventListener('click', event => { if (event.target === byId('drawer')) close(); });
  narrow.addEventListener('change', () => { if (active === 'rail') close(); });
  window.archiveUI = {open, close};
})();
