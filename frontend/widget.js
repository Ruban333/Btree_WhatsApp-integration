/*! BTree WhatsApp floating widget.
 * Embed on any page:
 *   <script src="https://YOUR-SERVER/widget.js" data-course="Full Stack Development"></script>
 * Optional attributes: data-server (defaults to the script's origin), data-phone (overrides the server's number),
 * data-text (custom pre-filled message), data-label, data-course.
 */
(function () {
  var s = document.currentScript, server = s.getAttribute('data-server') || new URL(s.src).origin;
  function build(cfg) {
    var phone = (s.getAttribute('data-phone') || cfg.number || '').replace(/\D/g, '');
    var course = s.getAttribute('data-course') || '';
    var text = s.getAttribute('data-text') ||
      'Hi ' + cfg.institute + ', I\'d like to know about ' + (course || 'your courses') + '. (via website)';
    var url = phone ? 'https://wa.me/' + phone + '?text=' + encodeURIComponent(text) : '#';
    if (!phone) console.warn('[btree-widget] No WhatsApp number configured. Set WHATSAPP_NUMBER in .env and restart.');

    var css = document.createElement('style');
    css.textContent =
      '.btw{position:fixed;right:20px;bottom:20px;z-index:99999;display:flex;align-items:center;gap:10px;font:14px system-ui,sans-serif}' +
      '.btw-tip{background:#fff;color:#1c2230;padding:8px 12px;border-radius:10px;box-shadow:0 4px 14px rgba(0,0,0,.18);max-width:200px}' +
      '.btw-btn{width:58px;height:58px;border-radius:50%;background:#25d366;display:grid;place-items:center;box-shadow:0 4px 14px rgba(0,0,0,.3);transition:transform .15s;text-decoration:none}' +
      '.btw-btn:hover{transform:scale(1.08)}.btw-btn:focus-visible{outline:3px solid #0b5e48;outline-offset:2px}' +
      '.btw-x{position:absolute;top:-8px;left:-8px;width:20px;height:20px;border-radius:50%;border:0;background:#555;color:#fff;cursor:pointer;font-size:12px;line-height:1}' +
      '@media(max-width:480px){.btw-tip{display:none}}';
    document.head.appendChild(css);

    var box = document.createElement('div');
    box.className = 'btw';
    var tip = document.createElement('div');
    tip.className = 'btw-tip';
    tip.textContent = s.getAttribute('data-label') || 'Chat with us on WhatsApp';
    var a = document.createElement('a');
    a.className = 'btw-btn'; a.href = url; a.target = '_blank'; a.rel = 'noopener';
    a.setAttribute('aria-label', 'Chat on WhatsApp');
    a.innerHTML = '<svg width="32" height="32" viewBox="0 0 24 24" fill="#fff" aria-hidden="true"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>';
    if (!phone) {
      tip.textContent = 'Widget works, but no WhatsApp number is set (WHATSAPP_NUMBER in .env)';
      a.onclick = function (e) { e.preventDefault(); alert('Set WHATSAPP_NUMBER in .env (with country code) and restart the server.'); };
    }
    var x = document.createElement('button');
    x.className = 'btw-x'; x.textContent = '×'; x.setAttribute('aria-label', 'Dismiss');
    x.onclick = function () { tip.remove(); x.remove(); };
    tip.style.position = 'relative'; tip.appendChild(x);
    box.appendChild(tip); box.appendChild(a);
    document.body.appendChild(box);
  }
  fetch(server + '/api/widget-config').then(function (r) { return r.json(); }).then(build)
    .catch(function () { build({ number: '', institute: 'BTree Systems' }); });
})();
