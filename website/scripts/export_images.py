#!/usr/bin/env python3
"""Maintainer-only responsive exports; Pillow 12.3.0. Production builds are offline."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets/chat'
COMPOSITIONS = {
 'hero-salon': {'desktop': [0,0,1643,957], 'mobile': [640,0,1643,957]},
 'signal-worktable': {'desktop': [0,0,1774,887]},
 'mesh-courtyard': {'desktop': [0,0,1642,958], 'mobile': [440,0,1190,958]},
 'calls-surround': {'desktop': [0,0,1774,887]},
 'call-portraits': {'desktop': [0,0,1920,819]},
 'recovery-table': {'desktop': [0,0,1774,887], 'mobile': [500,0,1774,887]},
 'storage-cabinets': {'desktop': [0,0,1774,887], 'mobile': [630,220,1620,887]},
 'closing-fox': {'desktop': [0,0,1852,849], 'mobile': [700,0,1852,849]}
}


def inventory():
    manifest = {'compositions': COMPOSITIONS, 'files': {}}
    for directory in [ROOT/'masters', ROOT/'assets']:
        for p in sorted(directory.rglob('*')):
            if not p.is_file():
                continue
            record = {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            if p.suffix in ['.png','.jpg','.webp','.avif']:
                with Image.open(p) as im:
                    record['width'], record['height'] = im.size
            manifest['files'][p.relative_to(ROOT).as_posix()] = record
    (ROOT/'asset-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


def export():
    OUT.mkdir(parents=True,exist_ok=True)
    variants = {}
    for name, plan in COMPOSITIONS.items():
        original = Image.open(ROOT/'masters'/f'{name}.png').convert('RGB')
        variants[name] = {}
        for kind, box in plan.items():
            assert box[2] <= original.width and box[3] <= original.height
            source = original.crop(box)
            widths = sorted(set([min(w,source.width) for w in ([480,768] if kind=='mobile' else [640,960,1440,source.width])]))
            variants[name][kind] = []
            for width in widths:
                size = (width,round(width*source.height/source.width))
                im = source.resize(size,Image.Resampling.LANCZOS)
                stem = f'{name}-{kind}-{width}'
                im.save(OUT/(stem+'.avif'),quality=57,speed=6)
                im.save(OUT/(stem+'.webp'),quality=83,method=6)
                im.save(OUT/(stem+'.jpg'),quality=84,optimize=True,progressive=True)
                variants[name][kind].append({'stem':stem,'width':size[0],'height':size[1]})
    (OUT/'variants.json').write_text(json.dumps(variants,indent=2)+'\n')
    # Keep the official original untouched; derive small lossless display sizes.
    mark=Image.open(ROOT/'assets/upstream/signal-app.png')
    for width in [128,256]:
        mark.resize((width,width),Image.Resampling.LANCZOS).save(ROOT/f'assets/upstream/signal-app-{width}.png',optimize=True)
    inventory()
    print('Exported actual crops and dimensions; original masters preserved.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory-only',action='store_true')
    args=parser.parse_args()
    inventory() if args.inventory_only else export()
