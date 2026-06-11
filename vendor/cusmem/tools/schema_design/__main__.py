from __future__ import annotations

import argparse
import os
from pathlib import Path

from tools.schema_design.pipeline import SchemaDesignPipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run schema design automation.')
    parser.add_argument('--input', required=True, type=Path, help='Input file or directory')
    parser.add_argument('--output', required=True, type=Path, help='Output directory')
    parser.add_argument('--mode', choices=('auto', 'interactive'), default='auto')
    parser.add_argument('--skip-stages', default='', help='Comma-separated stage numbers to skip')
    parser.add_argument('--only-stages', default='', help='Comma-separated stage numbers to run (overrides skip-stages, emits only requested stages)')
    parser.add_argument(
        '--llm-base-url', default='', help='LLM API base URL (default: $DEEPSEEK_BASE_URL or https://api.deepseek.com/v1)'
    )
    parser.add_argument('--llm-model', default='', help='LLM model name (default: $DEEPSEEK_MODEL or deepseek-chat)')
    parser.add_argument('--llm-api-key', default='', help='LLM API key (default: $DEEPSEEK_API_KEY)')
    parser.add_argument('--no-llm', action='store_true', help='Disable LLM, use rule-based fallback')
    parser.add_argument('--no-gates', action='store_true', help='Disable quality gates (for testing/debugging)')
    parser.add_argument('--max-fix-rounds', type=int, default=3, help='Maximum auto-fix rounds (default: 3)')
    parser.add_argument('--confidence-threshold', type=float, default=0.65,
                        help='Minimum confidence to skip human review (default: 0.65)')
    parser.add_argument('--candidate-pool', type=Path, default=None,
                        help='Path to candidate_pool.yaml for pool selection mode')
    parser.add_argument('--selection-mode', choices=('pool', 'legacy'), default='legacy',
                        help='Schema design mode: pool (select from candidate pool) or legacy (auto-discovery)')
    parser.add_argument('--llm', action='append', default=[], help='Extra LLM config key=value entries')
    args = parser.parse_args(argv)

    skip_stages = {int(item.strip()) for item in args.skip_stages.split(',') if item.strip()}
    only_stages: set[int] | None = None
    if args.only_stages:
        only_stages = {int(item.strip()) for item in args.only_stages.split(',') if item.strip()}
    llm_config: dict[str, str] | None = None if args.no_llm else {}
    if not args.no_llm:
        if args.llm_base_url:
            llm_config['base_url'] = args.llm_base_url
        elif 'DEEPSEEK_BASE_URL' not in os.environ:
            llm_config.setdefault('base_url', 'https://api.deepseek.com/v1')
        if args.llm_model:
            llm_config['model'] = args.llm_model
        elif 'DEEPSEEK_MODEL' not in os.environ:
            llm_config.setdefault('model', 'deepseek-chat')
        if args.llm_api_key:
            llm_config['api_key'] = args.llm_api_key
        for item in args.llm:
            if '=' in item:
                key, value = item.split('=', 1)
                llm_config[key] = value

    # Determine selection mode
    selection_mode = args.selection_mode
    if args.candidate_pool and args.selection_mode == 'legacy':
        selection_mode = 'pool'  # auto-enable pool mode when candidate pool is provided

    pipeline = SchemaDesignPipeline(
        args.input,
        args.output,
        mode=args.mode,
        skip_stages=skip_stages,
        llm_config=llm_config,
        max_fix_rounds=args.max_fix_rounds,
        candidate_pool=args.candidate_pool,
        selection_mode=selection_mode,
    )
    pipeline.run(only_stages=only_stages, no_gates=args.no_gates)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
