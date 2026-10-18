/**
 * Line, area, bar and pie charts drawn as plain SVG: no chart library, so they render quickly on older Fire TV
 * hardware. Built with DOM calls (never markup strings), so labels from an agent are always just text.
 */

const NS = 'http://www.w3.org/2000/svg';
const TONE = { good: '#6fd6a0', bad: '#f2776b', warn: '#f2c25b', info: '#8fb8ff', accent: '#c9a7ff', neutral: '#f4f1ea' };
const PALETTE = ['#8fb8ff', '#6fd6a0', '#f2c25b', '#c9a7ff', '#f2776b', '#7fd8e6'];

function el(name, attrs, text) {
  const e = document.createElementNS(NS, name);
  for (const k of Object.keys(attrs || {})) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  return e;
}

export function seriesColor(s, i) {
  return (s && s.tone && TONE[s.tone]) || PALETTE[i % PALETTE.length];
}

/** Normalises series points to [{x, y, label}] using shared category labels when given. */
export function normalise(series, labels) {
  return series.map((s) => {
    const pts = Array.isArray(s.points) ? s.points : [];
    return pts.map((p, i) => {
      if (Array.isArray(p)) return { x: p[0], y: Number(p[1]), label: labelFor(p[0], labels, i) };
      return { x: i, y: Number(p), label: labels && labels[i] !== undefined ? String(labels[i]) : String(i + 1) };
    }).filter((p) => isFinite(p.y));
  });
}

function labelFor(x, labels, i) {
  if (labels && labels[i] !== undefined) return String(labels[i]);
  if (typeof x === 'number' && x > 1e9) {
    const d = new Date(x * 1000);
    return d.getDate() + '/' + (d.getMonth() + 1) + ' ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }
  return String(x);
}

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function short(n) {
  const a = Math.abs(n);
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M';
  if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k';
  return String(Math.round(n * 100) / 100);
}

export function renderChart(kind, series, opts) {
  const w = opts.width;
  const h = opts.height;
  const svg = el('svg', { viewBox: '0 0 ' + w + ' ' + h, width: w, height: h, class: 'g-chart-svg', role: 'img', style: 'width:' + w + 'px;height:' + h + 'px' });
  const data = normalise(series, opts.labels);
  if (!data.length || data.every((d) => !d.length)) {
    svg.appendChild(el('text', { x: w / 2, y: h / 2, 'text-anchor': 'middle', class: 'g-axis' }, 'No data yet'));
    return svg;
  }
  if (kind === 'pie') return pie(svg, series, data[0], w, h);
  const pad = { l: 92, r: 24, t: 20, b: 58 };
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;
  let maxY = 0;
  let minY = 0;
  for (const d of data) for (const p of d) { maxY = Math.max(maxY, p.y); minY = Math.min(minY, p.y); }
  maxY = niceMax(maxY);
  const n = Math.max.apply(null, data.map((d) => d.length));
  const yOf = (y) => pad.t + ih - ((y - minY) / (maxY - minY || 1)) * ih;
  // grid + y labels
  for (let i = 0; i <= 4; i++) {
    const v = minY + ((maxY - minY) * i) / 4;
    const y = yOf(v);
    svg.appendChild(el('line', { x1: pad.l, x2: w - pad.r, y1: y, y2: y, class: 'g-gridline' }));
    svg.appendChild(el('text', { x: pad.l - 14, y: y + 8, 'text-anchor': 'end', class: 'g-axis' }, short(v) + (i === 4 && opts.unit ? ' ' + opts.unit : '')));
  }
  // x labels: at most 6
  const ref = data.reduce((a, b) => (b.length > a.length ? b : a), data[0]);
  const step = Math.max(1, Math.ceil(n / 6));
  const xOf = kind === 'bar'
    ? (i) => pad.l + (iw / n) * (i + 0.5)
    : (i) => pad.l + (n <= 1 ? iw / 2 : (iw * i) / (n - 1));
  for (let i = 0; i < ref.length; i += step) {
    svg.appendChild(el('text', { x: xOf(i), y: h - 18, 'text-anchor': 'middle', class: 'g-axis' }, ref[i].label.slice(0, 14)));
  }
  if (kind === 'bar') {
    const groupW = (iw / n) * 0.72;
    const bw = groupW / data.length;
    data.forEach((d, si) => d.forEach((p, i) => {
      const x = xOf(i) - groupW / 2 + si * bw;
      const y = yOf(Math.max(0, p.y));
      const y0 = yOf(Math.min(0, p.y));
      svg.appendChild(el('rect', { x: x + 2, y, width: Math.max(2, bw - 4), height: Math.max(2, y0 - y), rx: 6, fill: seriesColor(series[si], si), class: 'g-bar' }));
    }));
  } else {
    data.forEach((d, si) => {
      if (!d.length) return;
      const pts = d.map((p, i) => xOf(i) + ',' + yOf(p.y));
      const color = seriesColor(series[si], si);
      if (kind === 'area') {
        const area = 'M' + xOf(0) + ',' + yOf(Math.max(0, minY)) + ' L' + pts.join(' L') + ' L' + xOf(d.length - 1) + ',' + yOf(Math.max(0, minY)) + ' Z';
        svg.appendChild(el('path', { d: area, fill: color, opacity: 0.18 }));
      }
      svg.appendChild(el('polyline', { points: pts.join(' '), fill: 'none', stroke: color, 'stroke-width': 5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', class: 'g-line' }));
      if (d.length <= 40) d.forEach((p, i) => svg.appendChild(el('circle', { cx: xOf(i), cy: yOf(p.y), r: 6, fill: color })));
    });
  }
  return svg;
}

function pie(svg, series, d, w, h) {
  const total = d.reduce((a, p) => a + Math.max(0, p.y), 0) || 1;
  const r = Math.min(h / 2 - 16, w / 4);
  const cx = r + 24;
  const cy = h / 2;
  let a0 = -Math.PI / 2;
  d.forEach((p, i) => {
    const frac = Math.max(0, p.y) / total;
    const a1 = a0 + frac * Math.PI * 2;
    const color = PALETTE[i % PALETTE.length];
    if (frac >= 0.9999) svg.appendChild(el('circle', { cx, cy, r, fill: color }));
    else if (frac > 0) {
      const large = a1 - a0 > Math.PI ? 1 : 0;
      const path = 'M' + cx + ',' + cy + ' L' + (cx + r * Math.cos(a0)) + ',' + (cy + r * Math.sin(a0)) +
        ' A' + r + ',' + r + ' 0 ' + large + ' 1 ' + (cx + r * Math.cos(a1)) + ',' + (cy + r * Math.sin(a1)) + ' Z';
      svg.appendChild(el('path', { d: path, fill: color, stroke: '#09090b', 'stroke-width': 4 }));
    }
    const ly = 40 + i * 52;
    if (ly < h - 10) {
      svg.appendChild(el('rect', { x: cx + r + 48, y: ly - 22, width: 26, height: 26, rx: 6, fill: color }));
      svg.appendChild(el('text', { x: cx + r + 90, y: ly, class: 'g-legend' }, p.label.slice(0, 22) + '  ' + Math.round(frac * 100) + '%'));
    }
    a0 = a1;
  });
  svg.appendChild(el('circle', { cx, cy, r: r * 0.52, fill: '#101013' }));
  return svg;
}
