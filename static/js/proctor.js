// Proctoring layer: DETECTS and DETERS. A browser cannot truly lock a machine (see README §9).
// Events are debounced locally, batched every 5s, and the server is authoritative on counting.
const Proctor = (function ($) {
  let cfg, queue = [], lastSent = {}, timer, active = false, clipFilter = null, keyFilter = null;
  const DEBOUNCE_MS = 1000;

  function report(type, meta) {
    if (!active) return;
    const now = Date.now();
    if (lastSent[type] && now - lastSent[type] < DEBOUNCE_MS) return;
    lastSent[type] = now;
    queue.push({ type, client_ts: new Date().toISOString(), meta: meta || null });
    if (type === 'fullscreen_exit' || type === 'tab_switch') flush();
    cfg.onEvent && cfg.onEvent(type);
  }

  function flush(unloading) {
    if (!queue.length) return Promise.resolve();
    const events = queue.splice(0, 50);
    const url = '/attempts/' + cfg.attemptId + '/violations';
    if (unloading) { Api.keepalivePost(url, { events }); return Promise.resolve(); }
    return Api.post(url, { events }, { silent: true }).then(r => cfg.onServer && cfg.onServer(r.data))
      .catch(xhr => { if (xhr && xhr.status === 409) cfg.onClosed && cfg.onClosed(); });
  }

  function enterFullscreen() {
    const el = document.documentElement;
    return (el.requestFullscreen ? el.requestFullscreen() : Promise.reject()).catch(() => {});
  }

  function bind() {
    $(document).on('visibilitychange.p', () => { if (document.hidden) report('tab_switch'); });
    $(window).on('blur.p', () => report('blur'));
    $(document).on('fullscreenchange.p', () => {
      if (!document.fullscreenElement) { report('fullscreen_exit'); cfg.onFullscreenLost && cfg.onFullscreenLost(); }
      else cfg.onFullscreenBack && cfg.onFullscreenBack();
    });
    $(document).on('copy.p cut.p paste.p', e => {
      if (clipFilter && clipFilter(e)) return;   // e.g. copying code within the editor is allowed
      e.preventDefault(); report('copy_paste', { kind: e.type });
    });
    $(document).on('contextmenu.p dragstart.p', e => { e.preventDefault(); });
    $(document).on('selectstart.p', e => { if (!/^(TEXTAREA|INPUT)$/.test(e.target.tagName || '')) e.preventDefault(); });
    $(window).on('resize.p', () => report('resize', { w: innerWidth, h: innerHeight }));
    $(document).on('keydown.p', e => {
      const k = e.key.toLowerCase(), ctrl = e.ctrlKey || e.metaKey;
      const bad = e.key === 'F12' || (ctrl && e.shiftKey && ['i', 'j', 'c'].includes(k)) ||
        (ctrl && ['u', 'p', 's'].includes(k)) || (e.metaKey && e.altKey && ['i', 'j', 'c'].includes(k));
      if (bad) { e.preventDefault(); if (!(keyFilter && keyFilter(e))) report('shortcut', { key: e.key }); }
    });
    $(window).on('pagehide.p', () => { flush(true); });  // no return value: a returned value would trigger a 'Leave site?' dialog
  }

  return {
    // Must be called from a user gesture (click) so requestFullscreen is allowed.
    async start(config) {
      cfg = config; active = true;
      await enterFullscreen();
      bind();
      timer = setInterval(flush, 5000);
    },
    stop() { active = false; clearInterval(timer); $(document).off('.p'); $(window).off('.p'); },
    reenterFullscreen: enterFullscreen,
    setClipFilter(fn) { clipFilter = fn; },
    setKeyFilter(fn) { keyFilter = fn; },
    report(type, meta) { report(type, meta); },
    flush,
  };
})(jQuery);
