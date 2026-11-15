"""Checks only the public illustration, never runtime routing or availability."""
import unittest
from collections import deque
import xml.etree.ElementTree as ET
from itertools import combinations
from mesh import DATA, render_mesh


class IllustrationTopology(unittest.TestCase):
    def neighbours(self, removed=None):
        graph={node:set() for node in DATA['endpoints']+DATA['peer_nodes'] if node!=removed}
        for a,b in DATA['edges']:
            if removed not in (a,b):
                graph[a].add(b);graph[b].add(a)
        return graph

    def reachable(self, graph):
        seen={'S'};queue=deque(['S'])
        while queue:
            for node in graph[queue.popleft()]-seen:
                seen.add(node);queue.append(node)
        return seen

    def test_connected_and_valid_edges(self):
        nodes=set(DATA['endpoints']+DATA['peer_nodes'])
        self.assertEqual(len(DATA['peer_nodes']),8)
        self.assertEqual(len({frozenset(edge) for edge in DATA['edges']}),len(DATA['edges']))
        for a,b in DATA['edges']:
            self.assertNotEqual(a,b);self.assertIn(a,nodes);self.assertIn(b,nodes)
        self.assertEqual(self.reachable(self.neighbours()),nodes)

    def test_endpoint_branches(self):
        graph=self.neighbours()
        for node in DATA['endpoints']:
            self.assertGreaterEqual(len(graph[node]),2)

    def test_routes_are_valid_and_internally_disjoint(self):
        graph=self.neighbours();routes=DATA['example_routes']
        self.assertEqual(len(routes),2)
        for route in routes:
            self.assertEqual((route[0],route[-1]),('S','T'))
            self.assertEqual(len(set(route)),len(route))
            for a,b in zip(route,route[1:]):self.assertIn(b,graph[a])
        self.assertFalse(set(routes[0][1:-1])&set(routes[1][1:-1]))

    def test_no_mandatory_intermediate_peer(self):
        for node in DATA['peer_nodes']:
            with self.subTest(removed=node):self.assertIn('T',self.reachable(self.neighbours(node)))

    def test_both_renderings_preserve_all_nodes_and_edges(self):
        expected={'-'.join(edge) for edge in DATA['edges']}
        for layout in ['desktop','mobile']:
            root=ET.fromstring(render_mesh(layout));all_nodes=list(root.iter())
            self.assertEqual({n.get('data-edge') for n in all_nodes if n.get('data-edge')},expected)
            self.assertEqual({n.get('data-peer') for n in all_nodes if n.get('data-peer')},set(DATA['peer_nodes']))
            self.assertEqual({n.get('data-endpoint') for n in all_nodes if n.get('data-endpoint')},{'S','T'})
            self.assertEqual(len([n for n in all_nodes if n.get('data-route')]),2)
            self.assertTrue(root.find('title').text);self.assertTrue(root.find('desc').text)

    def test_mobile_relayouts_in_multiple_columns(self):
        p=DATA['mobile']['positions']
        self.assertGreaterEqual(len({p[n][0] for n in DATA['peer_nodes']}),2)
        self.assertTrue(all(p['S'][1]<p[n][1]<p['T'][1] for n in DATA['peer_nodes']))
        self.assertNotEqual(DATA['mobile'],DATA['desktop'])

    def test_scene_alignment_does_not_create_false_junctions(self):
        def side(a,b,c):
            return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        for layout in ['desktop','mobile']:
            positions=DATA[layout]['positions']
            _,_,width,height=DATA[layout]['viewBox']
            for x,y in positions.values():
                self.assertTrue(0<x<width and 0<y<height)
            for (a,b),(c,d) in combinations(DATA['edges'],2):
                if len({a,b,c,d})<4:
                    continue
                u,v,w,z=[positions[n] for n in (a,b,c,d)]
                with self.subTest(layout=layout,edges=(a,b,c,d)):
                    self.assertFalse(side(u,v,w)*side(u,v,z)<0 and side(w,z,u)*side(w,z,v)<0)


if __name__=='__main__':unittest.main()
