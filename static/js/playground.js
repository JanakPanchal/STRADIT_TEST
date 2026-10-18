// Coding playground: problem + editor + console for one coding question.
//  Editor      Monaco (served locally, works offline in a lab) with a textarea fallback; settings (font, theme, tab size,
//              wrap, minimap, line numbers); status bar; resizable + collapsible panes; focus mode
//  Autosave    debounce + max-wait to the server, local browser backup on every change, offline detection + retry with
//              backoff, save on tab hide/close, recovery of unsynced local changes, local code history (restore points)
//  Run         sample cases as chips with pass/fail dots and an expected-vs-actual line diff; several custom cases
//  Submit      judged on all (hidden) cases; submissions list with "load this code back into the editor"
//  Extras      notes tab (private, local), "copy sample into a custom case", keyboard shortcuts, quick help
//  Policy      copying inside the editor is allowed; pasting from outside / dropping text is blocked and logged
const Playground = (function ($) {
  const LANGS = {
    python: { label: 'Python 3', monaco: 'python' }, java: { label: 'Java', monaco: 'java' }, cpp: { label: 'C++', monaco: 'cpp' },
    c: { label: 'C', monaco: 'c' }, javascript: { label: 'JavaScript (Node)', monaco: 'javascript' },
  };
  const TEMPLATES = {
    python: 'def main():\n    # read input with input(), print the answer with print()\n    line = input()\n    print(line)\n\n\nif __name__ == "__main__":\n    main()\n',
    java: 'import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n        Scanner in = new Scanner(System.in);\n        // read input from stdin, print the answer to stdout\n        System.out.println(in.hasNextLine() ? in.nextLine() : "");\n    }\n}\n',
    cpp: '#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios::sync_with_stdio(false);\n    cin.tie(nullptr);\n    // read input from cin, print the answer to cout\n    return 0;\n}\n',
    c: '#include <stdio.h>\n\nint main(void) {\n    /* read input with scanf, print the answer with printf */\n    return 0;\n}\n',
    javascript: "const lines = require('fs').readFileSync(0, 'utf8').split('\\n');\n// read input from lines[], print the answer with console.log\nconsole.log(lines[0]);\n",
  };
  const COMMENT = { python: '# ', java: '// ', cpp: '// ', c: '// ', javascript: '// ' };
  const DEFAULTS = { size: 14, dark: false, tab: 4, wrap: false, minimap: false, lines: true };
  const MAX_CUSTOM = 5, MAX_HISTORY = 12;
  const E = s => Api.esc(s);
  let S = null, monacoPromise = null;

  // ------------------------------------------------------------------ storage (every access guarded: it can throw)
  const ls = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); return true; } catch (e) { return false; } },
    json(k, d) { try { return JSON.parse(localStorage.getItem(k)) || d; } catch (e) { return d; } },
    put(k, o) { return ls.set(k, JSON.stringify(o)); },
  };
  const key = (suffix) => 'pg:' + S.ctx.attemptId + ':' + S.q.question_id + ':' + suffix;
  const settings = () => Object.assign({}, DEFAULTS, ls.json('pg:settings', {}));

  function loadMonaco() {
    if (monacoPromise) return monacoPromise;
    monacoPromise = new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('timeout')), 10000);
      const s = document.createElement('script');
      s.src = '/static/vendor/monaco/vs/loader.js';
      s.onerror = () => { clearTimeout(t); reject(new Error('load')); };
      s.onload = () => {
        window.require.config({ paths: { vs: '/static/vendor/monaco/vs' } });
        window.require(['vs/editor/editor.main'], () => { clearTimeout(t); resolve(window.monaco); }, e => { clearTimeout(t); reject(e); });
      };
      document.head.appendChild(s);
    });
    return monacoPromise;
  }

  function starter(lang, q) {
    let t = TEMPLATES[lang];
    if (q.function_signature) {  // admin's starter code is written for one language: verbatim for Python, a comment elsewhere
      t = lang === 'python' ? q.function_signature + '\n'
        : q.function_signature.split('\n').map(l => COMMENT[lang] + l).join('\n') + '\n\n' + t;
    }
    return t;
  }

  // ------------------------------------------------------------------ editor adapters (Monaco / textarea) — one interface
  function monacoEditor(monaco, el, value, lang, st) {
    const ed = monaco.editor.create(el, {
      value, language: LANGS[lang].monaco, theme: st.dark ? 'vs-dark' : 'vs', fontSize: st.size, tabSize: st.tab, insertSpaces: true,
      wordWrap: st.wrap ? 'on' : 'off', minimap: { enabled: st.minimap }, lineNumbers: st.lines ? 'on' : 'off',
      automaticLayout: true, scrollBeyondLastLine: false, renderLineHighlight: 'line', lineNumbersMinChars: 3, contextmenu: false,
      quickSuggestions: false, suggestOnTriggerCharacters: false, parameterHints: { enabled: false },
      bracketPairColorization: { enabled: true }, autoClosingBrackets: 'always', padding: { top: 10 },
      dropIntoEditor: { enabled: false }, dragAndDrop: false,
    });
    return {
      kind: 'monaco', get: () => ed.getValue(), set: v => ed.setValue(v), focus: () => ed.focus(),
      onChange: fn => ed.onDidChangeModelContent(fn), onCursor: fn => ed.onDidChangeCursorPosition(e => fn(e.position.lineNumber, e.position.column)),
      setLang: l => monaco.editor.setModelLanguage(ed.getModel(), LANGS[l].monaco),
      apply: o => { ed.updateOptions({ fontSize: o.size, tabSize: o.tab, wordWrap: o.wrap ? 'on' : 'off', minimap: { enabled: o.minimap }, lineNumbers: o.lines ? 'on' : 'off' }); ed.getModel().updateOptions({ tabSize: o.tab }); monaco.editor.setTheme(o.dark ? 'vs-dark' : 'vs'); },
      selection: () => ed.getModel().getValueInRange(ed.getSelection()), currentLine: () => ed.getModel().getLineContent(ed.getPosition().lineNumber),
      shortcut: (run, submit, save) => {
        ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, run);
        ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.Enter, submit);
        ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, save);
      },
      dispose: () => ed.dispose(),
    };
  }
  function textareaEditor(el, value, st) {
    const $t = $('<textarea class="form-control code pg-fallback" spellcheck="false" style="height:100%;resize:none"></textarea>').val(value).appendTo(el);
    $t.on('keydown', function (e) {
      if (e.key === 'Tab') { e.preventDefault(); const p = this.selectionStart; this.value = this.value.slice(0, p) + '    ' + this.value.slice(this.selectionEnd); this.selectionStart = this.selectionEnd = p + 4; $t.trigger('input'); }
    });
    return {
      kind: 'textarea', get: () => $t.val(), set: v => $t.val(v), focus: () => $t.trigger('focus'), onChange: fn => $t.on('input', fn),
      onCursor: fn => $t.on('keyup click', () => { const t = $t[0], b = t.value.slice(0, t.selectionStart).split('\n'); fn(b.length, b[b.length - 1].length + 1); }),
      setLang: () => {}, apply: o => $t.css({ 'font-size': o.size + 'px', 'white-space': o.wrap ? 'pre-wrap' : 'pre' }),
      selection: () => { const t = $t[0]; return t.value.slice(t.selectionStart, t.selectionEnd); }, currentLine: () => '',
      shortcut: (run, submit, save) => $t.on('keydown', e => { if (e.ctrlKey || e.metaKey) { if (e.key === 'Enter') { e.preventDefault(); e.shiftKey ? submit() : run(); } if (e.key.toLowerCase() === 's') { e.preventDefault(); save(); } } }),
      dispose: () => $t.remove(),
    };
  }

  // ------------------------------------------------------------------ rendering helpers
  const fmtMs = v => v == null ? '–' : v + ' ms';
  const fmtMem = v => v == null ? '–' : (v >= 1024 ? (v / 1024).toFixed(1) + ' MB' : v + ' KB');
  const verdictClass = st => /accepted|passed/i.test(st) ? 'ok' : /wrong|fail/i.test(st) ? 'bad' : 'err';
  const clock = d => (d ? new Date(d) : new Date()).toLocaleTimeString();
  const trimEnd = s => s.replace(/\s+$/, '');

  // Line-by-line expected vs actual: marks the lines that differ so "why Wrong Answer" is obvious.
  function diffActual(expected, actual) {
    const e = trimEnd((expected || '').replace(/\r/g, '')).split('\n'), a = trimEnd((actual || '').replace(/\r/g, '')).split('\n');
    const n = Math.max(e.length, a.length); let html = '', firstBad = -1;
    for (let i = 0; i < n; i++) {
      const bad = e[i] === undefined || a[i] === undefined || trimEnd(e[i]) !== trimEnd(a[i]);
      if (bad && firstBad < 0) firstBad = i + 1;
      html += a[i] === undefined ? '<div class="pg-dl bad pg-miss">(missing line ' + (i + 1) + ')</div>' : '<div class="pg-dl' + (bad ? ' bad' : '') + '">' + (E(a[i]) || '&nbsp;') + '</div>';
    }
    return { html, firstBad };
  }
  const pre = (txt, cls) => '<pre class="code-out ' + (cls || '') + '">' + (txt === '' || txt == null ? '<span class="text-muted">(empty)</span>' : E(txt)) + '</pre>';

  // ------------------------------------------------------------------ mount
  async function mount(sel, q, ctx) {
    await unmount();
    const st = settings(), $root = $(sel);
    S = { q, ctx, st, lang: q.last_language && LANGS[q.last_language] ? q.last_language : 'python', codes: Object.assign({}, q.drafts || {}),
      dirty: false, saving: false, lastSaved: null, retry: 0, timer: null, maxTimer: null, editor: null, busy: false, dead: false,
      cases: (q.samples || []).map((s, i) => ({ kind: 'sample', name: 'Sample ' + (i + 1), input: s.input, expected: s.expected_output, res: null })),
      sel: 0, lastSubmitted: {}, bakTimer: null, histTimer: null, listeners: [] };
    ls.json(key('cases'), []).slice(0, MAX_CUSTOM).forEach((c, i) => S.cases.push({ kind: 'custom', name: 'Custom ' + (i + 1), input: c.input || '', res: null }));
    const langOpts = Object.entries(LANGS).map(([k, v]) => '<option value="' + k + '">' + v.label + '</option>').join('');
    $root.html(
      '<div class="pg ' + (st.dark ? 'pg-dark' : '') + '" id="pg">' +
      // ---- left: problem / notes / submissions
      '<div class="pg-left"><ul class="pg-tabs"><li class="on" data-l="prob">Problem</li><li data-l="notes">Notes</li><li data-l="subs">Submissions <span id="pg-subn" class="badge text-bg-secondary">0</span></li></ul>' +
      '<div id="pg-prob" class="pg-pane"><div class="pg-lab">Problem ' + q.n + ' of ' + q.total + ' · ' + q.marks + ' marks</div><div class="pg-statement">' + E(q.body) + '</div>' +
      (q.input_format ? '<div class="pg-lab mt-3">Input format</div><div>' + E(q.input_format) + '</div>' : '') + (q.output_format ? '<div class="pg-lab mt-3">Output format</div><div>' + E(q.output_format) + '</div>' : '') +
      '<div class="pg-lab mt-3">Limits</div><div>' + q.time_limit_ms + ' ms · ' + Math.round(q.memory_limit_kb / 1024) + ' MB · standard input → standard output</div>' +
      '<div class="pg-lab mt-3">Examples</div>' + q.samples.map((x, i) => '<div class="pg-sample"><div class="d-flex justify-content-between"><b class="small">Sample ' + (i + 1) + '</b><a href="#" class="small" data-tocustom="' + i + '">Try as custom input</a></div><div class="row g-2"><div class="col"><div class="pg-lab">Input</div>' + pre(x.input) + '</div><div class="col"><div class="pg-lab">Output</div>' + pre(x.expected_output) + '</div></div></div>').join('') + '</div>' +
      '<div id="pg-notes" class="pg-pane d-none"><div class="pg-lab">Private notes — stay in this browser, never graded</div><textarea id="pg-notes-t" class="form-control" rows="14" placeholder="Plan your approach, list edge cases, jot complexity…"></textarea></div>' +
      '<div id="pg-subs" class="pg-pane d-none"><div id="pg-sublist" class="text-muted">No submissions yet.</div></div></div>' +
      '<div class="pg-vsplit" id="pg-vs" title="Drag to resize · double-click to reset"></div>' +
      // ---- right: toolbar / editor / status / console
      '<div class="pg-right"><div class="pg-toolbar"><button class="btn btn-outline-secondary btn-sm" id="pg-focus" title="Hide the problem panel">⇤</button>' +
      '<select id="pg-lang" class="form-select form-select-sm pg-sel">' + langOpts + '</select>' +
      '<div class="pg-dd"><button class="btn btn-outline-secondary btn-sm" id="pg-set" title="Editor settings">⚙ Settings</button><div class="pg-menu d-none" id="pg-setm">' +
      '<label>Text size <b id="pg-sz">' + st.size + '</b>px</label><input type="range" id="pg-size" min="11" max="24" value="' + st.size + '" class="form-range">' +
      '<label>Theme</label><div class="btn-group btn-group-sm w-100 mb-2"><button class="btn btn-outline-secondary" data-th="0">Light</button><button class="btn btn-outline-secondary" data-th="1">Dark</button></div>' +
      '<label>Tab size</label><select id="pg-tab" class="form-select form-select-sm mb-2"><option>2</option><option>4</option><option>8</option></select>' +
      '<div class="form-check"><input class="form-check-input" type="checkbox" id="pg-wrap"><label class="form-check-label" for="pg-wrap">Wrap long lines</label></div>' +
      '<div class="form-check"><input class="form-check-input" type="checkbox" id="pg-lines"><label class="form-check-label" for="pg-lines">Line numbers</label></div>' +
      '<div class="form-check"><input class="form-check-input" type="checkbox" id="pg-mini"><label class="form-check-label" for="pg-mini">Minimap</label></div></div></div>' +
      '<div class="pg-dd"><button class="btn btn-outline-secondary btn-sm" id="pg-hist" title="Restore an earlier version of your code">↶ History</button><div class="pg-menu pg-menu-w d-none" id="pg-histm"></div></div>' +
      '<button class="btn btn-outline-secondary btn-sm" id="pg-reset" title="Replace with the starter template">Reset</button>' +
      '<button class="btn btn-outline-secondary btn-sm" id="pg-help" title="Shortcuts and tips">? Help</button>' +
      '<span class="pg-save" id="pg-save" title="Your code is saved automatically"><i></i><span>Ready</span></span><span class="flex-grow-1"></span>' +
      '<button class="btn btn-outline-primary btn-sm" id="pg-run" title="Ctrl/Cmd + Enter">▶ Run samples</button><button class="btn btn-primary btn-sm" id="pg-submit" title="Ctrl/Cmd + Shift + Enter">Submit</button></div>' +
      '<div id="pg-editor" class="pg-editor"></div>' +
      '<div class="pg-statusbar"><span id="pg-pos">Ln 1, Col 1</span><span>Spaces: <b id="pg-tabv">' + st.tab + '</b></span><span id="pg-langv">' + LANGS[S.lang].label + '</span><span class="flex-grow-1"></span><span id="pg-net"></span><span id="pg-lastsave" class="text-muted"></span></div>' +
      '<div class="pg-hsplit" id="pg-hs" title="Drag to resize · double-click to reset"></div>' +
      '<div class="pg-console" id="pg-console"><ul class="pg-tabs sm"><li class="on" data-c="cases">Test cases</li><li data-c="result">Result</li></ul>' +
      '<div class="pg-cpane" id="pgc-cases"></div>' +
      '<div class="pg-cpane d-none" id="pgc-result"><span class="text-muted">Press <b>Submit</b> to be judged against all test cases, including hidden ones. Your best submission counts.</span></div></div></div></div>');
    $('#pg-lang').val(S.lang); $('#pg-tab').val(String(st.tab)); $('#pg-wrap').prop('checked', st.wrap); $('#pg-lines').prop('checked', st.lines); $('#pg-mini').prop('checked', st.minimap);
    $('[data-th=' + (st.dark ? 1 : 0) + ']').addClass('active'); $('#pg-notes-t').val(ls.get(key('notes')) || '');
    restoreLayout(); renderCases(); updNet();

    // -------- initial code: newest of (server draft, unsynced local backup), else the template
    const startVal = pickInitial(S.lang);
    try {
      const monaco = await loadMonaco(); if (S.dead) return;
      S.editor = monacoEditor(monaco, document.getElementById('pg-editor'), startVal, S.lang, st);
    } catch (e) {
      Api.toast('Advanced editor unavailable — using the basic editor', 'warning');
      S.editor = textareaEditor(document.getElementById('pg-editor'), startVal, st); S.editor.apply(st);
    }
    S.codes[S.lang] = S.editor.get();
    S.editor.onChange(() => { S.codes[S.lang] = S.editor.get(); queueSave(); });
    S.editor.onCursor((l, c) => $('#pg-pos').text('Ln ' + l + ', Col ' + c));
    S.editor.shortcut(() => run(), () => submit(), () => saveNow(true));
    bindPolicy(); bindUi(); setSave(S.dirty ? 'unsaved' : 'saved'); S.editor.focus(); loadSubs();
    if (S.dirty) queueSave();   // recovered local changes go to the server straight away
    // periodic safety nets: max-wait flush while dirty, and a local restore point every 2 minutes if the code changed
    S.maxTimer = setInterval(() => { if (S.dirty && !S.saving && Date.now() - (S.lastAttempt || 0) > 10000) flush(); }, 5000);
    S.histTimer = setInterval(() => snapshot(false), 120000);
  }

  // The server's draft is the source of truth, EXCEPT when this browser holds newer changes that never reached the server
  // (offline, crash, closed tab): those are recovered so nothing typed is ever lost.
  function pickInitial(lang) {
    const server = S.codes[lang], local = ls.json(key('code:' + lang), null);
    if (local && local.synced === false && local.code === server) ls.put(key('code:' + lang), { code: server, t: Date.now(), synced: true });  // server already has it
    if (local && local.code !== undefined && local.synced === false && local.code !== server) {
      S.codes[lang] = local.code; S.dirty = true; S.recovered = true;
      setTimeout(() => Api.toast('Recovered unsaved changes from this browser (' + clock(local.t) + ')', 'success'), 400);
      return local.code;
    }
    return server !== undefined ? server : starter(lang, S.q);
  }

  // ------------------------------------------------------------------ autosave
  function setSave(state, extra) {
    S.saveState = state; const $s = $('#pg-save').attr('class', 'pg-save ' + state);
    const t = { saved: S.lastSaved ? 'Saved ' + clock(S.lastSaved) : 'All changes saved', saving: 'Saving…', unsaved: 'Unsaved changes…', offline: 'Offline — kept in this browser', error: 'Save failed — retrying' }[state] || state;
    $s.find('span').text(extra || t);
    $('#pg-lastsave').text(S.lastSaved ? 'Last saved ' + clock(S.lastSaved) : '');
  }
  function backup(synced) {
    if (!S || !S.editor) return;
    ls.put(key('code:' + S.lang), { code: S.codes[S.lang], t: Date.now(), synced: !!synced });
  }
  function queueSave() {
    if (!S) return; S.dirty = true; setSave(navigator.onLine ? 'unsaved' : 'offline');
    clearTimeout(S.bakTimer); S.bakTimer = setTimeout(() => backup(false), 300);   // local copy almost immediately
    clearTimeout(S.timer); S.timer = setTimeout(flush, 1200);                      // server copy after a short pause
  }
  function saveNow(manual) { if (!S) return; if (S.editor) S.codes[S.lang] = S.editor.get(); S.dirty = true; backup(false); return flush(manual); }
  function flush(manual) {
    if (!S || S.dead) return Promise.resolve();
    if (!S.dirty && !manual) return Promise.resolve();
    if (S.saving) { S.again = true; return Promise.resolve(); }
    clearTimeout(S.timer); S.saving = true; S.lastAttempt = Date.now(); setSave('saving');
    const lang = S.lang, code = S.editor ? S.editor.get() : (S.codes[lang] || '');
    return Api.put('/attempts/' + S.ctx.attemptId + '/code/draft', { question_id: S.q.question_id, language: lang, source_code: code }, { silent: true })
      .then(() => {
        if (!S) return; S.saving = false; S.retry = 0; S.lastSaved = new Date();
        const same = S.editor ? S.editor.get() === code : true;
        if (same) { S.dirty = false; ls.put(key('code:' + lang), { code, t: Date.now(), synced: true }); setSave('saved'); } else { setSave('unsaved'); queueSave(); }
        if (S.again) { S.again = false; flush(); }
      })
      .catch(xhr => {
        if (!S) return; S.saving = false;
        if (xhr && xhr.status === 409) { S.ctx.onClosed && S.ctx.onClosed(); return; }   // attempt already closed
        const offline = !navigator.onLine || !xhr || xhr.status === 0;
        setSave(offline ? 'offline' : 'error'); S.dirty = true; backup(false);
        S.retry = Math.min(S.retry + 1, 5); S.timer = setTimeout(flush, Math.min(3000 * 2 ** (S.retry - 1), 20000));   // 3s, 6s, 12s … 20s
      });
  }
  const updNet = () => { $('#pg-net').text(navigator.onLine ? '' : '● Offline'); };
  function onOnline() { updNet(); if (S && S.dirty) flush(); }
  function onOffline() { updNet(); if (S && S.dirty) setSave('offline'); }
  function onHide() {   // tab hidden / closing: send the latest code with keepalive so it survives the page going away
    if (!S || !S.dirty || !S.editor) return; backup(false);
    Api.keepalive('PUT', '/attempts/' + S.ctx.attemptId + '/code/draft', { question_id: S.q.question_id, language: S.lang, source_code: S.editor.get() });
  }
  function onVis() { if (document.hidden) onHide(); }

  // ------------------------------------------------------------------ restore points (local code history)
  function snapshot(force, label) {
    if (!S || !S.editor) return; const code = S.editor.get(), hist = ls.json(key('hist'), []);
    if (hist.length && hist[0].code === code && hist[0].lang === S.lang) return;
    hist.unshift({ t: Date.now(), lang: S.lang, code, label: label || 'Autosnapshot' });
    ls.put(key('hist'), hist.slice(0, MAX_HISTORY));
  }
  function renderHistory() {
    const hist = ls.json(key('hist'), []);
    $('#pg-histm').html(hist.length ? '<div class="pg-lab px-2 pt-1">Restore an earlier version</div>' + hist.map((h, i) => '<a href="#" data-h="' + i + '"><b>' + clock(h.t) + '</b> · ' + (LANGS[h.lang] || {}).label + ' · ' + E(h.label) + '<div class="text-muted small">' + E(h.code.split('\n').find(l => l.trim()) || '').slice(0, 60) + '</div></a>').join('') : '<div class="p-2 small text-muted">No restore points yet. One is saved each time you Run or Submit, and every couple of minutes while you work.</div>');
  }
  function restore(i) {
    const h = ls.json(key('hist'), [])[i]; if (!h) return;
    if (!confirm('Replace your current code with the version from ' + clock(h.t) + '? Your current code is kept in History.')) return;
    snapshot(true, 'Before restore');
    if (h.lang !== S.lang) { S.lang = h.lang; $('#pg-lang').val(h.lang); S.editor.setLang(h.lang); $('#pg-langv').text(LANGS[h.lang].label); }
    S.editor.set(h.code); S.codes[S.lang] = h.code; queueSave();
  }

  // ------------------------------------------------------------------ test cases (chips + detail + diff)
  function renderCases() {
    const c = S.cases, $p = $('#pgc-cases');
    const chips = c.map((x, i) => {
      const st = x.res ? (x.kind === 'sample' ? (x.res.passed ? 'ok' : 'bad') : (x.res.ok ? 'ok' : 'bad')) : 'idle';
      return '<button class="pg-chip ' + st + (i === S.sel ? ' on' : '') + '" data-i="' + i + '"><i></i>' + E(x.name) + '</button>';
    }).join('') + (c.filter(x => x.kind === 'custom').length < MAX_CUSTOM ? '<button class="pg-chip add" id="pg-addcase" title="Add a custom test case">＋ Custom</button>' : '');
    const cur = c[S.sel] || c[0]; let detail = '';
    if (!cur) detail = '<span class="text-muted">No sample cases.</span>';
    else if (cur.kind === 'sample') {
      const r = cur.res, d = r ? diffActual(cur.expected, r.stdout) : null;
      detail = '<div class="row g-2"><div class="col-md-4"><div class="pg-lab">Input</div>' + pre(cur.input) + '</div><div class="col-md-4"><div class="pg-lab">Expected output</div>' + pre(cur.expected) + '</div><div class="col-md-4"><div class="pg-lab">Your output</div>' +
        (r ? '<pre class="code-out pg-actual ' + (r.passed ? '' : 'pg-bad') + '">' + ((r.stdout === '' || r.stdout == null) ? '<span class="text-muted">(empty)</span>' : d.html) + '</pre>' : '<pre class="code-out"><span class="text-muted">Run to see your output</span></pre>') + '</div></div>' +
        (r ? '<div class="small mt-2"><span class="badge text-bg-' + (r.passed ? 'success' : 'danger') + '">' + (r.passed ? 'Passed' : E(r.status)) + '</span> ' + fmtMs(r.time_ms) + ' · ' + fmtMem(r.memory_kb) + (!r.passed && d.firstBad > 0 && r.stdout ? ' · <b>first difference on line ' + d.firstBad + '</b>' : '') + '</div>' +
          (r.stderr ? '<div class="pg-lab mt-2">Errors / compiler output</div><pre class="code-out pg-err">' + E(r.stderr) + '</pre>' : '') : '') +
        '<div class="mt-2"><a href="#" class="small" data-tocustom="' + S.sel + '">Copy this input into a new custom case</a></div>';
    } else {
      const r = cur.res;
      detail = '<div class="row g-2"><div class="col-md-6"><div class="pg-lab">Your input</div><textarea id="pg-cin" class="form-control code" rows="4" spellcheck="false"></textarea>' +
        '<div class="mt-2"><button class="btn btn-outline-primary btn-sm" id="pg-runc">▶ Run this input</button> <button class="btn btn-link btn-sm text-danger" id="pg-delc">Delete case</button></div></div>' +
        '<div class="col-md-6"><div class="pg-lab">Output</div><pre class="code-out" style="min-height:5.5rem">' + (r ? ((r.stdout ? E(r.stdout) : '<span class="text-muted">(no output)</span>') + (r.stderr ? '\n<span class="pg-err-t">' + E(r.stderr) + '</span>' : '')) : '<span class="text-muted">Output appears here.</span>') + '</pre>' +
        (r ? '<div class="small"><span class="badge text-bg-' + (r.ok ? 'success' : 'danger') + '">' + E(r.status) + '</span> ' + fmtMs(r.time_ms) + ' · ' + fmtMem(r.memory_kb) + '</div>' : '') + '</div></div>';
    }
    const samples = c.filter(x => x.kind === 'sample'), ran = samples.filter(x => x.res);
    const sum = ran.length ? '<div class="pg-summary ' + (ran.every(x => x.res.passed) && ran.length === samples.length ? 'ok' : 'bad') + '">' + ran.filter(x => x.res.passed).length + ' / ' + samples.length + ' sample cases passed</div>' : '';
    $p.html((!ls.get('pg_tip_seen') ? '<div class="pg-tip">New here? <b>Run samples</b> checks the examples, <b>Submit</b> scores all hidden tests. Your code autosaves. Click <b>? Help</b> for shortcuts. <a href="#" id="pg-tip-x">Got it</a></div>' : '') + sum + '<div class="pg-chips">' + chips + '</div><div class="pg-detail">' + detail + '</div>');
    if (cur && cur.kind === 'custom') $('#pg-cin').val(cur.input);
  }
  const saveCustom = () => ls.put(key('cases'), S.cases.filter(x => x.kind === 'custom').map(x => ({ input: x.input })));
  function addCustom(input) {
    if (S.cases.filter(x => x.kind === 'custom').length >= MAX_CUSTOM) { Api.toast('You can keep up to ' + MAX_CUSTOM + ' custom cases'); return; }
    S.cases.push({ kind: 'custom', name: 'Custom ' + (S.cases.filter(x => x.kind === 'custom').length + 1), input: input || '', res: null });
    S.sel = S.cases.length - 1; saveCustom(); showConsole('cases'); renderCases();
  }

  // ------------------------------------------------------------------ copy/paste policy (window capture: runs before Monaco)
  function bindPolicy() {
    const host = document.getElementById('pg-editor'), allowed = new Set(), inEditor = e => host.contains(e.target);
    const onClip = function (e) {
      if (!inEditor(e)) return;
      if (e.type === 'copy' || e.type === 'cut') {
        const sel = S.editor.selection(), line = S.editor.currentLine ? S.editor.currentLine() : '';
        [sel, line, line + '\n', line + '\r\n'].forEach(t => t && allowed.add(t)); e._pgInternal = true; return;
      }
      const text = (e.clipboardData && e.clipboardData.getData('text/plain')) || '';
      if (text && allowed.has(text)) { e._pgInternal = true; return; }
      e.preventDefault(); e.stopImmediatePropagation();
      Proctor.report('copy_paste', { kind: 'paste_external', chars: text.length });
      Api.toast('Pasting from outside the editor is not allowed and has been logged.', 'warning');
    };
    const onDrop = e => { if (!inEditor(e)) return; e.preventDefault(); e.stopImmediatePropagation(); Proctor.report('copy_paste', { kind: 'drop_external' }); };
    ['copy', 'cut', 'paste'].forEach(t => window.addEventListener(t, onClip, true)); window.addEventListener('drop', onDrop, true);
    S.listeners.push(() => { ['copy', 'cut', 'paste'].forEach(t => window.removeEventListener(t, onClip, true)); window.removeEventListener('drop', onDrop, true); });
    Proctor.setClipFilter(e => !!((e.originalEvent || e)._pgInternal));
    Proctor.setKeyFilter && Proctor.setKeyFilter(e => (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's' && inEditor(e));   // Ctrl+S = "save here", not a violation
  }

  // ------------------------------------------------------------------ UI wiring
  function applySettings() { S.editor.apply(S.st); $('#pg').toggleClass('pg-dark', S.st.dark); $('#pg-tabv').text(S.st.tab); $('#pg-sz').text(S.st.size); ls.put('pg:settings', S.st); }
  function bindUi() {
    const closeMenus = () => $('.pg-menu').addClass('d-none');
    const onDoc = e => { if (!$(e.target).closest('.pg-dd').length) closeMenus(); };
    document.addEventListener('click', onDoc); S.listeners.push(() => document.removeEventListener('click', onDoc));
    $('#pg-set').on('click', () => { const open = !$('#pg-setm').hasClass('d-none'); closeMenus(); if (!open) $('#pg-setm').removeClass('d-none'); });
    $('#pg-hist').on('click', () => { const open = !$('#pg-histm').hasClass('d-none'); closeMenus(); if (!open) { renderHistory(); $('#pg-histm').removeClass('d-none'); } });
    $('#pg-histm').on('click', 'a[data-h]', function (e) { e.preventDefault(); closeMenus(); restore(+$(this).data('h')); });
    $('#pg-size').on('input', function () { S.st.size = +this.value; applySettings(); });
    $('#pg-setm [data-th]').on('click', function () { S.st.dark = $(this).data('th') === 1; $('#pg-setm [data-th]').removeClass('active'); $(this).addClass('active'); applySettings(); });
    $('#pg-tab').on('change', function () { S.st.tab = +this.value; applySettings(); });
    $('#pg-wrap').on('change', function () { S.st.wrap = this.checked; applySettings(); });
    $('#pg-lines').on('change', function () { S.st.lines = this.checked; applySettings(); });
    $('#pg-mini').on('change', function () { S.st.minimap = this.checked; applySettings(); });

    $('.pg-left .pg-tabs li').on('click', function () { $('.pg-left .pg-tabs li').removeClass('on'); $(this).addClass('on'); ['prob', 'notes', 'subs'].forEach(n => $('#pg-' + n).toggleClass('d-none', n !== $(this).data('l'))); });
    $('.pg-console .pg-tabs li').on('click', function () { showConsole($(this).data('c')); });
    $('#pg-notes-t').on('input', function () { ls.set(key('notes'), this.value); });
    $('#pg-focus').on('click', toggleFocus);

    $('#pg-lang').on('change', function () {
      flush(true); snapshot(false, 'Before switching language'); S.lang = this.value; S.editor.setLang(S.lang); $('#pg-langv').text(LANGS[S.lang].label);
      S.editor.set(pickInitial(S.lang)); S.codes[S.lang] = S.editor.get(); S.dirty = false; setSave('saved'); S.editor.focus();
    });
    $('#pg-reset').on('click', function () { if (!confirm('Replace your code with the starter template for ' + LANGS[S.lang].label + '? The current code is kept in History.')) return; snapshot(true, 'Before reset'); S.editor.set(starter(S.lang, S.q)); S.codes[S.lang] = S.editor.get(); queueSave(); });
    $('#pg-help').on('click', showHelp);
    $('#pg-run').on('click', () => run()); $('#pg-submit').on('click', () => submit());

    // test-case chips and details (delegated: the pane is re-rendered)
    $('#pgc-cases').on('click', '.pg-chip[data-i]', function () { S.sel = +$(this).data('i'); renderCases(); })
      .on('click', '#pg-addcase', () => addCustom(''))
      .on('click', '#pg-delc', function () { S.cases.splice(S.sel, 1); let n = 0; S.cases.forEach(x => { if (x.kind === 'custom') x.name = 'Custom ' + (++n); }); S.sel = Math.max(0, S.sel - 1); saveCustom(); renderCases(); })
      .on('input', '#pg-cin', function () { S.cases[S.sel].input = this.value; saveCustom(); })
      .on('click', '#pg-runc', () => runCustom())
      .on('click', '#pg-tip-x', function (e) { e.preventDefault(); ls.set('pg_tip_seen', '1'); $(this).closest('.pg-tip').remove(); })
      .on('click', 'a[data-tocustom]', function (e) { e.preventDefault(); addCustom((S.cases[+$(this).data('tocustom')] || {}).input || ''); });
    $('#pg-prob').on('click', 'a[data-tocustom]', function (e) { e.preventDefault(); addCustom((S.q.samples[+$(this).data('tocustom')] || {}).input || ''); });
    $('#pg-sublist').on('click', 'button[data-view]', function () { viewSubmission(+$(this).data('view')); });

    // splitters
    drag('#pg-vs', 'x'); drag('#pg-hs', 'y');
    window.addEventListener('online', onOnline); window.addEventListener('offline', onOffline);
    window.addEventListener('pagehide', onHide); document.addEventListener('visibilitychange', onVis);
    S.listeners.push(() => { window.removeEventListener('online', onOnline); window.removeEventListener('offline', onOffline); window.removeEventListener('pagehide', onHide); document.removeEventListener('visibilitychange', onVis); });
  }
  function restoreLayout() {
    const w = ls.get('pg:left'), h = ls.get('pg:console');
    if (w) $('#pg').css('--pg-left', w + 'px'); if (h) $('#pg').css('--pg-console', h + 'px');
    if (ls.get('pg:focus') === '1') $('#pg').addClass('pg-focus');
  }
  function toggleFocus() { const on = !$('#pg').hasClass('pg-focus'); $('#pg').toggleClass('pg-focus', on); $('#pg-focus').text(on ? '⇥' : '⇤').attr('title', on ? 'Show the problem panel' : 'Hide the problem panel'); ls.set('pg:focus', on ? '1' : '0'); }
  function drag(sel, axis) {
    $(sel).on('mousedown', function (e) {
      e.preventDefault(); const $pg = $('#pg'), start = axis === 'x' ? e.clientX : e.clientY;
      const base = axis === 'x' ? $('.pg-left')[0].getBoundingClientRect().width : $('#pg-console')[0].getBoundingClientRect().height;
      const move = ev => {
        const d = (axis === 'x' ? ev.clientX : ev.clientY) - start;
        if (axis === 'x') { const v = Math.max(240, Math.min(window.innerWidth - 420, base + d)); $pg.css('--pg-left', v + 'px'); ls.set('pg:left', Math.round(v)); }
        else { const v = Math.max(110, Math.min(window.innerHeight - 320, base - d)); $pg.css('--pg-console', v + 'px'); ls.set('pg:console', Math.round(v)); }
      };
      const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); document.body.classList.remove('pg-dragging'); };
      document.body.classList.add('pg-dragging'); document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
    }).on('dblclick', function () { $('#pg').css(axis === 'x' ? '--pg-left' : '--pg-console', ''); try { localStorage.removeItem(axis === 'x' ? 'pg:left' : 'pg:console'); } catch (e) {} });
  }
  function showConsole(name) {
    $('.pg-console .pg-tabs li').removeClass('on').filter('[data-c=' + name + ']').addClass('on');
    ['cases', 'result'].forEach(n => $('#pgc-' + n).toggleClass('d-none', n !== name));
  }
  function showHelp() {
    let $m = $('#pg-help-modal');
    if (!$m.length) {
      $m = $('<div class="modal fade" id="pg-help-modal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content"><div class="modal-header"><h6 class="modal-title mb-0">Coding playground — quick help</h6><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body">' +
        '<div class="row g-4"><div class="col-md-6"><h6>Workflow</h6><ol class="small ps-3"><li>Read the problem; use standard input and output.</li><li>Choose a language (code is kept per language).</li><li><b>Run samples</b> and compare expected vs your output.</li><li>Add your own <b>custom cases</b> for edge cases.</li><li><b>Submit</b> to be scored on all hidden cases. Best submission counts.</li></ol>' +
        '<h6>Autosave</h6><ul class="small ps-3"><li>Saved to the server a moment after you stop typing, and on tab switch/close.</li><li>A copy is also kept in this browser. If you go offline or crash, unsaved changes are recovered next time.</li><li>The dot beside the toolbar shows the state: <b>green</b> saved, <b>amber</b> saving, <b>red</b> offline/failed.</li><li><b>↶ History</b> restores earlier versions.</li></ul>' +
        '<h6>Good to know</h6><ul class="small ps-3"><li>Java class must be <code>Main</code>. Print only the answer.</li><li>Pasting from outside the editor is blocked and logged; copying within the editor is fine.</li><li>Drag the dividers to resize panels; ⇤ hides the problem panel.</li></ul></div>' +
        '<div class="col-md-6"><h6>Keyboard shortcuts</h6><table class="table table-sm small"><tbody><tr><td><kbd>Ctrl/⌘</kbd> + <kbd>Enter</kbd></td><td>Run samples</td></tr><tr><td><kbd>Ctrl/⌘</kbd> + <kbd>Shift</kbd> + <kbd>Enter</kbd></td><td>Submit</td></tr><tr><td><kbd>Ctrl/⌘</kbd> + <kbd>S</kbd></td><td>Save now</td></tr><tr><td><kbd>Ctrl/⌘</kbd> + <kbd>Z</kbd> / <kbd>Y</kbd></td><td>Undo / redo</td></tr><tr><td><kbd>Ctrl/⌘</kbd> + <kbd>F</kbd> / <kbd>H</kbd></td><td>Find / replace</td></tr><tr><td><kbd>Ctrl/⌘</kbd> + <kbd>/</kbd></td><td>Comment line</td></tr><tr><td><kbd>Alt</kbd> + <kbd>↑</kbd>/<kbd>↓</kbd></td><td>Move line</td></tr><tr><td><kbd>Shift</kbd> + <kbd>Alt</kbd> + <kbd>↓</kbd></td><td>Duplicate line</td></tr><tr><td><kbd>Tab</kbd> / <kbd>Shift</kbd> + <kbd>Tab</kbd></td><td>Indent / outdent</td></tr></tbody></table>' +
        '<h6>Verdicts</h6><div class="small"><b>Accepted</b> all good · <b>Wrong Answer</b> output differs (see the highlighted line) · <b>Time Limit</b> too slow · <b>Compilation Error</b> fix the syntax · <b>Runtime Error</b> crashed</div></div></div>' +
        '<div class="mt-3"><a href="/help#coding" target="_blank">Open the full guide ↗</a></div></div></div></div></div>').appendTo('body');
    }
    bootstrap.Modal.getOrCreateInstance($m[0]).show();
  }

  // ------------------------------------------------------------------ run / custom / submit
  function busy(on, btn) { S.busy = on; $('#pg-run,#pg-submit,#pg-runc').prop('disabled', on); if (btn) { const $b = $(btn); $b.data('t', $b.data('t') || $b.text()).text(on ? 'Working…' : $b.data('t')); } }
  const payload = extra => Object.assign({ question_id: S.q.question_id, language: S.lang, source_code: S.editor.get() }, extra || {});

  async function run() {
    if (S.busy) return; await flush(true); snapshot(false, 'Run'); busy(true, '#pg-run'); showConsole('cases');
    S.cases.forEach(x => { if (x.kind === 'sample') x.res = null; }); renderCases(); $('#pgc-cases .pg-detail').prepend('<div class="text-muted mb-2">Running against the sample cases…</div>');
    try {
      const r = (await Api.post('/attempts/' + S.ctx.attemptId + '/code/run', payload())).data.cases;
      S.cases.filter(x => x.kind === 'sample').forEach((x, i) => { x.res = r[i] || null; });
      const firstBad = S.cases.findIndex(x => x.kind === 'sample' && x.res && !x.res.passed); if (firstBad >= 0) S.sel = firstBad;   // jump to what failed
    } catch (e) { /* toast already shown */ }
    renderCases(); busy(false, '#pg-run');
  }
  async function runCustom() {
    if (S.busy) return; const c = S.cases[S.sel]; if (!c || c.kind !== 'custom') return;
    await flush(true); busy(true); $('#pg-runc').text('Working…');
    try { c.res = (await Api.post('/attempts/' + S.ctx.attemptId + '/code/run-custom', payload({ stdin: c.input }))).data; } catch (e) { c.res = null; }
    busy(false); renderCases();
  }
  async function submit() {
    if (S.busy) return; const code = S.editor.get();
    if (S.lastSubmitted[S.lang] === code && !confirm('This code is identical to your last submission. Submit it again?')) return;
    await flush(true); snapshot(true, 'Submitted'); busy(true, '#pg-submit'); showConsole('result'); $('#pgc-result').html('<span class="text-muted">Judging against all test cases…</span>');
    try {
      const r = (await Api.post('/attempts/' + S.ctx.attemptId + '/code/submit', payload())).data, pct = r.total_cases ? Math.round(100 * r.passed_cases / r.total_cases) : 0;
      S.lastSubmitted[S.lang] = code;
      $('#pgc-result').html('<div class="pg-summary ' + verdictClass(r.status) + '"><b>' + E(r.status) + '</b> — passed ' + r.passed_cases + ' / ' + r.total_cases + ' test cases</div>' +
        '<div class="progress my-2" style="height:6px"><div class="progress-bar ' + (pct === 100 ? 'bg-success' : '') + '" style="width:' + pct + '%"></div></div>' +
        '<div class="small text-muted">' + fmtMs(r.runtime_ms) + ' · ' + fmtMem(r.memory_kb) + ' · Hidden test cases are not shown. You can submit again; your best submission counts.</div>');
      S.ctx.onSubmitted && S.ctx.onSubmitted(r); loadSubs();
    } catch (e) { $('#pgc-result').html('<span class="text-muted">The submission could not be judged. Please try again.</span>'); }
    busy(false, '#pg-submit');
  }
  async function loadSubs() {
    try {
      const rows = (await Api.get('/attempts/' + S.ctx.attemptId + '/code/submissions', { question_id: S.q.question_id }, { silent: true })).data; S.subs = rows;
      const best = rows.reduce((m, x) => Math.max(m, x.passed_cases), -1);
      $('#pg-subn').text(rows.length);
      $('#pg-sublist').html(rows.length ? rows.map((x, i) => '<div class="pg-sub"><div><b>#' + (rows.length - i) + '</b> · ' + (LANGS[x.language] || {}).label + (x.passed_cases === best && best > 0 ? ' <span class="badge text-bg-success">best</span>' : '') +
        '<div class="small text-muted">' + clock(x.created_at) + ' · ' + fmtMs(x.runtime_ms) + ' · ' + fmtMem(x.memory_kb) + '</div><button class="btn btn-link btn-sm p-0" data-view="' + x.id + '">Load this code into the editor</button></div>' +
        '<div class="text-end"><span class="badge text-bg-' + (x.passed_cases === x.total_cases ? 'success' : 'danger') + '">' + E(x.status) + '</span><div class="small">' + x.passed_cases + ' / ' + x.total_cases + '</div></div></div>').join('') : 'No submissions yet.');
    } catch (e) {}
  }
  async function viewSubmission(id) {
    try {
      const s = (await Api.get('/attempts/' + S.ctx.attemptId + '/code/submissions/' + id)).data;
      if (!confirm('Load submission code (' + (LANGS[s.language] || {}).label + ') into the editor? Your current code is kept in History.')) return;
      snapshot(true, 'Before loading a submission');
      if (s.language !== S.lang) { S.lang = s.language; $('#pg-lang').val(s.language); S.editor.setLang(s.language); $('#pg-langv').text(LANGS[s.language].label); }
      S.editor.set(s.source_code); S.codes[S.lang] = s.source_code; queueSave(); $('.pg-left .pg-tabs li[data-l=prob]').trigger('click');
    } catch (e) {}
  }

  async function unmount() {
    if (!S) return;
    if (S.dirty && S.editor) { try { await flush(true); } catch (e) {} }
    S.dead = true; [S.timer, S.bakTimer].forEach(clearTimeout); [S.maxTimer, S.histTimer].forEach(clearInterval);
    Proctor.setClipFilter(null); Proctor.setKeyFilter && Proctor.setKeyFilter(null);
    S.listeners.forEach(f => { try { f(); } catch (e) {} });
    try { S.editor && S.editor.dispose(); } catch (e) {}
    S = null;
  }
  return { mount, unmount, flush: () => (S ? flush(true) : Promise.resolve()) };
})(jQuery);
