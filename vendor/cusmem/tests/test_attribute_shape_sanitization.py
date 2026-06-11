from pydantic import BaseModel

from graphiti_core.utils.maintenance.attribute_utils import apply_capped_attributes


class ProductAttributes(BaseModel):
    product_type: str | None = None
    notes: list[str] | None = None


def test_string_attribute_accepts_compact_json_when_llm_returns_dict():
    merged, dropped = apply_capped_attributes(
        {'product_type': {'value': '铁路信号设备', 'confidence': 0.98}},
        ProductAttributes,
        {},
        merge_mode='overlay',
    )

    assert dropped == set()
    assert merged['product_type'] == '{"value":"铁路信号设备","confidence":0.98}'
    assert ProductAttributes(**merged).product_type == (
        '{"value":"铁路信号设备","confidence":0.98}'
    )
