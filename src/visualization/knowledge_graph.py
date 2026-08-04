"""Knowledge Graph visualization using the persistent KnowledgeGraph.

Renders entities and relationships from the KnowledgeGraph data structure
with entity-type color coding, confidence-based edge width, and dashed
edges for user-contributed relationships.

Validates: Requirements 12.1, 12.2, 12.3, 12.4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.graph.knowledge_graph import KnowledgeGraph

# Task 12.2 — entity-type color coding
ENTITY_TYPE_COLORS: dict[str, str] = {
    "Framework":      "#10b981",  # green
    "Capability":     "#f59e0b",  # yellow
    "Language":       "#3b82f6",  # blue
    "Migration_Path": "#8b5cf6",  # purple
    "CI_CD_Tool":     "#6b7280",  # gray
    "Cloud_Provider": "#06b6d4",  # cyan
    "User_Experience":"#f97316",  # orange (fallback)
    "Limitation":     "#ef4444",  # red (fallback)
}

_DEFAULT_COLOR = "#6b7280"


@dataclass
class GraphNode:
    id: str
    label: str
    group: str
    color: str = "#6b7280"
    size: int = 20
    score: int = 0


@dataclass
class GraphEdge:
    source: str
    target: str
    label: str = ""
    strength: float = 1.0
    dashed: bool = False


@dataclass
class KnowledgeGraphData:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


def build_from_persistent_graph(
    knowledge_graph: KnowledgeGraph,
    max_nodes: int = 200,
) -> KnowledgeGraphData:
    """Build visualization data from the persistent KnowledgeGraph.

    Validates: Requirements 12.1, 12.2, 12.3, 12.4

    Args:
        knowledge_graph: The persistent KnowledgeGraph instance.
        max_nodes: Cap on nodes to render (avoids browser overload).

    Returns:
        KnowledgeGraphData ready for render_graph_html().
    """
    graph = KnowledgeGraphData()
    seen_nodes: set[str] = set()

    # Collect entities (capped)
    entities = list(knowledge_graph._entities.values())[:max_nodes]

    for entity in entities:
        if entity.id in seen_nodes:
            continue
        seen_nodes.add(entity.id)

        # Task 12.2 — color by entity type
        color = ENTITY_TYPE_COLORS.get(entity.entity_type.value, _DEFAULT_COLOR)

        graph.nodes.append(GraphNode(
            id=entity.id,
            label=entity.name,
            group=entity.entity_type.value,
            color=color,
            size=24 if entity.entity_type.value == "Framework" else 16,
        ))

    # Collect relationships for visible nodes only
    for source_id, rels in knowledge_graph._adjacency.items():
        if source_id not in seen_nodes:
            continue
        for rel in rels:
            if rel.target_id not in seen_nodes:
                continue
            # Task 12.3 — confidence → edge width (confidence * 4 px)
            # Task 12.4 — dashed for user-contributed edges
            graph.edges.append(GraphEdge(
                source=source_id,
                target=rel.target_id,
                label=rel.relationship_type.value.replace("_", " ").lower(),
                strength=rel.confidence,
                dashed=(rel.source == "user"),
            ))

    return graph


def render_graph_html(graph: KnowledgeGraphData) -> str:
    """Render KnowledgeGraphData as an HTML/vis-network visualization.

    Validates: Requirements 12.1, 12.2, 12.3, 12.4
    """
    nodes_js: list[str] = []
    for node in graph.nodes:
        safe_label = node.label.replace('"', '\\"').replace("\n", "\\n")
        nodes_js.append(
            f'{{"id":"{node.id}","label":"{safe_label}",'
            f'"group":"{node.group}","color":"{node.color}",'
            f'"size":{node.size},"font":{{"size":11,"color":"#333"}}}}'
        )

    edges_js: list[str] = []
    for edge in graph.edges:
        # Task 12.3 — edge width = confidence * 4
        width = max(1, round(edge.strength * 4))
        safe_label = edge.label.replace('"', '\\"')
        dashes_val = "true" if edge.dashed else "false"
        edges_js.append(
            f'{{"from":"{edge.source}","to":"{edge.target}",'
            f'"label":"{safe_label}","width":{width},'
            f'"dashes":{dashes_val},'
            f'"arrows":"to","font":{{"size":9,"color":"#666"}}}}'
        )

    html = f"""
    <div id="kg-vis" style="width:100%;height:520px;border:1px solid #e5e7eb;border-radius:12px;background:#fafafa;"></div>
    <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <script>
        var nodes = new vis.DataSet([{",".join(nodes_js)}]);
        var edges = new vis.DataSet([{",".join(edges_js)}]);
        var container = document.getElementById('kg-vis');
        var options = {{
            physics: {{barnesHut: {{gravitationalConstant: -3000, springLength: 140}}}},
            edges: {{smooth: {{type: "continuous"}}}},
            interaction: {{hover: true}}
        }};
        new vis.Network(container, {{nodes: nodes, edges: edges}}, options);
    </script>
    """
    return html


# ── Legacy helpers kept for backward compatibility ────────────────────

def build_knowledge_graph(profile, matrix, kb, top_n: int = 5) -> KnowledgeGraphData:
    """Legacy builder from evaluation results (kept for backward compatibility)."""
    graph = KnowledgeGraphData()
    graph.nodes.append(GraphNode(
        id="project",
        label=f"Your Project\n({profile.primary_language})",
        group="profile",
        color="#2563eb",
        size=35,
    ))
    for fw_score in matrix.rankings[:top_n]:
        color = ENTITY_TYPE_COLORS.get("Framework", "#10b981")
        graph.nodes.append(GraphNode(
            id=f"fw_{fw_score.framework.lower().replace(' ', '_')}",
            label=f"{fw_score.framework}\n({fw_score.overall_score}/100)",
            group="framework",
            color=color,
            size=int(15 + fw_score.overall_score / 5),
            score=int(fw_score.overall_score),
        ))
    return graph
