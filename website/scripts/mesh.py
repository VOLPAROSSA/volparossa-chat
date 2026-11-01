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
    for node in DATA['peer_nodes']:
        x, y = positions[node]
        parts.extend([f'<g data-peer="{node}"><circle class="mesh-peer" cx="{x}" cy="{y}" r="23"/>',
                      f'<text class="mesh-node-letter" x="{x}" y="{y+5}">{node}</text></g>'])
    width = 138 if layout == 'desktop' else 112
    for node, label in [('S', 'Your device'), ('T', 'Their device')]:
        x, y = positions[node]
        parts.extend([f'<g data-endpoint="{node}"><rect class="mesh-endpoint" x="{x-width/2}" y="{y-35}" width="{width}" height="70" rx="7"/>',
                      f'<path d="M{x-13},{y-22}h26v17h-26z m0,0 13,10 13,-10" fill="none" stroke="#f6d498" stroke-width="1.5"/>',
                      f'<text class="mesh-device-label" x="{x}" y="{y+18}">{label}</text></g>'])
    parts.append('</svg>')
    return ''.join(parts)
