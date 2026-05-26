# Testing Guidelines (Common)

## Testing Philosophy

- Tests are a safety net for refactoring and regression prevention
- Write tests that give confidence, not tests that satisfy metrics
- Test behavior, not implementation details
- Fast tests enable fast iteration

## Test Types

| Type | Scope | Speed | When |
|------|-------|-------|------|
| Unit | Single function/class | Fast | Always |
| Integration | Multiple components | Medium | API, DB |
| E2E | Full user flow | Slow | Critical paths |

## Coverage

- Target 80%+ line and branch coverage
- 100% coverage on critical paths (auth, payments, data integrity)
- Don't chase coverage numbers; test what matters

## Best Practices

- Tests must be independent and isolated
- Use descriptive test names: `test_<action>_<condition>_<result>`
- Arrange-Act-Assert pattern for test structure
- One assertion per test (when practical)
- Mock external dependencies; don't mock domain logic
- Test error paths, not just happy paths
- Use factories/fixtures for test data, not production data

## When to Write Tests

- New features: write tests with the implementation
- Bug fixes: write a test that reproduces the bug first
- Refactoring: existing tests must pass before and after

## Test Pyramid

```
       /\
      /E2E\        Few, slow, critical flows
     /------\
    /Integration\  Moderate, API/DB
   /------------\
  /   Unit Tests  \  Many, fast, isolated
 /________________\
```
