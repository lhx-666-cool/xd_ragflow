from __future__ import annotations

from collections import defaultdict

from .models import ConceptGraph, GraphRelation
from .utils import log, stable_id


class GraphInferenceEngine:
    def __init__(self, config: dict) -> None:
        ic = config.get("inference", {})
        self.enabled = bool(ic.get("enabled", True))

        tc = ic.get("transitive_closure", {})
        self.tc_enabled = bool(tc.get("enabled", True))
        self.transitive_types: set[str] = set(tc.get("relation_types", []))
        self.cross_type_follow: dict[str, set[str]] = {}
        for rule in tc.get("cross_type_rules", []):
            source = rule.get("source", "")
            follow = rule.get("follow", "")
            if source and follow:
                self.cross_type_follow.setdefault(source, set()).add(follow)

        rc = ic.get("reverse_completion", {})
        self.rc_enabled = bool(rc.get("enabled", True))
        self.reverse_types: set[str] = set(rc.get("relation_types", []))
        self.reverse_map: dict[str, str] = dict(rc.get("reverse_map", {}))

    def infer(self, graph: ConceptGraph) -> ConceptGraph:
        if not self.enabled:
            return graph

        existing: set[tuple[str, str, str]] = {
            (r.head_entity_id, r.relation, r.tail_entity_id)
            for r in graph.relations
        }
        before = len(graph.relations)

        if self.tc_enabled and self.transitive_types:
            log("inference", "Running transitive closure inference...")
            self._transitive_closure(graph, existing)

        if self.rc_enabled and self.reverse_types:
            log("inference", "Running reverse relation completion...")
            self._reverse_completion(graph, existing)

        added = len(graph.relations) - before
        log("inference", f"Inferred {added} new edges ({before} -> {len(graph.relations)})")
        return graph

    def _transitive_closure(
        self,
        graph: ConceptGraph,
        existing: set[tuple[str, str, str]],
    ) -> None:
        adj: dict[str, dict[str, list[str]]] = {
            t: defaultdict(list) for t in self.transitive_types
        }
        names: dict[str, str] = {}
        for r in graph.relations:
            if r.relation in self.transitive_types:
                adj[r.relation][r.head_entity_id].append(r.tail_entity_id)
            names[r.head_entity_id] = r.head
            names[r.tail_entity_id] = r.tail

        added: list[GraphRelation] = []

        for rel_type in sorted(self.transitive_types):
            combined: dict[str, list[str]] = {}
            follow_types = {rel_type}
            if rel_type in self.cross_type_follow:
                follow_types |= self.cross_type_follow[rel_type]
            for t in follow_types:
                for eid, nbrs in adj.get(t, {}).items():
                    combined.setdefault(eid, []).extend(nbrs)

            if not combined:
                continue

            start_entities = set(adj.get(rel_type, {}).keys())
            if not start_entities:
                continue
            for start_id in sorted(start_entities):
                visited: set[str] = {start_id}
                queue: list[tuple[str, int]] = [(start_id, 0)]
                while queue:
                    curr, depth = queue.pop(0)
                    for nbr in combined.get(curr, []):
                        if nbr in visited:
                            continue
                        visited.add(nbr)
                        nd = depth + 1
                        queue.append((nbr, nd))
                        if nd >= 2:
                            key = (start_id, rel_type, nbr)
                            if key not in existing:
                                existing.add(key)
                                rid = stable_id(
                                    "relation",
                                    f"{start_id}|{rel_type}|{nbr}|tc",
                                )
                                added.append(
                                    GraphRelation(
                                        relation_id=rid,
                                        head_entity_id=start_id,
                                        tail_entity_id=nbr,
                                        head=names.get(start_id, ""),
                                        relation=rel_type,
                                        tail=names.get(nbr, ""),
                                        source_node_ids=[],
                                        evidences=[],
                                        raw_relation_ids=[],
                                        is_inferred=True,
                                    )
                                )

        if added:
            graph.relations.extend(added)
            graph.relations.sort(
                key=lambda r: (r.head.lower(), r.relation, r.tail.lower()),
            )

    def _reverse_completion(
        self,
        graph: ConceptGraph,
        existing: set[tuple[str, str, str]],
    ) -> None:
        added: list[GraphRelation] = []
        current_relations = list(graph.relations)

        for r in current_relations:
            if r.relation not in self.reverse_types:
                continue

            rev_type = self.reverse_map.get(r.relation)
            if rev_type is None:
                continue
            rev_key = (r.tail_entity_id, rev_type, r.head_entity_id)
            if rev_key in existing:
                continue

            existing.add(rev_key)
            rid = stable_id(
                "relation",
                f"{r.tail_entity_id}|{rev_type}|{r.head_entity_id}|rev",
            )
            added.append(
                GraphRelation(
                    relation_id=rid,
                    head_entity_id=r.tail_entity_id,
                    tail_entity_id=r.head_entity_id,
                    head=r.tail,
                    relation=rev_type,
                    tail=r.head,
                    source_node_ids=[],
                    evidences=[],
                    raw_relation_ids=[],
                    is_inferred=True,
                )
            )

        if added:
            graph.relations.extend(added)
            graph.relations.sort(
                key=lambda r: (r.head.lower(), r.relation, r.tail.lower()),
            )
