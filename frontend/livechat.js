/* Live Chat: stages (Active -> Requesting -> Intervened -> Closed), intervention, tracked history, Chat Profile panel.
   The same conversation component is shown in a drawer from the Contacts page (window.openConversation). */
(() => {
  const PV = (window.PageViews = window.PageViews || {});

  const stageOf = l => l.stage || ((l.chat_status === 'resolved') ? 'closed' : l.intervened_by ? 'intervened' : l.requested_at ? 'requesting' : 'active');
  const waitText = since => {
    const d = CRM.lt(since); if (!d) return '';
    const m = Math.floor((Date.now() - d) / 60000);
    return m < 1 ? 'under a minute' : m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
  };
  const badge = l => {
    const st = stageOf(l);
    return {
      active: '<span class="cstat bot"><i></i>Active · bot</span>',
      requesting: `<span class="cstat warn"><i></i>Requesting${l.requested_at ? ' · waiting ' + esc(waitText(l.requested_at)) : ''}</span>`,
      intervened: `<span class="cstat ok"><i></i>Intervened by ${esc(l.intervened_by_name || 'a team member')}</span>`,
      closed: `<span class="cstat idle"><i></i>Closed${l.closed_by_name ? ' by ' + esc(l.closed_by_name) : ''}</span>`,
    }[st];
  };
  const SENDER = n => ({'menu-bot': 'Chatbot', ai: 'AI agent', 'ai-ack': 'AI agent', 'ai-handover': 'AI agent', campaign: 'Campaign', system: 'System',
    auto: 'Auto', 'auto-reply': 'Auto-reply', bot: 'Bot', agent: 'Team'}[n] || n);
  const ROBOT = new Set(['menu-bot', 'ai', 'ai-ack', 'ai-handover', 'campaign', 'system', 'auto', 'auto-reply', 'bot', 'agent', '']);
  const senderAvatar = s => ROBOT.has(s || '') ? `<div class="av s botav" title="${esc(SENDER(s))}">${ico('Bot')}</div>` : avatar(s, true);
  // Tap-to-reply options are sent as "1. Label" + a long wa.me link. Show agents the compact version the lead sees as options.
  const fmtBody = t => esc(t)
    .replace(/(\d+)\. ([^\n]+)\nhttps:\/\/wa\.me\/\d+\?text=\S+/g, (_, n, l) => `<span class="optchip"><b>${n}.</b> ${l}</span>`)
    .replace(/\s*(<span class="optchip">)/g, '$1')
    .replace(/\*([^*\n]+)\*/g, '<b>$1</b>');
  const trunc = (s, n) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

  // ---------- one conversation: chat + profile ----------
  function mount(root, id, opts = {}) {
    root.innerHTML = `<div class="cv"><div class="cv-chat"><div class="chat-h"></div><div class="msgs"></div><div class="cv-foot"></div></div><aside class="cv-prof"></aside></div>`;
    const cv = root.querySelector('.cv'), head = cv.querySelector('.chat-h'), msgs = cv.querySelector('.msgs'),
      foot = cv.querySelector('.cv-foot'), prof = cv.querySelector('.cv-prof');
    let sigH = '', sigM = '', sigF = '', sigP = '', firstDraw = true, templates = null, lastStage = null;
    const openSecs = new Set(['contact']);
    const changed = () => { if (opts.onChange) opts.onChange(lastStage); };

    const ACT = {
      intervene: () => api(`/leads/${id}/intervene`, 'POST'),
      takeover: () => api(`/leads/${id}/takeover`, 'POST'),
      release: () => confirm('Hand this chat back to the bot? It moves to Active and starts again from the welcome menu.') && api(`/leads/${id}/release`, 'POST'),
      resolve: () => confirm('Resolve this chat? It moves to Closed. A new message from the contact reopens it.') && api(`/leads/${id}/resolve`, 'POST'),
      reopen: () => api(`/leads/${id}/reopen`, 'POST'),
      called: () => api(`/leads/${id}/callback-done`, 'POST'),
    };
    cv.addEventListener('click', guard(async e => {
      const b = e.target.closest('[data-act]'); if (!b) return;
      const a = b.dataset.act;
      if (a === 'profile') { cv.classList.toggle('prof-open'); return; }
      if (a === 'back') { if (opts.onBack) opts.onBack(); return; }
      if (!ACT[a]) return;
      b.disabled = true;
      try { await ACT[a](); } finally { b.disabled = false; }
      sigH = sigF = sigP = ''; await reload(); changed();
    }));

    async function reload() {
      if (!cv.isConnected) return;
      let d, p;
      try { [d, p] = await Promise.all([api(`/leads/${id}/messages`), api(`/contacts/${id}/profile`)]); }
      catch (e) {   // e.g. 403: the chat is being handled by someone else
        if (!cv.isConnected) return;
        root.innerHTML = `<div class="empty lockedchat"><div style="font-size:30px">🔒</div><b>${esc(e.message)}</b><p class="muted" style="margin:6px 0 0">Ask a manager if you need to take it over.</p></div>`;
        return;
      }
      if (!cv.isConnected) return;
      lastStage = stageOf(d.lead);
      drawHead(d); drawMsgs(d); drawFoot(d); drawProfile(p, d);
      firstDraw = false;
    }

    function drawHead(d) {
      const l = d.lead, st = stageOf(l);
      const sig = [l.name, l.phone, st, l.intervened_by, l.needs_callback, l.waiting, l.closed_by_name].join('|');
      if (sig === sigH) return; sigH = sig;
      head.innerHTML = `${opts.onBack ? '<button class="b ghost sm back" data-act="back" aria-label="Back to conversations">←</button>' : ''}${avatar(l.name)}
        <div class="nm"><b>${esc(l.name)}</b><span>${esc(l.phone)}</span></div>${badge(l)}
        <div class="chat-acts">${l.needs_callback && st !== 'closed' ? '<button class="b sm" data-act="called" title="You phoned this contact">Mark as called</button>' : ''}${st === 'intervened' && l.intervened_by === ME.id ? '<button class="b ghost sm" data-act="release" title="Give the chat back to the bot (Active)">Release to bot</button>' : ''}${st !== 'closed' ? '<button class="b ghost sm" data-act="resolve" title="Move this chat to Closed">Resolve</button>' : ''}<button class="b ghost sm prof-toggle" data-act="profile">Profile</button></div>`;
    }

    function drawMsgs(d) {
      const sig = d.messages.map(m => m.id + m.status).join(',');
      if (sig === sigM) return; sigM = sig;
      const atBottom = firstDraw || msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 90;
      let html = '', lastDay = '';
      for (const m of d.messages) {
        const dk = CRM.dayKey(m.created_at);
        if (dk !== lastDay) { html += `<div class="dsep"><span>${esc(CRM.dayLabel(m.created_at))}</span></div>`; lastDay = dk; }
        if (m.direction === 'sys') { html += `<div class="evt"><span>${esc(m.body)}</span><small>${CRM.time(m.created_at)}</small></div>`; continue; }
        const out = m.direction === 'out';
        const tag = m.kind === 'button' ? '<span class="tap">Tapped option</span><br>' : m.kind === 'template' ? '<span class="tap">Template</span><br>' : '';
        const meta = (out ? esc(SENDER(m.sender)) + ' · ' : '') + CRM.time(m.created_at) + (out ? ' ' + CRM.tick(m.status) : '') + (m.error ? `<span class="err" title="${esc(m.error)}">${esc(trunc(m.error, 50))}</span>` : '');
        html += `<div class="mrow ${m.direction}">${out ? senderAvatar(m.sender) : ''}<div class="m ${m.direction}">${tag}${fmtBody(m.body)}<small>${meta}</small></div></div>`;
      }
      msgs.innerHTML = html || '<div class="muted" style="margin:auto">No messages yet.</div>';
      if (atBottom) msgs.scrollTop = msgs.scrollHeight;
    }

    async function drawFoot(d) {
      const l = d.lead, st = stageOf(l), mine = l.intervened_by === ME.id;
      const sig = [st, l.intervened_by, d.window_open, l.closed_by_name].join('|');
      if (sig === sigF) return; sigF = sig;
      if (st === 'closed') { foot.innerHTML = `<div class="foot-note"><span>This chat is closed${l.closed_by_name ? ' by <b>' + esc(l.closed_by_name) + '</b>' : ''}.</span><button class="b ghost" data-act="reopen">Reopen</button></div>`; return; }
      if (l.intervened_by && !mine) { foot.innerHTML = `<div class="foot-note"><b>This chat is Intervened By ${esc(l.intervened_by_name || 'someone')}</b>${can('chats_all') ? '<button class="b ghost" data-act="takeover">Take Over</button>' : ''}</div>`; return; }
      if (!l.intervened_by) {
        foot.innerHTML = `<div class="foot-note"><span>${st === 'requesting' ? 'The contact asked for a counsellor and is waiting.' : 'The bot is handling this chat.'}</span><button class="b" data-act="intervene">Intervene</button></div>`; return; }
      if (!templates) templates = (await api('/templates')).filter(t => t.status === 'APPROVED');
      foot.innerHTML = `<form class="composer" id="cmp-${id}"><input class="txt" placeholder="${d.window_open ? 'Type a reply…' : 'Window closed. Use a template →'}" ${d.window_open ? '' : 'disabled'} autocomplete="off">
        <button class="b" ${d.window_open ? '' : 'disabled'}>${ico('Tester')}Send</button>
        <select class="tp"><option value="">Send template…</option>${templates.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}</select></form>`;
      const f = foot.querySelector('form'), inp = f.querySelector('.txt');
      f.onsubmit = guard(async e => { e.preventDefault(); const t = inp.value.trim(); if (!t) return;
        inp.value = ''; await api(`/leads/${id}/send`, 'POST', {text: t}); await reload(); changed(); });
      f.querySelector('.tp').onchange = guard(async e => { if (!e.target.value) return;
        await api(`/templates/${e.target.value}/send/${id}`, 'POST'); e.target.value = ''; toast('Template sent'); await reload(); changed(); });
      if (!firstDraw) inp.focus();
    }

    // ---------- profile panel ----------
    const kv = (k, v) => `<div class="pf-kv"><span>${k}</span><b>${v}</b></div>`;
    const sec = (key, title, count, body) => `<details class="pf-sec" data-sec="${key}" ${openSecs.has(key) ? 'open' : ''}><summary>${title}${count != null ? ` <span class="n">${count}</span>` : ''}</summary><div class="pf-b">${body}</div></details>`;

    function drawProfile(p, d) {
      const sig = JSON.stringify(p) + '|' + stageOf(d.lead) + d.lead.intervened_by_name + d.lead.waiting;
      if (sig === sigP) return;
      if (prof.contains(document.activeElement) && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) && sigP) return;  // do not disturb typing
      sigP = sig;
      const st = stageOf(d.lead), cat = CRM.cat;
      const myTags = cat.tags.filter(t => p.tag_ids.includes(t.id)), freeTags = cat.tags.filter(t => !p.tag_ids.includes(t.id));
      const myLists = cat.lists.filter(x => p.list_ids.includes(x.id)), freeLists = cat.lists.filter(x => !p.list_ids.includes(x.id));
      const attrs = Object.entries(p.attrs || {});
      prof.innerHTML = `<div class="pf-top"><span>Chat Profile</span><button class="b ghost sm pf-x" data-act="profile" aria-label="Close profile">✕</button></div>
        <div class="pf-card">
          ${kv('Stage', badge(d.lead))}
          ${kv('Intervened By', esc(d.lead.intervened_by_name || '—'))}
          ${st === 'closed' ? kv('Closed By', esc(p.closed_by_name || '—') + (p.closed_at ? `<div class="muted" style="font-weight:400;font-size:12px">${esc(CRM.dateTime(p.closed_at))}</div>` : '')) : ''}
          ${kv('Last Active', esc(CRM.dateTime(p.last_active)))}
          ${kv('Template Messages', p.template_messages)}
          ${kv('Session Messages', p.session_messages)}
          ${kv('Unresolved Queries', p.unresolved)}
          ${kv('Source', esc(p.source || '—'))}
          ${kv('First Message', esc(trunc(p.first_message, 60) || '—'))}
          ${kv('WA Conversation', p.conversation_active ? '<span class="pill ready">Active</span>' : '<span class="pill idle">Inactive</span>')}
          <div class="pf-kv"><span>Opted In</span><label class="sw" title="${p.opt_state === 'opted_out' ? 'Replied STOP or switched off' : ''}"><input type="checkbox" id="pf-opt-${id}" ${p.consent ? 'checked' : ''}><i></i></label></div>
        </div>
        ${sec('act', 'Activity', p.activity.length, p.activity.length ? p.activity.map(a => `<div class="act"><span>${esc(a.body)}</span><small>${esc(CRM.dateTime(a.created_at))}</small></div>`).join('') : '<span class="muted">Nothing tracked yet.</span>')}
        ${sec('contact', 'Contact', null, `
          <label>Name</label><input data-f="name" value="${esc(p.name)}">
          <label>Phone</label><input value="${esc(p.phone)}" disabled>
          <label>Email</label><input data-f="email" value="${esc(p.email || '')}" placeholder="—">
          <label>Course</label><select data-f="course">${courseOpts(p.course, '— none —')}</select>
          <label>Lead status</label><select data-f="status">${statusOpts(p.status)}</select>`)}
        ${sec('tags', 'Tags', myTags.length, `<div class="chips">${myTags.map(t => CRM.tagChip(t, true)).join('') || '<span class="muted">No tags</span>'}</div>
          <select id="pf-addtag-${id}"><option value="">+ Add tag…</option>${freeTags.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}<option value="__new">New tag…</option></select>`)}
        ${sec('lists', 'Lists', myLists.length, `<div class="chips">${myLists.map(x => CRM.listChip(x, true)).join('') || '<span class="muted">Not in any list</span>'}</div>
          <select id="pf-addlist-${id}"><option value="">+ Add to list…</option>${freeLists.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}<option value="__new">New list…</option></select>`)}
        ${sec('attrs', 'Attributes', attrs.length, `${attrs.map(([k, v]) => `<div class="attr"><input data-ak value="${esc(k)}" placeholder="name"><input data-av value="${esc(v)}" placeholder="value"><button class="b ghost sm" data-adel aria-label="Remove">✕</button></div>`).join('')}
          <div class="attr"><input data-ak placeholder="new attribute, e.g. city"><input data-av placeholder="value"><button class="b ghost sm" data-aadd>Add</button></div>
          <p class="muted" style="font-size:12px;margin:6px 0 0">Use in templates as {{attribute_name}}.</p>`)}
        ${sec('notes', 'Notes', null, `<textarea data-f="notes" placeholder="Internal notes about this contact">${esc(p.notes || '')}</textarea>`)}
        ${sec('camp', 'Campaigns', p.campaigns.length, p.campaigns.length ? p.campaigns.map(c => `<div class="kv" style="padding:7px 0"><span>${esc(c.name)}<div class="muted" style="font-size:12px">${esc(CRM.dateTime(c.created_at))}</div></span>${CRM.statusPill(c.status)}</div>`).join('') : '<span class="muted">No campaigns sent to this contact.</span>')}`;
      wireProfile(p);
    }

    function wireProfile(p) {
      const patch = guard(async body => { await api(`/contacts/${id}`, 'PATCH', body); sigP = ''; await reload(); changed(); });
      prof.querySelectorAll('details.pf-sec').forEach(dt => dt.addEventListener('toggle', () => dt.open ? openSecs.add(dt.dataset.sec) : openSecs.delete(dt.dataset.sec)));
      prof.querySelector(`#pf-opt-${id}`).onchange = e => patch({consent: e.target.checked});
      prof.querySelectorAll('[data-f]').forEach(el => el.onchange = () => { if (el.dataset.f === 'name' && !el.value.trim()) { el.value = p.name; return; } patch({[el.dataset.f]: el.value}); });
      const setIds = guard(async (kind, ids) => { await api(`/contacts/${id}/${kind}`, 'POST', {ids}); sigP = ''; await CRM.catalog(); await reload(); changed(); });
      prof.querySelectorAll('[data-untag]').forEach(b => b.onclick = () => setIds('tags', p.tag_ids.filter(t => t !== +b.dataset.untag)));
      prof.querySelectorAll('[data-unlist]').forEach(b => b.onclick = () => setIds('lists', p.list_ids.filter(t => t !== +b.dataset.unlist)));
      prof.querySelector(`#pf-addtag-${id}`).onchange = guard(async e => {
        let v = e.target.value; if (!v) return;
        if (v === '__new') { const name = prompt('Tag name?'); if (!name) { e.target.value = ''; return; } v = (await api('/tags', 'POST', {name})).id; }
        await setIds('tags', [...p.tag_ids, +v]); });
      prof.querySelector(`#pf-addlist-${id}`).onchange = guard(async e => {
        let v = e.target.value; if (!v) return;
        if (v === '__new') { const name = prompt('List name?'); if (!name) { e.target.value = ''; return; } v = (await api('/lists', 'POST', {name})).id; }
        await setIds('lists', [...p.list_ids, +v]); });
      const saveAttrs = guard(async () => {
        const out = {};
        prof.querySelectorAll('.attr').forEach(r => { const k = r.querySelector('[data-ak]').value.trim(), v = r.querySelector('[data-av]').value.trim(); if (k && v) out[k] = v; });
        await api(`/contacts/${id}/attrs`, 'PUT', {attrs: out}); sigP = ''; await reload(); });
      prof.querySelectorAll('.attr [data-ak],.attr [data-av]').forEach(el => el.onchange = saveAttrs);
      prof.querySelectorAll('[data-adel]').forEach(b => b.onclick = () => { b.closest('.attr').querySelector('[data-av]').value = ''; saveAttrs(); });
      const add = prof.querySelector('[data-aadd]'); if (add) add.onclick = saveAttrs;
    }

    reload().catch(e => toast(e.message));
    const timer = setInterval(() => { if (!cv.isConnected) { clearInterval(timer); return; } if (!document.hidden) reload().catch(() => {}); }, 4000);
    return {reload};
  }
  window.mountConversation = mount;

  // Contact drawer: used from Contacts, Campaigns and anywhere a contact is clicked.
  window.openConversation = async (id, name, onChange) => {
    await CRM.catalog();
    const d = CRM.drawer({title: name || 'Contact'});
    mount(d.body, id, {onChange});
  };

  // ---------- Live Chat page ----------
  PV.Inbox = async function () {
    await CRM.catalog();
    const STAGES = [['active', 'Active'], ['requesting', 'Requesting'], ['intervened', 'Intervened'], ['closed', 'Closed']];
    let tab = sessionStorage.getItem('lcs') || 'active', q = '', onlyUnread = false, cur = +sessionStorage.getItem('cur') || null, mounted = null;
    const recent = () => JSON.parse(sessionStorage.getItem('lcrecent') || '[]');
    const collapsed = g => sessionStorage.getItem('lcg_' + g) === '1';
    $('#main').innerHTML = `<div class="lc-stages" id="lcstages"></div><div class="lc-strip" id="lcstrip"></div>
      <div class="card lc ${sessionStorage.getItem('detail') === '1' ? 'detail' : ''}" id="lc">
      <div class="lc-list"><div class="lc-tools"><div class="row" style="flex-wrap:nowrap;gap:8px"><input id="lcq" placeholder="Search name or mobile number" autocomplete="off" style="flex:1;min-width:0"><button class="b ghost sm" id="lcun" title="Show only chats with unread messages">Unread</button></div></div>
        <div class="lc-items" id="lcitems"></div></div>
      <div class="lc-view" id="lcview"></div></div>
      ${cfg.mode === 'mock' ? `<div class="card"><h2>Simulate an incoming message <span class="muted" style="font-weight:400">(mock mode only)</span></h2>
        <form id="sim" class="row"><input name="phone" placeholder="Phone" required><input name="name" placeholder="Name"><input name="text" placeholder="e.g. hi, Talk to a counsellor, STOP" required style="flex:1"><button class="b ghost">Simulate</button></form></div>` : ''}`;

    const emptyView = () => { $('#lcview').innerHTML = `<div class="lc-empty"><div class="lc-empty-ic">${ico('Chats')}</div><h3>Select a chat to continue!</h3>
      <ul><li><b>Active</b> contacts are being handled by the bot.</li><li><b>Requesting</b> contacts asked for a counsellor and are waiting for someone to pick them up.</li>
      <li><b>Intervened</b> chats are taken by a team member. Press <b>Intervene</b> to take one.</li><li><b>Closed</b> chats are resolved. A new message reopens them.</li></ul></div>`; };

    const markHtml = c => {
      if (c.stage === 'requesting') return `<span class="cmark warn">Waiting ${esc(waitText(c.requested_at))}</span>`;
      if (c.stage === 'intervened') return `<span class="cmark ok">${esc(c.intervened_by_name || '')}</span>`;
      if (c.stage === 'closed') return `<span class="cmark idle">Closed${c.closed_by_name ? ' · ' + esc(c.closed_by_name) : ''}</span>`;
      return '';
    };
    const itemHtml = c => `<div class="conv ${c.id === cur ? 'on' : ''}" data-id="${c.id}" data-name="${esc(c.name)}">${avatar(c.name)}<div class="tx"><b><span class="nm">${esc(c.name)}</span><span class="when">${esc(CRM.short(c.last_at))}</span></b>
      <small>${c.last_dir === 'out' ? '↩ ' : ''}${esc(c.last_body || '')}</small>${markHtml(c)}</div>${c.unread ? `<span class="ubadge">${c.unread}</span>` : ''}</div>`;
    const group = (g, title, items) => `<div class="lc-group ${collapsed(g) ? 'shut' : ''}" data-g="${g}"><span>${title} (${items.length})</span><i>▾</i></div><div class="lc-gbody" ${collapsed(g) ? 'hidden' : ''}>${items.map(itemHtml).join('')}</div>`;

    const drawList = data => {
      $('#lcstages').innerHTML = STAGES.map(([k, l]) => `<button class="${k === tab ? 'on' : ''}" data-s="${k}"><span>${l}</span><b>${data.counts[k] || 0}</b></button>`).join('');
      const box = $('#lcitems'), top = box.scrollTop; let html;
      if (tab === 'intervened') {
        const mine = data.items.filter(c => c.group === 'me'), others = data.items.filter(c => c.group === 'others');
        html = group('me', 'Intervened by me', mine) + (data.sees_all ? group('others', 'Intervened by others', others) : '');
      } else html = data.items.map(itemHtml).join('');
      box.innerHTML = html || `<div class="empty">${tab === 'requesting' ? 'No one is waiting for a counsellor.' : tab === 'closed' ? 'No closed chats yet.' : 'No chats here.'}</div>`;
      box.scrollTop = top;
    };
    const drawStrip = () => {
      const r = recent();
      $('#lcstrip').innerHTML = r.map(c => `<div class="sc ${c.id === cur ? 'on' : ''}" data-id="${c.id}" title="${esc(c.name)}">${avatar(c.name)}<span>${esc(trunc(c.name, 12))}</span><button class="x" data-close="${c.id}" aria-label="Close">×</button></div>`).join('');
      $('#lcstrip').style.display = r.length ? '' : 'none';
      fit();
    };
    // Fill the rest of the page: the panel runs from where it starts down to the bottom of the window.
    const fit = () => {
      const lc = $('#lc'); if (!lc) return;
      const top = lc.getBoundingClientRect().top + window.scrollY, h = Math.max(420, window.innerHeight - top - 16);
      if (window.matchMedia('(max-width:900px)').matches) { lc.style.height = ''; $('#lcview').style.height = h + 'px'; }
      else { lc.style.height = h + 'px'; $('#lcview').style.height = ''; }
    };
    window.addEventListener('resize', fit);
    const remember = (id, name) => { const r = recent().filter(c => c.id !== id); r.unshift({id, name}); sessionStorage.setItem('lcrecent', JSON.stringify(r.slice(0, 8))); };

    const onChange = stage => { if (stage && stage !== tab) { tab = stage; sessionStorage.setItem('lcs', tab); } refreshList(); };
    function showConv(id, name) {
      cur = id; sessionStorage.setItem('cur', id); sessionStorage.setItem('detail', '1');
      if (name) remember(id, name);
      $('#lc').classList.add('detail'); drawStrip();
      document.querySelectorAll('#lcitems .conv').forEach(el => el.classList.toggle('on', +el.dataset.id === id));
      if (mounted === id) return; mounted = id;
      mountConversation($('#lcview'), id, {onChange, onBack: () => { sessionStorage.setItem('detail', '0'); $('#lc').classList.remove('detail'); }});
    }
    let lastData = null;
    async function refreshList() {
      const data = await api(`/livechat/conversations?filter=${tab}&q=${encodeURIComponent(q)}${onlyUnread ? '&unread=1' : ''}`);
      lastData = data; drawList(data);
      $('#lcun').classList.toggle('on', onlyUnread);
      if (window.setNavBadge) window.setNavBadge('Inbox', data.counts.alert);
      return data;
    }
    await refreshList(); drawStrip();
    if (cur && sessionStorage.getItem('detail') === '1') { const nm = (recent().find(c => c.id === cur) || {}).name; showConv(cur, nm); } else emptyView();
    $('#lcitems').onclick = e => {
      const g = e.target.closest('.lc-group');
      if (g) { const k = g.dataset.g, shut = !collapsed(k); sessionStorage.setItem('lcg_' + k, shut ? '1' : '0'); g.classList.toggle('shut', shut); g.nextElementSibling.hidden = shut; return; }
      const el = e.target.closest('.conv'); if (el) showConv(+el.dataset.id, el.dataset.name); };
    $('#lcstages').onclick = e => { const b = e.target.closest('[data-s]'); if (b) { tab = b.dataset.s; sessionStorage.setItem('lcs', tab); refreshList(); } };
    $('#lcstrip').onclick = e => {
      const x = e.target.closest('[data-close]');
      if (x) { e.stopPropagation(); const id = +x.dataset.close; sessionStorage.setItem('lcrecent', JSON.stringify(recent().filter(c => c.id !== id))); if (id === cur) { cur = null; mounted = null; sessionStorage.setItem('detail', '0'); $('#lc').classList.remove('detail'); emptyView(); } drawStrip(); return; }
      const el = e.target.closest('.sc'); if (el) showConv(+el.dataset.id, (recent().find(c => c.id === +el.dataset.id) || {}).name); };
    $('#lcq').oninput = CRM.debounce(e => { q = e.target.value; refreshList(); }, 250);
    $('#lcun').onclick = () => { onlyUnread = !onlyUnread; refreshList(); };
    if ($('#sim')) $('#sim').onsubmit = guard(async e => { e.preventDefault(); const f = new FormData(e.target);
      await api('/mock/incoming', 'POST', {phone: f.get('phone'), name: f.get('name'), text: f.get('text')}); e.target.text.value = ''; await refreshList(); });
    poll(refreshList, 5000);
  };
})();
