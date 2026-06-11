from graphiti_rag.schema_loader import load_graph_schema


def test_gbt_schema_covers_standard_authorship_roles():
    loaded = load_graph_schema('schemas/gbt25338.yaml')

    assert 'Person' in loaded.entity_types
    assert 'DRAFTED_BY' in loaded.edge_types
    assert 'PROPOSED_BY' in loaded.edge_types
    assert ('Standard', 'Organization') in loaded.edge_type_map
    assert 'DRAFTED_BY' in loaded.edge_type_map[('Standard', 'Organization')]
    assert 'PROPOSED_BY' in loaded.edge_type_map[('Standard', 'Organization')]
    assert ('Standard', 'Person') in loaded.edge_type_map
    assert 'DRAFTED_BY' in loaded.edge_type_map[('Standard', 'Person')]
