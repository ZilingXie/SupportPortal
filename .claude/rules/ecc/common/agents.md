# Agent Delegation Guidelines

## When to Use Agents

- **planner**: Complex multi-step features, before writing code
- **architect**: System design decisions, technology choices
- **code-reviewer**: Before finalizing PRs
- **security-reviewer**: Auth, payments, user input handling
- **python-reviewer**: Python-specific code review
- **fastapi-reviewer**: FastAPI-specific PR review
- **database-reviewer**: Schema changes, query optimization
- **tdd-guide**: When writing tests first is required
- **build-error-resolver**: Build/import/type-check failures
- **refactor-cleaner**: Dead code removal, deduplication
- **doc-updater**: Documentation sync with code changes
- **docs-lookup**: Finding API references and documentation

## Delegation Rules

- Delegate parallelizable research to multiple agents
- Use the right specialist for each domain
- Don't use agents for trivial one-line changes
- Review agent output before committing
