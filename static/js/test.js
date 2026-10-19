$(async function () {
  const me = await Api.requireRole('candidate'); if (!me) return;
  const testId = (location.pathname.match(/^\/test\/(\d+)/) || [])[1];
  if (!testId) { location.href = '/dashboard'; return; }
  let attemptId, total = 0, current = 1, remaining = 0, tick, answered = {}, cache = {}, saveTimers = {}, finished = false;
  const LANGS = { python: 'Python 3', java: 'Java', cpp: 'C++', c: 'C', javascript: 'JavaScript' };

  // Rules page: fill real numbers for THIS test; the Start button needs explicit agreement.
  try {
    const info = (await Api.get('/tests')).data.find(t => String(t.test_id) === String(testId));
    if (!info || !['available', 'in_progress'].includes(info.status)) { location.href = '/dashboard'; return; }
    $('#i-test').text(info.title + ' · ' + info.stage);
    $('#s-dur').text(info.duration_min); $('#s-q').text(info.question_count); $('#s-cut').text(info.cutoff_score); $('#s-warn').text(info.max_violations);
    $('#dq-n').text(info.max_violations);
    if (info.status === 'in_progress') $('#begin').text('Resume test');
  } catch (e) { return; }
  // System check: critical failures block the Start button, warnings only inform.
  let sysOk = false;
  const syncStart = () => $('#begin').prop('disabled', !($('#agree').prop('checked') && sysOk));
  async function systemCheck() {
    $('#sc-rows').text('Checking…'); const rows = []; let critical = true;
    const add = (lvl, title, detail) => { rows.push([lvl, title, detail]); if (lvl === 'fail') critical = false; };
    document.fullscreenEnabled ? add('ok', 'Fullscreen is supported', 'The test runs in fullscreen.') : add('fail', 'This browser cannot use fullscreen', 'Use a desktop version of Chrome, Edge or Firefox.');
    let storage = false; try { sessionStorage.setItem('_t', '1'); sessionStorage.removeItem('_t'); storage = navigator.cookieEnabled; } catch (e) {}
    storage ? add('ok', 'Cookies and storage are enabled', 'Needed to keep you signed in.') : add('fail', 'Cookies or storage are blocked', 'Allow cookies for this site (not a private/incognito window with blocking).');
    innerWidth >= 1000 && innerHeight >= 600 ? add('ok', 'Screen size is large enough', innerWidth + ' × ' + innerHeight + ' px') : add('warn', 'Your window is small (' + innerWidth + ' × ' + innerHeight + ' px)', 'Maximise the window or use a larger screen so the editor and questions fit.');
    const ua = navigator.userAgent; /Mobi|Android|iPhone|iPad/i.test(ua) ? add('fail', 'Phones and tablets are not supported', 'Use a laptop or desktop computer.') : (/Chrome|Chromium|Edg|Firefox/i.test(ua) ? add('ok', 'Browser is supported', '') : add('warn', 'Untested browser', 'Chrome, Edge or Firefox are recommended.'));
    try {
      const t0 = performance.now(); const h = await $.getJSON('/api/v1/health'); const ms = Math.round(performance.now() - t0);
      ms < 1500 ? add('ok', 'Connected to the server', ms + ' ms response') : add('warn', 'Connection is slow', ms + ' ms response — move closer to the router or switch networks.');
      const skew = Math.abs(Date.now() - new Date(h.meta.server_time).getTime()) / 60000;
      skew < 2 ? add('ok', 'Computer clock is correct', '') : add('warn', 'Your computer clock is ' + Math.round(skew) + ' min off', 'This does not change your test timer (the server keeps it) but fix it so times shown match.');
    } catch (e) { add('fail', 'Cannot reach the server', 'Check your internet connection and reload the page.'); }
    const ic = { ok: '✓', warn: '!', fail: '✕' };
    $('#sc-rows').html(rows.map(([l, t, d]) => '<div class="sc-row"><span class="sc-ic sc-' + l + '">' + ic[l] + '</span><div><b>' + Api.esc(t) + '</b>' + (d ? '<div class="text-muted">' + Api.esc(d) + '</div>' : '') + '</div></div>').join(''));
    sysOk = critical; syncStart(); return critical;
  }
  $('#sc-again').on('click', systemCheck); systemCheck();
  $('#agree').on('change', syncStart);

  $('#begin').on('click', async function () {
    $(this).prop('disabled', true);
    Proctor.reenterFullscreen();   // must run inside the click: the browser drops the user gesture after a network wait
    let started;
    try { started = await Api.post('/tests/' + testId + '/start'); } catch (e) { location.href = '/dashboard'; return; }
    const d = started.data; attemptId = d.attempt_id;
    // fullscreen needs this click's user gesture
    await Proctor.start({
      attemptId,
      onFullscreenLost: () => { if (!finished) $('#fs-overlay').css('display', 'flex'); },
      onFullscreenBack: () => $('#fs-overlay').hide(),
      onServer: r => { setWarn(r.violation_count, r.max_violations); $('#vcount').text(r.violation_count); if (r.auto_submitted) finish('You exceeded the allowed number of violations.'); },
      onClosed: () => finish(),
    });
    $('#intro').addClass('d-none'); $('#main').removeClass('d-none');
    if (!document.fullscreenElement && !document.webkitFullscreenElement) $('#fs-overlay').css('display', 'flex');  // refused: make the candidate enter it
    applyOverview(d.overview); cache[1] = d.first_question; show(1);
    tick = setInterval(() => { remaining = Math.max(0, remaining - 1); drawTimer(); if (remaining === 0) submit(true); }, 1000);
    setInterval(syncOverview, 30000);  // re-sync with the server clock
  });
  $('#fs-back').on('click', () => Proctor.reenterFullscreen());

  function setWarn(c, m) { $('#warn').text(c); $('#warn-max').text(m); $('#vmax').text(m); }
  function drawTimer() {
    const m = Math.floor(remaining / 60), s = remaining % 60;
    $('#timer').text(String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0')).toggleClass('text-danger', remaining < 60);
  }
  function applyOverview(o) {
    total = o.total; remaining = o.remaining_seconds; $('#stage-name').text(o.stage.toUpperCase());
    setWarn(o.violation_count, o.max_violations); $('#vcount').text(o.violation_count);
    o.questions.forEach(q => { answered[q.n] = q.answered; }); drawTimer(); drawPalette();
  }
  function syncOverview() { if (!finished) Api.get('/attempts/' + attemptId + '/overview', null, { silent: true }).then(r => applyOverview(r.data)).catch(xhr => { if (xhr && xhr.status === 409) finish(); }); }
  function drawPalette() {
    let h = '', done = 0;
    for (let n = 1; n <= total; n++) {
      if (answered[n]) done++;
      h += '<button class="btn btn-sm ' + (n === current ? 'btn-primary' : answered[n] ? 'btn-success' : 'btn-outline-secondary') + '" data-n="' + n + '" title="Question ' + n + '">' + n + '</button>';
    }
    $('#palette').html('<div class="pal-head"><span>Questions</span><b>' + done + ' / ' + total + ' answered</b></div><div class="pal-grid">' + h + '</div>' +
      '<div class="pal-legend"><span><i class="lg lg-cur"></i>Current</span><span><i class="lg lg-ans"></i>Answered</span><span><i class="lg lg-no"></i>Not answered</span></div>');
  }
  $('#palette').on('click', 'button', function () { flushSave(); show(+$(this).data('n')); });
  $('#prev').on('click', () => { flushSave(); if (current > 1) show(current - 1); });
  $('#next').on('click', () => { flushSave(); if (current < total) show(current + 1); });
  $('#finish').on('click', () => { if (confirm('Submit the test now? You cannot change answers afterwards.')) submit(false); });

  async function load(n) {
    if (cache[n]) return cache[n];
    const r = await Api.get('/attempts/' + attemptId + '/question', { n }); cache[n] = r.data; return r.data;
  }
  async function show(n) {
    if (finished) return;
    await Playground.unmount();                       // saves the draft and disposes the editor
    if (cache[n] && cache[n].type === 'coding') delete cache[n];   // drafts change, never serve a stale copy
    const q = await load(n).catch(() => null); if (!q) return;
    $('#main').toggleClass('coding-mode', q.type === 'coding');
    current = n; drawPalette(); $('#prev').prop('disabled', n === 1); $('#next').prop('disabled', n === total);
    q.type === 'coding' ? renderCoding(q) : renderChoice(q);
    if (n < total) load(n + 1).catch(() => {});  // prefetch next
  }

  function renderChoice(q) {
    let h = '<h6 class="text-muted">Question ' + q.n + ' of ' + q.total + ' · ' + (q.type === 'likert' ? 'select the option that fits you best' : q.marks + ' mark(s)') + '</h6><p class="fs-5" style="white-space:pre-wrap">' + Api.esc(q.body) + '</p>';
    q.options.forEach((o, i) => {
      h += '<div class="form-check mb-2"><input class="form-check-input" type="radio" name="opt" id="o' + i + '" value="' + i + '"' + (q.selected === i ? ' checked' : '') + '><label class="form-check-label" for="o' + i + '">' + Api.esc(o) + '</label></div>';
    });
    $('#qbox').html(h + '<button id="clear" class="btn btn-link btn-sm p-0">Clear answer</button>');
    $('input[name=opt]').on('change', function () { queueSave(q, +this.value); });
    $('#clear').on('click', () => { $('input[name=opt]').prop('checked', false); queueSave(q, null); });
  }
  // dirty-check + debounce: only changed answers are sent
  function queueSave(q, sel) {
    q.selected = sel; answered[q.n] = sel !== null; drawPalette();
    clearTimeout(saveTimers[q.question_id]);
    saveTimers[q.question_id] = setTimeout(() => doSave(q, sel), 400);
    saveTimers[q.question_id + 'p'] = [q, sel];
  }
  function doSave(q, sel) { delete saveTimers[q.question_id + 'p']; return Api.put('/attempts/' + attemptId + '/answers/' + q.question_id, { selected: sel }, { silent: true }).catch(xhr => { if (xhr && xhr.status === 409) finish(); else Api.toast('Could not save answer – retrying on next change'); }); }
  function flushSave() { Object.keys(saveTimers).filter(k => String(k).endsWith('p')).forEach(k => { const [q, sel] = saveTimers[k]; clearTimeout(saveTimers[q.question_id]); doSave(q, sel); }); }

  function renderCoding(q) {
    Playground.mount('#qbox', q, {
      attemptId,
      onSubmitted: () => { answered[q.n] = true; drawPalette(); },
      onClosed: () => finish(),
    });
  }

  async function submit(timeout) {
    if (finished) return; flushSave(); await Playground.unmount(); await Proctor.flush();
    try { const r = await Api.post('/attempts/' + attemptId + '/submit', undefined, { silent: true }); finish(timeout ? 'Time is up – your test was submitted automatically.' : null, r.data); }
    catch (e) { finish(); }
  }
  async function finish(msg, data) {
    if (finished) return; finished = true; clearInterval(tick); Proctor.stop(); Playground.unmount();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    $('#fs-overlay').hide(); $('#main,#intro').addClass('d-none'); $('#done').removeClass('d-none');
    try { data = data || (await Api.get('/attempts/' + attemptId + '/result', null, { silent: true })).data; } catch (e) { data = {}; }
    let t = msg ? msg + ' ' : '';
    t += data.score !== undefined ? 'Score: ' + data.score + '/' + data.max_score + ' (cutoff ' + data.cutoff + ') – ' + (data.passed ? 'you cleared this stage.' : 'you did not clear this stage.') : (data.message || 'Your responses were recorded.');
    $('#done-msg').text(t);
  }
});
