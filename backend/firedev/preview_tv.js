/* FireDev TV helper, added by the preview gateway to each page of a live preview.
 *
 * It only works inside FireDev's preview frame. FireDev keeps the remote: it sends the arrow keys and OK here while you
 * use the page. This script moves a focus ring between links, buttons and fields, clicks with OK, scrolls when nothing
 * is further in that direction, and reports the page, its title and any errors so FireDev (and the agent) can see them.
 * Back always goes to FireDev, even when a field in the page has the keyboard focus.
 * Plain ES5, for older TV WebViews. It never touches FireDev's own page: it can only post messages to it.
 */
(function () {
  if (window.parent === window || window.__fdTv) return;
  window.__fdTv = true;
  var parentWin = window.parent;
  var ring = null;
  var current = null;
  var SEL = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[tabindex]:not([tabindex="-1"]),[onclick],[contenteditable=""],[contenteditable=true]';

  function post(msg) { msg.fd = msg.fd || 'info'; try { parentWin.postMessage(msg, '*'); } catch (e) { /* parent gone */ } }
  function page() { return location.pathname + location.search + location.hash; }

  function style() {
    var s = document.createElement('style');
    s.textContent = '.__fd-ring{position:fixed;z-index:2147483647;pointer-events:none;border:4px solid #ffb020;border-radius:10px;' +
      'box-shadow:0 0 0 3px rgba(0,0,0,.55),0 0 22px rgba(255,176,32,.55);transition:all .12s ease-out;display:none}';
    (document.head || document.documentElement).appendChild(s);
    ring = document.createElement('div');
    ring.className = '__fd-ring';
    document.documentElement.appendChild(ring);
  }

  function visible(el) {
    if (!el || el.disabled) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    var cs = window.getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity || '1') > 0.05;
  }
  function onScreen(r) { return r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth; }
  function candidates() {
    var all = document.querySelectorAll(SEL);
    var out = [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      // Skip things nested inside another candidate (an icon inside a link), keep the outer one.
      if (el.parentElement && el.parentElement.closest && el.parentElement.closest(SEL) && el.tagName !== 'INPUT') continue;
      if (visible(el)) out.push(el);
    }
    return out;
  }

  function draw() {
    if (!ring) return;
    if (!current || !document.documentElement.contains(current)) { ring.style.display = 'none'; current = null; return; }
    var r = current.getBoundingClientRect();
    ring.style.display = 'block';
    ring.style.left = (r.left - 6) + 'px';
    ring.style.top = (r.top - 6) + 'px';
    ring.style.width = (r.width + 12 - 8) + 'px';
    ring.style.height = (r.height + 12 - 8) + 'px';
  }
  function label(el) {
    return ((el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('title') || el.getAttribute('placeholder') || '') + '').replace(/\s+/g, ' ').trim().slice(0, 80);
  }
  function setCurrent(el) {
    current = el;
    if (el) {
      try { el.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } catch (e) { el.scrollIntoView(false); }
      post({ fd: 'focus', label: label(el), tag: el.tagName.toLowerCase(), href: el.getAttribute('href') || null });
    }
    draw();
  }

  function nearest(dir) {
    var list = candidates();
    if (!current || list.indexOf(current) < 0) {
      // Start from the first thing on screen.
      for (var k = 0; k < list.length; k++) if (onScreen(list[k].getBoundingClientRect())) return list[k];
      return null;
    }
    var a = current.getBoundingClientRect();
    var ax = a.left + a.width / 2, ay = a.top + a.height / 2;
    var best = null, bestScore = Infinity;
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      if (el === current) continue;
      var b = el.getBoundingClientRect();
      var bx = b.left + b.width / 2, by = b.top + b.height / 2;
      var primary, cross;
      if (dir === 'right') { if (bx <= ax + 1) continue; primary = b.left - a.right; cross = Math.abs(by - ay); }
      else if (dir === 'left') { if (bx >= ax - 1) continue; primary = a.left - b.right; cross = Math.abs(by - ay); }
      else if (dir === 'down') { if (by <= ay + 1) continue; primary = b.top - a.bottom; cross = Math.abs(bx - ax); }
      else { if (by >= ay - 1) continue; primary = a.top - b.bottom; cross = Math.abs(bx - ax); }
      var overlap = dir === 'left' || dir === 'right' ? Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) : Math.min(a.right, b.right) - Math.max(a.left, b.left);
      if (overlap > 0) cross *= 0.3;
      var score = Math.max(0, primary) + cross * 2.2;
      // Prefer what's on screen: far-away targets only win when nothing nearer exists.
      if (!onScreen(b)) score += 600;
      if (score < bestScore) { bestScore = score; best = el; }
    }
    // Far below the fold: scroll first instead of jumping.
    if (best && (dir === 'up' || dir === 'down')) {
      var br = best.getBoundingClientRect();
      var gap = dir === 'down' ? br.top - window.innerHeight : -br.bottom;
      if (gap > window.innerHeight * 0.6) return null;
    }
    return best;
  }

  function scrollBy(dir, amount) {
    var dy = dir === 'down' ? 1 : dir === 'up' ? -1 : 0;
    var dx = dir === 'right' ? 1 : dir === 'left' ? -1 : 0;
    var before = [window.scrollX, window.scrollY];
    window.scrollBy({ left: dx * window.innerWidth * (amount || 0.6), top: dy * window.innerHeight * (amount || 0.6), behavior: 'smooth' });
    setTimeout(function () {
      if (window.scrollX === before[0] && window.scrollY === before[1]) post({ fd: 'edge', dir: dir });
      draw();
    }, 420);
  }

  function typing() {
    var a = document.activeElement;
    return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable || a.tagName === 'SELECT');
  }

  function act(action) {
    if (action === 'back') { if (typing()) document.activeElement.blur(); post({ fd: 'key', action: 'back' }); return; }
    if (action === 'select') {
      if (!current) { setCurrent(nearest('down')); return; }
      var t = current.tagName;
      if (t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT' || current.isContentEditable) {
        var type = (current.getAttribute('type') || '').toLowerCase();
        if (t === 'INPUT' && (type === 'checkbox' || type === 'radio' || type === 'submit' || type === 'button')) current.click();
        else current.focus();
      } else {
        current.click();
      }
      post({ fd: 'clicked', label: label(current) });
      setTimeout(draw, 60);
      return;
    }
    if (action === 'up' || action === 'down' || action === 'left' || action === 'right') {
      var next = nearest(action);
      if (next) setCurrent(next);
      else scrollBy(action);
    }
  }

  function findByText(text) {
    text = String(text || '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();
    if (!text) return null;
    var list = candidates();
    var best = null, bestScore = 0;
    for (var i = 0; i < list.length; i++) {
      var l = label(list[i]).toLowerCase();
      if (!l) continue;
      var score = l === text ? 3 : l.indexOf(text) === 0 ? 2 : l.indexOf(text) >= 0 ? 1 : 0;
      if (score > bestScore) { bestScore = score; best = list[i]; }
    }
    return best;
  }

  function command(m) {
    switch (m.op) {
      case 'key': return act(m.action);
      case 'reload':
        try { sessionStorage.setItem('__fd_scroll', JSON.stringify([page(), window.scrollX, window.scrollY])); } catch (e) { /* no storage */ }
        return location.reload();
      case 'back': return history.back();
      case 'forward': return history.forward();
      case 'top': return window.scrollTo({ top: 0, behavior: 'smooth' });
      case 'bottom': return window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
      case 'scroll': return scrollBy(m.dir || 'down', m.amount || 0.8);
      case 'go': if (typeof m.path === 'string' && m.path.charAt(0) === '/' && m.path.charAt(1) !== '/') location.assign(m.path); return null;
      case 'click': {
        var el = findByText(m.text);
        if (!el) { post({ fd: 'notfound', text: m.text }); return null; }
        setCurrent(el);
        el.click();
        post({ fd: 'clicked', label: label(el) });
        return null;
      }
      case 'ring': current = null; if (m.show) setCurrent(nearest('down')); else draw(); return null;
      default: return null;
    }
  }

  window.addEventListener('message', function (e) {
    if (e.source !== parentWin || !e.data || e.data.fd !== 'cmd') return;
    command(e.data);
  });

  // When the page itself has the keyboard (a field is focused), keys arrive here instead of at FireDev.
  window.addEventListener('keydown', function (e) {
    var k = e.key, code = e.keyCode;
    if (k === 'Escape' || k === 'GoBack' || k === 'BrowserBack' || code === 27 || code === 4) { e.preventDefault(); act('back'); return; }
    if (typing()) {
      var a = document.activeElement;
      if ((k === 'ArrowDown' || k === 'ArrowUp') && a.tagName !== 'TEXTAREA' && a.tagName !== 'SELECT') { e.preventDefault(); a.blur(); act(k === 'ArrowDown' ? 'down' : 'up'); }
      return;
    }
    var map = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right', Enter: 'select' };
    if (map[k]) { e.preventDefault(); act(map[k]); }
  }, true);

  function report() { post({ fd: 'nav', page: page(), title: document.title || '' }); }
  ['pushState', 'replaceState'].forEach(function (fn) {
    var orig = history[fn];
    history[fn] = function () { var r = orig.apply(this, arguments); setTimeout(report, 0); return r; };
  });
  window.addEventListener('popstate', report);
  window.addEventListener('hashchange', report);
  window.addEventListener('scroll', draw, true);
  window.addEventListener('resize', draw);

  window.addEventListener('error', function (e) {
    if (e && e.message) post({ fd: 'error', message: e.message + (e.filename ? ' (' + String(e.filename).split('/').pop() + ':' + e.lineno + ')' : '') });
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e && e.reason;
    post({ fd: 'error', message: 'Unhandled promise rejection: ' + (r && r.message ? r.message : String(r)).slice(0, 300) });
  });
  var cerr = console.error;
  console.error = function () {
    try { post({ fd: 'error', message: Array.prototype.map.call(arguments, function (a) { return a && a.message ? a.message : String(a); }).join(' ').slice(0, 400) }); } catch (e) { /* ignore */ }
    return cerr.apply(console, arguments);
  };

  function ready() {
    style();
    try {
      var saved = JSON.parse(sessionStorage.getItem('__fd_scroll') || 'null');
      sessionStorage.removeItem('__fd_scroll');
      if (saved && saved[0] === page()) window.scrollTo(saved[1], saved[2]);
    } catch (e) { /* no storage */ }
    post({ fd: 'hello', page: page(), title: document.title || '' });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
  else ready();
})();
