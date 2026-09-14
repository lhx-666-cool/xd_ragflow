from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import ConceptGraph
from .utils import ensure_dir, write_json


class ConceptGraphExporter:
    def export(
        self,
        output_dir: Path,
        concept_graph: ConceptGraph,
        docnode_mapping: dict[str, dict[str, list[str]]],
    ) -> None:
        ensure_dir(output_dir)
        write_json(output_dir / "concept_kg.json", concept_graph.to_dict())
        write_json(output_dir / "docnode_to_concepts.json", docnode_mapping)
        self._write_entity_table(output_dir / "entity_table.csv", concept_graph)
        self._write_relation_table(output_dir / "relation_table.csv", concept_graph)
        self._write_graphml(output_dir / "concept_kg.graphml", concept_graph)

    def _write_entity_table(self, path: Path, concept_graph: ConceptGraph) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "entity_id",
                    "canonical_name",
                    "type",
                    "aliases",
                    "merged_source_node_ids",
                    "merged_definitions",
                    "merged_raw_entity_ids",
                ],
            )
            writer.writeheader()
            for entity in concept_graph.entities:
                writer.writerow(
                    {
                        "entity_id": entity.entity_id,
                        "canonical_name": entity.canonical_name,
                        "type": entity.entity_type,
                        "aliases": " | ".join(entity.aliases),
                        "merged_source_node_ids": " | ".join(entity.merged_source_node_ids),
                        "merged_definitions": " | ".join(entity.merged_definitions),
                        "merged_raw_entity_ids": " | ".join(entity.merged_raw_entity_ids),
                    }
                )

    def _write_relation_table(self, path: Path, concept_graph: ConceptGraph) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "relation_id",
                    "head_entity_id",
                    "head",
                    "relation",
                    "tail_entity_id",
                    "tail",
                    "source_node_ids",
                    "evidences",
                    "raw_relation_ids",
                    "is_inferred",
                ],
            )
            writer.writeheader()
            for relation in concept_graph.relations:
                writer.writerow(
                    {
                        "relation_id": relation.relation_id,
                        "head_entity_id": relation.head_entity_id,
                        "head": relation.head,
                        "relation": relation.relation,
                        "tail_entity_id": relation.tail_entity_id,
                        "tail": relation.tail,
                        "source_node_ids": " | ".join(relation.source_node_ids),
                        "evidences": " | ".join(relation.evidences),
                        "raw_relation_ids": " | ".join(relation.raw_relation_ids),
                        "is_inferred": str(relation.is_inferred),
                    }
                )

    def _write_graphml(self, path: Path, concept_graph: ConceptGraph) -> None:
        graphml = ET.Element(
            "graphml",
            attrib={"xmlns": "http://graphml.graphdrawing.org/xmlns"},
        )
        for key_id, attr_name, attr_for in [
            ("d0", "label", "node"),
            ("d1", "type", "node"),
            ("d2", "sources", "node"),
            ("d3", "relation", "edge"),
            ("d4", "evidence", "edge"),
            ("d5", "sources", "edge"),
            ("d6", "is_inferred", "edge"),
        ]:
            ET.SubElement(
                graphml,
                "key",
                attrib={"id": key_id, "for": attr_for, "attr.name": attr_name, "attr.type": "string"},
            )

        graph = ET.SubElement(graphml, "graph", attrib={"edgedefault": "directed"})
        for entity in concept_graph.entities:
            node = ET.SubElement(graph, "node", attrib={"id": entity.entity_id})
            ET.SubElement(node, "data", attrib={"key": "d0"}).text = entity.canonical_name
            ET.SubElement(node, "data", attrib={"key": "d1"}).text = entity.entity_type
            ET.SubElement(node, "data", attrib={"key": "d2"}).text = " | ".join(entity.merged_source_node_ids)

        for relation in concept_graph.relations:
            edge = ET.SubElement(
                graph,
                "edge",
                attrib={
                    "id": relation.relation_id,
                    "source": relation.head_entity_id,
                    "target": relation.tail_entity_id,
                },
            )
            ET.SubElement(edge, "data", attrib={"key": "d3"}).text = relation.relation
            ET.SubElement(edge, "data", attrib={"key": "d4"}).text = " | ".join(relation.evidences)
            ET.SubElement(edge, "data", attrib={"key": "d5"}).text = " | ".join(relation.source_node_ids)
            ET.SubElement(edge, "data", attrib={"key": "d6"}).text = str(relation.is_inferred).lower()

        ET.ElementTree(graphml).write(path, encoding="utf-8", xml_declaration=True)
