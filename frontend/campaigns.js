/* Campaigns: audience from lists, tags, segments or filters; progress and stats; pause / resume / retry / duplicate. */
(() => {
  const PV = (window.PageViews = window.PageViews || {});
  const pct = (n, d) => (d ? Math.round((100 * n) / d) + '%' : '–');
  const LIVE = new Set(['running', 'scheduled']);

  const audienceText = a => {
    const cat = CRM.cat, parts = [];
    const names = (ids, arr) => (ids || []).map(i => (arr.find(x => x.id === +i) || {}).name).filter(Boolean);
    if ((a.lists || []).length) parts.push('list: ' + names(a.lists, cat.lists).join(' or '));
    if ((a.tags || []).length) parts.push('tag: ' + names(a.tags, cat.tags).join(' or '));
    if (a.segment) parts.push('segment: ' + ((cat.segments.find(s => s.id === +a.segment) || {}).name || '?'));
    if (a.course) parts.push(a.course);
    if (a.status) parts.push('status ' + a.status);
    return parts.join(' · ') || 'All opted-in contacts';
  };
  const progress = b => {
    const total = b.audience_total || 0, done = b.stats.attempted;
    return total ? Math.min(100, Math.round((100 * done) / total)) : (b.status === 'completed' ? 100 : 0);
  };
  const bar = p => `<div class="bar" style="margin:0"><i style="width:${p}%"></i></div>`;

  // ---------- page ----------
  PV.Campaigns = async function () {
    await CRM.catalog();
    let list = await api('/broadcasts');
    $('#main').innerHTML = `<div class="card"><div class="sec" style="margin-top:0"><h2>Campaigns</h2><span class="sp"></span><button class="b" id="newc">+ New campaign</button></div>
      <p class="muted" style="margin-top:0">Send an approved template to a group of opted-in contacts. Messages go out a few seconds apart to protect your number.</p>
      <div class="tbl"><table id="ctab"></table></div></div>`;
    const draw = () => {
      $('#ctab').innerHTML = `<tr><th>Campaign</th><th>Audience</th><th>Status</th><th>Progress</th><th>Delivered</th><th>Read</th><th>Replied</th><th>Failed</th><th>Created</th></tr>` +
        (list.map(b => { const s = b.stats, sent = s.sent;
          return `<tr class="crow" data-id="${b.id}"><td><b>${esc(b.name)}</b><div class="muted" style="font-size:12.5px">${esc(b.template_name)}</div></td>
            <td class="muted" style="max-width:220px;font-size:13px">${esc(audienceText(b.audience))}</td><td>${CRM.statusPill(b.status)}</td>
            <td style="min-width:120px"><div style="font-size:12.5px;margin-bottom:4px">${s.attempted}${b.audience_total ? ' / ' + b.audience_total : ''}</div>${bar(progress(b))}</td>
            <td>${s.delivered} <span class="muted">${pct(s.delivered, sent)}</span></td><td>${s.read} <span class="muted">${pct(s.read, sent)}</span></td>
            <td>${s.replied} <span class="muted">${pct(s.replied, sent)}</span></td><td>${s.failed ? `<b style="color:var(--bad)">${s.failed}</b>` : 0}</td>
            <td class="muted" style="font-size:12.5px">${esc(CRM.dateTime(b.created_at))}<div>${esc(b.created_by || '')}</div></td></tr>`; }).join('') || '<tr><td colspan="9"><div class="empty">No campaigns yet. Create one to message a list, tag or segment.</div></td></tr>');
      $('#ctab').querySelectorAll('.crow').forEach(tr => tr.onclick = () => openDetail(+tr.dataset.id));
    };
    draw();
    $('#newc').onclick = () => newCampaign(() => render());
    poll(async () => { list = await api('/broadcasts'); draw(); }, 5000);
  };

  // ---------- new campaign ----------
  async function newCampaign(done) {
    const tpls = (await api('/templates')).filter(t => t.status === 'APPROVED'), cat = CRM.cat;
    if (!tpls.length) { toast('Approve a template first (Templates page).'); return; }
    const m = CRM.modal(`<h2>New campaign</h2><form id="ncf">
      <label>Campaign name</label><input id="ncn" required placeholder="e.g. October batch reminder" style="width:100%">
      <label>Template</label><select id="nct" style="width:100%">${tpls.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}</select>
      <div class="chat" style="border-radius:12px;padding:12px;margin-top:8px"><div class="m out" style="max-width:100%" id="ncp"></div></div>
      <h2 style="margin:20px 0 4px">Audience</h2><p class="muted" style="margin:0 0 6px;font-size:12.5px">Only contacts who opted in are included. Leave everything empty to include all of them.</p>
      <label>In any of these lists</label>${(cat.lists.length ? cat.lists.map(x => `<button type="button" class="tagc pickc" data-g="lists" data-id="${x.id}">${esc(x.name)}</button>`).join('') : '<span class="muted">No lists yet</span>')}
      <label>With any of these tags</label>${(cat.tags.length ? cat.tags.map(x => `<button type="button" class="tagc pickc" data-g="tags" data-id="${x.id}" style="--c:${esc(x.color)}">${esc(x.name)}</button>`).join('') : '<span class="muted">No tags yet</span>')}
      <div class="row" style="align-items:flex-start;margin-top:6px"><div style="flex:1;min-width:170px"><label>Matching segment</label><select id="ncs" style="width:100%"><option value="">— none —</option>${cat.segments.map(s => `<option value="${s.id}">${esc(s.name)} (${s.contacts})</option>`).join('')}</select></div>
        <div style="flex:1;min-width:170px"><label>Course</label><select id="ncc" style="width:100%">${courseOpts('', 'Any course')}</select></div>
        <div style="flex:1;min-width:140px"><label>Status</label><select id="ncst" style="width:100%"><option value="">Any status</option>${STATUSES.map(s => `<option>${s}</option>`).join('')}</select></div></div>
      <div class="segprev" id="ncaud"></div>
      <h2 style="margin:20px 0 6px">When</h2>
      <div class="row"><label class="row" style="gap:6px;margin:0;color:var(--ink)"><input type="radio" name="when" value="now" checked> Send now</label>
        <label class="row" style="gap:6px;margin:0;color:var(--ink)"><input type="radio" name="when" value="later"> Schedule</label><input type="datetime-local" id="ncw" disabled></div>
      <div class="row" style="margin-top:20px;justify-content:flex-end"><button type="button" class="b ghost" data-x>Cancel</button><button class="b" id="ncgo">Create campaign</button></div></form>`, 760);
    const C = m.card, body = () => ({name: C.querySelector('#ncn').value, template_id: +C.querySelector('#nct').value,
      lists: [...C.querySelectorAll('[data-g=lists].on')].map(b => +b.dataset.id), tags: [...C.querySelectorAll('[data-g=tags].on')].map(b => +b.dataset.id),
      segment: +C.querySelector('#ncs').value || null, course: C.querySelector('#ncc').value, status: C.querySelector('#ncst').value});
    let count = 0;
    const preview = CRM.debounce(async () => {
      const box = C.querySelector('#ncaud');
      const r = await api('/broadcasts/audience', 'POST', body()).catch(() => null);
      if (!r) return; count = r.count;
      box.innerHTML = r.count ? `<b>${r.count}</b> contact${r.count === 1 ? '' : 's'} will receive this${r.sample.length ? ': ' + r.sample.map(s => esc(s.name)).join(', ') + (r.count > r.sample.length ? '…' : '') : ''}${r.not_opted_in ? `<div class="muted" style="font-size:12.5px;margin-top:4px">${r.not_opted_in} contact${r.not_opted_in === 1 ? '' : 's'} not opted in are left out.</div>` : ''}`
        : '<span style="color:var(--bad)">No opted-in contacts match this audience.</span>';
    }, 250);
    const showTpl = () => { const t = tpls.find(x => x.id === +C.querySelector('#nct').value);
      C.querySelector('#ncp').textContent = (t.body.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, k) => ({name: 'Asha', course: 'Full Stack Development', institute: 'BTree Systems'}[k] || `{{${k}}}`))) + (t.buttons.length ? '\n\n' + t.buttons.map((b, i) => `${i + 1}. ${b.text}`).join('\n') : ''); };
    C.querySelectorAll('.pickc').forEach(b => b.onclick = () => { b.classList.toggle('on'); preview(); });
    ['#ncs', '#ncc', '#ncst'].forEach(s => C.querySelector(s).onchange = preview);
    C.querySelector('#nct').onchange = showTpl; showTpl(); preview();
    C.querySelectorAll('[name=when]').forEach(r => r.onchange = () => { C.querySelector('#ncw').disabled = r.value !== 'later' || !r.checked; });
    C.querySelector('[data-x]').onclick = m.close;
    C.querySelector('form').onsubmit = guard(async e => { e.preventDefault();
      const b = body(), later = C.querySelector('[name=when]:checked').value === 'later', w = C.querySelector('#ncw').value;
      if (later && !w) { toast('Pick a date and time'); return; }
      b.scheduled_at = later ? new Date(w).toISOString().slice(0, 19).replace('T', ' ') : null;
      if (!later && !confirm(`Send “${b.name}” to ${count} contact${count === 1 ? '' : 's'} now?`)) return;
      await api('/broadcasts', 'POST', b); m.close(); toast(later ? 'Campaign scheduled' : 'Campaign started'); done(); });
  }

  // ---------- campaign detail ----------
  async function openDetail(id) {
    await CRM.catalog();
    let filter = 'all', q = '', timer = null;
    const d = CRM.drawer({title: 'Campaign', width: 'min(980px,100vw)', onClose: () => clearInterval(timer)});
    d.body.style.overflow = 'auto';
    const draw = async () => {
      if (!d.el.isConnected) { clearInterval(timer); return; }
      const b = await api('/broadcasts/' + id), s = b.stats, sent = s.sent;
      d.title.textContent = b.name;
      const can_ = {pause: ['running', 'scheduled'].includes(b.status), resume: ['paused', 'draft'].includes(b.status), cancel: ['running', 'scheduled', 'paused', 'draft'].includes(b.status),
        retry: ['completed', 'cancelled'].includes(b.status), del: b.status !== 'running'};
      const rec = b.recipients.filter(r => (filter === 'all' || (filter === 'replied' ? r.replied : filter === 'delivered' ? ['delivered', 'read'].includes(r.status) : r.status === filter))
        && (!q || (r.name + r.phone).toLowerCase().includes(q.toLowerCase())));
      const tile = (l, v, sub) => `<div class="card stat sm" style="margin:0"><div class="lab">${l}</div><b>${v}</b>${sub ? `<span class="sub">${sub}</span>` : ''}</div>`;
      const keepScroll = d.body.scrollTop;
      d.body.innerHTML = `<div style="padding:20px 24px">
        <div class="row" style="gap:10px;margin-bottom:6px">${CRM.statusPill(b.status)}<span class="muted">Template <b style="color:var(--ink)">${esc(b.template_name)}</b></span><span class="sp" style="flex:1"></span>
          ${can_.pause ? '<button class="b ghost sm" data-a="pause">Pause</button>' : ''}${can_.resume ? `<button class="b sm" data-a="resume">${b.status === 'draft' ? 'Start' : 'Resume'}</button>` : ''}
          ${can_.retry ? '<button class="b sm" data-a="retry">Retry failed / remaining</button>' : ''}${can_.cancel ? '<button class="b ghost sm" data-a="cancel">Cancel</button>' : ''}
          <button class="b ghost sm" data-a="duplicate">Duplicate</button><button class="b ghost sm" data-a="export">Export CSV</button>
          ${can_.del ? '<button class="b danger sm" data-a="delete">Delete</button>' : ''}</div>
        <p class="muted" style="margin:0 0 14px;font-size:13px">Audience: ${esc(audienceText(b.audience))} · created by ${esc(b.created_by || '—')} on ${esc(CRM.dateTime(b.created_at))}${b.scheduled_at && b.status === 'scheduled' ? ` · scheduled for ${esc(CRM.dateTime(b.scheduled_at))}` : ''}${b.finished_at ? ` · finished ${esc(CRM.dateTime(b.finished_at))}` : ''}</p>
        <div style="margin-bottom:16px"><div class="row" style="justify-content:space-between;font-size:13px;margin-bottom:6px"><span>${s.attempted} of ${b.audience_total || '…'} contacts processed</span><b>${progress(b)}%</b></div>${bar(progress(b))}</div>
        <div class="grid six" style="margin-bottom:18px">${tile('Audience', b.audience_total || '—')}${tile('Sent', sent)}${tile('Delivered', s.delivered, pct(s.delivered, sent))}${tile('Read', s.read, pct(s.read, sent))}${tile('Replied', s.replied, pct(s.replied, sent))}${tile('Failed', s.failed)}</div>
        <div class="row" style="gap:8px;margin-bottom:10px"><div class="seg" id="rf">${[['all', 'All'], ['delivered', 'Delivered'], ['read', 'Read'], ['replied', 'Replied'], ['failed', 'Failed']].map(([k, l]) => `<button data-f="${k}" class="${k === filter ? 'on' : ''}">${l}</button>`).join('')}</div>
          <input id="rq" placeholder="Search recipient" value="${esc(q)}" style="flex:1;min-width:160px"></div>
        <div class="tbl"><table><tr><th>Recipient</th><th>Status</th><th>Replied</th><th>Details</th><th>Time</th></tr>
        ${rec.map(r => `<tr class="crow" data-lead="${r.lead_id}" data-n="${esc(r.name)}"><td><div class="who">${avatar(r.name, true)}<div><b>${esc(r.name)}</b><div class="muted" style="font-size:12.5px">${esc(r.phone)}</div></div></div></td>
          <td>${CRM.statusPill(r.status)}</td><td>${r.replied ? pill('yes', 'replied') : '<span class="muted">—</span>'}</td><td class="muted" style="max-width:260px;font-size:13px">${esc(r.error || '')}</td><td class="muted" style="font-size:12.5px">${esc(CRM.dateTime(r.created_at))}</td></tr>`).join('') || '<tr><td colspan="5"><div class="empty">No recipients yet.</div></td></tr>'}</table></div></div>`;
      d.body.scrollTop = keepScroll;
      const ACTIONS = {pause: () => api(`/broadcasts/${id}/pause`, 'POST'), resume: () => api(`/broadcasts/${id}/resume`, 'POST'), cancel: () => confirm('Cancel this campaign? Messages already sent stay sent.') && api(`/broadcasts/${id}/cancel`, 'POST'),
        retry: () => api(`/broadcasts/${id}/retry`, 'POST'), export: () => { location.href = `/api/broadcasts/${id}/export`; }, duplicate: async () => { const r = await api(`/broadcasts/${id}/duplicate`, 'POST'); toast('Copy created as a draft'); d.close(); openDetail(r.id); },
        delete: async () => { if (!confirm('Delete this campaign? Message history stays in the chats.')) return; await api(`/broadcasts/${id}`, 'DELETE'); d.close(); render(); }};
      d.body.querySelectorAll('[data-a]').forEach(btn => btn.onclick = guard(async () => { await ACTIONS[btn.dataset.a](); await draw(); }));
      d.body.querySelectorAll('#rf [data-f]').forEach(bt => bt.onclick = () => { filter = bt.dataset.f; draw(); });
      d.body.querySelector('#rq').oninput = CRM.debounce(e => { q = e.target.value; draw().then(() => { const i = d.body.querySelector('#rq'); i.focus(); i.setSelectionRange(q.length, q.length); }); }, 250);
      d.body.querySelectorAll('.crow').forEach(tr => tr.onclick = () => openConversation(+tr.dataset.lead, tr.dataset.n));
      return b;
    };
    const b0 = await draw();
    timer = setInterval(() => { if (document.hidden) return; if (!d.el.isConnected) { clearInterval(timer); return; } if (!d.body.contains(document.activeElement) || document.activeElement.tagName !== 'INPUT') draw().catch(() => {}); }, 3500);
  }
})();
