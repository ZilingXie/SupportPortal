from graphiti_core.prompts.extract_nodes import extract_message, extract_text

ENTITY_TYPES_CONTEXT = [
    {'entity_type_id': 0, 'entity_type_name': 'Entity', 'entity_type_description': '泛型实体'},
    {'entity_type_id': 1, 'entity_type_name': 'Standard', 'entity_type_description': '标准'},
    {'entity_type_id': 2, 'entity_type_name': 'Product', 'entity_type_description': '产品/设备'},
    {
        'entity_type_id': 3,
        'entity_type_name': 'TechnicalTerm',
        'entity_type_description': '技术术语',
    },
    {
        'entity_type_id': 4,
        'entity_type_name': 'TechnicalParameter',
        'entity_type_description': '技术参数/数值',
    },
    {'entity_type_id': 5, 'entity_type_name': 'Rating', 'entity_type_description': '防护/性能等级'},
    {
        'entity_type_id': 6,
        'entity_type_name': 'TestItem',
        'entity_type_description': '检测/测试项目',
    },
    {
        'entity_type_id': 7,
        'entity_type_name': 'Organization',
        'entity_type_description': '机构/组织',
    },
    {'entity_type_id': 8, 'entity_type_name': 'Section', 'entity_type_description': '标准章节条款'},
    {
        'entity_type_id': 9,
        'entity_type_name': 'EnvironmentalCondition',
        'entity_type_description': '环境条件',
    },
]


def _context():
    return {
        'entity_types': ENTITY_TYPES_CONTEXT,
        'previous_episodes': [],
        'episode_content': '第5.4节规定转辙机应满足IP66，湿度不大于90%。',
        'custom_extraction_instructions': '',
    }


def test_text_prompt_does_not_forbid_numeric_parameter_entities():
    user_prompt = extract_text(_context())[1].content

    assert '数字数值' not in user_prompt
    assert '参数值' in user_prompt
    assert 'TechnicalParameter' in user_prompt
    assert 'EnvironmentalCondition' in user_prompt


def test_message_prompt_warns_entity_type_ids_are_from_runtime_schema():
    user_prompt = extract_message(_context())[1].content

    assert 'entity_type_id 必须使用上方实体类型定义中的实际编号' in user_prompt
    assert '不要照抄示例编号' in user_prompt
