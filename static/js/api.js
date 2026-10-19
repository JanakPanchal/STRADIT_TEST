// Single API wrapper: JWT auto-attach, 401 -> silent refresh -> retry, global error toasts.
// All pages call the server ONLY through this module; no business logic in page scripts.
const Api = (function ($) {
  const BASE = '/api/v1';
  let token = sessionStorage.getItem('at');
  let refreshing = null;

  function setToken(t) { token = t; t ? sessionStorage.setItem('at', t) : sessionStorage.removeItem('at'); }

  let inflight = 0;
  $(function () { $('body').append('<div id="load-bar"></div>'); });
  $(document).on('ajaxSend', () => { if (inflight++ === 0) $('#load-bar').addClass('on'); })
    .on('ajaxComplete', () => { if (--inflight <= 0) { inflight = 0; $('#load-bar').removeClass('on'); } });

  function esc(s) { return $('<div>').text(s == null ? '' : String(s)).html(); }

  function toast(msg, kind) {
    const $t = $('<div class="alert alert-' + (kind || 'danger') + ' shadow position-fixed top-0 end-0 m-3" style="z-index:3000;max-width:420px"></div>').text(msg);
    $('body').append($t);
    setTimeout(() => $t.fadeOut(300, () => $t.remove()), 4500);
  }

  function refresh() {
    if (!refreshing) {
      refreshing = new Promise((resolve, reject) => {
        $.ajax({ url: BASE + '/auth/refresh', method: 'POST' })
          .done(r => { setToken(r.data.access_token); resolve(); })
          .fail(() => { setToken(null); reject(); })
          .always(() => { refreshing = null; });
      });
    }
    return refreshing;
  }

  function errMsg(xhr) {
    const e = xhr.responseJSON && xhr.responseJSON.error;
    if (!e) return 'Network error';
    if (e.details && Array.isArray(e.details) && e.details[0]) {
      const d = e.details[0];
      return e.message + ': ' + (d.msg || d.error || '') + (d.loc ? ' (' + d.loc.slice(1).join('.') + ')' : '') + (d.row ? ' row ' + d.row : '');
    }
    return e.message;
  }

  function request(method, url, data, opts, retried) {
    opts = opts || {};
    const settings = { url: BASE + url, method, headers: {}, dataType: 'json' };
    if (token) settings.headers.Authorization = 'Bearer ' + token;
    if (data instanceof FormData) { settings.data = data; settings.processData = false; settings.contentType = false; }
    else if (data !== undefined && method !== 'GET') { settings.data = JSON.stringify(data); settings.contentType = 'application/json'; }
    else if (data) settings.data = data;
    return new Promise((resolve, reject) => {
      $.ajax(settings).done(r => resolve(r)).fail(xhr => {
        if (xhr.status === 401 && !retried && !opts.noRefresh) {
          return refresh().then(() => request(method, url, data, opts, true).then(resolve, reject))
            .catch(() => { Api.logoutLocal(); reject(xhr); });
        }
        if (!opts.silent) toast(errMsg(xhr));
        reject(xhr);
      });
    });
  }

  return {
    get: (u, d, o) => request('GET', u, d, o),
    post: (u, d, o) => request('POST', u, d, o),
    put: (u, d, o) => request('PUT', u, d, o),
    del: (u, o) => request('DELETE', u, undefined, o),
    esc, toast, errMsg, setToken,
    hasToken: () => !!token,
    // Delete a test; if candidates already attempted it, show the real numbers and require a second confirmation.
    async deleteTest(id, title) {
      if (!confirm('Delete "' + title + '" and all its questions?')) return false;
      try { await request('DELETE', '/admin/tests/' + id, undefined, { silent: true }); return true; }
      catch (xhr) {
        const e = xhr.responseJSON && xhr.responseJSON.error;
        if (e && e.code === 'test_in_use') {
          if (!confirm('"' + title + '" has ' + e.details.attempts + ' candidate attempt(s) (' + e.details.assigned + ' assigned).\n\nDeleting it PERMANENTLY removes all of their answers, scores, violations and code submissions.\n\nDelete everything?')) return false;
          await request('DELETE', '/admin/tests/' + id + '?force=true'); return true;
        }
        toast(errMsg(xhr)); return false;
      }
    },
    login(email, password) {
      return new Promise((resolve, reject) => {
        $.ajax({ url: BASE + '/auth/login', method: 'POST', data: { username: email, password } })
          .done(r => { setToken(r.data.access_token); resolve(r.data); })
          .fail(xhr => { toast(errMsg(xhr)); reject(xhr); });
      });
    },
    logoutLocal() { setToken(null); location.href = '/login'; },
    logout() { $.ajax({ url: BASE + '/auth/logout', method: 'POST' }).always(() => Api.logoutLocal()); },
    // authenticated file download (CSV export)
    async download(url, filename) {
      const go = () => fetch(BASE + url, { headers: { Authorization: 'Bearer ' + token } });
      let r = await go();
      if (r.status === 401) { await refresh(); r = await go(); }
      if (!r.ok) { toast('Download failed'); return; }
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await r.blob()); a.download = filename; a.click();
    },
    // keepalive fetch survives page unload and (unlike sendBeacon) can carry the auth header
    keepalive(method, url, data) {
      try { fetch(BASE + url, { method, keepalive: true, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token }, body: JSON.stringify(data) }); } catch (e) {}
    },
    keepalivePost(url, data) {
      try { fetch(BASE + url, { method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token }, body: JSON.stringify(data) }); } catch (e) {}
    },
    async requireRole(role) {
      try {
        const r = await request('GET', '/auth/me', undefined, { silent: true });
        if (r.data.role !== role) { location.href = r.data.role === 'admin' ? '/admin' : '/dashboard'; return null; }
        return r.data;
      } catch (e) { Api.logoutLocal(); return null; }
    },
  };
})(jQuery);

// Tab guard: every page of the site announces itself so the test page can tell when other tabs are open.
// (A browser cannot see tabs of OTHER sites; those are caught by the tab-switch/blur proctoring instead.)
const TabGuard = (function () {
  const id = Math.random().toString(36).slice(2), peers = {}, listeners = [];
  let ch = null, last = 0;
  try { ch = new BroadcastChannel('stradit-tabs'); } catch (e) { return { count: () => 0, onChange: () => {} }; }
  const count = () => { const now = Date.now(); Object.keys(peers).forEach(k => { if (now - peers[k] > 5000) delete peers[k]; }); return Object.keys(peers).length; };
  const ping = () => { ch.postMessage({ id }); const c = count(); if (c !== last) { last = c; listeners.forEach(f => f(c)); } };
  ch.onmessage = e => { if (e.data && e.data.id && e.data.id !== id) { const had = e.data.id in peers; peers[e.data.id] = Date.now(); if (!had) ping(); } };
  setInterval(ping, 2000); ping();
  window.addEventListener('pagehide', () => { try { ch.postMessage({ id, bye: 1 }); } catch (e) {} });
  ch.addEventListener('message', e => { if (e.data && e.data.bye) { delete peers[e.data.id]; ping(); } });
  return { count, onChange: f => listeners.push(f) };
})();
