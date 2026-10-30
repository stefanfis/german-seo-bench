document.addEventListener('DOMContentLoaded', function () {
  var b = document.getElementById('mehr');
  if (!b) return;
  b.addEventListener('click', function () {
    document.getElementById('tipps').textContent =
      'Bohrlöcher vorher anzeichnen und mit der Wasserwaage prüfen.';
  });
});
