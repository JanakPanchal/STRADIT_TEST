$(function () {
  $('#f-form').on('submit', async e => {
    e.preventDefault(); $('#f-go').prop('disabled', true).text('Sending…');
    try { const r = await Api.post('/auth/forgot', { email: $('#f-email').val().trim() }, { noRefresh: true });
      $('#f-form-box').addClass('d-none'); $('#f-msg').text(r.data.message); $('#f-done').removeClass('d-none'); }
    catch (x) { $('#f-go').prop('disabled', false).text('Send reset link'); }
  });
});
