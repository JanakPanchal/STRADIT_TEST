$(async function () {
  const me = await Api.requireRole('candidate'); if (!me) return;
  $('#who').text(me.candidate.name);
  if (me.must_change_password) $('#pw-box').removeClass('d-none');
  $('#logout').on('click', Api.logout);
  $('#pw-form').on('submit', e => {
    e.preventDefault();
    Api.post('/auth/change-password', { old_password: $('#old-pw').val(), new_password: $('#new-pw').val() })
      .then(() => { Api.toast('Password changed. Please sign in again.', 'success'); setTimeout(Api.logoutLocal, 1200); }).catch(() => {});
  });
  const badge = { available: 'primary', in_progress: 'warning', passed: 'success', failed: 'danger', locked: 'secondary',
    not_open: 'secondary', closed: 'secondary', disqualified: 'dark' };
  const label = { passed: 'Completed – cleared', failed: 'Completed – not cleared' };
  const r = await Api.get('/tests');
  $('#state').text('Status — ' + r.meta.candidate_state.replaceAll('_', ' '));
  const byStage = {}; r.data.forEach(t => { byStage[t.stage] = t; });
  $('#tracker').html(['aptitude', 'behaviour', 'coding'].map((s, i) => {
    const t = byStage[s]; let cls = 'todo', lab = t ? t.status.replace('_', ' ') : 'not assigned';
    if (t && t.status === 'passed') cls = 'done'; else if (t && (t.status === 'available' || t.status === 'in_progress')) cls = 'now';
    return '<div class="step ' + cls + '"><div class="n">Stage 0' + (i + 1) + '</div><div class="t">' + s + '</div><div class="s">' + Api.esc(lab) + '</div></div>';
  }).join(''));
  // dismissible onboarding card (shown until the candidate closes it) + a plain-language "what to do next"
  let seen = null; try { seen = localStorage.getItem('welcome_seen'); } catch (e) {}
  if (!seen) $('#welcome').removeClass('d-none');
  $('#welcome-x').on('click', () => { $('#welcome').addClass('d-none'); try { localStorage.setItem('welcome_seen', '1'); } catch (e) {} });
  const nextT = r.data.find(t => t.status === 'in_progress') || r.data.find(t => t.status === 'available');
  const state = r.meta.candidate_state;
  let msg = '';
  if (!r.data.length) msg = '<b>Nothing to do yet.</b> No test has been assigned to you. Please contact the administrator.';
  else if (nextT && nextT.status === 'in_progress') msg = '<b>You have a test in progress.</b> <a href="/test/' + nextT.test_id + '">Resume “' + Api.esc(nextT.title) + '”</a> — your timer is still running.';
  else if (nextT) msg = '<b>Your next step:</b> take the <span class="text-capitalize">' + nextT.stage + '</span> test (' + nextT.duration_min + ' min, ' + (nextT.question_count || '') + ' question' + (nextT.question_count === 1 ? '' : 's') + '). Read the rules, then press Start.';
  else if (state === 'FINAL_RESULT') msg = '<b>All done.</b> You have cleared every stage assigned to you. The recruitment team will contact you.';
  else if (state === 'CANDIDATE_REJECTED') msg = '<b>This round is complete.</b> You did not reach the qualifying score for one of the stages, so you are not eligible for later stages. Thank you for taking part.';
  else if (state === 'DISQUALIFIED') msg = '<b>Your assessment has ended.</b> Please contact the administrator if you have questions.';
  else if (r.data.some(t => ['locked', 'not_open', 'closed'].includes(t.status))) msg = '<b>Nothing to start right now.</b> Your next stage is locked or not open yet — see the note under each test below.';
  if (msg) $('#next').html('<div class="alert">' + msg + '</div>');
  if (!r.data.length) $('#tests').html('<div class="text-muted">No tests assigned yet. Please contact the administrator.</div>');
  r.data.forEach(t => {
    const live = t.status === 'available' || t.status === 'in_progress';
    const locked = ['locked', 'not_open', 'closed'].includes(t.status);
    const act = live ? '<a class="btn btn-primary" href="/test/' + t.test_id + '">' + (t.status === 'available' ? 'Start' : 'Resume') + '</a>' : '';
    $('#tests').append('<div class="test-row ' + (live ? 'live' : locked ? 'locked' : '') + '"><div><h5 class="mb-1">' + Api.esc(t.title) + '</h5>' +
      '<span class="badge me-2 text-bg-' + badge[t.status] + '">' + Api.esc(label[t.status] || t.status.replace('_', ' ')) + '</span>' +
      '<small class="text-muted text-capitalize">' + t.stage + ' · ' + t.duration_min + ' min</small>' +
      (t.reason ? '<div class="small text-muted mt-1">' + Api.esc(t.reason) + '</div>' : '') + '</div><div>' + act + '</div></div>');
  });
});
