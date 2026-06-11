#!/usr/bin/env python3
"""GB/T 25338 标准文档录入入口.

实体类型和关系类型来自用户 schema 配置文件，不在此入口硬编码。
默认 schema 路径配置在 graphrag_config.yaml 的 schema.path。
"""

import asyncio
import os
from pathlib import Path

from graphiti_rag import GraphRAG


async def main():
    # 从 graphrag_config.yaml 加载连接、模型和用户 schema。
    from graphiti_rag.config_loader import load_config

    config = load_config()

    print('城市轨道交通标准知识图谱 — Schema 配置:')
    print(f'  schema_path: {config.schema_path or "未配置"}')
    print(f'  schema_mode: {config.schema_mode}')
    print('\n实体类型:')
    for name, model in (config.entity_types or {}).items():
        print(f'  {name}: {model.__doc__}')
    print(f'\n关系类型: {list((config.edge_types or {}).keys())}')
    print('\n开始录入 GB/T 25338.1-2019 ...\n')

    default_input = Path(__file__).with_name('GBT+25338.1-2019.txt')
    input_path = os.environ.get('GRAPHRAG_INPUT', str(default_input))

    rag = GraphRAG(config)
    result = await rag.ingest([input_path])
    print(f'\n录入完成: {result["files"]} 文件 → {result["chunks"]} chunks')

    await rag.close()


if __name__ == '__main__':
    asyncio.run(main())
