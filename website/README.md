# VOLPAROSSA Chat website

The Conversation Salon is a static, English product page at
`https://volparossa.org/volparossa-chat/`. It describes the current Signal
integration honestly: private backup recovery has scoped desktop evidence;
decentralised messaging, calling, mobile integration and contribution management
remain in development. Building the website never builds or runs Signal or Core.

## Build and preview

Python 3.11+ is the only build dependency. All source, fonts, artwork exports,
original marks and theme files are local; visitors make no third-party requests.

```sh
python3 website/scripts/build_site.py
python3 website/scripts/check_site.py
python3 website/scripts/test_mesh.py
python3 -m http.server 8780 --directory website/preview
```

Open `http://localhost:8780/volparossa-chat/`. The default output is `noindex,nofollow`
and has no sitemap. A production build requires an explicit flag:

```sh
python3 website/scripts/build_site.py --production --output website/dist/volparossa-chat
python3 website/scripts/check_site.py --production --output website/dist/volparossa-chat
```

Canonical origin and prefix are configurable with `--origin` and `--base-path`;
all local assets use relative paths. Only the generated project directory is
published, never masters, build instructions, tests or source documents.
The builder refuses to erase directories it does not own.

## Shared theme and content

`vendor/volparossa-theme` is the unmodified source-only export of theme 1.0.3
from central commit `179bf4893d7c3e48ffd51ce52377d302f98ae743`.
The build verifies every SHA-256 in `theme-version.json`. Header, footer,
navigation, original branding, typography and menu come from that export.
Chat composition lives separately in `src/chat.css`; product copy in `src/index.html`.

`src/content.json` is the one configuration point for the documentation CTA.
The central `/docs/#chat` entry was added in
[central PR #16](https://github.com/VOLPAROSSA/volparossa.github.io/pull/16)
and verified live before that destination replaced the GitHub fallback.
The application registry remains unpublished until the product page is deployed
and verified; activation is a separate central change.

## Illustrations and exact topology

`masters/` retains original generated scenes, prompts and the social typography
composition. `asset-manifest.json` records original dimensions, crop boxes, export
dimensions, byte counts and SHA-256 hashes. Re-export with Pillow 12.3.0:

```sh
python3 website/scripts/export_images.py
CHROME_PATH=/usr/bin/chromium node website/scripts/render_social.mjs
python3 website/scripts/export_images.py --inventory-only
```

The social renderer needs the local preview and the optional QA dependencies.
No scene is an enlarged crop of the concept page. The Signal message panel is
an explicitly labelled HTML/CSS example with invented messages. The call panel
contains three generated fictional adults and noninteractive decorative controls.
Original Signal assets, source hashes and notices are under `assets/upstream/`.
The public [asset credits](assets/chat/credits.html) explain provenance and
independent affiliation. No working-client claim follows from either illustration.

`src/network.json` preserves the supplied topology: two endpoints, eight peers,
15 edges and two internally node-disjoint example routes. `scripts/mesh.py`
renders desktop and mobile SVGs from that one dataset. All edges remain visible;
mobile has an independent vertical arrangement, never a cropped desktop graph.
No animation or JavaScript is needed. The topology tests establish properties
of this illustration only, not runtime failover, service readiness or availability.

## Validation and publication

Optional tools in `package-lock.json`: Playwright 1.62.1, axe 4.14.0 and
Lighthouse 13.6.0. Use `npm ci --prefix website` to install them locally when
needed. Browser QA runs against the project subpath:

```sh
CHROME_PATH=/usr/bin/chromium node website/scripts/browser_check.mjs
```

`QA_ENGINES` optionally adds `webkit` when its operating-system dependencies
exist. `review/QA.md` records actual results and unavailable engines. Website CI
builds a noindex preview artifact and checks local assets, metadata, theme hashes
and topology. Existing source-contract CI is retained unchanged.

The user explicitly authorized publication after completion, superseding the
attached package's preview-only delivery wording. Production publication uses
the manually dispatched **Chat website** workflow on reviewed `main`, with
Pages/OIDC permissions confined to its deploy job. GitHub Pages was already
configured for Actions. No DNS, CNAME, repository protection or product runtime
changes are part of this release.
