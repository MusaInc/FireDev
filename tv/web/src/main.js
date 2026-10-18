import { createApp } from './app.js';

// Screens build content with conditional children (cond ? node : null); the DOM's append() would print "null".
for (const proto of [Element.prototype, DocumentFragment.prototype]) {
  proto.append = function (...children) {
    for (const c of children) {
      if (c === null || c === undefined || c === false) continue;
      this.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
  };
}

let started = false;
function launch() {
  if (started) return;
  started = true;
  createApp();
  const boot = document.getElementById('boot');
  if (boot) boot.parentNode.removeChild(boot);
}

function start() {
  if (document.fonts && document.fonts.load) {
    // Lay out with the real fonts so the first frame doesn't reflow, but never let a stuck font load hold the TV.
    Promise.all([document.fonts.load('600 40px Outfit'), document.fonts.load('400 26px "FireDev Mono"')]).then(launch, launch);
    setTimeout(launch, 2500);
  } else {
    launch();
  }
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
else start();
