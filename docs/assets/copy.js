/* Copy button for .term code blocks (all language pages).
   Progressive enhancement: requires clipboard API (secure context);
   without it — or without JS — the blocks stay plain. Blocks that are
   illustration, not instruction, opt out with class "no-copy". */
(function () {
  if (!navigator.clipboard) return;
  document.querySelectorAll('.term:not(.no-copy)').forEach(function (term) {
    var pre = term.querySelector('pre');
    var bar = term.querySelector('.bar');
    if (!pre || !bar) return;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'copybtn';
    btn.textContent = 'copy';
    btn.setAttribute('data-goatcounter-click', 'copy-code');
    btn.addEventListener('click', function () {
      navigator.clipboard.writeText(pre.textContent).then(function () {
        btn.textContent = 'copied ✓';
        btn.classList.add('did');
        setTimeout(function () {
          btn.textContent = 'copy';
          btn.classList.remove('did');
        }, 1600);
      });
    });
    bar.appendChild(btn);
  });
})();
