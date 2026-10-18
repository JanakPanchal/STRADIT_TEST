$(function () {
  // colleges are onboarded by the admin; candidates choose from that list
  let hasColleges = false;
  $.getJSON('/api/v1/colleges').done(r => {
    hasColleges = r.data.length > 0;
    r.data.forEach(c => $('#r-college').append($('<option>').val(c.id).text(c.name)));
    $('#r-college').prop('required', hasColleges);
    if (!hasColleges) $('#r-college').closest('.col-12').hide();
  });
  const go = d => { location.href = d.role === 'admin' ? '/admin' : '/dashboard'; };
  $('#t-login,#t-reg').on('click', function () {
    const reg = this.id === 't-reg';
    $('#t-login').toggleClass('active', !reg); $('#t-reg').toggleClass('active', reg);
    $('#login-form').toggleClass('d-none', reg); $('#reg-form').toggleClass('d-none', !reg);
  });
  $('#login-form').on('submit', e => {
    e.preventDefault();
    Api.login($('#l-email').val(), $('#l-pass').val()).then(go).catch(() => {});
  });
  $('#reg-form').on('submit', e => {
    e.preventDefault();
    Api.post('/auth/register', { name: $('#r-name').val(), email: $('#r-email').val(), password: $('#r-pass').val(),
      college_id: $('#r-college').val() ? +$('#r-college').val() : null, branch: $('#r-branch').val(), batch: $('#r-batch').val() }, { noRefresh: true })
      .then(() => Api.login($('#r-email').val(), $('#r-pass').val()).then(go)).catch(() => {});
  });
});
