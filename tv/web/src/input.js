/**
 * Remote and keyboard input → actions: up, down, left, right, select, back, menu, playpause, rewind, forward.
 *
 * Fire TV: the app shell sends Back as Escape and the centre button as Enter, and calls
 * window.__fdKey(action, down) for Menu, Play/Pause, Rewind and Fast-forward (WebViews deliver them unreliably).
 * Browser: arrows, Enter = OK, Escape/Backspace = Back, M = Menu, P = Play/Pause, [ and ] = Rewind / Fast-forward.
 *
 * Select reports both press and release (for the held confirmation on level 3 approvals). Arrows auto-repeat
 * while held (throttled).
 */

const MAP = {
  ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  Enter: 'select', NumpadEnter: 'select', ' ': 'select',
  Escape: 'back', Backspace: 'back', BrowserBack: 'back', GoBack: 'back',
  m: 'menu', M: 'menu', ContextMenu: 'menu',
  p: 'playpause', P: 'playpause', MediaPlayPause: 'playpause',
  '[': 'rewind', MediaRewind: 'rewind', ']': 'forward', MediaFastForward: 'forward',
};
const KEYCODES = { 13: 'select', 23: 'select', 27: 'back', 4: 'back', 8: 'back', 37: 'left', 38: 'up', 39: 'right', 40: 'down', 82: 'menu', 179: 'playpause', 227: 'rewind', 228: 'forward' };

export const ARROW_REPEAT_MS = 120;

export function createInput(onAction) {
  let lastArrow = -Infinity;
  let selectDown = false;

  function fire(action, down, repeat) {
    if (action === 'up' || action === 'down' || action === 'left' || action === 'right') {
      if (!down) return;
      const now = performance.now();
      if (repeat && now - lastArrow < ARROW_REPEAT_MS) return;
      lastArrow = now;
      onAction(action, true);
      return;
    }
    if (action === 'select') {
      if (down && selectDown) return; // auto-repeat of a held OK
      selectDown = down;
      onAction('select', down);
      return;
    }
    if (down && !repeat) onAction(action, true);
  }

  function key(e, down) {
    // Typing in a field (the SSH setup): letters, space and Backspace edit it; Up/Down leave it, Back and OK as usual.
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA') && !/^(Escape|Enter|ArrowUp|ArrowDown|GoBack|BrowserBack)$/.test(e.key) && e.keyCode !== 4) return;
    const action = MAP[e.key] || KEYCODES[e.keyCode];
    if (!action) return;
    e.preventDefault();
    fire(action, down, e.repeat);
  }
  window.addEventListener('keydown', (e) => key(e, true));
  window.addEventListener('keyup', (e) => key(e, false));
  window.__fdKey = (action, down) => fire(action, down !== false, false);
  return { press: (a) => { fire(a, true, false); if (a === 'select') fire(a, false, false); } };
}
