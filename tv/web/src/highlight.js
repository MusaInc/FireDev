/**
 * A small syntax highlighter for the live code panel: one line at a time, regular expressions only, so it costs
 * almost nothing on an old Fire TV. Returns HTML with <i class="k|s|c|n|t|a|p|f|v"> spans (keyword, string, comment,
 * number, tag, attribute, property, function, value). Block comments that span lines are tracked with `state`.
 */

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const span = (cls, s) => '<i class="' + cls + '">' + esc(s) + '</i>';

const JS_KW = /^(?:const|let|var|function|return|if|else|for|while|do|switch|case|break|continue|new|class|extends|import|export|from|default|async|await|try|catch|finally|throw|typeof|instanceof|in|of|this|super|null|undefined|true|false|yield|static|get|set|interface|type|enum|implements|public|private|protected|readonly|as|fun|val|def|lambda|pass|None|True|False|elif|with|self|and|or|not|is|nonlocal|global|del|raise|except|package|struct|impl|fn|pub|mut|use|mod|go|func|defer|chan|select|range|map)$/;

/** Generic C-like/Python tokenizer. */
function code(line, state, hash) {
  let out = '';
  let i = 0;
  if (state.block) {
    const end = line.indexOf('*/');
    if (end < 0) return span('c', line);
    out += span('c', line.slice(0, end + 2));
    i = end + 2;
    state.block = false;
  }
  const re = /(\/\*.*?(?:\*\/|$))|(\/\/.*$)|(#.*$)|("(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?|`(?:[^`\\]|\\.)*`?)|(\b\d[\d_.]*(?:e[+-]?\d+)?[a-z%]*\b)|([A-Za-z_$][\w$]*)(?=\s*\()|([A-Za-z_$][\w$]*)/g;
  re.lastIndex = i;
  let m;
  let last = i;
  while ((m = re.exec(line))) {
    if (m.index > last) out += esc(line.slice(last, m.index));
    if (m[1]) { out += span('c', m[1]); if (!m[1].endsWith('*/')) state.block = true; }
    else if (m[2]) out += span('c', m[2]);
    else if (m[3]) out += hash ? span('c', m[3]) : esc(m[3]);
    else if (m[4]) out += span('s', m[4]);
    else if (m[5]) out += span('n', m[5]);
    else if (m[6]) out += JS_KW.test(m[6]) ? span('k', m[6]) : span('f', m[6]);
    else if (m[7]) out += JS_KW.test(m[7]) ? span('k', m[7]) : esc(m[7]);
    last = re.lastIndex;
    if (m[0] === '') re.lastIndex++;
  }
  return out + esc(line.slice(last));
}

function css(line, state) {
  if (state.block) {
    const end = line.indexOf('*/');
    if (end < 0) return span('c', line);
    state.block = false;
    return span('c', line.slice(0, end + 2)) + css(line.slice(end + 2), state);
  }
  const c = line.indexOf('/*');
  if (c >= 0) {
    const end = line.indexOf('*/', c + 2);
    if (end < 0) state.block = true;
    return css(line.slice(0, c), state) + span('c', end < 0 ? line.slice(c) : line.slice(c, end + 2)) + (end < 0 ? '' : css(line.slice(end + 2), state));
  }
  // property: value;  or  selector {
  const pm = /^(\s*)(--?[\w-]+)(\s*:\s*)([^;{}]*)(;?\s*\}?\s*)$/.exec(line);
  if (pm && !/[{]/.test(line)) return esc(pm[1]) + span('p', pm[2]) + esc(pm[3]) + value(pm[4]) + esc(pm[5]);
  return line.replace(/([^{}]+)(\{?)/g, (all, sel, brace) => (sel.trim() ? span(/^\s*@/.test(sel) ? 'k' : 't', sel) : esc(sel)) + esc(brace));
}

function value(v) {
  return v.replace(/("[^"]*"?|'[^']*'?)|(#[0-9a-fA-F]{3,8}\b)|(-?\d*\.?\d+(?:px|rem|em|%|vh|vw|s|ms|deg|fr)?)|([\w-]+)(?=\()|([^"'#\d\w-]+|[\w-]+)/g,
    (all, str, hex, num, fn, rest) => str ? span('s', str) : hex ? span('n', hex) : num ? span('n', num) : fn ? span('f', fn) : esc(rest || all));
}

function html(line, state) {
  if (state.inCss) {
    if (/<\/style/i.test(line)) state.inCss = false;
    else return css(line, state);
  }
  if (state.inJs) {
    if (/<\/script/i.test(line)) state.inJs = false;
    else return code(line, state);
  }
  if (state.comment) {
    const end = line.indexOf('-->');
    if (end < 0) return span('c', line);
    state.comment = false;
    return span('c', line.slice(0, end + 3)) + html(line.slice(end + 3), state);
  }
  let out = '';
  const re = /(<!--[\s\S]*?(?:-->|$))|(<\/?)([\w:-]+)([^>]*?)(\/?>|$)|([^<]+|<)/g;
  let m;
  while ((m = re.exec(line))) {
    if (m[0] === '') { re.lastIndex++; continue; }
    if (m[1]) { out += span('c', m[1]); if (!m[1].endsWith('-->')) state.comment = true; continue; }
    if (m[3]) {
      out += esc(m[2]) + span('t', m[3]) + m[4].replace(/([\w:@.-]+)(=)?("[^"]*"?|'[^']*'?|[^\s>]+)?/g,
        (all, name, eq, val) => span('a', name) + (eq ? esc(eq) : '') + (val ? span('s', val) : '')) + esc(m[5]);
      if (m[2] === '<' && /^style$/i.test(m[3]) && m[5]) state.inCss = true;
      if (m[2] === '<' && /^script$/i.test(m[3]) && m[5] && !/src=/.test(m[4])) state.inJs = true;
      continue;
    }
    out += esc(m[6] || '');
  }
  return out;
}

function json(line) {
  return line.replace(/("(?:[^"\\]|\\.)*"?)(\s*:)?|(-?\d+\.?\d*(?:e[+-]?\d+)?)|\b(true|false|null)\b|([^"\d\w-]+|[\w-]+)/g,
    (all, str, colon, num, kw, rest) => str ? span(colon ? 'p' : 's', str) + (colon ? esc(colon) : '') : num ? span('n', num) : kw ? span('k', kw) : esc(rest || all));
}

function md(line) {
  if (/^\s*#/.test(line)) return span('k', line);
  if (/^\s*[-*] /.test(line)) return span('p', line.match(/^\s*[-*] /)[0]) + esc(line.replace(/^\s*[-*] /, ''));
  return esc(line).replace(/`([^`]+)`/g, '<i class="s">`$1`</i>');
}

/** highlighter(lang): { line(text) for a finished line (advances multi-line state), peek(text) for the line still
 *  being typed (leaves the state alone, so it can be called every frame). */
export function highlighter(lang) {
  let state = {};
  const fn = { html, css, json, md, js: code, py: (l, s) => code(l, s, true), sh: (l, s) => code(l, s, true), yaml: (l, s) => code(l, s, true), sql: code }[lang];
  return {
    line(text) { return fn ? fn(text, state) : esc(text); },
    peek(text) { return fn ? fn(text, Object.assign({}, state)) : esc(text); },
    reset() { state = {}; },
  };
}

export { esc };
