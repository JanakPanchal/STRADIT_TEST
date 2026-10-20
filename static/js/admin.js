$(async function () {
  const adminMe = await Api.requireRole('admin'); if (!adminMe) return;
  $('#admin-who').text(adminMe.email);
  const E = Api.esc;
  const STATES = ['REGISTERED', 'APTITUDE_IN_PROGRESS', 'APTITUDE_EVALUATED', 'BEHAVIOUR_IN_PROGRESS', 'BEHAVIOUR_EVALUATED', 'CODING_IN_PROGRESS', 'CODING_EVALUATED', 'FINAL_RESULT', 'CANDIDATE_REJECTED', 'DISQUALIFIED'];
  let tests = [], curTest = null, cPage = 1;
  const utc = s => s ? new Date(s.endsWith('Z') ? s : s + 'Z') : null;           // server sends naive UTC
  const toLocalInput = s => { const d = utc(s); return d ? new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ''; };
  const toIso = v => v ? new Date(v).toISOString() : null;
  const fmt = s => s ? utc(s).toLocaleString() : '';
  STATES.forEach(s => $('#f-state').append('<option>' + s + '</option>'));
  $('#logout').on('click', Api.logout);

  // clean, bookmarkable addresses: /admin, /admin/candidates, /admin/colleges ...
  const SLUG = { overview: '', tests: 'tests', colleges: 'colleges', cands: 'candidates', integrity: 'integrity', results: 'results', audit: 'audit' };
  let silentNav = false;
  $('#tabs a[data-tab]').on('click', function () {
    const tab = $(this).data('tab'), path = '/admin' + (SLUG[tab] ? '/' + SLUG[tab] : '');
    if (!silentNav && location.pathname !== path) history.pushState({ tab }, '', path);
    document.title = $(this).data('title') + ' · Admin';
    $('#tabs a').removeClass('active'); $(this).addClass('active'); $('#page-title').text($(this).data('title')); $('#page-sub').text($(this).data('sub')); $('#page-help').attr('href', '/admin/help#' + ($(this).data('help') || 'start'));
    ['overview', 'tests', 'colleges', 'cands', 'integrity', 'results', 'audit'].forEach(t => $('#tab-' + t).toggleClass('d-none', t !== $(this).data('tab')));
    ({ results: loadResults, overview: loadOverview, colleges: loadColleges, cands: () => { refreshCollegeSelects(); loadFilterOptions(); loadCands(); }, audit: loadAudit }[$(this).data('tab')] || (() => {}))();
  });

  // ---------------------------------------------------------------- tests
  async function loadTests() {
    tests = (await Api.get('/admin/tests', { page_size: 100 })).data;
    $('#tests-body').html(tests.map(t => '<tr><td>' + E(t.title) + (t.is_active ? '' : ' <span class="badge text-bg-secondary">inactive</span>') + '</td><td>' + t.stage + '</td><td>' + t.question_count + '</td><td>' + t.cutoff_score + '</td><td class="text-nowrap">' +
      '<button class="btn btn-sm btn-outline-primary" data-act="q" data-id="' + t.id + '">Questions</button> <button class="btn btn-sm btn-outline-secondary" data-act="edit" data-id="' + t.id + '">Edit</button> ' +
      '<button class="btn btn-sm btn-outline-danger" data-act="del" data-id="' + t.id + '">Delete</button></td></tr>').join(''));
    const opts = tests.map(t => '<option value="' + t.id + '">' + E(t.title) + ' (' + t.stage + ')</option>').join('');
    $('#a-test,#r-test,#i-test').html(opts);
  }
  $('#tests-body').on('click', 'button', async function () {
    const id = +$(this).data('id'), t = tests.find(x => x.id === id), act = $(this).data('act');
    if (act === 'q') location.href = '/admin/test-center/' + id;
    if (act === 'edit') {
      $('#tf-h').text('Edit test #' + id); $('#t-id').val(id); $('#t-title').val(t.title); $('#t-stage').val(t.stage); $('#t-dur').val(t.duration_min);
      $('#t-start').val(toLocalInput(t.start_at)); $('#t-end').val(toLocalInput(t.end_at)); $('#t-cut').val(t.cutoff_score); $('#t-maxv').val(t.max_violations); $('#t-show').prop('checked', t.show_result);
    }
    if (act === 'del' && await Api.deleteTest(id, t.title)) { Api.toast('Deleted', 'success'); loadTests(); }
  });
  function resetTestForm() { $('#test-form')[0].reset(); $('#t-id').val(''); $('#tf-h').text('Create test'); }
  $('#t-reset').on('click', resetTestForm);
  $('#test-form').on('submit', async e => {
    e.preventDefault();
    const body = { title: $('#t-title').val(), stage: $('#t-stage').val(), duration_min: +$('#t-dur').val(), start_at: toIso($('#t-start').val()), end_at: toIso($('#t-end').val()),
      cutoff_score: +$('#t-cut').val(), max_violations: +$('#t-maxv').val(), show_result: $('#t-show').prop('checked') };
    const id = $('#t-id').val();
    try { id ? await Api.put('/admin/tests/' + id, body) : await Api.post('/admin/tests', body); Api.toast('Saved', 'success'); resetTestForm(); loadTests(); } catch (e) {}
  });

  // ---------------------------------------------------------------- questions
  async function openQuestions(t) {
    curTest = t; $('#q-panel').removeClass('d-none'); $('#q-title').text('Questions – ' + t.title + ' (' + t.stage + ')');
    $('#q-form').toggleClass('d-none', t.stage === 'coding');
    const qs = (await Api.get('/admin/tests/' + t.id + '/questions')).data;
    $('#q-list').html(qs.map((q, i) => '<div class="border rounded p-2 mb-2"><div class="d-flex justify-content-between"><b>' + (i + 1) + '. ' + E(q.body) + '</b><button class="btn btn-sm btn-outline-danger" data-qid="' + q.id + '">×</button></div>' +
      (q.options ? '<ol class="mb-0 small">' + q.options.map((o, j) => '<li' + (j === q.correct_option ? ' class="fw-bold text-success"' : '') + '>' + E(o.text) + (o.score !== undefined ? ' <span class="badge text-bg-info">' + o.score + '</span>' : '') + '</li>').join('') + '</ol>'
        : '<div class="small text-muted">' + q.test_cases.length + ' test cases (' + q.test_cases.filter(c => !c.is_sample).length + ' hidden) · ' + q.marks + ' marks</div>') + '</div>').join('') || '<div class="text-muted">No questions yet.</div>');
  }
  $('#q-list').on('click', 'button[data-qid]', async function () {
    if (!confirm('Delete question?')) return;
    try { await Api.del('/admin/tests/' + curTest.id + '/questions/' + $(this).data('qid')); openQuestions(curTest); loadTests(); } catch (e) {}
  });
  $('#q-form').on('submit', async e => {
    e.preventDefault();
    const likert = curTest.stage === 'behaviour';
    const lines = $('#q-opts').val().split('\n').map(s => s.trim()).filter(Boolean);
    const options = lines.map(l => { if (!likert) return { text: l }; const i = l.lastIndexOf('|'); return { text: l.slice(0, i), score: parseFloat(l.slice(i + 1)) }; });
    const body = { type: likert ? 'likert' : 'mcq', body: $('#q-body').val(), options, marks: +$('#q-marks').val() || 1, difficulty: $('#q-diff').val() };
    if (likert) body.trait = $('#q-trait').val().trim() || null;
    if (!likert) body.correct_option = ($('#q-correct').val() ? +$('#q-correct').val() - 1 : null);
    try { await Api.post('/admin/tests/' + curTest.id + '/questions', body); $('#q-form')[0].reset(); openQuestions(curTest); loadTests(); } catch (e) {}
  });
  $('#q-import').on('change', async function () {
    const fd = new FormData(); fd.append('file', this.files[0]); this.value = '';
    try { const r = await Api.post('/admin/tests/' + curTest.id + '/questions/import', fd); Api.toast('Imported ' + r.data.imported + ' questions', 'success'); openQuestions(curTest); loadTests(); } catch (e) {}
  });

  // ---------------------------------------------------------------- candidates
  const fmtDate = s => { const d = new Date(s.endsWith('Z') ? s : s + 'Z'); return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };
  const OUT = { cleared: ['Cleared', 'success'], not_cleared: ['Not cleared', 'danger'], in_progress: ['In progress', 'warning'], not_started: ['Not started', 'secondary'], disqualified: ['Disqualified', 'dark'] };
  const CELL = { cleared: ['Cleared', 'success'], not_cleared: ['Not cleared', 'danger'], in_progress: ['In progress', 'warning'], assigned: ['Assigned', 'secondary'], not_assigned: ['—', 'light'], disqualified: ['Disqualified', 'dark'] };
  const F = { outcome: '' };
  const filterIds = { search: '#f-search', batch: '#f-batch', college_id: '#f-college', branch: '#f-branch', test_id: '#f-test', access: '#f-access', stage: '#f-stage', stage_result: '#f-sres', sort: '#f-sort' };
  function params(extra) {
    const p = Object.assign({}, extra);
    Object.entries(filterIds).forEach(([k, sel]) => { const v = $(sel).val(); if (v) p[k] = v; });
    if (F.outcome) p.outcome = F.outcome;
    if (!p.stage) delete p.stage_result;
    return p;
  }
  async function loadFilterOptions() {
    const o = (await Api.get('/admin/candidates/filters')).data;
    const fill = (sel, list, all) => $(sel).html('<option value="">' + all + '</option>' + list.map(v => '<option>' + E(v) + '</option>').join(''));
    fill('#f-batch', o.batches, 'All'); fill('#f-branch', o.branches, 'All'); $('#f-college').html('<option value="">All</option>' + o.colleges.map(c => '<option value="' + c.id + '">' + E(c.name) + '</option>').join(''));
    $('#f-test').html('<option value="">Any</option>' + o.tests.map(t => '<option value="' + t.id + '">' + E(t.title) + ' (' + t.stage + ')</option>').join(''));
  }
  function cell(c) {
    const [lab, kind] = CELL[c.status] || ['—', 'light'];
    if (c.status === 'not_assigned') return '<span class="text-muted">—</span>';
    const sc = c.score !== undefined ? '<div class="small fw-bold">' + c.score + ' / ' + c.max_score + '</div>' : '';
    return '<span class="badge text-bg-' + kind + '">' + lab + '</span>' + sc;
  }
  async function loadCands() {
    const size = 15;
    const r = await Api.get('/admin/candidates', params({ page: cPage, page_size: size }));
    const k = r.meta.counts, tiles = [['', 'All', k.all], ['cleared', 'Cleared', k.cleared], ['not_cleared', 'Not cleared', k.not_cleared], ['in_progress', 'In progress', k.in_progress], ['not_started', 'Not started', k.not_started], ['disqualified', 'Disqualified', k.disqualified]];
    $('#c-tiles').html(tiles.map(t => '<div class="pointer tile' + (F.outcome === t[0] ? ' tile-on' : '') + '" data-o="' + t[0] + '"><div class="v">' + t[2] + '</div><div class="l">' + t[1] + '</div></div>').join(''));
    $('#c-never').text(k.never_signed_in); $('#c-invite-all').toggleClass('d-none', !k.never_signed_in);
    $('#c-body').html(r.data.map(c => '<tr><td><input type="checkbox" class="csel form-check-input" value="' + c.id + '"></td>' +
      '<td><b>' + E(c.name) + '</b><div class="small text-muted">' + E(c.email) + '</div><div class="small ' + (c.last_login_at ? 'text-muted' : 'acc-never') + '">' + (c.last_login_at ? 'Signed in ' + fmtDate(c.last_login_at) : 'Never signed in' + (c.invited_at ? ' · invited ' + fmtDate(c.invited_at) : '')) + '</div></td>' +
      '<td>' + E(c.batch || '—') + '<div class="small text-muted">' + E([c.college, c.branch].filter(Boolean).join(' · ')) + '</div></td>' +
      ['aptitude', 'behaviour', 'coding'].map(s => '<td>' + cell(c.stages[s]) + '</td>').join('') +
      '<td>' + (c.violations ? '<b class="text-danger">' + c.violations + '</b>' : '<span class="text-muted">0</span>') + '</td>' +
      '<td><span class="badge text-bg-' + OUT[c.outcome][1] + '">' + OUT[c.outcome][0] + '</span></td>' +
      '<td class="text-nowrap"><button class="btn btn-sm btn-outline-primary" data-res="' + c.id + '">Details</button> <button class="btn btn-sm btn-outline-secondary" data-edit="' + c.id + '">Edit</button> <button class="btn btn-sm btn-outline-danger" data-del="' + c.id + '" data-name="' + E(c.name) + '">Delete</button></td></tr>').join('') || '<tr><td colspan="9" class="text-muted py-4">No candidates match these filters.</td></tr>');
    const pages = Math.max(1, Math.ceil(r.meta.total / r.meta.page_size));
    if (cPage > pages) { cPage = pages; return loadCands(); }
    $('#c-pager').html('<button class="btn btn-sm btn-outline-secondary" id="pp"' + (cPage <= 1 ? ' disabled' : '') + '>‹</button> Page ' + cPage + ' / ' + pages + ' · ' + r.meta.total + ' candidates <button class="btn btn-sm btn-outline-secondary" id="np"' + (cPage >= pages ? ' disabled' : '') + '>›</button>');
    $('#c-all').prop('checked', false); updSel();
  }
  const updSel = () => { const n = $('.csel:checked').length; $('#c-sel').text(n ? n + ' selected' : ''); $('#c-bulkdel,#c-invite-sel').toggleClass('d-none', !n); };
  $('#c-body').on('change', '.csel', updSel); $('#c-all').on('change', function () { $('.csel').prop('checked', this.checked); updSel(); });
  $('#c-pager').on('click', '#pp', () => { cPage--; loadCands(); }).on('click', '#np', () => { cPage++; loadCands(); });
  $('#c-tiles').on('click', '.tile', function () { F.outcome = String($(this).data('o')); cPage = 1; loadCands(); });
  let ft; $('#f-search').on('input', () => { clearTimeout(ft); ft = setTimeout(() => { cPage = 1; loadCands(); }, 300); });
  $('#f-batch,#f-college,#f-branch,#f-test,#f-access,#f-sres,#f-sort').on('change', () => { cPage = 1; loadCands(); });
  $('#f-stage').on('change', function () { $('#f-sres').prop('disabled', !this.value); if (!this.value) $('#f-sres').val(''); cPage = 1; loadCands(); });
  $('#f-reset').on('click', () => { Object.values(filterIds).forEach(sel => $(sel).val('')); $('#f-sort').val('newest'); $('#f-sres').prop('disabled', true); F.outcome = ''; cPage = 1; loadCands(); });
  $('#f-export').on('click', () => { const p = params(); delete p.sort; Api.download('/admin/candidates/export?' + $.param(p), 'candidates.csv'); });
  async function invite(body, label) {
    if (!confirm('Email ' + label + ' a link to set their own password?\n\nNo password is sent. The link works once and expires in 7 days. Sending again replaces the old link.')) return;
    try {
      const r = (await Api.post('/admin/candidates/invite', body)).data;
      Api.toast(r.queued + ' invitation' + (r.queued === 1 ? '' : 's') + ' queued' + (r.skipped_inactive ? ' (' + r.skipped_inactive + ' inactive skipped)' : ''), 'success');
      if (r.warning) $('#c-detail').html('<div class="alert alert-warning"><b>Email is not set up yet.</b> ' + E(r.warning) + ' Set <code>MAIL_BACKEND=smtp</code> and the SMTP settings (see the README) to deliver real email.</div>');
      loadCands();
    } catch (e) {}
  }
  $('#c-invite-sel').on('click', () => { const ids = $('.csel:checked').map((_, el) => +el.value).get(); invite({ ids }, ids.length + ' selected candidate' + (ids.length === 1 ? '' : 's')); });
  $('#c-invite-all').on('click', () => invite({ all_pending: true }, 'everyone who has not signed in yet (' + $('#c-never').text() + ')'));
  $('#c-created').on('click', '#inv-created', function () { invite({ ids: JSON.parse($(this).attr('data-ids')) }, 'these ' + JSON.parse($(this).attr('data-ids')).length + ' new candidates'); });
  function showCreated(list) {
    $('#c-created').html('<div class="alert alert-warning small">Passwords are shown <b>once</b>. <a href="#" id="dl-pw">Download CSV</a> · or <button class="btn btn-outline-primary btn-sm py-0" id="inv-created" data-ids=\'' + JSON.stringify(list.map(c => c.id)) + '\'>✉ email invitations instead</button><pre class="mb-0">' + list.map(c => E(c.email + '  ' + c.password)).join('\n') + '</pre></div>');
    $('#dl-pw').on('click', ev => { ev.preventDefault(); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['email,password\n' + list.map(c => c.email + ',' + c.password).join('\n')], { type: 'text/csv' })); a.download = 'candidate_passwords.csv'; a.click(); });
  }
  $('#c-form').on('submit', async e => {
    e.preventDefault();
    try { const r = await Api.post('/admin/candidates', { name: $('#c-name').val(), email: $('#c-email').val(), college_id: $('#c-college').val() ? +$('#c-college').val() : null, branch: $('#c-branch').val(), batch: $('#c-batch').val() }); showCreated([r.data]); $('#c-form')[0].reset(); loadCands(); loadFilterOptions(); } catch (e) {}
  });
  $('#c-bulk').on('change', async function () {
    const fd = new FormData(); fd.append('file', this.files[0]); this.value = '';
    try { const r = (await Api.post('/admin/candidates/bulk', fd)).data; showCreated(r.created); if (r.skipped.length) Api.toast(r.skipped.length + ' rows skipped: ' + r.skipped.slice(0, 3).map(s => 'row ' + s.row + ' ' + s.reason).join('; '), 'warning'); loadCands(); loadFilterOptions(); } catch (e) {}
  });
  $('#a-go').on('click', async () => {
    const ids = $('.csel:checked').map((_, el) => +el.value).get(), batch = $('#a-batch').val().trim();
    try { const r = await Api.post('/admin/candidates/assign', { test_id: +$('#a-test').val(), candidate_ids: ids, batch: batch || null }); Api.toast('Assigned ' + r.data.assigned + ', skipped ' + r.data.skipped_already_has_stage, 'success'); loadCands(); } catch (e) {}
  });
  let cData = {};
  const modal = new bootstrap.Modal(document.getElementById('c-modal'));
  $('#c-body').on('click', 'button', async function () {
    const id = $(this).data('res') || $(this).data('edit') || $(this).data('del');
    if ($(this).data('res')) { await showResults(id); $('#c-detail')[0].scrollIntoView({ behavior: 'smooth' }); }
    if ($(this).data('edit')) openEdit(id);
    if ($(this).data('del')) deleteCands([id], $(this).data('name'));
  });
  async function openEdit(id) {
    const d = (await Api.get('/admin/candidates/' + id + '/results')).data, c = d.candidate;
    cData = c; $('#e-id').val(id); $('#e-name').val(c.name); $('#e-email').val(c.email); $('#e-college').val(c.college_id || ''); $('#e-branch').val(c.branch); $('#e-batch').val(c.batch);
    $('#e-active').prop('checked', c.is_active !== false); $('#e-out').empty();
    $('#e-dq').toggleClass('d-none', c.state === 'DISQUALIFIED'); $('#e-reinstate').toggleClass('d-none', c.state !== 'DISQUALIFIED'); modal.show();
  }
  $('#e-form').on('submit', async e => {
    e.preventDefault();
    try { await Api.put('/admin/candidates/' + $('#e-id').val(), { name: $('#e-name').val(), email: $('#e-email').val(), college_id: $('#e-college').val() ? +$('#e-college').val() : null, branch: $('#e-branch').val(), batch: $('#e-batch').val(), is_active: $('#e-active').prop('checked') });
      Api.toast('Candidate updated', 'success'); modal.hide(); loadCands(); loadFilterOptions(); } catch (e) {}
  });
  $('#e-reset').on('click', async () => {
    if (!confirm('Generate a new password for this candidate? Their current sessions are signed out.')) return;
    try { const r = (await Api.post('/admin/candidates/' + $('#e-id').val() + '/reset-password')).data;
      $('#e-out').html('<div class="alert alert-warning small mb-0">New password (shown once): <b>' + E(r.password) + '</b><br>The candidate must change it at first sign-in.</div>'); } catch (e) {}
  });
  $('#e-dq').on('click', async () => { if (!confirm('Disqualify this candidate? This closes any running test and blocks further stages.')) return; try { await Api.post('/admin/candidates/' + $('#e-id').val() + '/disqualify'); modal.hide(); loadCands(); } catch (e) {} });
  $('#e-reinstate').on('click', async () => { try { await Api.post('/admin/candidates/' + $('#e-id').val() + '/reinstate'); Api.toast('Reinstated', 'success'); modal.hide(); loadCands(); } catch (e) {} });
  $('#e-del').on('click', () => { deleteCands([+$('#e-id').val()], $('#e-name').val(), true); });
  $('#c-bulkdel').on('click', () => deleteCands($('.csel:checked').map((_, el) => +el.value).get()));
  async function deleteCands(ids, name, fromModal) {
    const msg = ids.length === 1 ? 'Delete "' + name + '"?' : 'Delete ' + ids.length + ' selected candidates?';
    if (!confirm(msg + '\n\nThis PERMANENTLY removes their account, answers, scores, violations and code submissions. This cannot be undone.')) return;
    try { const r = ids.length === 1 ? await Api.del('/admin/candidates/' + ids[0]) : await Api.post('/admin/candidates/bulk-delete', { ids });
      Api.toast('Deleted ' + r.data.deleted + ' candidate(s)', 'success'); if (fromModal) modal.hide(); $('#c-detail').empty(); loadCands(); loadFilterOptions(); } catch (e) {}
  }
  // per-stage actions inside the details panel
  $('#c-detail').on('click', 'button[data-retake]', async function () {
    const [cid, tid] = String($(this).data('retake')).split(':');
    if (!confirm('Allow a retake? This DELETES the candidate\'s current attempt (answers, score, violations, code) for this test.')) return;
    try { await Api.post('/admin/candidates/' + cid + '/tests/' + tid + '/retake'); Api.toast('Attempt reset', 'success'); showResults(cid); loadCands(); } catch (e) {}
  }).on('click', 'button[data-unassign]', async function () {
    const [cid, tid] = String($(this).data('unassign')).split(':');
    if (!confirm('Remove this test from the candidate?')) return;
    try { await Api.del('/admin/candidates/' + cid + '/tests/' + tid); Api.toast('Unassigned', 'success'); showResults(cid); loadCands(); } catch (e) {}
  });
  // personality profile (behaviour stage): radar + trait bars + reliability flags + interviewer prompts
  function radar(traits) {
    const n = traits.length, R = 78, C = 110, pt = (i, v) => { const a = -Math.PI / 2 + i * 2 * Math.PI / n; return [C + Math.cos(a) * R * v / 100, C + Math.sin(a) * R * v / 100]; };
    const poly = v => traits.map((t, i) => pt(i, v(t)).map(x => x.toFixed(1)).join(',')).join(' ');
    let g = [25, 50, 75, 100].map(r => '<polygon points="' + poly(() => r) + '" class="rd-grid"/>').join('') + traits.map((t, i) => { const [x, y] = pt(i, 100); return '<line x1="' + C + '" y1="' + C + '" x2="' + x.toFixed(1) + '" y2="' + y.toFixed(1) + '" class="rd-grid"/>'; }).join('');
    g += '<polygon points="' + poly(t => t.score ?? 0) + '" class="rd-area"/>' + traits.map((t, i) => { const [x, y] = pt(i, t.score ?? 0); return '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="3.5" class="rd-dot"/>'; }).join('');
    g += traits.map((t, i) => { const [x, y] = pt(i, 122), a = x < C - 8 ? 'end' : x > C + 8 ? 'start' : 'middle'; return '<text x="' + x.toFixed(1) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="' + a + '" class="rd-lab">' + E(t.label.length > 16 ? t.label.slice(0, 15) + '…' : t.label) + '</text>'; }).join('');
    return '<svg viewBox="-30 -4 280 228" class="radar" role="img" aria-label="Trait profile radar chart">' + g + '</svg>';
  }
  function personalityHtml(p) {
    if (!p) return '<div class="small text-muted mt-2">No personality profile: tag the behaviour questions with a trait in the Test Center to get one.</div>';
    const REL = { ok: ['Answers look consistent', 'success'], caution: ['Treat with caution', 'warning'], low: ['Low confidence', 'danger'] }[p.reliability];
    let h = '<div class="pp mt-3"><div class="d-flex justify-content-between align-items-center flex-wrap gap-2"><h6 class="mb-0">Personality profile</h6><span class="badge text-bg-' + REL[1] + '">' + REL[0] + '</span></div>';
    const T = p.traits.filter(t => t.score !== null);
    h += '<div class="pp-grid">' + (T.length >= 3 ? '<div>' + radar(T) + '</div>' : '') + '<div class="pp-bars">' + (p.traits.map(t => {
      const band = t.band || 'none';
      return '<div class="pp-row"><div class="pp-name">' + E(t.label) + '<span class="pp-n">' + t.answered + '/' + t.items + ' answered</span></div>' +
        '<div class="pp-track"><i class="pp-fill ' + band + '" style="width:' + (t.score ?? 0) + '%"></i><b class="pp-mark" style="left:40%"></b><b class="pp-mark" style="left:70%"></b></div>' +
        '<div class="pp-val">' + (t.score === null ? '–' : Math.round(t.score) + '%') + '<span class="pp-n">' + (t.percentile === null ? '' : 'top ' + Math.max(1, 100 - t.percentile) + '%') + '</span></div></div>';
    }).join('') || '<div class="text-muted">No scored traits.</div>') +
      '<div class="pp-key small text-muted">Bars show % of the scale. Marks at 40% and 70%: below 40 low, 70 and over high. ' + (p.cohort_size >= p.min_cohort ? 'Ranking is among ' + p.cohort_size + ' candidates who took this test.' : 'Ranking appears once ' + p.min_cohort + ' candidates have submitted (' + p.cohort_size + ' so far).') + '</div></div></div>';
    if (p.flags.length) h += '<div class="pp-flags">' + p.flags.map(f => '<div class="pp-flag"><b>' + E(f.label) + '</b><span>' + E(f.detail) + '</span></div>').join('') + '<div class="small text-muted">' + E(p.note) + '</div></div>';
    if (p.prompts.length) h += '<details class="mt-2" open><summary>Interview prompts (' + p.prompts.length + ')</summary>' + p.prompts.map(x => '<div class="pp-prompt"><b>' + E(x.trait) + '</b> <span class="text-muted small">' + E(x.reason) + '</span><ul>' + x.questions.map(q => '<li>' + E(q) + '</li>').join('') + '</ul></div>').join('') + '</details>';
    else h += '<div class="small text-muted mt-2">No trait scored below 40%, so no probing questions are suggested.</div>';
    return h + '</div>';
  }
  // candidate analysis: scorecard matrix across stages, trait heat-map, strengths and watch-outs (all derived from the results payload)
  function analysisHtml(d) {
    const mins = a => a.submitted_at && a.started_at ? Math.max(1, Math.round((new Date(a.submitted_at + 'Z') - new Date(a.started_at + 'Z')) / 60000)) : null;
    const pct = a => a.max_score ? Math.round(a.score / a.max_score * 100) : null;
    const rows = d.stages.map(s => {
      const a = s.attempt; if (!a) return '<tr><td class="text-capitalize"><b>' + s.stage + '</b></td><td colspan="6" class="text-muted">Not attempted</td></tr>';
      const p = pct(a), cutPct = a.max_score ? Math.round(s.cutoff / a.max_score * 100) : null, m = mins(a), w = a.violation_count;
      const cell = (txt, cls) => '<td class="an-c ' + (cls || '') + '">' + txt + '</td>';
      return '<tr><td class="text-capitalize"><b>' + s.stage + '</b></td>' + cell(a.score !== null ? a.score + ' / ' + a.max_score : '–') +
        cell(p === null ? '–' : p + '%', p === null ? '' : p >= (cutPct ?? 0) ? 'an-good' : 'an-bad') + cell(a.passed ? 'Cleared' : a.status === 'submitted' ? 'Not cleared' : a.status, a.passed ? 'an-good' : a.status === 'submitted' ? 'an-bad' : '') +
        cell(a.score !== null && s.cutoff ? (a.score - s.cutoff >= 0 ? '+' : '') + (a.score - s.cutoff).toFixed(1).replace(/\.0$/, '') : '–') + cell(m === null ? '–' : m + ' min') + cell(w, w >= 3 ? 'an-bad' : w ? 'an-warn' : 'an-good') + '</tr>';
    }).join('');
    const good = [], watch = [];
    d.stages.forEach(s => {
      const a = s.attempt; if (!a) return;
      const p = pct(a); if (p !== null && a.passed && p >= 80) good.push('Strong ' + s.stage + ' result (' + p + '%).');
      if (a.status === 'submitted' && !a.passed) watch.push('Did not clear the ' + s.stage + ' cutoff.');
      if (a.violation_count >= 3) watch.push(a.violation_count + ' proctoring warnings in ' + s.stage + '; review the log.');
      else if (a.violation_count === 0 && a.status === 'submitted') good.push('No proctoring warnings in ' + s.stage + '.');
      if (a.submit_reason && /violation/i.test(a.submit_reason)) watch.push('The ' + s.stage + ' test was auto-submitted for violations.');
      const pr = a.personality; if (!pr) return;
      pr.traits.filter(t => t.band === 'high').sort((x, y) => y.score - x.score).slice(0, 3).forEach(t => good.push(t.label + ' ' + Math.round(t.score) + '%: ' + (t.reading || 'high')));
      pr.traits.filter(t => t.band === 'low').sort((x, y) => x.score - y.score).slice(0, 3).forEach(t => watch.push(t.label + ' ' + Math.round(t.score) + '%: ' + (t.reading || 'low')));
      pr.flags.forEach(f => watch.push(f.label + '.'));
    });
    const beh = (d.stages.find(s => s.stage === 'behaviour') || {}).attempt, pr = beh && beh.personality;
    let h = '<div class="an mt-3"><h6 class="mb-2">Candidate analysis</h6><div class="table-responsive"><table class="table table-sm an-table"><thead><tr><th>Stage</th><th>Score</th><th>Percent</th><th>Result</th><th>Vs cutoff</th><th>Time taken</th><th>Warnings</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
    if (pr && pr.traits.length) {
      h += '<div class="an-sub">Personality matrix</div><div class="table-responsive"><table class="table table-sm an-table"><thead><tr><th>Trait</th><th>Score</th><th>Level</th><th>Rank</th><th>What it suggests</th></tr></thead><tbody>' + pr.traits.map(t => {
        const lvl = { low: 'Low', mid: 'Moderate', high: 'High' }[t.band] || '–';
        return '<tr><td><b>' + E(t.label) + '</b></td><td class="an-c an-' + (t.band || 'none') + '">' + (t.score === null ? '–' : Math.round(t.score) + '%') + '</td><td class="an-c an-' + (t.band || 'none') + '">' + lvl + '</td><td class="an-c">' + (t.percentile === null ? '–' : 'top ' + Math.max(1, 100 - t.percentile) + '%') + '</td><td class="small">' + (t.reading ? E(t.reading) : t.band === 'mid' ? 'Mid-range; no strong lean either way.' : '–') + '</td></tr>';
      }).join('') + '</tbody></table></div>';
    }
    h += '<div class="an-two"><div><div class="an-sub an-g">Strengths</div>' + (good.length ? '<ul>' + good.map(x => '<li>' + E(x) + '</li>').join('') + '</ul>' : '<div class="small text-muted">None stand out yet.</div>') + '</div>' +
      '<div><div class="an-sub an-w">Watch-outs</div>' + (watch.length ? '<ul>' + watch.map(x => '<li>' + E(x) + '</li>').join('') + '</ul>' : '<div class="small text-muted">Nothing flagged.</div>') + '</div></div>' +
      '<div class="small text-muted">Generated from scores and signals to help an interviewer decide where to look. It is not a hiring decision.</div></div>';
    return h;
  }
  async function showResults(id) {
    const d = (await Api.get('/admin/candidates/' + id + '/results')).data;
    let h = '<div class="card card-body"><h6>' + E(d.candidate.name) + ' – ' + d.candidate.state.replaceAll('_', ' ') + '</h6>';
    h += analysisHtml(d);
    d.stages.forEach(s => {
      const a = s.attempt; h += '<hr><b class="text-capitalize">' + s.stage + '</b> – ' + E(s.title) + ' (cutoff ' + s.cutoff + ')' +
        (a ? ' <button class="btn btn-outline-secondary btn-sm ms-2" data-retake="' + d.candidate.id + ':' + s.test_id + '">Allow retake</button>' : ' <button class="btn btn-outline-secondary btn-sm ms-2" data-unassign="' + d.candidate.id + ':' + s.test_id + '">Unassign</button>');
      if (!a) { h += '<div class="text-muted">Not attempted</div>'; return; }
      h += '<div>Status: <b>' + a.status + '</b> (' + E(a.submit_reason || '') + ') · Score: <b>' + (a.score ?? '-') + '/' + (a.max_score ?? '-') + '</b> · ' + (a.passed ? '<span class="text-success">cleared</span>' : '<span class="text-danger">not cleared</span>') + ' · started ' + fmt(a.started_at) + ' · IP ' + E(a.start_ip) + '</div>';
      if (s.stage === 'behaviour') h += personalityHtml(a.personality);
      h += '<details class="mt-1"><summary>Per-question breakdown</summary><table class="table table-sm"><tr><th>Question</th><th>Answer</th><th>Score</th></tr>' +
        a.breakdown.map(b => '<tr><td>' + E(b.body.slice(0, 80)) + '</td><td>' + E(b.selected_text ?? (b.type === 'coding' ? '(see submissions)' : '—')) + '</td><td>' + (b.is_correct === true ? '✔ ' : b.is_correct === false ? '✘ ' : '') + (b.score ?? '') + '</td></tr>').join('') + '</table></details>';
      if (a.code_submissions.length) h += '<details><summary>Code submissions (' + a.code_submissions.length + ')</summary>' + a.code_submissions.map(c => '<div class="small mt-1"><b>' + c.kind + '</b> ' + c.language + ' · ' + E(c.status) + ' · ' + c.passed_cases + '/' + c.total_cases + ' · ' + fmt(c.created_at) + '<pre class="code-out">' + E(c.source_code) + '</pre></div>').join('') + '</details>';
      h += '<details open><summary>Proctoring log – ' + a.violation_count + ' counted, ' + a.violations.length + ' events</summary><table class="table table-sm"><tr><th>Time</th><th>Type</th><th>Counted</th></tr>' +
        a.violations.map(v => '<tr><td>' + fmt(v.at) + '</td><td>' + v.type + '</td><td>' + (v.counted ? 'yes' : 'no (clustered/info)') + '</td></tr>').join('') + '</table></details>';
    });
    $('#c-detail').html(h + '</div>');
  }

  // ---------------------------------------------------------------- results & audit
  $('#r-go').on('click', () => {
    const id = $('#r-test').val(); if (!id) return;
    let q = '/admin/results/export?test_id=' + id + ($('#r-passed').prop('checked') ? '&passed_only=true' : '') + ($('#r-min').val() ? '&min_score=' + $('#r-min').val() : '');
    Api.download(q, 'results_test_' + id + '.csv');
  });
  async function loadAudit() {
    const r = await Api.get('/admin/audit', { page_size: 100 });
    $('#au-body').html(r.data.map(a => '<tr><td>' + fmt(a.at) + '</td><td>' + (a.actor_id ?? '') + '</td><td>' + E(a.action) + '</td><td>' + E((a.entity || '') + ' ' + (a.entity_id ?? '')) + '</td><td class="small">' + E(a.detail ? JSON.stringify(a.detail) : '') + '</td><td>' + E(a.ip) + '</td></tr>').join(''));
  }
  // ---------------------------------------------------------------- overview / setup checklist
  async function loadOverview() {
    const [co, ts, cs] = await Promise.all([Api.get('/admin/colleges', { page_size: 1 }), Api.get('/admin/tests', { page_size: 100 }), Api.get('/admin/candidates', { page_size: 1 })]);
    const T = ts.data, c = cs.meta.counts;
    const live = T.filter(t => t.is_active && t.question_count > 0).length, withQ = T.filter(t => t.question_count > 0).length;
    const assigned = T.reduce((a, t) => a + t.assigned_count, 0), attempts = T.reduce((a, t) => a + t.attempt_count, 0);
    $('#ov-stats').html([[co.meta.total, 'Colleges'], [T.length + ' / ' + live, 'Tests / live'], [c.all, 'Candidates'], [c.in_progress, 'In progress'], [c.cleared, 'Cleared']].map(x => '<div><div class="v">' + x[0] + '</div><div class="l">' + x[1] + '</div></div>').join(''));
    const steps = [
      [co.meta.total > 0, 'Onboard your colleges', 'Add each college taking part. Candidates choose theirs when they register.', 'Open Colleges', () => $('#tabs a[data-tab=colleges]').trigger('click')],
      [T.length > 0, 'Create a test', 'One per stage: Aptitude (MCQ), Behaviour (Likert) or Coding. It starts as a draft.', 'Open Test Center', () => location.href = '/admin/test-center'],
      [withQ > 0, 'Add questions', 'Use the builder or upload a CSV/JSON list. Uploads are previewed before anything is saved.', 'Open Test Center', () => location.href = '/admin/test-center'],
      [live > 0, 'Publish the test', 'The Review step checks cutoff and marks. Only published tests are visible to candidates.', 'Open Test Center', () => location.href = '/admin/test-center'],
      [c.all > 0, 'Add candidates', 'Add one by one or upload a CSV. Generated passwords are shown once; download them.', 'Open Candidates', () => $('#tabs a[data-tab=cands]').trigger('click')],
      [assigned > 0, 'Assign tests to candidates', 'Assign by batch name or tick candidates. Each candidate gets one test per stage.', 'Open Candidates', () => $('#tabs a[data-tab=cands]').trigger('click')],
      [attempts > 0, 'Monitor and review', 'Watch progress, cleared / not cleared, warnings and the integrity signals.', 'Open Candidates', () => $('#tabs a[data-tab=cands]').trigger('click')],
    ];
    const firstTodo = steps.findIndex(s => !s[0]);
    $('#ov-steps').html(steps.map((s, i) => '<div class="ck ' + (s[0] ? 'ck-done' : i === firstTodo ? 'ck-now' : '') + '"><span class="ck-n">' + (s[0] ? '✓' : i + 1) + '</span><div class="flex-grow-1"><b>' + s[1] + '</b><div class="small text-muted">' + s[2] + '</div></div>' +
      (s[0] ? '<span class="small text-muted">Done</span>' : '<button class="btn btn-' + (i === firstTodo ? 'primary' : 'outline-secondary') + ' btn-sm" data-ck="' + i + '">' + s[3] + '</button>') + '</div>').join(''));
    $('#ov-steps').off('click').on('click', 'button[data-ck]', function () { steps[+$(this).data('ck')][4](); });
  }

  // ---------------------------------------------------------------- colleges
  let colleges = [];
  async function loadColleges() {
    colleges = (await Api.get('/admin/colleges', { search: $('#co-search').val() || undefined, page_size: 500 })).data;
    $('#co-body').html(colleges.map(c => '<tr><td><b>' + E(c.name) + '</b><div class="small text-muted">' + E(c.code) + '</div></td><td>' + E([c.city, c.state].filter(Boolean).join(', ') || '—') + '</td>' +
      '<td>' + E(c.contact_name || '—') + '<div class="small text-muted">' + E([c.contact_email, c.contact_phone].filter(Boolean).join(' · ')) + '</div></td><td>' + c.candidate_count + '</td>' +
      '<td><span class="badge text-bg-' + (c.is_active ? 'success' : 'secondary') + '">' + (c.is_active ? 'Active' : 'Hidden') + '</span></td>' +
      '<td class="text-nowrap"><button class="btn btn-sm btn-outline-secondary" data-coedit="' + c.id + '">Edit</button> <button class="btn btn-sm btn-outline-danger" data-codel="' + c.id + '">Delete</button></td></tr>').join('') || '<tr><td colspan="6" class="text-muted py-4">No colleges yet. Onboard the first one on the left.</td></tr>');
    refreshCollegeSelects();
  }
  async function refreshCollegeSelects() {
    const all = (await Api.get('/admin/colleges', { page_size: 500 })).data;
    const opts = all.map(c => '<option value="' + c.id + '">' + E(c.name) + (c.is_active ? '' : ' (hidden)') + '</option>').join('');
    const keep = { c: $('#c-college').val(), e: $('#e-college').val() };
    $('#c-college').html('<option value="">College…</option>' + opts).val(keep.c);
    $('#e-college').html('<option value="">— none —</option>' + opts).val(keep.e);
  }
  const coBody = () => ({ name: $('#co-name').val().trim(), code: $('#co-code').val().trim(), city: $('#co-city').val(), state: $('#co-state').val(), address: $('#co-addr').val(), website: $('#co-web').val(),
    contact_name: $('#co-cname').val(), contact_email: $('#co-cemail').val() || null, contact_phone: $('#co-cphone').val(), notes: $('#co-notes').val(), is_active: $('#co-active').prop('checked') });
  function resetCo() { $('#co-form')[0].reset(); $('#co-id').val(''); $('#co-active').prop('checked', true); $('#co-h').text('Onboard a college'); }
  $('#co-reset').on('click', resetCo);
  $('#co-form').on('submit', async e => {
    e.preventDefault(); const id = $('#co-id').val();
    try { id ? await Api.put('/admin/colleges/' + id, coBody()) : await Api.post('/admin/colleges', coBody()); Api.toast(id ? 'College updated' : 'College onboarded', 'success'); resetCo(); loadColleges(); } catch (e) {}
  });
  let cot; $('#co-search').on('input', () => { clearTimeout(cot); cot = setTimeout(loadColleges, 250); });
  $('#co-body').on('click', 'button', async function () {
    const eid = $(this).data('coedit'), did = $(this).data('codel');
    if (eid) { const c = colleges.find(x => x.id === eid); $('#co-h').text('Edit college'); $('#co-id').val(c.id); $('#co-name').val(c.name); $('#co-code').val(c.code); $('#co-city').val(c.city); $('#co-state').val(c.state); $('#co-addr').val(c.address); $('#co-web').val(c.website);
      $('#co-cname').val(c.contact_name); $('#co-cemail').val(c.contact_email); $('#co-cphone').val(c.contact_phone); $('#co-notes').val(c.notes); $('#co-active').prop('checked', c.is_active); $('#co-name')[0].scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    if (did) {
      const c = colleges.find(x => x.id === did);
      if (!confirm('Delete "' + c.name + '"?')) return;
      try { await Api.del('/admin/colleges/' + did, { silent: true }); Api.toast('Deleted', 'success'); loadColleges(); }
      catch (xhr) {
        const er = xhr.responseJSON && xhr.responseJSON.error;
        if (er && er.code === 'college_in_use') {
          const others = colleges.filter(x => x.id !== did); if (!others.length) { Api.toast('Add another college first, or remove its candidates'); return; }
          const pick = prompt(er.details.candidates + ' candidate(s) belong to "' + c.name + '".\nMove them to which college? Type the number:\n\n' + others.map((x, i) => (i + 1) + ' = ' + x.name).join('\n') + '\n\n(Or hide the college instead by editing it and un-ticking Active.)');
          const target = others[(parseInt(pick, 10) || 0) - 1]; if (!target) return;
          try { await Api.del('/admin/colleges/' + did + '?move_to=' + target.id); Api.toast('Deleted; candidates moved to ' + target.name, 'success'); loadColleges(); } catch (e) {}
        } else Api.toast(Api.errMsg(xhr));
      }
    }
  });

  // ---------------------------------------------------------------- results & analytics
  let rData = null, rTable = false, testsReady = Promise.resolve();
  const bar = (pct, cls) => '<div class="rbar"><i class="' + (cls || '') + '" style="width:' + Math.max(0, Math.min(100, pct)) + '%"></i></div>';
  const FLAG = { easy: ['▲', 'Too easy'], hard: ['▼', 'Very hard'], key: ['⚑', 'Check key'], flat: ['＝', 'No spread'], skipped: ['∅', 'Often skipped'] };
  async function loadResults() {
    await testsReady;   // opened by address (/admin/results): the test list may still be loading
    const id = $('#r-test').val(); if (!id) { $('#r-body').html('<div class="text-muted py-4">Create a test first.</div>'); return; }
    $('#r-body').html('<div class="text-muted py-4">Loading…</div>');
    rData = (await Api.get('/admin/tests/' + id + '/analytics')).data; renderResults();
  }
  function histogram(h, d) {
    const max = Math.max(1, ...h.bins);
    const cols = h.bins.map((n, i) => { const lo = i * 10, below = h.below_cutoff[i], above = n - below;
      return '<div class="hcol" title="' + lo + '–' + (lo + 10) + '%: ' + n + ' candidate' + (n === 1 ? '' : 's') + ' (' + above + ' cleared, ' + below + ' below the cutoff)"><div class="hn">' + (n || '') + '</div>' +
        '<div class="hbars" style="height:' + (100 * n / max) + '%"><i class="below" style="flex:' + below + '"></i><i class="above" style="flex:' + above + '"></i></div><div class="hl">' + lo + '</div></div>'; }).join('');
    return '<div class="hist">' + cols + '</div><div class="hleg"><span><i class="above"></i> Cleared the cutoff</span><span><i class="below"></i> Below the cutoff</span><span class="text-muted">Score as % of marks available · bars show number of candidates</span></div>';
  }
  function renderResults() {
    const d = rData, $b = $('#r-body');
    if (d.empty) { $b.html('<div class="card card-body"><b>No completed attempts yet.</b><div class="small text-muted">' + d.assigned + ' candidate(s) assigned, ' + d.started + ' started. Analytics appear once candidates submit.</div></div>'); return; }
    const tile = (v, l, sub) => '<div><div class="v">' + v + '</div><div class="l">' + l + '</div>' + (sub ? '<div class="small text-muted">' + sub + '</div>' : '') + '</div>';
    let h = '<div class="stat-row">' + tile(d.completed, 'Completed', d.assigned + ' assigned · ' + d.in_progress + ' running · ' + d.not_started + ' not started') + tile(d.pass_rate + '%', 'Cleared the cutoff', Math.round(d.pass_rate * d.completed / 100) + ' of ' + d.completed) +
      tile(d.avg_percent + '%', 'Average score', 'median ' + d.median_percent + '% · top ' + d.top_percent + '%') + tile(d.median_minutes + ' min', 'Median time', 'of ' + d.allotted_minutes + ' allowed') +
      tile(d.auto_submitted + d.disqualified, 'Auto-submitted / disqualified', d.with_violations + ' with warnings · avg ' + d.avg_violations) + '</div>';
    h += '<h6>Score distribution</h6>' + (rTable ? '<table class="table table-sm" style="max-width:520px"><thead><tr><th>Score range</th><th>Candidates</th><th>Below cutoff</th></tr></thead><tbody>' + d.histogram.bins.map((n, i) => '<tr><td>' + i * 10 + '–' + (i * 10 + 10) + '%</td><td>' + n + '</td><td>' + d.histogram.below_cutoff[i] + '</td></tr>').join('') + '</tbody></table>' : histogram(d.histogram, d));
    // item analysis
    const flagged = d.items.filter(i => i.flags.length).length;
    h += '<div class="d-flex justify-content-between align-items-end mt-5"><h6 class="mb-0">Question analysis <span class="text-muted">(' + d.items.length + ' questions · ' + flagged + ' need a look)</span></h6><label class="tc-check"><input type="checkbox" id="r-flagged"> Only flagged</label></div>' +
      '<div class="small text-muted mb-2">Each question is judged only on the candidates who were served it. A question needs at least 5 candidates before it can be flagged.</div><div id="r-items"></div>';
    // groups
    const grp = (title, rows) => rows.length ? '<h6 class="mt-5">' + title + '</h6><table class="table table-sm"><thead><tr><th>' + title.replace('By ', '') + '</th><th>Completed</th><th style="width:34%">Cleared the cutoff</th><th>Average score</th></tr></thead><tbody>' +
      rows.map(r => '<tr><td>' + E(r.name) + '</td><td>' + r.completed + '</td><td>' + (rTable ? r.pass_rate + '%' : bar(r.pass_rate, 'ink') + '<span class="rbar-n">' + r.pass_rate + '%</span>') + '</td><td>' + (r.avg_percent ?? '–') + '%</td></tr>').join('') + '</tbody></table>' : '';
    h += grp('By college', d.by_college) + grp('By batch', d.by_batch);
    $b.html(h); renderItems();
  }
  function renderItems() {
    const only = $('#r-flagged').prop('checked'); const rows = rData.items.filter(i => !only || i.flags.length);
    const measure = i => i.type === 'mcq' ? (i.correct_rate == null ? '–' : (rTable ? Math.round(i.correct_rate * 100) + '% correct' : bar(i.correct_rate * 100, 'ink') + '<span class="rbar-n">' + Math.round(i.correct_rate * 100) + '% correct</span>'))
      : i.type === 'likert' ? 'avg ' + (i.avg_score ?? '–') + ' of ' + i.max_score : (i.avg_ratio == null ? '–' : (rTable ? Math.round(i.avg_ratio * 100) + '% of tests passed' : bar(i.avg_ratio * 100, 'ink') + '<span class="rbar-n">' + Math.round(i.avg_ratio * 100) + '% of tests passed</span>') + ' · solved by ' + i.solved);
    $('#r-items').html(rows.map(i => '<div class="ri' + (i.flags.length ? ' flagged' : '') + '"><div class="ri-h" data-ri="' + i.id + '"><div class="ri-body"><div>' + E(i.body) + '</div><div class="small text-muted">' + ({ mcq: 'MCQ', likert: 'Likert', coding: 'Coding' }[i.type]) + ' · ' + i.difficulty + ' · served to ' + i.served + (i.answered !== undefined ? ' · answered ' + i.answered : '') + '</div></div>' +
      '<div class="ri-m">' + measure(i) + '</div><div class="ri-f">' + (i.flags.map(f => '<span class="flag f-' + f[0] + '" title="' + E(f[1]) + '">' + FLAG[f[0]][0] + ' ' + FLAG[f[0]][1] + '</span>').join('') || '<span class="text-muted small">—</span>') + '</div></div>' +
      '<div class="ri-d d-none" id="ri-' + i.id + '">' + (i.flags.length ? '<ul class="small">' + i.flags.map(f => '<li>' + E(f[1]) + '</li>').join('') + '</ul>' : '') + (i.options ? '<div class="pg-lab">How candidates answered</div>' + i.options.map(o => { const tot = Math.max(1, i.answered); return '<div class="ro"><span class="ro-t">' + (o.correct ? '✓ ' : '') + E(o.text) + (o.score !== undefined ? ' <span class="text-muted">(score ' + o.score + ')</span>' : '') + '</span>' + bar(100 * o.chosen / tot, o.correct ? 'ink' : 'grey') + '<span class="rbar-n">' + o.chosen + ' · ' + Math.round(100 * o.chosen / tot) + '%</span></div>'; }).join('') : '') + '</div></div>').join('') || '<div class="text-muted py-3">Nothing flagged. Good.</div>');
  }
  $('#r-body').on('click', '.ri-h', function () { $('#ri-' + $(this).data('ri')).toggleClass('d-none'); }).on('change', '#r-flagged', renderItems);
  $('#r-test').on('change', loadResults);
  $('#r-table-toggle').on('click', function () { rTable = !rTable; $(this).text(rTable ? 'View as charts' : 'View numbers as a table'); if (rData) renderResults(); });

  // ---------------------------------------------------------------- integrity
  $('#i-th').on('input', function () { $('#i-th-v').text(this.value); });
  $('#i-go').on('click', async () => {
    const id = $('#i-test').val(); if (!id) return;
    const r = (await Api.get('/admin/tests/' + id + '/integrity', { similarity: $('#i-th').val() / 100 })).data;
    const who = x => '<a href="#" data-cand="' + x.candidate_id + '">' + E(x.name) + '</a>';
    let h = '<h6>Attempt flags (' + r.attempt_flags.length + ')</h6>' + (r.attempt_flags.length ? '<table class="table table-sm"><thead><tr><th>Candidate</th><th>Score</th><th>Why flagged</th></tr></thead><tbody>' +
      r.attempt_flags.map(f => '<tr><td>' + who(f) + '<div class="small text-muted">' + E(f.batch) + '</div></td><td>' + f.score + ' / ' + f.max_score + '</td><td>' + f.reasons.map(E).join('<br>') + '</td></tr>').join('') + '</tbody></table>' : '<p class="text-muted">None.</p>');
    if (r.test.stage === 'coding') {
      h += '<h6 class="mt-4">Similar code (' + r.code_pairs.length + ')</h6>' + (r.code_pairs.length ? '<table class="table table-sm"><thead><tr><th>Similarity</th><th>Candidate A</th><th>Candidate B</th><th></th></tr></thead><tbody>' +
        r.code_pairs.map(p => '<tr><td><b class="text-danger">' + Math.round(p.similarity * 100) + '%</b></td><td>' + who(p.a) + '</td><td>' + who(p.b) + '</td><td><button class="btn btn-outline-primary btn-sm" data-cmp="' + p.a.submission_id + ':' + p.b.submission_id + '">Compare</button></td></tr>').join('') + '</tbody></table><div id="i-cmp"></div>' : '<p class="text-muted">No pairs above the threshold. Very short solutions are ignored on purpose.</p>');
    } else {
      h += '<h6 class="mt-4">Shared wrong answers (' + r.answer_pairs.length + ')</h6>' + (r.answer_pairs.length ? '<table class="table table-sm"><thead><tr><th>Candidate A</th><th>Candidate B</th><th>Same wrong option on</th></tr></thead><tbody>' +
        r.answer_pairs.map(p => '<tr><td>' + who(p.a) + '</td><td>' + who(p.b) + '</td><td><b>' + p.shared_wrong + '</b> questions (' + Math.round(p.ratio * 100) + '% of their wrong answers)</td></tr>').join('') + '</tbody></table>' : '<p class="text-muted">None.</p>');
    }
    h += '<h6 class="mt-4">Shared start IP (' + r.shared_ip.length + ')</h6>' + (r.shared_ip.length ? r.shared_ip.map(x => '<div class="small">' + E(x.ip) + ' — ' + x.candidates.map(E).join(', ') + '</div>').join('') : '<p class="text-muted">None.</p>');
    $('#i-out').html(h);
  });
  $('#i-out').on('click', 'button[data-cmp]', async function () {
    const [a, b] = String($(this).data('cmp')).split(':');
    const [x, y] = await Promise.all([Api.get('/admin/code-submissions/' + a), Api.get('/admin/code-submissions/' + b)]);
    $('#i-cmp').html('<div class="row g-3 mt-2"><div class="col-md-6"><pre class="code-out">' + E(x.data.source_code) + '</pre></div><div class="col-md-6"><pre class="code-out">' + E(y.data.source_code) + '</pre></div></div>');
  }).on('click', 'a[data-cand]', function (e) { e.preventDefault(); $('#tabs a[data-tab=cands]').trigger('click'); showResults($(this).data('cand')); });

  testsReady = loadTests(); loadOverview();
  const openByPath = () => {
    const seg = location.pathname.replace(/^\/admin\/?/, '').replace(/\/$/, ''), legacy = location.hash.slice(1);
    const tab = Object.keys(SLUG).find(k => SLUG[k] === seg) || (legacy && SLUG[legacy] !== undefined ? legacy : 'overview');
    silentNav = true; $('#tabs a[data-tab=' + tab + ']').trigger('click'); silentNav = false;
  };
  openByPath(); window.addEventListener('popstate', openByPath);
});
