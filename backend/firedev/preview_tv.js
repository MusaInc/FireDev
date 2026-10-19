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
    // Pages with smooth scrolling are still moving: follow them to where they stop.
    setTimeout(draw, 250);
    setTimeout(draw, 700);
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

  // --- camera: the TV's view of the page while the agent works ---------------------------------------------------
  // A gentle tour of the real page's sections while nothing changes, and a smooth move to whatever a real edit
  // touched (found from hints the TV derives from the edited code). Scrolling is eased by hand (scrollTo per frame):
  // older TV WebViews ignore smooth scrolling. Anything the viewer does stops it at once.
  var cam = { tour: false, timer: 0, anim: 0, hl: null, hlTimer: 0 };
  var DWELL = 7000;

  function closestEl(el, sel) {
    while (el && el.nodeType === 1) { if ((el.matches || el.webkitMatchesSelector || el.msMatchesSelector).call(el, sel)) return el; el = el.parentElement; }
    return null;
  }
  function topOf(el) { return el.getBoundingClientRect().top + (window.pageYOffset || document.documentElement.scrollTop || 0); }
  function scrollY() { return window.pageYOffset || document.documentElement.scrollTop || 0; }
  function maxY() { return Math.max(0, (document.documentElement.scrollHeight || 0) - window.innerHeight); }

  function glide(to, ms, done) {
    cancelGlide();
    var from = scrollY();
    to = Math.max(0, Math.min(maxY(), Math.round(to)));
    if (Math.abs(to - from) < 4) { if (done) done(); return; }
    var start = 0;
    function step(t) {
      if (!start) start = t;
      var k = Math.min(1, (t - start) / ms);
      var e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; // ease in and out
      window.scrollTo(0, from + (to - from) * e);
      drawHighlight();
      if (k < 1) cam.anim = requestAnimationFrame(step);
      else { cam.anim = 0; if (done) done(); }
    }
    cam.anim = requestAnimationFrame(step);
  }
  function cancelGlide() { if (cam.anim) cancelAnimationFrame(cam.anim); cam.anim = 0; }

  /** The page's real sections, top to bottom: tall, wide blocks (header, sections, footer, anything with an id). */
  function sections() {
    var all = document.querySelectorAll('header,nav,main>*,section,article,footer,[id]');
    var vh = window.innerHeight, vw = window.innerWidth, list = [];
    for (var i = 0; i < all.length; i++) {
      var r = all[i].getBoundingClientRect();
      if (r.height < Math.min(140, vh * 0.2) || r.width < vw * 0.4 || !visible(all[i])) continue;
      list.push({ el: all[i], top: r.top + scrollY() });
    }
    list.sort(function (a, b) { return a.top - b.top; });
    var out = [];
    for (var j = 0; j < list.length; j++) if (!out.length || list[j].top - out[out.length - 1].top > 80) out.push(list[j]);
    return out;
  }

  function nameOf(el) {
    var t = el.tagName.toLowerCase();
    var aria = el.getAttribute('aria-label') || '';
    var raw = (aria.split(/\s+/).length <= 3 ? aria : '') || el.id || '';
    if (!raw && el.className && typeof el.className === 'string') {
      var c = el.className.split(/\s+/)[0] || '';
      if (/^[a-z][a-z-]{2,}$/i.test(c) && !/^(container|wrap|wrapper|inner|row|col|grid|flex|section|block|content|js-)/i.test(c)) raw = c;
    }
    if (raw) {
      raw = raw.replace(/[-_]+/g, ' ').replace(/\b(section|wrap|wrapper|container|inner|content|block)\b/gi, '').replace(/\s+/g, ' ').trim().toLowerCase();
      if (/^(top|intro|banner|masthead|home|landing|splash)$/.test(raw)) raw = 'hero';
      if (raw) return raw.slice(0, 30);
    }
    if (t === 'header') return 'header';
    if (t === 'footer') return 'footer';
    if (t === 'nav') return 'navigation';
    var h = el.querySelector && el.querySelector('h1,h2,h3');
    var txt = h ? (h.innerText || h.textContent || '').replace(/\s+/g, ' ').trim() : '';
    return txt && txt.length <= 40 ? '“' + txt + '”' : '';
  }

  function tourStep() {
    clearTimeout(cam.timer);
    if (!cam.tour) return;
    if (maxY() < window.innerHeight * 0.15) { post({ fd: 'camera', state: 'still' }); return; }
    var list = sections(), y = scrollY(), next = null;
    for (var i = 0; i < list.length; i++) if (list[i].top > y + 60 && list[i].top - window.innerHeight * 0.06 <= maxY() + 10) { next = list[i]; break; }
    var to = next ? next.top - window.innerHeight * 0.06 : 0;
    post({ fd: 'camera', state: 'tour', name: next ? nameOf(next.el) : 'top' });
    glide(to, next ? 2200 : 3400, function () { if (cam.tour) cam.timer = setTimeout(tourStep, DWELL); });
  }

  function setTour(on) {
    cam.tour = !!on;
    clearTimeout(cam.timer);
    if (on) cam.timer = setTimeout(tourStep, 2500); // a moment on the current view first
    else cancelGlide();
  }

  function query(sel) {
    try { return document.querySelectorAll(sel); } catch (e) { return []; }
  }
  function usesAsset(name) {
    var out = [], all = document.querySelectorAll('img,source,image,use,object,embed,[style]');
    for (var i = 0; i < all.length; i++) {
      var a = all[i], u = (a.getAttribute('src') || a.getAttribute('srcset') || a.getAttribute('href') || a.getAttribute('xlink:href') || a.getAttribute('data') || a.getAttribute('style') || '');
      if (u.indexOf(name) >= 0) out.push(a.tagName === 'SOURCE' ? a.parentElement : a);
    }
    if (!out.length) { // a CSS background
      var els = document.querySelectorAll('body *');
      for (var j = 0; j < els.length && j < 1500; j++) { var bg = window.getComputedStyle(els[j]).backgroundImage || ''; if (bg.indexOf(name) >= 0) out.push(els[j]); }
    }
    return out;
  }

  // Really part of the page, not parked off it (a skip link, an off-canvas menu).
  function onPage(el) {
    var r = el.getBoundingClientRect();
    return r.bottom + scrollY() > 0 && r.right > 0 && r.left < window.innerWidth && r.width * r.height > 64;
  }

  /** The element a hint means, if it means one: a visible match, and not one of many (".btn" says nothing). */
  function resolve(hints) {
    for (var i = 0; i < (hints || []).length && i < 8; i++) {
      var h = hints[i], found = h.asset ? usesAsset(String(h.asset)) : h.sel ? query(String(h.sel)) : [];
      var vis = [];
      for (var j = 0; j < found.length; j++) if (found[j] && visible(found[j]) && onPage(found[j])) vis.push(found[j]);
      if (vis.length && vis.length <= 6) return vis[0];
    }
    return null;
  }

  /** Where to look for an element: its section, or itself when it is big enough to be one. */
  function frameOf(el) {
    if (el.getBoundingClientRect().height >= window.innerHeight * 0.35) return el;
    var box = closestEl(el.parentElement, 'section,header,footer,nav,article,main>*');
    if (box) return box;
    for (var p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (p.id && p.getBoundingClientRect().height >= window.innerHeight * 0.35) return p;
    }
    return el;
  }

  function focusOn(m) {
    var el = resolve(m.hints);
    if (!el) { post({ fd: 'camera', state: 'kept' }); return; }
    setTour(false);
    var box = frameOf(el), r = box.getBoundingClientRect(), vh = window.innerHeight;
    var to = r.height < vh * 0.8 ? scrollY() + r.top - (vh - r.height) / 2 : scrollY() + r.top - vh * 0.06;
    post({ fd: 'camera', state: 'follow', name: nameOf(box) || nameOf(el) });
    glide(to, 1500, function () { if (m.highlight) highlight(el.getBoundingClientRect().height > 30 ? el : box); });
  }

  function highlight(el) {
    if (!cam.hl) {
      cam.hl = document.createElement('div');
      cam.hl.style.cssText = 'position:fixed;z-index:2147483646;pointer-events:none;border:3px solid rgba(255,140,80,.95);border-radius:14px;' +
        'box-shadow:0 0 0 4px rgba(255,106,61,.18),0 0 36px rgba(255,106,61,.45);transition:opacity .5s ease;opacity:0';
      document.documentElement.appendChild(cam.hl);
    }
    cam.hlEl = el;
    drawHighlight();
    cam.hl.style.opacity = '1';
    clearTimeout(cam.hlTimer);
    cam.hlTimer = setTimeout(function () { if (cam.hl) cam.hl.style.opacity = '0'; cam.hlTimer = setTimeout(function () { cam.hlEl = null; }, 600); }, 2200);
  }
  function drawHighlight() {
    if (!cam.hl || !cam.hlEl) return;
    var r = cam.hlEl.getBoundingClientRect();
    cam.hl.style.left = (r.left - 8) + 'px'; cam.hl.style.top = (Math.max(-8, r.top - 8)) + 'px';
    cam.hl.style.width = (r.width + 10) + 'px'; cam.hl.style.height = (Math.min(r.height, window.innerHeight) + 10) + 'px';
  }

  // The viewer takes over: stop moving at once (the TV resumes after a quiet spell).
  function viewerMoved() {
    if (!cam.tour && !cam.anim) return;
    setTour(false);
    post({ fd: 'camera', state: 'paused' });
  }
  ['wheel', 'touchstart', 'mousedown', 'keydown'].forEach(function (t) { window.addEventListener(t, viewerMoved, { passive: true, capture: true }); });

  function command(m) {
    switch (m.op) {
      case 'key': viewerMoved(); return act(m.action);
      case 'camera': return setTour(m.mode === 'tour');
      case 'focus': return focusOn(m);
      case 'reload':
        saveScroll();
        return location.reload();
      case 'savescroll': return saveScroll();
      case 'css': return swapStyles();
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

  function saveScroll() {
    try { sessionStorage.setItem('__fd_scroll', JSON.stringify([page(), window.scrollX, window.scrollY])); } catch (e) { /* no storage */ }
  }

  // Stylesheets reloaded in place: the new sheet loads beside the old one, which goes once it has applied, so the
  // page never loses its styles, its scroll or its state. No same-origin sheets: the TV reloads the page instead.
  function swapStyles() {
    var links = document.querySelectorAll('link[rel~="stylesheet"]');
    var n = 0, pending = 0;
    for (var i = 0; i < links.length; i++) {
      var old = links[i];
      var u;
      try { u = new URL(old.href, location.href); } catch (e) { continue; }
      if (u.origin !== location.origin) continue;
      u.searchParams.set('__fd', String(Date.now()));
      var fresh = old.cloneNode();
      fresh.href = u.toString();
      fresh.onload = fresh.onerror = (function (o) { return function () {
        if (o.parentNode) o.parentNode.removeChild(o);
        if (--pending === 0) setTimeout(function () { post({ fd: 'cssdone' }); }, 60); // the new styles have applied
      }; })(old);
      pending++;
      old.parentNode.insertBefore(fresh, old.nextSibling);
      n++;
    }
    post({ fd: 'css', n: n });
  }

  // How much the page shows: text and media. The TV keeps a page that hasn't drawn anything yet off screen.
  function contentSize() {
    var b = document.body;
    if (!b) return 0;
    return (b.innerText || b.textContent || '').replace(/\s+/g, '').length + b.querySelectorAll('img,svg,canvas,video,picture,iframe,input,button').length;
  }

  function watchContent() {
    if (contentSize() > 0 || !window.MutationObserver) return;
    var mo = new MutationObserver(function () {
      if (contentSize() > 0) { mo.disconnect(); post({ fd: 'content', n: contentSize() }); }
    });
    mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  }

  function report() { post({ fd: 'nav', page: page(), title: document.title || '' }); }
  ['pushState', 'replaceState'].forEach(function (fn) {
    var orig = history[fn];
    history[fn] = function () { var r = orig.apply(this, arguments); setTimeout(report, 0); return r; };
  });
  window.addEventListener('popstate', report);
  window.addEventListener('hashchange', report);
  window.addEventListener('scroll', function () { draw(); drawHighlight(); }, true);
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
    post({ fd: 'hello', page: page(), title: document.title || '', content: contentSize() });
    watchContent();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
  else ready();
})();
