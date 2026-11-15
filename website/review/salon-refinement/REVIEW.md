# Salon composition refinement — 15 November 2026

The published Chat page used much taller, more separated sections than the
selected Conversation Salon concept. This correction restores compact illustrated
bands, serif editorial copy, a laptop-shaped message example, a narrow privacy
strip and the two light development panels. It keeps the generous salon hero,
shared theme and original artwork masters.

The independent section-three correction still takes precedence over the
original central-chest concept. The eight equal peers now sit on the eight
pavilion roofs. Smaller brass markers and fine routes replace the large floating
letter circles on desktop. All 15 edges and both internally disjoint routes are
unchanged. Mobile uses its own complete arrangement. A new geometry check rules
out unmarked crossings in either arrangement.

The recovery labels sit below the archive boxes; the three closed cabinets have
a dedicated crop beside the contribution calculation. The paper transitions and
closing panorama retain the illustrated room. At larger text sizes the privacy
and cabinet/calculation layouts wrap rather than squeezing words into columns.

All **50 original paragraphs are retained verbatim**, checked against the parent
revision. Secondary explanations are accessible through native `details` elements.
Development badges, the private-cache/training exclusion, illustration captions,
independent affiliation and the **one-test-machine** recovery boundary remain in
the initial view. No product capability or source-evidence claim has changed.

## Actual rendered comparison

Measured at Chromium 1440×900, default text, disclosures closed:

| Section | Previous height | Refined height |
| --- | ---: | ---: |
| Hero | 738 px | 666 px |
| Signal | 642 px | 501 px |
| Network | 1,385 px | 930 px |
| Calls | 631 px | 548 px |
| Recovery | 1,135 px | 742 px |
| Reciprocal storage | 689 px | 427 px |
| Privacy | 530 px | 207 px |
| Development | 355 px | 307 px |
| Closing | 441 px | 361 px |

The rendered content is about 28% shorter while retaining all nine sections and
all original explanatory paragraphs. Native text and the corrected mesh remain
responsive; this is not a flattened screenshot of the concept.

- [Previous desktop](../desktop-full.webp), [refined desktop](desktop-full.webp).
- [Refined mobile](mobile-full.webp), [wide Firefox](firefox-wide.webp).
- [Desktop network](desktop-network.webp), [complete mobile network](mobile-network.webp).
- [Signal scene](desktop-signal.webp), [recovery scene](desktop-recovery.webp).
- [Expanded explanations](expanded-desktop.webp), [200% text](text-200-percent.webp).

These are browser screenshots converted to WebP; the 2560 px Firefox capture is
scaled to 1440 px for review. The section-height measurements come from fresh
before/after captures at the same viewport.

## Verification

- Offline preview and production builds/checks pass, including pinned theme
  hashes, assets, licenses, relative project paths, metadata and sitemap.
- Seven graph tests pass: original connectivity/routes, endpoint branches,
  no mandatory intermediate peer, full SVG renderings, independent mobile layout,
  and no false geometric junctions after alignment.
- Thirteen browser profiles pass: Chromium 320, 390, 768, 844 landscape, 1440 and
  1920 px; Firefox 390, 1440 and 2560 px; no JavaScript, 200% text, 320 px reflow
  and intentionally blocked images.
- Zero axe A/AA violations at 390 and 1440 px, **both with disclosures closed
  and with every explanation expanded**. Keyboard activation and no-JavaScript
  disclosures pass. No horizontal overflow, header overlap, unexpected external
  requests, console errors or failed assets in the normal profiles.
- Final Chromium 390 px initial transfer: **282,678 bytes**. Largest measured
  image transfer after scrolling: **738,590 bytes** at 2560 px. No Chat JavaScript
  was added; the pinned shared menu remains the only runtime script.
- A local Lighthouse sample during visual QA scored **97 performance, 100
  accessibility and 100 best practices**, with LCP 2.64 s, CLS 0.000032 and TBT
  0 ms. It is a lab sample, not a field-performance measurement. The configuration
  and measured values are in [lighthouse-mobile.json](lighthouse-mobile.json).
- The existing QA setup also needed repair: a committed workspace-specific
  `node_modules` symlink and accidentally renamed browser package identifiers
  prevented a clean install. The symlink is removed, the original package names
  and versions are restored, `npm ci --ignore-scripts` succeeds, and `npm run qa`
  points to the actual runner. No runtime dependencies are shipped to visitors.

The complete measurements are in [browser-results.json](browser-results.json),
[accessibility.json](accessibility.json) and [section-heights.json](section-heights.json).
The November 1 source/provenance review remains the content basis. No new native
Signal/Core trial, shared-theme modification or integration readiness claim is
part of this visual correction.
