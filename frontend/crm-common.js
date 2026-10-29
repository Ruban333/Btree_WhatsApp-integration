/* Shared helpers for the Live Chat, Contacts and Campaigns pages. Loaded before them; they use window.CRM.
   Relies on globals from the main page script at call time: $, esc, api, toast, guard, avatar, pill, ico. */
(() => {
  const CRM = (window.CRM = {});

  // Timestamps from the server are UTC "YYYY-MM-DD HH:MM:SS". Show them in the viewer's local time.
  CRM.lt = s => (s ? new Date(String(s).replace(' ', 'T') + 'Z') : null);
  CRM.time = s => { const d = CRM.lt(s); return d ? d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : ''; };
  CRM.dateTime = s => { const d = CRM.lt(s); return d ? d.toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}) : '—'; };
  CRM.dayKey = s => { const d = CRM.lt(s); return d ? d.toDateString() : ''; };
  CRM.dayLabel = s => {
    const d = CRM.lt(s); if (!d) return '';
    const t = new Date(), y = new Date(Date.now() - 864e5);
    if (d.toDateString() === t.toDateString()) return 'Today';
    if (d.toDateString() === y.toDateString()) return 'Yesterday';
    return d.toLocaleDateString([], {day: '2-digit', month: '2-digit', year: 'numeric'});
  };
  CRM.ago = s => {
    const d = CRM.lt(s); if (!d) return '—';
    const sec = (Date.now() - d) / 1000;
    return sec < 60 ? 'just now' : sec < 3600 ? Math.floor(sec / 60) + ' min ago' : sec < 86400 ? Math.floor(sec / 3600) + ' h ago' : d.toLocaleDateString();
  };
  CRM.short = s => {  // list timestamps: time today, else date
    const d = CRM.lt(s); if (!d) return '';
    return d.toDateString() === new Date().toDateString() ? CRM.time(s) : d.toLocaleDateString([], {day: 'numeric', month: 'short'});
  };

  // Tags and lists (names only) are needed on several pages.
  CRM.cat = {tags: [], lists: [], segments: []};
  CRM.catalog = async () => {
    const [tags, lists, segments] = await Promise.all([api('/tags'), api('/lists'), api('/segments')]);
    CRM.cat = {tags, lists, segments};
    return CRM.cat;
  };
  CRM.tagChip = (t, remove) => `<span class="tagc" style="--c:${esc(t.color || '#1a5bec')}">${esc(t.name)}${remove ? `<button type="button" class="x" data-untag="${t.id}" aria-label="Remove tag">×</button>` : ''}</span>`;
  CRM.listChip = (l, remove) => `<span class="tagc list">${esc(l.name)}${remove ? `<button type="button" class="x" data-unlist="${l.id}" aria-label="Remove from list">×</button>` : ''}</span>`;

  // Slide-over panel (like the contact view in AiSensy). Closes on Esc, on a click outside, and when the page changes.
  CRM.drawer = (opts = {}) => {
    const scrim = document.createElement('div');
    scrim.className = 'drawer-scrim';
    scrim.innerHTML = `<div class="drawer" style="width:${opts.width || 'min(1180px,100vw)'}">
      <div class="drawer-h"><button class="b ghost sm" data-close aria-label="Close">✕</button><b class="drawer-t">${esc(opts.title || '')}</b><span class="sp"></span><div class="drawer-act"></div></div>
      <div class="drawer-b"></div></div>`;
    document.body.appendChild(scrim);
    const api_ = {el: scrim, body: scrim.querySelector('.drawer-b'), actions: scrim.querySelector('.drawer-act'), title: scrim.querySelector('.drawer-t')};
    let closed = false;
    api_.close = () => { if (closed) return; closed = true; document.removeEventListener('keydown', onKey); scrim.remove(); if (opts.onClose) opts.onClose(); };
    const onKey = e => { if (e.key === 'Escape' && document.body.lastElementChild === scrim) api_.close(); };
    document.addEventListener('keydown', onKey);
    scrim.addEventListener('mousedown', e => { if (e.target === scrim) api_.close(); });
    scrim.querySelector('[data-close]').onclick = api_.close;
    return api_;
  };

  CRM.modal = (html, wide) => {
    const m = document.createElement('div');
    m.className = 'modal';
    m.innerHTML = `<div class="card" style="text-align:left;max-width:${wide || 520}px;max-height:92vh;overflow:auto">${html}</div>`;
    document.body.appendChild(m);
    const close = () => { document.removeEventListener('keydown', onKey); m.remove(); };
    const onKey = e => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    m.addEventListener('mousedown', e => { if (e.target === m) close(); });
    return {el: m, card: m.firstElementChild, close};
  };

  CRM.closeOverlays = () => document.querySelectorAll('.drawer-scrim, body > .modal').forEach(x => x.remove());

  // Debounce for search boxes.
  CRM.debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  CRM.statusPill = st => `<span class="pill ${esc(st)}">${esc(st)}</span>`;
  CRM.tick = st => st === 'read' ? '<span class="tick read" title="Read">✓✓</span>' : st === 'delivered' ? '<span class="tick" title="Delivered">✓✓</span>'
    : st === 'sent' ? '<span class="tick" title="Sent">✓</span>' : st === 'failed' ? '<span class="tick bad" title="Failed">⚠</span>' : '';
})();
