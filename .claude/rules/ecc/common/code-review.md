# Code Review Guidelines

## Review Priorities

1. **Correctness**: Does the code do what it claims? Are edge cases handled?
2. **Security**: SQL injection, XSS, secrets exposure, auth bypass
3. **Performance**: N+1 queries, unnecessary allocations, blocking calls
4. **Maintainability**: Clear naming, appropriate abstractions, no dead code
5. **Style**: Consistent with project conventions

## Review Checklist

- [ ] Public functions have type annotations
- [ ] Error paths are handled, not swallowed
- [ ] No hardcoded secrets or magic numbers
- [ ] Input is validated at system boundaries
- [ ] Database queries are parameterized
- [ ] New code has appropriate tests
- [ ] No debug output left in production paths
- [ ] Dependencies are justified and up to date

## What to Skip

- Subjective style preferences
- Issues in untouched code (unless security-critical)
- "Perfect is the enemy of good" refactors
