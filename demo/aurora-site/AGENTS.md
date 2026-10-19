# Aurora Lodge website

This folder is the website for **Aurora Lodge**, a small luxury retreat of glass cabins in Finnish Lapland where guests
sleep under the northern lights. It starts empty: the site is built here, by voice, live on a TV.

## What "done" looks like

A site a top design agency would be proud to ship: calm, confident, expensive-feeling.

- Premium typography: a characterful display serif for headlines (for example Fraunces or Cormorant Garamond) and a clean
  sans for text (for example Inter or Manrope), from Google Fonts with `display=swap` and good system fallbacks.
  Big, tightly set headlines; generous line height and spacing in body text.
- A strong art direction. Visuals are made with CSS and inline SVG (gradients, shapes, landscapes, glows, grain), not
  stock photos: there is no network while building, so never reference remote images.
- Excellent spacing and rhythm, a clear grid, a restrained palette with one accent. Real, specific copy: no lorem ipsum.
- Tasteful motion: subtle entrances and gentle ambient movement. Respect `prefers-reduced-motion`.
- Fully responsive, from a phone (390 px) to a TV (1920 px). No horizontal scrolling at any width.
- Accessible: one `h1`, landmarks (`header`, `main`, `footer`), alt text, visible focus, sufficient contrast.

## How it's built

- Plain static files, no build step and no packages: `index.html`, `styles.css`, `script.js`, and `assets/` for SVGs.
- Write `index.html` and `styles.css` early so the page can be shown while you work, then refine them.
- Your code is replayed live on the TV as you write it, so keep it readable: normal formatting, one CSS declaration
  per line, short well-named sections. Never minify.
- Every in-page link (`href="#..."`) must point to an element that exists.
- It is also shown on an older Fire TV: animate only `transform` and `opacity`, avoid large `filter: blur()` and
  `backdrop-filter` areas, keep JavaScript small, and never run work in a tight loop.

## Checks

`firedev run test` runs `tests/site.check.mjs` in a real browser: it loads the page at phone, tablet, laptop and TV sizes
and checks for script errors, horizontal overflow, broken in-page links, missing alt text and the basics above. Run it
when you finish a change, and fix anything it reports. Don't edit the checks to make them pass.
