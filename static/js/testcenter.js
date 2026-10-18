// Test Center: list + workspace (Settings / Questions / Review). Clean URLs:
//   /admin/test-center            /admin/test-center/3            /admin/test-center/3/questions
$(async function () {
  const me = await Api.requireRole('admin'); if (!me) return;
  $('#admin-who').text(me.email); $('#logout').on('click', Api.logout);
  const E = Api.esc;
  const TYPE = { aptitude: 'mcq', behaviour: 'likert', coding: 'coding' };
  const TYPE_LABEL = { aptitude: 'MCQ', behaviour: 'Likert', coding: 'Coding' };
  const TEMPLATE = {
    aptitude: ['aptitude_mcq.csv', 'CSV columns: type, body, options (separate with |), correct (1-based number of the right option), marks, difficulty.'],
    behaviour: ['behaviour_likert.csv', 'CSV columns: type, body, options (separate with |), scores (same count, separate with |). Reverse-scored statements list their scores in reverse.'],
    coding: ['coding.json', 'JSON list of coding problems. Each needs test_cases with at least one sample and one hidden case.'] };
  const PRESETS = {
    agree5: [['Strongly disagree', 1], ['Disagree', 2], ['Neutral', 3], ['Agree', 4], ['Strongly agree', 5]],
    freq5: [['Never', 1], ['Rarely', 2], ['Sometimes', 3], ['Often', 4], ['Always', 5]],
    forced4: [['Strongly disagree', 1], ['Disagree', 2], ['Agree', 3], ['Strongly agree', 4]],
    agree7: [['Strongly disagree', 1], ['Disagree', 2], ['Somewhat disagree', 3], ['Neutral', 4], ['Somewhat agree', 5], ['Agree', 6], ['Strongly agree', 7]],
  };
  const PRESET_NAMES = { agree5: '5-point agreement', freq5: '5-point frequency', forced4: '4-point forced choice', agree7: '7-point agreement', custom: 'Custom scale' };
  const LETTERS = 'ABCDEFGH';
  let tests = [], cur = null, summary = null, qs = [], editing = null, pendingFile = null, curStep = 'settings', settingsDirty = false;
  const listFilter = { q: '', status: 'all', stage: '' };
  const isMcq = () => cur.stage === 'aptitude', isLikert = () => cur.stage === 'behaviour', isCode = () => cur.stage === 'coding';
  const utc = s => s ? new Date(s.endsWith('Z') ? s : s + 'Z') : null;
  const toLocalInput = s => { const d = utc(s); return d ? new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ''; };
  const toIso = v => v ? new Date(v).toISOString() : null;
  const url = (id, step) => '/admin/test-center' + (id ? '/' + id + (step ? '/' + step : '') : '');
  const drawer = new bootstrap.Offcanvas(document.getElementById('tc-drawer'));
  const typeModal = new bootstrap.Modal(document.getElementById('tc-type-modal'));
  const importModal = new bootstrap.Modal(document.getElementById('tc-import-modal'));

  // =================================================================== test list
  async function loadList() {
    tests = (await Api.get('/admin/tests', { page_size: 100 })).data; renderList();
  }
  function renderList() {
    const f = listFilter, q = f.q.toLowerCase();
    const rows = tests.filter(t => (f.status === 'all' || (f.status === 'live') === t.is_active) && (!f.stage || t.stage === f.stage) && (!q || t.title.toLowerCase().includes(q)));
    $('#tc-items').html(rows.map(t => '<div class="tc-item' + (cur && cur.id === t.id ? ' on' : '') + '" data-id="' + t.id + '"><div class="d-flex justify-content-between gap-2"><b>' + E(t.title) + '</b>' +
      '<span class="tc-dot ' + (t.is_active ? 'live' : 'draft') + '" title="' + (t.is_active ? 'Live' : 'Draft') + '"></span></div><div class="small text-muted text-capitalize">' + t.stage + ' · ' + TYPE_LABEL[t.stage] + ' · ' + t.question_count + ' question' + (t.question_count === 1 ? '' : 's') + ' · cutoff ' + t.cutoff_score + '</div></div>').join('') ||
      '<div class="text-muted small py-3">' + (tests.length ? 'No tests match these filters.' : 'No tests yet.') + '</div>');
  }
  $('#tc-search').on('input', function () { listFilter.q = this.value; renderList(); });
  $('#tc-filters').on('click', 'button', function () {
    if ($(this).data('f')) { listFilter.status = $(this).data('f'); $('#tc-filters [data-f]').removeClass('on'); $(this).addClass('on'); }
    else { const s = $(this).data('s'); listFilter.stage = listFilter.stage === s ? '' : s; $('#tc-filters [data-s]').removeClass('on'); if (listFilter.stage) $(this).addClass('on'); }
    renderList();
  });
  $('#tc-items').on('click', '.tc-item', function () { guard(() => select(+$(this).data('id'))); });
  $('#tc-new,#tc-new2').on('click', () => guard(() => typeModal.show()));
  $('.tc-type').on('click', function () { typeModal.hide(); startNew($(this).data('new')); });
  function guard(fn) { if (settingsDirty && !confirm('You have unsaved settings. Discard them?')) return; settingsDirty = false; fn(); }
  window.addEventListener('beforeunload', e => { if (settingsDirty) { e.preventDefault(); e.returnValue = ''; } });

  // =================================================================== workspace shell
  function showMain() { $('#tc-empty').addClass('d-none'); $('#tc-main').removeClass('d-none'); }
  function startNew(stage) {
    cur = { id: null, stage, is_active: false }; summary = null; qs = []; showMain(); renderHeader(); renderSteps();
    fillSettings({ title: '', stage, duration_min: stage === 'coding' ? 60 : 30, cutoff_score: 0, max_violations: 3, show_result: false, questions_per_attempt: null });
    $('#b-clone,#b-del').addClass('d-none'); history.replaceState(null, '', url()); step('settings'); $('#s-title').focus(); renderList();
  }
  async function select(id, wantStep) {
    const s = (await Api.get('/admin/tests/' + id + '/summary')).data; summary = s; cur = s.test; showMain();
    $('#b-clone,#b-del').removeClass('d-none'); renderHeader(); fillSettings(cur); renderList();
    qs = (await Api.get('/admin/tests/' + id + '/questions')).data; renderSteps();
    step(wantStep || (s.question_count ? 'questions' : 'settings'));
  }
  async function refreshSummary() {
    if (!cur || !cur.id) return; summary = (await Api.get('/admin/tests/' + cur.id + '/summary')).data; cur = summary.test; renderHeader(); renderSteps(); loadList();
  }
  function renderHeader() {
    document.title = (cur.title || 'New test') + ' · Test Center';
    $('#tc-title').text(cur.title || 'New ' + cur.stage + ' test');
    $('#tc-badges').html('<span class="badge text-bg-' + (cur.is_active ? 'success' : 'secondary') + ' me-1">' + (cur.is_active ? 'Live' : 'Draft') + '</span><span class="badge text-capitalize">' + cur.stage + ' · ' + TYPE_LABEL[cur.stage] + '</span>' + (cur.id ? '' : ' <span class="badge text-bg-warning">Not saved yet</span>'));
    const $p = $('#b-publish');
    if (!cur.id) { $p.prop('disabled', true).text('Publish').attr('title', 'Save the settings first'); return; }
    if (cur.is_active) $p.prop('disabled', false).removeClass('btn-primary').addClass('btn-outline-secondary').text('Unpublish').attr('title', 'Hide this test from candidates');
    else $p.removeClass('btn-outline-secondary').addClass('btn-primary').text('Publish').prop('disabled', !(summary && summary.ready)).attr('title', summary && summary.ready ? 'Make this test visible to assigned candidates' : 'Fix the issues in “Review & publish” first');
  }
  function renderSteps() {
    const n = summary ? summary.question_count : qs.length, ready = summary && summary.ready, issues = summary ? summary.errors.length : 0;
    const set = (step, state, text) => { const $li = $('#steps [data-step=' + step + ']'); $li.removeClass('done warn').addClass(state || ''); $li.find('.n').text(state === 'done' ? '✓' : state === 'warn' ? '!' : $li.find('.n').data('n')); $('#st-' + step).text(text); };
    $('#steps .n').each(function (i) { $(this).data('n', i + 1); });
    set('settings', cur.id ? 'done' : '', cur.id ? (cur.duration_min + ' min · cutoff ' + cur.cutoff_score) : 'Timing, cutoff, rules');
    set('questions', n ? 'done' : '', n ? n + ' question' + (n === 1 ? '' : 's') : 'Add or import');
    set('review', !cur.id ? '' : issues ? 'warn' : ready ? 'done' : '', !cur.id ? 'Final checks' : issues ? issues + ' to fix' : ready ? (cur.is_active ? 'Live' : 'Ready to publish') : 'Final checks');
  }
  $('#steps').on('click', 'li', function () { const s = $(this).data('step'); if (s !== 'settings' && !cur.id) { Api.toast('Save the settings first', 'warning'); return; } guard(() => step(s)); });
  function step(name) {
    curStep = name; $('#steps li').removeClass('on').filter('[data-step=' + name + ']').addClass('on');
    ['settings', 'questions', 'review'].forEach(s => $('#step-' + s).toggleClass('d-none', s !== name));
    history.replaceState(null, '', cur.id ? url(cur.id, name) : url());
    if (name === 'questions') { configureImport(); renderQuestions(); }
    if (name === 'review') loadReview();
    if (name === 'settings') updateSettingsHints();
  }
  $('#b-clone').on('click', async () => { try { const r = await Api.post('/admin/tests/' + cur.id + '/clone'); await loadList(); select(r.data.id, 'settings'); Api.toast('Duplicated as a draft', 'success'); } catch (e) {} });
  $('#b-del').on('click', async () => { if (await Api.deleteTest(cur.id, cur.title)) { cur = null; settingsDirty = false; $('#tc-main').addClass('d-none'); $('#tc-empty').removeClass('d-none'); history.replaceState(null, '', url()); loadList(); Api.toast('Deleted', 'success'); } });
  $('#b-publish').on('click', async () => {
    try {
      if (cur.is_active) { await Api.post('/admin/tests/' + cur.id + '/unpublish'); Api.toast('Unpublished: candidates can no longer see it', 'success'); }
      else { await Api.post('/admin/tests/' + cur.id + '/publish'); Api.toast('Published: assigned candidates can now see it', 'success'); }
      await refreshSummary(); if (curStep === 'review') loadReview();
    } catch (e) {}
  });

  // =================================================================== 1 · settings
  function fillSettings(t) {
    $('#s-title').val(t.title); $('#s-dur').val(t.duration_min); $('#s-cut').val(t.cutoff_score); $('#s-pool').val(t.questions_per_attempt || '');
    $('#s-start').val(toLocalInput(t.start_at)); $('#s-end').val(toLocalInput(t.end_at)); $('#s-maxv').val(t.max_violations); $('#s-show').prop('checked', !!t.show_result);
    $('#s-type').html('<b class="text-capitalize">' + cur.stage + '</b><small>' + TYPE_LABEL[cur.stage] + (cur.id ? ' · fixed' : '') + '</small>');
    const cfg = TEMPLATE[cur.stage]; settingsDirty = false; setState(cur.id ? 'All changes saved' : 'Not saved yet', cur.id ? 'ok' : 'warn'); updateSettingsHints();
  }
  function setState(text, kind) { $('#s-state').text(text).attr('class', 'tc-state ' + (kind || '')); }
  function updateSettingsHints() {
    const total = summary ? summary.per_candidate.max_marks : 0, cut = parseFloat($('#s-cut').val());
    $('#s-cut-h').html(total && !isNaN(cut) ? '= <b>' + Math.round(100 * cut / total) + '%</b> of ' + total + ' marks' + (cut > (summary ? summary.per_candidate.min_marks : total) ? ' <span class="text-danger">· higher than some candidates can score</span>' : '') : 'Marks a candidate must reach to unlock the next stage.');
    const pool = parseInt($('#s-pool').val(), 10), bank = summary ? summary.question_count : 0;
    $('#s-pool-h').html(pool ? (bank ? 'Each candidate gets <b>' + Math.min(pool, bank) + '</b> of ' + bank + ' questions' + (bank < pool * 2 ? ' <span class="text-danger">· bank is small; aim for 2–3× this</span>' : '') : 'Add questions to see the effect.') : 'Empty = everyone gets every question. A number draws that many at random per candidate.');
    const s = $('#s-start').val(), e = $('#s-end').val(), dur = parseInt($('#s-dur').val(), 10);
    $('#s-win-h').html(s && e && new Date(e) <= new Date(s) ? '<span class="text-danger">Closing time must be after the opening time.</span>' : (s && e && dur && (new Date(e) - new Date(s)) / 60000 < dur ? '<span class="text-danger">The window is shorter than the duration.</span>' : 'Leave both empty for a test that is always open. A late starter\'s timer never runs past the closing time.'));
    const n = pool ? Math.min(pool, bank) : bank;   // reflects what is typed, not just what is saved
    $('#s-sum').html('<ul class="tc-sum"><li><b>' + (dur || '–') + ' minutes</b> on a server-kept timer</li><li><b>' + (n || '?') + ' question' + (n === 1 ? '' : 's') + '</b>' + (pool ? ' drawn at random from the bank' : '') + ', shuffled per candidate</li><li>Clear with <b>' + (isNaN(cut) ? '–' : cut) + (total ? ' / ' + total : '') + ' marks</b></li><li>Auto-submit after <b>' + ($('#s-maxv').val() || '–') + ' warnings</b></li><li>' + ($('#s-show').prop('checked') ? 'Score <b>shown</b> after submitting' : 'Score <b>hidden</b> from candidates') + '</li><li>' + (s || e ? 'Open ' + (s ? new Date(s).toLocaleString() : 'now') + ' → ' + (e ? new Date(e).toLocaleString() : 'no end') : 'Always open') + '</li></ul>');
  }
  $('#s-form').on('input change', 'input', function () { settingsDirty = true; setState('Unsaved changes', 'warn'); updateSettingsHints(); });
  $('#s-discard').on('click', () => { if (cur.id) fillSettings(cur); else startNew(cur.stage); });
  $('#s-form').on('submit', async e => {
    e.preventDefault();
    const body = { title: $('#s-title').val().trim(), stage: cur.stage, duration_min: +$('#s-dur').val(), cutoff_score: +$('#s-cut').val(), start_at: toIso($('#s-start').val()), end_at: toIso($('#s-end').val()),
      max_violations: +$('#s-maxv').val(), questions_per_attempt: $('#s-pool').val() ? +$('#s-pool').val() : null, show_result: $('#s-show').prop('checked'), is_active: !!cur.is_active };
    $('#s-save').prop('disabled', true).text('Saving…'); setState('Saving…');
    try {
      if (!cur.id) { const r = await Api.post('/admin/tests', body); settingsDirty = false; await loadList(); await select(r.data.id, 'questions'); Api.toast('Draft created. Now add questions.', 'success'); }
      else { await Api.put('/admin/tests/' + cur.id, body); settingsDirty = false; await refreshSummary(); fillSettings(cur); Api.toast('Settings saved', 'success'); }
    } catch (e) { setState('Not saved', 'bad'); }
    $('#s-save').prop('disabled', false).text('Save settings');
  });

  // =================================================================== 2 · questions
  function qPreviewText(q) { return q.body.length > 150 ? q.body.slice(0, 150) + '…' : q.body; }
  function qMeta(q) {
    if (q.type === 'coding') { const hid = q.test_cases.filter(c => !c.is_sample).length; return q.test_cases.length + ' test cases (' + hid + ' hidden) · ' + q.marks + ' marks'; }
    if (q.type === 'likert') return q.options.length + '-point scale · scores ' + Math.min(...q.options.map(o => o.score)) + '–' + Math.max(...q.options.map(o => o.score));
    return q.options.length + ' options · ' + q.marks + ' mark' + (q.marks === 1 ? '' : 's');
  }
  function filteredQs() { const t = $('#q-search').val().toLowerCase(), d = $('#q-diff').val(); return qs.filter(q => (!t || q.body.toLowerCase().includes(t)) && (!d || q.difficulty === d)); }
  function renderQuestions() {
    const total = qs.reduce((a, q) => a + (q.type === 'likert' ? Math.max(...q.options.map(o => o.score)) : q.marks), 0), by = d => qs.filter(q => q.difficulty === d).length;
    $('#q-stats').html('<div><b>' + qs.length + '</b><span>questions</span></div><div><b>' + total + '</b><span>total marks</span></div><div class="tc-diffbars"><i class="e" style="flex:' + by('easy') + '"></i><i class="m" style="flex:' + by('medium') + '"></i><i class="h" style="flex:' + by('hard') + '"></i><span>easy ' + by('easy') + ' · medium ' + by('medium') + ' · hard ' + by('hard') + '</span></div>');
    const list = filteredQs();
    if (!qs.length) { $('#q-list').html('<div class="tc-emptystate"><h5>No questions yet</h5><p class="text-muted">Write your first ' + TYPE_LABEL[cur.stage] + ' question, or import a prepared list from a file.</p><button class="btn btn-primary me-2" data-act="add">＋ Add a question</button><button class="btn btn-outline-secondary" data-act="import">⇪ Import a file</button></div>'); updateBulk(); return; }
    $('#q-list').html(list.map(q => { const i = qs.indexOf(q) + 1;
      return '<div class="tc-q" data-id="' + q.id + '"><label class="tc-check"><input type="checkbox" class="q-sel" value="' + q.id + '"></label><span class="tc-qn">' + i + '</span>' +
        '<div class="tc-qbody"><div>' + E(qPreviewText(q)) + '</div><div class="small text-muted">' + qMeta(q) + '</div></div><span class="tc-diff ' + q.difficulty + '">' + q.difficulty + '</span>' +
        '<div class="text-nowrap"><button class="btn btn-outline-secondary btn-sm" data-edit="' + q.id + '">Edit</button> <button class="btn btn-outline-secondary btn-sm" data-dup="' + q.id + '" title="Duplicate">⧉</button> <button class="btn btn-outline-danger btn-sm" data-rm="' + q.id + '" title="Delete">×</button></div></div>'; }).join('') || '<div class="text-muted py-4">No questions match.</div>');
    updateBulk();
  }
  function updateBulk() { const n = $('.q-sel:checked').length; $('#q-delsel').toggleClass('d-none', !n).text('Delete ' + n + ' selected'); $('#q-all').prop('checked', n && n === $('.q-sel').length); }
  $('#q-search,#q-diff').on('input change', renderQuestions);
  $('#q-all').on('change', function () { $('.q-sel').prop('checked', this.checked); updateBulk(); });
  $('#q-list').on('change', '.q-sel', updateBulk)
    .on('click', '[data-act=add]', () => openDrawer(null)).on('click', '[data-act=import]', openImport)
    .on('click', '[data-edit]', function () { openDrawer(qs.find(q => q.id === +$(this).data('edit'))); })
    .on('click', '[data-dup]', async function () { const q = qs.find(x => x.id === +$(this).data('dup')); try { await Api.post('/admin/tests/' + cur.id + '/questions', toPayload(q)); Api.toast('Question duplicated', 'success'); await reloadQs(); } catch (e) {} })
    .on('click', '[data-rm]', async function () { if (!confirm('Delete this question?')) return; try { await Api.del('/admin/tests/' + cur.id + '/questions/' + $(this).data('rm')); await reloadQs(); } catch (e) {} });
  $('#q-delsel').on('click', async () => {
    const ids = $('.q-sel:checked').map((_, e) => +e.value).get(); if (!confirm('Delete ' + ids.length + ' question(s)?')) return;
    try { for (const id of ids) await Api.del('/admin/tests/' + cur.id + '/questions/' + id, { silent: true }); } catch (e) { Api.toast('Some questions could not be deleted (locked once candidates start)', 'warning'); }
    await reloadQs();
  });
  async function reloadQs() { qs = (await Api.get('/admin/tests/' + cur.id + '/questions')).data; await refreshSummary(); renderQuestions(); }
  const toPayload = q => ({ type: q.type, body: q.body, difficulty: q.difficulty, marks: q.marks, correct_option: q.correct_option, options: q.options && q.options.map(o => o.score !== undefined ? { text: o.text, score: o.score } : { text: o.text }),
    input_format: q.input_format, output_format: q.output_format, function_signature: q.function_signature, time_limit_ms: q.time_limit_ms, memory_limit_kb: q.memory_limit_kb,
    test_cases: (q.test_cases || []).map(c => ({ input: c.input, expected_output: c.expected_output, is_sample: c.is_sample })) });
  $('#q-add').on('click', () => openDrawer(null)); $('#q-import').on('click', openImport);

  // ---------- builder drawer with live candidate preview
  function optRow(text, score, correct) {
    const idx = $('#qd-form .opt-row').length;
    return $('<div class="tc-opt-row opt-row">' + (isMcq() ? '<label class="tc-correct" title="Mark as the correct answer"><input type="radio" name="correct"' + (correct ? ' checked' : '') + '><span class="L">' + LETTERS[idx] + '</span></label>' : '<span class="L2">' + (idx + 1) + '</span>') +
      '<input class="form-control opt-text" placeholder="' + (isMcq() ? 'Option text' : 'Label') + '">' + (isLikert() ? '<input type="number" step="any" class="form-control opt-score" placeholder="score" value="' + (score ?? '') + '">' : '') + '<button type="button" class="btn btn-outline-danger btn-sm rm" title="Remove">×</button></div>').find('.opt-text').val(text || '').end();
  }
  function relabel() { $('#qd-form .opt-row').each(function (i) { $(this).find('.L').text(LETTERS[i]); $(this).find('.L2').text(i + 1); }); }
  function caseRow(c) {
    c = c || {};
    const $r = $('<div class="tc-case case-row"><div class="row g-2"><div class="col"><label>Input</label><textarea class="form-control code c-in" rows="2"></textarea></div><div class="col"><label>Expected output</label><textarea class="form-control code c-out" rows="2"></textarea></div>' +
      '<div class="col-auto" style="width:130px"><label>Visibility</label><select class="form-select c-vis"><option value="1">Sample (shown)</option><option value="0">Hidden (scoring)</option></select><button type="button" class="btn btn-link btn-sm p-0 mt-1 rm">Remove</button></div></div></div>');
    $r.find('.c-in').val(c.input || ''); $r.find('.c-out').val(c.expected_output || ''); $r.find('.c-vis').val(c.is_sample === false ? '0' : '1'); return $r;
  }
  function buildForm(q) {
    const lbl = isCode() ? 'Problem statement' : isLikert() ? 'Statement' : 'Question';
    let h = '<div class="mb-3"><label>' + lbl + '</label><textarea class="form-control" id="qd-body" rows="' + (isCode() ? 5 : 3) + '" placeholder="' + (isLikert() ? 'e.g. I stay calm when a deadline moves up.' : isCode() ? 'Describe the problem, constraints and what to print.' : 'Write the question…') + '"></textarea></div>';
    if (isLikert()) h += '<div class="row g-2 mb-2"><div class="col-8"><label>Scale preset</label><select class="form-select" id="qd-preset">' + Object.entries(PRESET_NAMES).map(([k, v]) => '<option value="' + k + '">' + v + '</option>').join('') + '</select></div><div class="col-4 d-flex align-items-end"><label class="tc-switch"><input type="checkbox" id="qd-rev"><span></span><em>Reverse-scored</em></label></div></div>';
    if (!isCode()) h += '<label>' + (isMcq() ? 'Options — select the correct one' : 'Response scale — each option carries a score') + '</label><div id="qd-opts"></div><button type="button" id="qd-addopt" class="btn btn-outline-secondary btn-sm mt-1 mb-3">＋ Add option</button>';
    else h += '<div class="row g-2 mb-3"><div class="col-6"><label>Input format</label><textarea class="form-control" id="qd-inf" rows="2"></textarea></div><div class="col-6"><label>Output format</label><textarea class="form-control" id="qd-outf" rows="2"></textarea></div>' +
      '<div class="col-12"><label>Starter code <span class="tc-unit">shown to candidates (optional)</span></label><textarea class="form-control code" id="qd-sig" rows="3"></textarea></div><div class="col-6"><label>Time limit <span class="tc-unit">ms</span></label><input class="form-control" id="qd-tl" type="number" value="2000" min="100" max="15000"></div><div class="col-6"><label>Memory limit <span class="tc-unit">KB</span></label><input class="form-control" id="qd-ml" type="number" value="128000" min="16000" max="512000"></div></div>' +
      '<label>Test cases <span class="tc-unit" id="qd-casecount"></span></label><div id="qd-cases"></div><button type="button" id="qd-addcase" class="btn btn-outline-secondary btn-sm mt-1 mb-3">＋ Add test case</button>';
    h += '<div class="row g-3"><div class="col-6' + (isLikert() ? ' d-none' : '') + '"><label>Marks</label><input class="form-control" id="qd-marks" type="number" step="any" min="0.1" value="1"></div>' +
      '<div class="col-6"><label>Difficulty</label><div class="tc-seg" id="qd-diff"><button type="button" data-d="easy">Easy</button><button type="button" data-d="medium" class="on">Medium</button><button type="button" data-d="hard">Hard</button></div></div></div>';
    $('#qd-form').html(h);
  }
  function fillPreset(name, reverse) {
    $('#qd-opts').empty(); if (name === 'custom') { [0, 1, 2].forEach(() => $('#qd-opts').append(optRow('', ''))); return; }
    const p = PRESETS[name].map(x => x.slice()); if (reverse) { const sc = p.map(x => x[1]).reverse(); p.forEach((x, i) => { x[1] = sc[i]; }); }
    p.forEach(x => $('#qd-opts').append(optRow(x[0], x[1])));
  }
  function openDrawer(q) {
    editing = q ? q.id : null; buildForm(q);
    $('#qd-title').text((q ? 'Edit ' : 'Add ') + TYPE_LABEL[cur.stage] + ' question'); $('#qd-sub').text(cur.title); $('#qd-more').toggleClass('d-none', !!q); $('#qd-err').text('');
    if (q) {
      $('#qd-body').val(q.body); $('#qd-marks').val(q.marks); $('#qd-diff button').removeClass('on').filter('[data-d=' + q.difficulty + ']').addClass('on');
      if (isCode()) { $('#qd-inf').val(q.input_format || ''); $('#qd-outf').val(q.output_format || ''); $('#qd-sig').val(q.function_signature || ''); $('#qd-tl').val(q.time_limit_ms); $('#qd-ml').val(q.memory_limit_kb); q.test_cases.forEach(c => $('#qd-cases').append(caseRow(c))); }
      else { q.options.forEach((o, i) => $('#qd-opts').append(optRow(o.text, o.score, i === q.correct_option))); if (isLikert()) $('#qd-preset').val('custom'); }
    } else {
      if (isMcq()) [0, 1, 2, 3].forEach(i => $('#qd-opts').append(optRow('', '', i === 0)));
      if (isLikert()) fillPreset('agree5', false);
      if (isCode()) $('#qd-cases').append(caseRow({ is_sample: true }), caseRow({ is_sample: false }), caseRow({ is_sample: false }));
    }
    refreshForm(); drawer.show(); setTimeout(() => $('#qd-body').focus(), 400);
  }
  function collect(strict) {
    const p = { type: TYPE[cur.stage], body: $('#qd-body').val().trim(), difficulty: $('#qd-diff .on').data('d') || 'medium' }, errs = [];
    if (!isLikert()) p.marks = +$('#qd-marks').val() || 1;
    if (!p.body) errs.push('Write the ' + (isLikert() ? 'statement' : 'question') + ' text.');
    if (isCode()) {
      Object.assign(p, { input_format: $('#qd-inf').val() || null, output_format: $('#qd-outf').val() || null, function_signature: $('#qd-sig').val() || null, time_limit_ms: +$('#qd-tl').val(), memory_limit_kb: +$('#qd-ml').val() });
      p.test_cases = $('#qd-cases .case-row').map(function () { return { input: $(this).find('.c-in').val(), expected_output: $(this).find('.c-out').val(), is_sample: $(this).find('.c-vis').val() === '1' }; }).get();
      if (!p.test_cases.some(c => c.is_sample)) errs.push('Add at least one sample test case.'); if (!p.test_cases.some(c => !c.is_sample)) errs.push('Add at least one hidden test case.');
      if (p.test_cases.some(c => !c.expected_output.trim())) errs.push('Every test case needs an expected output.');
    } else {
      p.options = $('#qd-opts .opt-row').map(function () { const o = { text: $(this).find('.opt-text').val().trim() }; if (isLikert()) o.score = parseFloat($(this).find('.opt-score').val()); return o; }).get();
      if (p.options.length < 2) errs.push('Add at least two options.');
      if (p.options.some(o => !o.text)) errs.push('Every option needs text.');
      if (isLikert() && p.options.some(o => isNaN(o.score))) errs.push('Every option needs a numeric score.');
      if (isMcq()) { const i = $('#qd-opts .opt-row').index($('#qd-opts .opt-row').has('input[type=radio]:checked')); if (i < 0) errs.push('Select the correct option.'); else p.correct_option = i;
        const t = p.options.map(o => o.text.toLowerCase()).filter(Boolean); if (new Set(t).size !== t.length) errs.push('Two options have the same text.'); }
    }
    return { p, errs };
  }
  // live preview: renders the candidate's view from whatever is typed so far
  function refreshForm() {
    const { p } = collect(false), L = LETTERS; let h = '';
    if (isCode()) { $('#qd-casecount').text($('#qd-cases .case-row .c-vis').filter((_, e) => e.value === '1').length + ' sample · ' + $('#qd-cases .case-row .c-vis').filter((_, e) => e.value === '0').length + ' hidden');
      h = '<div class="pv-lab">Problem · ' + (p.marks || 1) + ' marks</div><div class="pv-stmt">' + E(p.body || 'Your problem statement appears here.') + '</div>' + (p.input_format ? '<div class="pv-lab">Input</div><div>' + E(p.input_format) + '</div>' : '') + (p.output_format ? '<div class="pv-lab">Output</div><div>' + E(p.output_format) + '</div>' : '') +
        p.test_cases.filter(c => c.is_sample).map((c, i) => '<div class="pv-lab">Sample ' + (i + 1) + '</div><div class="row g-2"><div class="col"><pre class="code-out">' + E(c.input || '') + '</pre></div><div class="col"><pre class="code-out">' + E(c.expected_output || '') + '</pre></div></div>').join('') + '<div class="pv-note">Hidden cases are never shown to candidates.</div>'; }
    else h = '<div class="pv-lab">' + (isLikert() ? 'Statement · choose what fits you' : 'Question · ' + (p.marks || 1) + ' mark(s)') + '</div><div class="pv-stmt">' + E(p.body || (isLikert() ? 'Your statement appears here.' : 'Your question appears here.')) + '</div>' +
      (p.options || []).map((o, i) => '<div class="pv-opt"><i></i><span>' + E(o.text || 'Option ' + (i + 1)) + '</span></div>').join('') + '<div class="pv-note">Options appear in a different order for each candidate.</div>';
    $('#qd-preview').html(h);
  }
  $('#tc-drawer').on('input change', 'input,textarea,select', refreshForm)
    .on('click', '#qd-diff button', function () { $('#qd-diff button').removeClass('on'); $(this).addClass('on'); })
    .on('click', '.rm', function () { $(this).closest('.opt-row,.case-row').remove(); relabel(); refreshForm(); })
    .on('click', '#qd-addopt', () => { if ($('#qd-opts .opt-row').length < (isMcq() ? 8 : 10)) { $('#qd-opts').append(optRow('', '')); refreshForm(); } })
    .on('click', '#qd-addcase', () => { $('#qd-cases').append(caseRow({ is_sample: false })); refreshForm(); })
    .on('change', '#qd-preset', function () { fillPreset(this.value, $('#qd-rev').prop('checked')); refreshForm(); })
    .on('change', '#qd-rev', function () { if ($('#qd-preset').val() !== 'custom') { fillPreset($('#qd-preset').val(), this.checked); refreshForm(); } });
  async function saveQuestion(another) {
    const { p, errs } = collect(true); $('#qd-err').text(errs[0] || ''); if (errs.length) return;
    $('#qd-save,#qd-more').prop('disabled', true);
    try {
      editing ? await Api.put('/admin/tests/' + cur.id + '/questions/' + editing, p) : await Api.post('/admin/tests/' + cur.id + '/questions', p);
      Api.toast(editing ? 'Question updated' : 'Question added', 'success'); await reloadQs();
      if (another && !editing) openDrawer(null); else drawer.hide();
    } catch (e) {}
    $('#qd-save,#qd-more').prop('disabled', false);
  }
  $('#qd-save').on('click', () => saveQuestion(false)); $('#qd-more').on('click', () => saveQuestion(true));
  const closeDrawer = () => { const { p } = collect(false); if (p.body && !editing && !confirm('Discard this question?')) return; drawer.hide(); };
  $('#qd-cancel,#qd-x').on('click', closeDrawer);

  // ---------- import dialog (drag & drop, validated preview, nothing saved until confirmed)
  function configureImport() { const [file, help] = TEMPLATE[cur.stage]; $('#up-tpl').attr('href', '/static/templates/' + file).text('Download template (' + file + ')'); $('#up-help').text(help); }
  function openImport() { configureImport(); $('#up-preview').empty(); $('#up-file').val(''); pendingFile = null; importModal.show(); }
  $('#up-drop').on('dragover', e => { e.preventDefault(); $('#up-drop').addClass('over'); }).on('dragleave drop', () => $('#up-drop').removeClass('over'))
    .on('drop', e => { e.preventDefault(); const f = e.originalEvent.dataTransfer.files[0]; if (f) handleFile(f); });
  $('#up-file').on('change', function () { if (this.files[0]) handleFile(this.files[0]); });
  async function handleFile(f) {
    pendingFile = f; $('#up-preview').html('<span class="text-muted">Checking the file…</span>'); const fd = new FormData(); fd.append('file', f);
    try {
      const r = (await Api.post('/admin/tests/' + cur.id + '/questions/import?dry_run=true', fd, { silent: true })).data;
      $('#up-preview').html('<div class="alert"><b>' + E(f.name) + '</b> is valid: <b>' + r.count + '</b> question' + (r.count === 1 ? '' : 's') + ' will be added.</div><table class="table table-sm"><thead><tr><th>#</th><th>Question</th><th>Marks</th></tr></thead><tbody>' +
        r.preview.slice(0, 8).map(p => '<tr><td>' + p.n + '</td><td>' + E(p.body) + '</td><td>' + p.marks + '</td></tr>').join('') + '</tbody></table>' + (r.count > 8 ? '<div class="small text-muted mb-2">…and ' + (r.count - 8) + ' more</div>' : '') +
        '<button id="up-go" class="btn btn-primary btn-sm">Import ' + r.count + ' question' + (r.count === 1 ? '' : 's') + '</button> <button id="up-no" class="btn btn-link btn-sm">Choose another file</button>');
    } catch (xhr) {
      const er = xhr.responseJSON && xhr.responseJSON.error, det = er && Array.isArray(er.details) ? er.details : [];
      $('#up-preview').html('<div class="alert alert-danger"><b>' + E(er ? er.message : 'Upload failed') + '</b><ul class="mb-0 mt-2">' + det.slice(0, 15).map(d => '<li>' + (d.row ? 'Row ' + d.row + ': ' : '') + E(d.error || d.msg) + '</li>').join('') + '</ul></div>');
    }
  }
  $('#up-preview').on('click', '#up-no', () => { $('#up-preview').empty(); $('#up-file').val(''); pendingFile = null; })
    .on('click', '#up-go', async () => {
      const fd = new FormData(); fd.append('file', pendingFile);
      try { const r = await Api.post('/admin/tests/' + cur.id + '/questions/import', fd); Api.toast('Imported ' + r.data.imported + ' questions', 'success'); importModal.hide(); await reloadQs(); } catch (e) {}
    });

  // =================================================================== 3 · review & publish
  async function loadReview() {
    await refreshSummary(); const s = summary, t = cur;
    const checks = [];
    s.errors.forEach(x => checks.push(['fail', x])); s.warnings.forEach(x => checks.push(['warn', x]));
    if (s.question_count) checks.push(['ok', s.question_count + ' question' + (s.question_count === 1 ? '' : 's') + ' in the bank (' + s.total_marks + ' marks total)']);
    if (!s.errors.some(x => /Cutoff/.test(x)) && s.question_count) checks.push(['ok', 'Cutoff ' + t.cutoff_score + ' is reachable (' + Math.round(100 * t.cutoff_score / (s.per_candidate.min_marks || 1)) + '% of the smallest possible total)']);
    if (t.questions_per_attempt && !s.errors.some(x => /pool/.test(x))) checks.push(['ok', 'Each candidate gets ' + s.per_candidate.questions + ' of ' + s.question_count + ' questions']);
    const icon = { ok: '✓', warn: '!', fail: '✕' };
    $('#rv').html('<div class="tc-verdict ' + (s.errors.length ? 'bad' : 'ok') + '"><b>' + (s.errors.length ? s.errors.length + ' issue' + (s.errors.length === 1 ? '' : 's') + ' to fix before publishing' : t.is_active ? 'This test is live' : 'Ready to publish') + '</b><span>' + (s.errors.length ? 'Open the Questions or Settings step to fix them.' : t.is_active ? 'Assigned candidates can see and take it.' : 'Candidates cannot see it until you publish.') + '</span></div>' +
      '<div class="row g-4"><div class="col-lg-7"><h6>Checks</h6>' + checks.map(c => '<div class="tc-check-row ' + c[0] + '"><span>' + icon[c[0]] + '</span><div>' + E(c[1]) + '</div></div>').join('') + '</div>' +
      '<div class="col-lg-5"><h6>What candidates will see</h6><div class="tc-aside"><ul class="tc-sum"><li><b>' + t.duration_min + ' minutes</b></li><li><b>' + s.per_candidate.questions + ' question' + (s.per_candidate.questions === 1 ? '' : 's') + '</b>, ' + (s.per_candidate.min_marks === s.per_candidate.max_marks ? s.per_candidate.max_marks : s.per_candidate.min_marks + '–' + s.per_candidate.max_marks) + ' marks</li><li>Qualifying score <b>' + t.cutoff_score + '</b></li><li>Auto-submit after <b>' + t.max_violations + '</b> warnings</li><li>Score ' + (t.show_result ? '<b>shown</b>' : '<b>hidden</b>') + ' after submitting</li></ul></div>' +
      '<div class="mt-3"><button id="rv-pub" class="btn ' + (t.is_active ? 'btn-outline-secondary' : 'btn-primary') + '"' + (!t.is_active && !s.ready ? ' disabled' : '') + '>' + (t.is_active ? 'Unpublish' : 'Publish test') + '</button> <a class="btn btn-link" href="/admin/candidates">Assign to candidates →</a></div></div></div>');
  }
  $('#rv').on('click', '#rv-pub', () => $('#b-publish').trigger('click'));

  // =================================================================== boot
  await loadList();
  const m = location.pathname.match(/test-center\/(\d+)(?:\/(settings|questions|review))?/);
  if (m) select(+m[1], m[2]);
});
