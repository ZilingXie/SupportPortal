from graphiti_rag.schema_loader import load_graph_schema


def test_load_graph_schema_builds_models_and_edge_map(tmp_path):
    schema_file = tmp_path / 'schema.yaml'
    schema_file.write_text(
        """
entity_types:
  Product:
    description: "产品"
    ontology: "铁路信号 -> 设备"
    properties:
      official_name:
        type: string
        description: "规范名称"
      synonyms:
        type: list[string]
        description: "同义词"
  TechnicalParameter:
    description: "参数"

edge_types:
  SPECIFIES:
    description: "规定"
    source_types: ["Product"]
    target_types: ["TechnicalParameter"]
""",
        encoding='utf-8',
    )

    loaded = load_graph_schema(schema_file)

    assert list(loaded.entity_types) == ['Product', 'TechnicalParameter']
    assert list(loaded.edge_types) == ['SPECIFIES']
    assert ('Product', 'TechnicalParameter') in loaded.edge_type_map
    assert loaded.edge_type_map[('Product', 'TechnicalParameter')] == ['SPECIFIES']
    assert '领域本体' in (loaded.entity_types['Product'].__doc__ or '')
    assert set(loaded.entity_types['Product'].model_fields) == {'official_name', 'synonyms'}


def test_load_graph_schema_rejects_invalid_type_names(tmp_path):
    schema_file = tmp_path / 'schema.yaml'
    schema_file.write_text(
        """
entity_types:
  Invalid-Name:
    description: "bad"
""",
        encoding='utf-8',
    )

    try:
        load_graph_schema(schema_file)
    except ValueError as exc:
        assert 'Invalid entity type name' in str(exc)
    else:
        raise AssertionError('expected invalid schema type name to fail')
