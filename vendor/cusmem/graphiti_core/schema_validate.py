"""KAG-lite hard validation — filter entities/edges against schema before Neo4j write."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def validate_against_schema(
    nodes: list[Any],
    edges: list[Any],
    entity_types: dict[str, Any] | None,
    edge_types: dict[str, Any] | None,
    edge_type_map: dict[tuple[str, str], list[str]] | None,
    mode: str = 'strict',
) -> tuple[list[Any], list[Any]]:
    """Filter entities and edges against schema constraints.

    Args:
        nodes: extracted EntityNode list
        edges: extracted EntityEdge list
        entity_types: schema entity type definitions
        edge_types: schema edge type definitions
        edge_type_map: (source_type, target_type) → allowed edge names
        mode: 'strict' (hard filter) or 'lenient' (no-op)

    Returns:
        (filtered_nodes, filtered_edges)
    """
    if mode == 'lenient' or (not entity_types and not edge_types):
        return nodes, edges

    allowed_entity_types = set(entity_types.keys()) if entity_types else set()
    allowed_edge_types = set(edge_types.keys()) if edge_types else set()

    # Filter entities
    kept_nodes = []
    for node in nodes:
        node_types = {label for label in (node.labels or []) if label != 'Entity'}
        if not node_types:
            # Generic Entity only — drop in strict mode
            logger.debug(f'Dropping generic entity: {node.name}')
            continue
        if allowed_entity_types and not node_types & allowed_entity_types:
            logger.debug(f'Dropping entity with unknown types {node_types}: {node.name}')
            continue
        kept_nodes.append(node)

    # Build name → labels map for edge validation
    name_to_labels: dict[str, set[str]] = {}
    for n in kept_nodes:
        name_to_labels[n.name] = {label for label in (n.labels or []) if label != 'Entity'}

    # Filter edges
    kept_edges = []
    for edge in edges:
        # Edge type check
        if allowed_edge_types and edge.name not in allowed_edge_types:
            logger.debug(f'Dropping unknown edge type: {edge.name}')
            continue

        # Source/target type check via edge_type_map. Also drops edges whose
        # source or target was filtered out in the entity step.
        if edge_type_map and edge.source_node_uuid and edge.target_node_uuid:
            src_node = next((n for n in kept_nodes if n.uuid == edge.source_node_uuid), None)
            tgt_node = next((n for n in kept_nodes if n.uuid == edge.target_node_uuid), None)
            if src_node is None or tgt_node is None:
                logger.debug(f'Dropping edge {edge.name}: source/target filtered out')
                continue
            if src_node and tgt_node:
                src_types = {label for label in (src_node.labels or []) if label != 'Entity'}
                tgt_types = {label for label in (tgt_node.labels or []) if label != 'Entity'}
                if src_types and tgt_types:
                    allowed = _check_edge_type_map(edge.name, src_types, tgt_types, edge_type_map)
                    if not allowed:
                        logger.debug(
                            f'Dropping edge {edge.name}: {src_types}→{tgt_types} not in map'
                        )
                        continue

        kept_edges.append(edge)

    logger.info(
        f'Schema validation ({mode}): {len(nodes)}→{len(kept_nodes)} nodes, '
        f'{len(edges)}→{len(kept_edges)} edges'
    )
    return kept_nodes, kept_edges


def _check_edge_type_map(
    edge_name: str,
    src_types: set[str],
    tgt_types: set[str],
    edge_type_map: dict[tuple[str, str], list[str]],
) -> bool:
    """Check if any (src, tgt) pair allows this edge type."""
    for st in src_types:
        for tt in tgt_types:
            if (st, tt) in edge_type_map and edge_name in edge_type_map[(st, tt)]:
                return True
            if ('*', tt) in edge_type_map and edge_name in edge_type_map[('*', tt)]:
                return True
            if (st, '*') in edge_type_map and edge_name in edge_type_map[(st, '*')]:
                return True
    # If map has no matching rules, allow (don't block)
    return not bool(edge_type_map)
