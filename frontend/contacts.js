/* Contacts: table with filters, bulk actions, import/export; plus Segments, Lists and Tags (sub-pages). */
(() => {
  const PV = (window.PageViews = window.PageViews || {});
  const SUBS = [['contacts', 'Contacts'], ['segments', 'Segments'], ['lists', 'Lists'], ['tags', 'Tags']];
  const bulkOK = () => can('contacts_bulk');
  const chipPicker = (items, selected, name) => `<div class="chips pick" data-pick="${name}">${items.map(i => `<button type="button" class="tagc pickc ${selected.has(i.id) ? 'on' : ''}" data-id="${i.id}" style="--c:${esc(i.color || '#1a5bec')}">${esc(i.name)}</button>`).join('') || '<span class="muted">None yet</span>'}</div>`;
  const picked = (root, name) => [...root.querySelectorAll(`[data-pick="${name}"] .pickc.on`)].map(b => +b.dataset.id);
  const wirePickers = root => root.querySelectorAll('.pickc').forEach(b => b.onclick = () => b.classList.toggle('on'));
  const downloadText = (name, text) => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], {type: 'text/csv'})); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); };

  PV.Contacts = async function () {
    await CRM.catalog();
    const sub = sessionStorage.getItem('ctab') || 'contacts';
    $('#main').innerHTML = `<div class="seg subseg" id="subseg">${SUBS.map(([k, l]) => `<button data-s="${k}" class="${k === sub ? 'on' : ''}">${l}</button>`).join('')}</div><div id="sub" style="margin-top:16px"></div>`;
    $('#subseg').onclick = e => { const b = e.target.closest('[data-s]'); if (b) { sessionStorage.setItem('ctab', b.dataset.s); render(); } };
    const root = $('#sub');
    ({contacts: contactsTab, segments: segmentsTab, lists: listsTab, tags: tagsTab}[sub] || contactsTab)(root);
  };
  const gotoContacts = preset => { sessionStorage.setItem('cf', JSON.stringify(preset)); sessionStorage.setItem('ctab', 'contacts'); render(); };

  // ======================= Contacts =======================
  async function contactsTab(root) {
    const f = JSON.parse(sessionStorage.getItem('cf') || '{}');
    let page = 1, limit = +sessionStorage.getItem('cl') || 25, sort = sessionStorage.getItem('csort') || 'recent', data = {items: [], total: 0}, sel = new Set(), allMatching = false;
    const qs = () => new URLSearchParams({...f, page, limit, sort}).toString();
    const filterOnly = () => Object.fromEntries(Object.entries(f).filter(([, v]) => v));
    const opt = (items, cur, blank, val = x => x.id, lab = x => x.name) => `<option value="">${blank}</option>` + items.map(x => `<option value="${esc(val(x))}" ${String(val(x)) === String(cur) ? 'selected' : ''}>${esc(lab(x))}</option>`).join('');
    const cat = CRM.cat;
    root.innerHTML = `
      <div class="card" style="padding:16px">
        <div class="row ctools" style="gap:8px">
          <input id="cq" placeholder="Search name, mobile number or email" value="${esc(f.q || '')}" style="flex:1 1 240px;min-width:200px">
          <select id="fs">${opt(STATUSES.map(s => ({id: s, name: s})), f.status, 'All statuses')}</select>
          <select id="fc">${opt(courses.map(c => ({id: c.name, name: c.name})), f.course, 'All courses')}</select>
          <select id="ft">${opt(cat.tags, f.tag, 'All tags')}</select>
          <select id="fl">${opt(cat.lists, f.list, 'All lists')}</select>
          <select id="fg">${opt(cat.segments, f.segment, 'All segments')}</select>
          <select id="fo"><option value="">Opt-in: any</option><option value="yes" ${f.consent === 'yes' ? 'selected' : ''}>Opted in</option><option value="no" ${f.consent === 'no' ? 'selected' : ''}>Not opted in</option></select>
          <button class="b ghost sm" id="fclear">Clear</button>
        </div>
        <div class="row" style="margin-top:12px"><span class="muted" id="ccount"></span><span class="sp" style="flex:1"></span>
          ${bulkOK() ? '<button class="b ghost" id="cimp">Import CSV</button><button class="b ghost" id="cexp">Export</button>' : ''}
          <button class="b" id="cadd">+ Add contact</button></div>
      </div>
      <div class="card" style="padding:0;overflow:hidden">
        <div id="bulkbar" class="bulkbar" hidden></div>
        <div class="tbl"><table id="ctable"></table></div>
        <div class="pager" id="pager"></div>
      </div>`;

    const bulkBar = () => {
      const bar = $('#bulkbar'), n = allMatching ? data.total : sel.size;
      if (!bulkOK() || !n) { bar.hidden = true; return; }
      bar.hidden = false;
      bar.innerHTML = `<b>${n} selected</b>${!allMatching && data.total > data.items.length && sel.size === data.items.length ? `<button class="b ghost sm" id="selall">Select all ${data.total} matching</button>` : ''}
        <select id="bact"><option value="add_tag">Add tag</option><option value="remove_tag">Remove tag</option><option value="add_list">Add to list</option><option value="remove_list">Remove from list</option><option value="set_status">Set status</option><option value="opt_in">Opt in</option><option value="opt_out">Opt out</option><option value="delete">Delete</option></select>
        <select id="bval"></select><button class="b sm" id="bapply">Apply</button><button class="b ghost sm" id="bclear">Clear selection</button>`;
      const fillVal = () => {
        const a = $('#bact').value, v = $('#bval');
        const items = {add_tag: cat.tags, remove_tag: cat.tags, add_list: cat.lists, remove_list: cat.lists}[a];
        v.style.display = ['opt_in', 'opt_out', 'delete'].includes(a) ? 'none' : '';
        v.innerHTML = a === 'set_status' ? STATUSES.map(s => `<option>${s}</option>`).join('') : (items || []).map(i => `<option value="${i.id}">${esc(i.name)}</option>`).join('') || '<option value="">(create one first)</option>';
      };
      $('#bact').onchange = fillVal; fillVal();
      if ($('#selall')) $('#selall').onclick = () => { allMatching = true; bulkBar(); };
      $('#bclear').onclick = () => { sel.clear(); allMatching = false; draw(); };
      $('#bapply').onclick = guard(async () => {
        let a = $('#bact').value, v = $('#bval').value;
        if (a === 'opt_in') { a = 'set_consent'; v = 'yes'; } else if (a === 'opt_out') { a = 'set_consent'; v = 'no'; }
        if (a === 'delete' && !confirm(`Delete ${n} contact${n === 1 ? '' : 's'} and all their messages? This cannot be undone.`)) return;
        if (a === 'set_consent' && v === 'yes' && !confirm('Only opt contacts in if they agreed to receive WhatsApp messages. Continue?')) return;
        const r = await api('/contacts/bulk', 'POST', {...(allMatching ? {filter: filterOnly()} : {ids: [...sel]}), action: a, value: v});
        toast(`Done: ${r.affected} contact${r.affected === 1 ? '' : 's'}`); sel.clear(); allMatching = false; await CRM.catalog(); await load();
      });
    };

    const draw = () => {
      const rows = data.items;
      $('#ccount').textContent = `${data.total} contact${data.total === 1 ? '' : 's'}`;
      $('#ctable').innerHTML = `<tr>${bulkOK() ? `<th style="width:42px"><input type="checkbox" id="sall" ${rows.length && rows.every(r => sel.has(r.id)) ? 'checked' : ''}></th>` : ''}<th>Name</th><th>Tags</th><th>Course</th><th>Status</th><th>Opt-in</th><th>Last active</th><th></th></tr>` +
        (rows.map(r => `<tr class="crow" data-id="${r.id}">${bulkOK() ? `<td><input type="checkbox" data-sel="${r.id}" ${sel.has(r.id) || allMatching ? 'checked' : ''}></td>` : ''}
          <td><div class="who">${avatar(r.name, true)}<div><b>${esc(r.name)}</b>${r.unread ? ` <span class="ubadge">${r.unread}</span>` : ''}<div class="muted" style="font-size:12.5px">${esc(r.phone)}</div></div></div></td>
          <td><div class="chips">${r.tags.slice(0, 3).map(t => CRM.tagChip(t)).join('')}${r.tags.length > 3 ? `<span class="muted" style="font-size:12px">+${r.tags.length - 3}</span>` : ''}</div></td>
          <td>${esc(r.course) || '<span class="muted">—</span>'}</td><td>${CRM.statusPill(r.status)}</td>
          <td>${r.consent ? pill('yes', 'opted in') : pill('no', 'not opted in')}</td><td>${esc(CRM.ago(r.last_active))}</td>
          <td style="text-align:right"><button class="b ghost sm" data-chat="${r.id}">Chat</button></td></tr>`).join('') || `<tr><td colspan="8"><div class="empty">No contacts match.</div></td></tr>`);
      const from = data.total ? (page - 1) * limit + 1 : 0, to = Math.min(page * limit, data.total);
      $('#pager').innerHTML = `<span class="muted">${from}–${to} of ${data.total}</span><span class="sp"></span>
        <select id="plim">${[25, 50, 100].map(n => `<option ${n === limit ? 'selected' : ''}>${n}</option>`).join('')}</select>
        <select id="psort"><option value="recent" ${sort === 'recent' ? 'selected' : ''}>Newest first</option><option value="name" ${sort === 'name' ? 'selected' : ''}>Name A–Z</option><option value="active" ${sort === 'active' ? 'selected' : ''}>Recently active</option></select>
        <button class="b ghost sm" id="pprev" ${page <= 1 ? 'disabled' : ''}>Previous</button><button class="b ghost sm" id="pnext" ${to >= data.total ? 'disabled' : ''}>Next</button>`;
      $('#plim').onchange = e => { limit = +e.target.value; sessionStorage.setItem('cl', limit); page = 1; load(); };
      $('#psort').onchange = e => { sort = e.target.value; sessionStorage.setItem('csort', sort); page = 1; load(); };
      $('#pprev').onclick = () => { page--; load(); }; $('#pnext').onclick = () => { page++; load(); };
      if ($('#sall')) $('#sall').onchange = e => { allMatching = false; rows.forEach(r => e.target.checked ? sel.add(r.id) : sel.delete(r.id)); draw(); };
      $('#ctable').querySelectorAll('[data-sel]').forEach(c => c.onchange = e => { allMatching = false; e.target.checked ? sel.add(+c.dataset.sel) : sel.delete(+c.dataset.sel); bulkBar(); const all = $('#sall'); if (all) all.checked = rows.every(r => sel.has(r.id)); });
      $('#ctable').querySelectorAll('.crow').forEach(tr => tr.onclick = e => {
        if (e.target.closest('input')) return;
        const r = rows.find(x => x.id === +tr.dataset.id); openConversation(r.id, r.name, load); });
      bulkBar();
    };
    async function load() { data = await api('/contacts?' + qs()); if (page > 1 && !data.items.length) { page = 1; return load(); } draw(); }
    const setF = (k, v) => { f[k] = v; sessionStorage.setItem('cf', JSON.stringify(f)); page = 1; sel.clear(); allMatching = false; load(); };
    $('#cq').oninput = CRM.debounce(e => setF('q', e.target.value), 300);
    for (const [id, k] of [['fs', 'status'], ['fc', 'course'], ['ft', 'tag'], ['fl', 'list'], ['fg', 'segment'], ['fo', 'consent']]) $('#' + id).onchange = e => setF(k, e.target.value);
    $('#fclear').onclick = () => { sessionStorage.removeItem('cf'); render(); };
    $('#cadd').onclick = () => addContact(load);
    if ($('#cimp')) $('#cimp').onclick = () => importContacts(() => { CRM.catalog().then(load); });
    if ($('#cexp')) $('#cexp').onclick = () => { location.href = '/api/contacts/export?' + new URLSearchParams(filterOnly()).toString(); };
    await load();
  }

  function addContact(done) {
    const cat = CRM.cat, m = CRM.modal(`<h2>Add a contact</h2><form id="acf">
      <div class="row"><div style="flex:1;min-width:160px"><label>Name</label><input name="name" required style="width:100%"></div><div style="flex:1;min-width:160px"><label>WhatsApp number</label><input name="phone" required placeholder="9876543210" style="width:100%"></div></div>
      <div class="row"><div style="flex:1;min-width:160px"><label>Email</label><input name="email" type="email" style="width:100%"></div><div style="flex:1;min-width:160px"><label>Course</label><select name="course" style="width:100%">${courseOpts('', '— none —')}</select></div></div>
      <label>Tags</label>${chipPicker(cat.tags, new Set(), 'tags')}<label>Lists</label>${chipPicker(cat.lists, new Set(), 'lists')}
      <label class="row" style="gap:8px;color:var(--ink);font-weight:500;margin-top:14px"><input type="checkbox" name="consent"> This person agreed to receive WhatsApp messages (opted in)</label>
      <div class="row" style="margin-top:18px;justify-content:flex-end"><button type="button" class="b ghost" data-x>Cancel</button><button class="b">Add contact</button></div></form>`);
    wirePickers(m.card); m.card.querySelector('[data-x]').onclick = m.close;
    m.card.querySelector('form').onsubmit = guard(async e => { e.preventDefault(); const f = e.target;
      await api('/contacts', 'POST', {name: f.name.value, phone: f.phone.value, email: f.email.value, course: f.course.value, consent: f.consent.checked, tag_ids: picked(m.card, 'tags'), list_ids: picked(m.card, 'lists')});
      m.close(); toast('Contact added'); await CRM.catalog(); done(); });
  }

  function importContacts(done) {
    const cat = CRM.cat, sample = 'name,phone,email,course,tags,city\nAsha,9876543210,asha@example.com,Full Stack Development,"Webinar; VIP",Chennai\n';
    const m = CRM.modal(`<h2>Import contacts from CSV</h2>
      <p class="muted" style="margin-top:0">One row per person. Needs a <b>phone</b> column (or mobile / whatsapp). Optional: name, email, course, status, source, tags (separated by ; ), consent (yes/no). Any other column becomes an attribute, e.g. <code>city</code>. Existing numbers are updated, not duplicated.
      <a href="#" id="imps">Download a sample</a></p>
      <form id="imf"><label>CSV file</label><input type="file" id="imfile" accept=".csv,text/csv" style="width:100%">
        <label>…or paste CSV</label><textarea id="imtext" placeholder="${esc(sample)}" style="min-height:110px"></textarea>
        <div class="row" style="align-items:flex-start"><div style="flex:1;min-width:180px"><label>Add everyone to list</label><select id="iml" style="width:100%"><option value="">— none —</option>${cat.lists.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select></div>
          <div style="flex:1;min-width:180px"><label>Add tag to everyone</label><select id="imt" style="width:100%"><option value="">— none —</option>${cat.tags.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select></div></div>
        <label class="row" style="gap:8px;color:var(--ink);font-weight:500;margin-top:14px"><input type="checkbox" id="imopt"> I confirm everyone in this file agreed to receive WhatsApp messages from us</label>
        <p class="muted" style="font-size:12.5px;margin:4px 0 0">Without this, people are imported as not opted in and will not receive campaigns, unless the file has a consent column.</p>
        <div id="imres" style="margin-top:12px"></div>
        <div class="row" style="margin-top:18px;justify-content:flex-end"><button type="button" class="b ghost" data-x>Close</button><button class="b" id="imgo">Import</button></div></form>`, 620);
    m.card.querySelector('[data-x]').onclick = m.close;
    m.card.querySelector('#imps').onclick = e => { e.preventDefault(); downloadText('contacts-sample.csv', sample); };
    m.card.querySelector('#imfile').onchange = e => { const file = e.target.files[0]; if (!file) return; const r = new FileReader(); r.onload = () => { m.card.querySelector('#imtext').value = r.result; }; r.readAsText(file); };
    m.card.querySelector('form').onsubmit = guard(async e => { e.preventDefault();
      const csv = m.card.querySelector('#imtext').value.trim(); if (!csv) { toast('Choose a file or paste CSV first'); return; }
      const go = m.card.querySelector('#imgo'); go.disabled = true; go.textContent = 'Importing…';
      try {
        const r = await api('/contacts/import', 'POST', {csv, list_id: +m.card.querySelector('#iml').value || null, tag_id: +m.card.querySelector('#imt').value || null, opted_in: m.card.querySelector('#imopt').checked});
        m.card.querySelector('#imres').innerHTML = `<div class="row" style="gap:8px">${pill('yes', r.created + ' added')}${pill('def', r.updated + ' updated')}${r.skipped ? pill('failed', r.skipped + ' skipped') : ''}</div>${r.errors.length ? `<div class="res" style="margin-top:8px;max-height:120px">${esc(r.errors.join('\n'))}</div>` : ''}`;
        done();
      } finally { go.disabled = false; go.textContent = 'Import'; } });
  }

  // ======================= Segments =======================
  const OPS = {is: 'is', is_not: 'is not', has: 'has', not_has: 'does not have', in: 'is in', not_in: 'is not in', contains: 'contains', exists: 'has a value',
    within_days: 'within the last … days', older_than_days: 'more than … days ago'};
  const FIELDS = {
    course: ['Course', ['is', 'is_not'], 'course'], status: ['Status', ['is', 'is_not'], 'status'], source: ['Source', ['is', 'is_not'], 'text'],
    tag: ['Tag', ['has', 'not_has'], 'tag'], list: ['List', ['in', 'not_in'], 'list'],
    consent: ['Opted in', ['is'], 'yesno'], has_replied: ['Has replied', ['is'], 'yesno'], callback: ['Callback pending', ['is'], 'yesno'], intervened: ['Has an agent on the chat', ['is'], 'yesno'],
    stage: ['Chat stage', ['is', 'is_not'], 'stage'],
    last_active: ['Last message from contact', ['within_days', 'older_than_days'], 'number'], created: ['Added', ['within_days', 'older_than_days'], 'number'],
    name: ['Name', ['contains'], 'text'], phone: ['Phone', ['contains'], 'text'], email: ['Email', ['contains'], 'text'], attr: ['Attribute', ['is', 'is_not', 'contains', 'exists'], 'attr'],
  };
  const describe = rules => (rules.conditions || []).map(c => {
    const [label, , kind] = FIELDS[c.field] || [c.field, [], 'text'];
    let v = c.value;
    if (kind === 'tag') v = (CRM.cat.tags.find(t => t.id === +v) || {}).name; else if (kind === 'list') v = (CRM.cat.lists.find(t => t.id === +v) || {}).name; else if (kind === 'yesno') v = ['1', 'true', 'yes'].includes(String(v)) ? 'yes' : 'no';
    return `${c.field === 'attr' ? esc(c.key) : label} ${OPS[c.op] || c.op}${c.op === 'exists' ? '' : ' ' + esc(v)}`;
  }).join(rules.match === 'any' ? ' OR ' : ' AND ') || 'Everyone';

  async function segmentsTab(root) {
    const segs = await api('/segments');
    root.innerHTML = `<div class="card"><div class="sec" style="margin-top:0"><h2>Segments</h2><span class="sp"></span><button class="b" id="newseg">+ New segment</button></div>
      <p class="muted" style="margin-top:0">A segment is a saved filter that updates itself: people join and leave as their tags, status or activity change. Use segments as the audience of a campaign.</p>
      <div class="tbl"><table><tr><th>Segment</th><th>Rules</th><th>Contacts</th><th style="text-align:right">Actions</th></tr>
      ${segs.map(s => `<tr><td><b>${esc(s.name)}</b></td><td class="muted" style="max-width:420px">${describe(s.rules)}</td><td><b>${s.contacts}</b></td>
        <td style="text-align:right"><div class="row" style="gap:6px;justify-content:flex-end;flex-wrap:nowrap"><button class="b ghost sm" data-view="${s.id}">View contacts</button><button class="b ghost sm" data-edit="${s.id}">Edit</button><button class="b danger sm" data-del="${s.id}" data-n="${esc(s.name)}">Delete</button></div></td></tr>`).join('') || '<tr><td colspan="4"><div class="empty">No segments yet. Create one, for example “Interested in Full Stack and not contacted this week”.</div></td></tr>'}</table></div></div>`;
    $('#newseg').onclick = () => segmentEditor(null, () => render());
    root.querySelectorAll('[data-view]').forEach(b => b.onclick = () => gotoContacts({segment: b.dataset.view}));
    root.querySelectorAll('[data-edit]').forEach(b => b.onclick = () => segmentEditor(segs.find(s => s.id === +b.dataset.edit), () => render()));
    root.querySelectorAll('[data-del]').forEach(b => b.onclick = guard(async () => { if (confirm(`Delete the segment “${b.dataset.n}”? Contacts are not deleted.`)) { await api('/segments/' + b.dataset.del, 'DELETE'); render(); } }));
  }

  function segmentEditor(seg, done) {
    const cat = CRM.cat, rules = seg ? JSON.parse(JSON.stringify(seg.rules)) : {match: 'all', conditions: [{field: 'status', op: 'is', value: 'new'}]};
    const m = CRM.modal(`<h2>${seg ? 'Edit' : 'New'} segment</h2><form id="sgf">
      <label>Name</label><input id="sgn" value="${esc(seg ? seg.name : '')}" required placeholder="e.g. Hot leads this week" style="width:100%">
      <div class="row" style="margin-top:14px;gap:8px"><span>Contacts who match</span><select id="sgm"><option value="all" ${rules.match !== 'any' ? 'selected' : ''}>ALL</option><option value="any" ${rules.match === 'any' ? 'selected' : ''}>ANY</option></select><span>of these conditions:</span></div>
      <div id="sgc" style="margin-top:10px"></div><button type="button" class="b ghost sm" id="sga">+ Add condition</button>
      <div id="sgp" class="segprev"></div>
      <div class="row" style="margin-top:16px;justify-content:flex-end"><button type="button" class="b ghost" data-x>Cancel</button><button class="b">Save segment</button></div></form>`, 760);
    const rowInput = (c, i) => {
      const kind = FIELDS[c.field][2], v = c.value;
      if (kind === 'course') return `<select data-i="${i}" data-k="value">${courses.map(x => `<option ${x.name === v ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select>`;
      if (kind === 'stage') return `<select data-i="${i}" data-k="value">${['active', 'requesting', 'intervened', 'closed'].map(x => `<option ${x === v ? 'selected' : ''}>${x}</option>`).join('')}</select>`;
      if (kind === 'status') return `<select data-i="${i}" data-k="value">${STATUSES.map(s => `<option ${s === v ? 'selected' : ''}>${s}</option>`).join('')}</select>`;
      if (kind === 'tag') return `<select data-i="${i}" data-k="value"><option value="">pick…</option>${cat.tags.map(t => `<option value="${t.id}" ${String(t.id) === String(v) ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select>`;
      if (kind === 'list') return `<select data-i="${i}" data-k="value"><option value="">pick…</option>${cat.lists.map(t => `<option value="${t.id}" ${String(t.id) === String(v) ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select>`;
      if (kind === 'yesno') return `<select data-i="${i}" data-k="value"><option value="yes" ${['yes', '1', 'true'].includes(String(v)) ? 'selected' : ''}>yes</option><option value="no" ${['no', '0', 'false'].includes(String(v)) ? 'selected' : ''}>no</option></select>`;
      if (kind === 'number') return `<input data-i="${i}" data-k="value" type="number" min="0" value="${esc(v)}" style="width:100px" placeholder="days">`;
      if (kind === 'attr') return `<input data-i="${i}" data-k="key" value="${esc(c.key || '')}" placeholder="attribute, e.g. city" style="width:140px">${c.op === 'exists' ? '' : `<input data-i="${i}" data-k="value" value="${esc(v)}" placeholder="value" style="width:140px">`}`;
      return `<input data-i="${i}" data-k="value" value="${esc(v)}" placeholder="text">`;
    };
    const defaults = f => ({course: courses[0] ? courses[0].name : '', status: 'new', stage: 'requesting', tag: cat.tags[0] ? String(cat.tags[0].id) : '', list: cat.lists[0] ? String(cat.lists[0].id) : '', consent: 'yes', has_replied: 'yes', callback: 'yes', intervened: 'yes', last_active: '7', created: '7'}[f] || '');
    const draw = () => {
      m.card.querySelector('#sgc').innerHTML = rules.conditions.map((c, i) => `<div class="cond"><select data-i="${i}" data-k="field">${Object.entries(FIELDS).map(([k, [l]]) => `<option value="${k}" ${k === c.field ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <select data-i="${i}" data-k="op">${FIELDS[c.field][1].map(o => `<option value="${o}" ${o === c.op ? 'selected' : ''}>${OPS[o]}</option>`).join('')}</select>${rowInput(c, i)}<button type="button" class="b ghost sm" data-rm="${i}" aria-label="Remove">✕</button></div>`).join('') || '<div class="muted">No conditions: this segment includes everyone.</div>';
      m.card.querySelectorAll('#sgc [data-k]').forEach(el => el[el.tagName === 'SELECT' ? 'onchange' : 'oninput'] = () => {
        const c = rules.conditions[+el.dataset.i];
        if (el.dataset.k === 'field') { c.field = el.value; c.op = FIELDS[c.field][1][0]; c.value = defaults(c.field); delete c.key; draw(); }
        else if (el.dataset.k === 'op') { c.op = el.value; draw(); } else c[el.dataset.k] = el.value;
        preview(); });
      m.card.querySelectorAll('#sgc [data-rm]').forEach(b => b.onclick = () => { rules.conditions.splice(+b.dataset.rm, 1); draw(); preview(); });
    };
    const preview = CRM.debounce(async () => {
      const box = m.card.querySelector('#sgp');
      try { const r = await fetch('/api/segments/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: 'x', rules: {match: m.card.querySelector('#sgm').value, conditions: rules.conditions}})});
        const j = await r.json(); if (!r.ok) { box.innerHTML = `<span class="muted">${esc(typeof j.detail === 'string' ? j.detail : 'Complete the conditions')}</span>`; return; }
        box.innerHTML = `<b>${j.count}</b> contact${j.count === 1 ? '' : 's'} match${j.sample.length ? ': ' + j.sample.map(s => esc(s.name)).join(', ') + (j.count > j.sample.length ? '…' : '') : ''}`; } catch (e) { box.textContent = ''; }
    }, 350);
    draw(); preview();
    m.card.querySelector('#sga').onclick = () => { rules.conditions.push({field: 'status', op: 'is', value: 'new'}); draw(); preview(); };
    m.card.querySelector('#sgm').onchange = preview;
    m.card.querySelector('[data-x]').onclick = m.close;
    m.card.querySelector('form').onsubmit = guard(async e => { e.preventDefault();
      const body = {name: m.card.querySelector('#sgn').value, rules: {match: m.card.querySelector('#sgm').value, conditions: rules.conditions}};
      seg ? await api('/segments/' + seg.id, 'PUT', body) : await api('/segments', 'POST', body);
      m.close(); toast('Segment saved'); done(); });
  }

  // ======================= Lists =======================
  async function listsTab(root) {
    const lists = await api('/lists');
    root.innerHTML = `<div class="card"><div class="sec" style="margin-top:0"><h2>Lists</h2></div>
      <p class="muted" style="margin-top:0">A list is a fixed group you build yourself (an event, a batch, an import). Add people from the Contacts table or when importing a CSV.</p>
      <form id="nl" class="row" style="margin-bottom:14px"><input name="name" placeholder="New list name, e.g. October batch" required style="flex:1;min-width:220px"><button class="b">Create list</button></form>
      <div class="tbl"><table><tr><th>List</th><th>Contacts</th><th style="text-align:right">Actions</th></tr>
      ${lists.map(l => `<tr><td><b>${esc(l.name)}</b></td><td><b>${l.contacts}</b></td><td style="text-align:right"><div class="row" style="gap:6px;justify-content:flex-end;flex-wrap:nowrap"><button class="b ghost sm" data-view="${l.id}">View contacts</button><button class="b ghost sm" data-ren="${l.id}" data-n="${esc(l.name)}">Rename</button><button class="b danger sm" data-del="${l.id}" data-n="${esc(l.name)}">Delete</button></div></td></tr>`).join('') || '<tr><td colspan="3"><div class="empty">No lists yet.</div></td></tr>'}</table></div></div>`;
    $('#nl').onsubmit = guard(async e => { e.preventDefault(); await api('/lists', 'POST', {name: e.target.name.value}); toast('List created'); render(); });
    root.querySelectorAll('[data-view]').forEach(b => b.onclick = () => gotoContacts({list: b.dataset.view}));
    root.querySelectorAll('[data-ren]').forEach(b => b.onclick = guard(async () => { const n = prompt('Rename list', b.dataset.n); if (n && n.trim()) { await api('/lists/' + b.dataset.ren, 'PUT', {name: n}); render(); } }));
    root.querySelectorAll('[data-del]').forEach(b => b.onclick = guard(async () => { if (confirm(`Delete the list “${b.dataset.n}”? Contacts are not deleted.`)) { await api('/lists/' + b.dataset.del, 'DELETE'); render(); } }));
  }

  // ======================= Tags =======================
  async function tagsTab(root) {
    const tags = await api('/tags');
    root.innerHTML = `<div class="card"><div class="sec" style="margin-top:0"><h2>Tags</h2></div>
      <p class="muted" style="margin-top:0">Tags are labels you stick on contacts (VIP, Webinar, Hot lead). Filter by them, add them in bulk, or target them in campaigns.</p>
      <form id="nt" class="row" style="margin-bottom:14px"><input name="name" placeholder="New tag name" required style="flex:1;min-width:200px"><input name="color" type="color" value="#1a5bec" title="Tag colour" style="width:56px;padding:3px;height:42px"><button class="b">Create tag</button></form>
      <div class="tbl"><table><tr><th>Tag</th><th>Contacts</th><th style="text-align:right">Actions</th></tr>
      ${tags.map(t => `<tr><td>${CRM.tagChip(t)}</td><td><b>${t.contacts}</b></td><td style="text-align:right"><div class="row" style="gap:6px;justify-content:flex-end;flex-wrap:nowrap"><button class="b ghost sm" data-view="${t.id}">View contacts</button><button class="b ghost sm" data-edit="${t.id}">Edit</button><button class="b danger sm" data-del="${t.id}" data-n="${esc(t.name)}">Delete</button></div></td></tr>`).join('') || '<tr><td colspan="3"><div class="empty">No tags yet.</div></td></tr>'}</table></div></div>`;
    $('#nt').onsubmit = guard(async e => { e.preventDefault(); await api('/tags', 'POST', {name: e.target.name.value, color: e.target.color.value}); toast('Tag created'); render(); });
    root.querySelectorAll('[data-view]').forEach(b => b.onclick = () => gotoContacts({tag: b.dataset.view}));
    root.querySelectorAll('[data-edit]').forEach(b => b.onclick = () => { const t = tags.find(x => x.id === +b.dataset.edit);
      const m = CRM.modal(`<h2>Edit tag</h2><form class="row" style="margin-top:8px"><input name="name" value="${esc(t.name)}" required style="flex:1"><input name="color" type="color" value="${esc(t.color)}" style="width:56px;padding:3px;height:42px"><button class="b">Save</button></form>`, 420);
      m.card.querySelector('form').onsubmit = guard(async e => { e.preventDefault(); await api('/tags/' + t.id, 'PUT', {name: e.target.name.value, color: e.target.color.value}); m.close(); render(); }); });
    root.querySelectorAll('[data-del]').forEach(b => b.onclick = guard(async () => { if (confirm(`Delete the tag “${b.dataset.n}”? It is removed from all contacts.`)) { await api('/tags/' + b.dataset.del, 'DELETE'); render(); } }));
  }
})();
