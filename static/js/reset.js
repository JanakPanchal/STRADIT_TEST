$(async function () {
  const token = new URLSearchParams(location.search).get('token') || '';
  let purpose = null;
  try { const r = await Api.get('/auth/reset-check', { token }, { silent: true, noRefresh: true }); if (r.data.valid) purpose = r.data.purpose; } catch (e) {}
  $('#r-load').addClass('d-none');
  if (!purpose) { $('#r-bad').removeClass('d-none'); return; }
  if (purpose === 'invite') { $('#r-title').text('Welcome — set your password'); $('#r-sub').text('This is your first sign-in. Choose a password only you know (at least 8 characters).'); }
  $('#r-form-box').removeClass('d-none'); $('#r-p1').focus();
  // simple strength meter: length + variety; advisory only (the server enforces the real minimum)
  $('#r-p1').on('input', function () {
    const v = this.value; let n = 0; if (v.length >= 8) n++; if (v.length >= 12) n++; if (/[a-z]/.test(v) && /[A-Z]/.test(v)) n++; if (/\d/.test(v)) n++; if (/[^A-Za-z0-9]/.test(v)) n++;
    const lab = ['', 'Weak', 'Weak', 'Fair', 'Good', 'Strong'][v ? n : 0] || 'Weak';
    $('#pw-bar').css({ width: (v ? n * 20 : 0) + '%', background: n >= 4 ? '#1a7f37' : n === 3 ? '#b26a00' : '#e30613' }); $('#pw-txt').text(v ? lab : '');
  });
  $('#r-p2').on('input', () => $('#r-mismatch').text($('#r-p2').val() && $('#r-p2').val() !== $('#r-p1').val() ? 'The two passwords do not match.' : ''));
  $('#r-form').on('submit', async e => {
    e.preventDefault();
    if ($('#r-p1').val() !== $('#r-p2').val()) { $('#r-mismatch').text('The two passwords do not match.'); return; }
    $('#r-go').prop('disabled', true).text('Saving…');
    try { await Api.post('/auth/reset', { token, new_password: $('#r-p1').val() }, { noRefresh: true });
      $('#r-form-box').addClass('d-none'); $('#r-ok').removeClass('d-none'); setTimeout(() => { location.href = '/login'; }, 2200); }
    catch (x) { $('#r-go').prop('disabled', false).text('Save password'); }
  });
});
