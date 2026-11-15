"""Render two accessible, static layouts of one illustrative graph. No live data."""
import json
from pathlib import Path

DATA = json.loads((Path(__file__).resolve().parents[1] / 'src/network.json').read_text())


def render_mesh(layout):
    plan = DATA[layout]
    positions = plan['positions']
    prefix = 'mesh-' + layout
    view = ' '.join(map(str, plan['viewBox']))
    def path(nodes):
        return 'M' + ' L'.join(','.join(map(str, positions[n])) for n in nodes)
    parts = [f'<svg class="chat-mesh chat-mesh-{layout}" viewBox="{view}" role="img" aria-labelledby="{prefix}-title {prefix}-desc">',
             f'<title id="{prefix}-title">Two conversation partners connected through eight equal peers</title>',
             f'<desc id="{prefix}-desc">An illustrative decentralised network with cross-links and two routes that share no intermediate peer. Every peer can be removed without disconnecting the endpoints in this illustration. Not live routing or a claim of implemented failover.</desc>']
    # Draw every edge first. A dark under-stroke creates separation from scenery.
    # This supplied layout has no interior edge crossings, except at real nodes.
    for a, b in DATA['edges']:
        d = path([a, b])
        parts.extend([f'<path class="mesh-underlay" d="{d}"/>', f'<path class="mesh-edge" data-edge="{a}-{b}" d="{d}"/>'])
    for i, route in enumerate(DATA['example_routes'], 1):
        parts.append(f'<path class="mesh-route mesh-route-{i}" data-route="{i}" d="{path(route)}"/>')
    radius = 3.5 if layout == 'desktop' else 5
    for node in DATA['peer_nodes']:
        x, y = positions[node]
        label_x = x if layout == 'desktop' else x + 15
        parts.extend([f'<g data-peer="{node}"><title>Participating peer {node}</title><circle class="mesh-peer" cx="{x}" cy="{y}" r="{radius}"/>',
                      f'<text class="mesh-node-letter" x="{label_x}" y="{y+5}">{node}</text></g>'])
        parts.append(f'<path class="mesh-spark" d="M{x-9},{y}h18 M{x},{y-9}v18"/>')
    width = 150 if layout == 'desktop' else 112
    for node, label in [('S', 'Your device'), ('T', 'Their device')]:
        x, y = positions[node]
        if layout == 'desktop':
            parts.append(f'<g data-endpoint="{node}"><title>{label}</title><circle class="mesh-peer" cx="{x}" cy="{y}" r="3.5"/></g>')
            continue
        parts.extend([f'<g data-endpoint="{node}"><rect class="mesh-endpoint" x="{x-width/2}" y="{y-30}" width="{width}" height="62" rx="5"/>',
                      f'<path d="M{x-13},{y-22}h26v17h-26z m0,0 13,10 13,-10" fill="none" stroke="#f6d498" stroke-width="1.5"/>',
                      f'<text class="mesh-device-label" x="{x}" y="{y+18}">{label}</text></g>'])
    if layout == 'desktop':
        # One sealed envelope on one valid route, never a broadcast or live status.
        a,b = (positions[node] for node in DATA['example_routes'][0][2:4])
        x,y = ((a[i]+b[i])/2 for i in (0,1))
        parts.append(f'<g class="mesh-message" aria-hidden="true" transform="translate({x} {y}) rotate(7)"><rect x="-23" y="-15" width="46" height="30" rx="1"/><path d="M-23,-15 0,2 23,-15 M-23,15 -6,-1 M23,15 6,-1"/><circle cx="0" cy="1" r="4"/></g>')
    parts.append('</svg>')
    return ''.join(parts)
