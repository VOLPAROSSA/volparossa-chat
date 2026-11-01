# Chat website review — 1 November 2026

## Result

All nine sections are implemented and visually reviewed in actual Chromium and
Firefox renders. The salon artwork uses standalone masters, the original third
panel is replaced, and the corrected mesh remains complete on mobile. The
shared theme, original marks, nav and footer are pinned and verified unchanged.
No Signal/Core runtime, DNS, CNAME or repository-security change was made.

The user explicitly requested publication. The package's preview-only delivery
wording is therefore superseded; the reviewed main revision is released through
the explicit Pages workflow after CI, then verified live before central registry
activation. The central documentation dependency is already published through
[PR #16](https://github.com/VOLPAROSSA/volparossa.github.io/pull/16).

## Browser and accessibility checks

Thirteen automated profiles passed, plus a final high-density mobile check:

- Chromium: 320×740, 390×844, 768×1024, 1440×900, 1920×1080, 844×390.
- Firefox: 390×844, 1440×900 and 2560×1440.
- JavaScript disabled, 200% text at 1440, reflow at 320 CSS px / 225 px height,
  and intentionally blocked images. Full topology remains available throughout.
- Final Chromium mobile at 390×844 and DPR 3: original-mark display derivatives,
  all lazy images, keyboard disclosure activation, zero axe violations and budgets.
- Skip link, focus transfer, native mobile menu, Escape/outside-click handling,
  all local anchors and assets, no overflow/covered heading, complete graph,
  reduced-motion scrolling, no console errors or unexpected external requests.
- axe WCAG 2/2.1/2.2 A/AA: **zero violations** at desktop and mobile.
  Gradients and copy were also visually reviewed for legibility over photography.

WebKit could not start: this host lacks GTK4, Graphene, HarfBuzz ICU, Manette,
Hyphen and GLESv2 libraries. No WebKit result is claimed. The image-blocked test
intentionally records failed image requests; meaningful HTML/SVG remains present.

Visual refinements made after browser inspection: replace round paper masks
with continuous gradients; increase hero clearance for enlarged navigation;
preserve all three cabinets on mobile; expose the archive boxes above the
explanatory fragment labels; retain the complete fox in ordinary desktop/mobile
compositions. All 13 profiles passed after these layout corrections. A later
lossless Signal-mark display-size optimization was checked at DPR 3 and with
Lighthouse; it changes neither layout nor shared branding source bytes.

## Measurements

Lighthouse **13.6.0**, local Python server, mobile 412×823, DPR 1.75, simulated
150 ms RTT / 1.638 Mbps and 4× CPU slowdown:

| Metric | Measured |
| --- | --- |
| Performance | **98** |
| Accessibility / best practices | **100 / 100** |
| LCP | **2.40 s** |
| FCP | **1.35 s** |
| CLS | **0.000032** |
| Total blocking time | **31 ms** |
| Audit's loaded bytes | **364,732 bytes** |

The preview intentionally has `noindex,nofollow`, causing the preview SEO score
of 66. Production instead emits indexable metadata, its exact canonical and a
one-page sitemap. This lab measurement is not a production field-performance
guarantee. The earlier 2.55 s LCP prompted removal of the unnecessarily large,
eager original Signal mark from visitor requests; its original bytes are retained.

The final DPR 3 mobile check transfers **322,126 bytes initially**, **666,587 bytes
after scrolling the full page**. The 768 px mobile hero is 56,832 bytes AVIF,
110,960 bytes WebP, 135,976 bytes JPEG. The largest measured desktop image load
was 833,659 bytes at 2560 px. Shared menu JavaScript is **291 bytes gzip**;
additional Chat JavaScript is **zero**. These remain within the stated budgets.

## Source/build checks

- Offline preview, production and alternate nested-prefix builds/checks pass.
- Production built twice: **185 files with identical hashes**.
- Six topology checks pass: valid connected edges, both endpoint branches,
  valid internally disjoint routes, every single-peer removal retains a route,
  both renderings preserve all nodes/edges, and genuine multi-column mobile layout.
- Existing Python source checks: **42 run, 41 passed, 1 pre-existing optional
  pinned-checkout check skipped**; no source checkout was downloaded for it.
- Existing Node backup source-contract checks: **14 passed**. These are synthetic
  connector checks, not a new native Signal/Core or provider-availability trial.
- Shared theme/file hashes, original icon/mark provenance, local licenses,
  supporting credits, project canonical/OG/sitemap, asset dimensions and budgets
  are checked. Build previews contain no masters or source instructions.

## Render evidence

These files are actual browser screenshots, converted to WebP for review:

- [Desktop page](desktop-full.webp) — Chromium, 1440 px.
- [Mobile page](mobile-full.webp) — Chromium, 390 px.
- [Desktop mesh](desktop-mesh.webp) and [mobile mesh](mobile-mesh.webp) — all eight
  peers and 15 connections, both routes visible simultaneously.
- [Wide Firefox page](firefox-wide.webp) — rendered at 2560 px; review image scaled
  to 1440 px. [200% text](text-200-percent.webp) retains text and navigation.
- [Browser measurements](browser-results.json), [axe results](accessibility.json),
  [high-density mobile](high-density-mobile.json), [Lighthouse conditions/results](lighthouse-mobile.json).

The initial Signal website fetch returned HTTP 403. The brief's permitted
HTML/CSS illustration fallback is used and visibly labelled. Original Signal
mark/license sources are pinned; the synthetic call participants are labelled.
No capture or mock-up is presented as a working VOLPAROSSA client.
