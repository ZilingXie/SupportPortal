# Coding Style (Common)

## General Principles

- **KISS**: Keep It Simple, Stupid — prefer simple solutions over clever ones
- **DRY**: Don't Repeat Yourself — but tolerate 3 occurrences before abstracting
- **YAGNI**: You Aren't Gonna Need It — don't build for hypothetical futures

## File Organization

- Many small, focused files over few large files
- Each module should do one thing well
- Group by feature, not by file type

## Functions

- One function = one responsibility
- Keep functions small (~30 lines target)
- Prefer pure functions where possible
- Use descriptive names that say what the function does

## Naming

- Names should answer "what" not "how"
- Avoid abbreviations unless universally understood
- Boolean variables: `is_`, `has_`, `should_` prefix
- Functions: verb or verb+noun (`get_user`, `calculate_total`)

## Comments

- Comment WHY, not WHAT (the code says what)
- Remove commented-out code (git history preserves it)
- Keep comments current with code changes

## Immutability

- Prefer creating new objects over mutating existing ones
- Use immutable data structures where practical
- Avoid shared mutable state
