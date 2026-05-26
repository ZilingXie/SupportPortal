---
name: database-migrations
description: Database migration best practices for schema changes, data migrations, rollbacks, and zero-downtime deployments across PostgreSQL and common ORMs.
origin: ECC
---

# Database Migration Patterns

Safe, reversible database schema changes for production systems.

## When to Activate

- Creating or altering database tables
- Adding/removing columns or indexes
- Running data migrations (backfill, transform)
- Planning zero-downtime schema changes
- Setting up migration tooling for a new project

## Core Principles

1. **Every change is a migration** — never alter production databases manually
2. **Migrations are forward-only in production** — rollbacks use new forward migrations
3. **Schema and data migrations are separate** — never mix DDL and DML in one migration
4. **Test migrations against production-sized data** — a migration that works on 100 rows may lock on 10M
5. **Migrations are immutable once deployed** — never edit a migration that has run in production

## Migration Safety Checklist

Before applying any migration:

- [ ] Migration has both UP and DOWN (or is explicitly marked irreversible)
- [ ] No full table locks on large tables (use concurrent operations)
- [ ] New columns have defaults or are nullable (never add NOT NULL without default)
- [ ] Indexes created concurrently (not inline with CREATE TABLE for existing tables)
- [ ] Data backfill is a separate migration from schema change
- [ ] Tested against a copy of production data
- [ ] Rollback plan documented

## PostgreSQL Patterns

### Adding a Column Safely

```sql
-- GOOD: Nullable column, no lock
ALTER TABLE users ADD COLUMN avatar_url TEXT;

-- GOOD: Column with default (Postgres 11+ is instant, no rewrite)
ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true;

-- BAD: NOT NULL without default on existing table (requires full rewrite)
ALTER TABLE users ADD COLUMN role TEXT NOT NULL;
```

### Adding an Index Without Downtime

```sql
-- BAD: Blocks writes on large tables
CREATE INDEX idx_users_email ON users (email);

-- GOOD: Non-blocking, allows concurrent writes
CREATE INDEX CONCURRENTLY idx_users_email ON users (email);
```

### Renaming a Column (Zero-Downtime)

Never rename directly. Use expand-contract:

```sql
-- Step 1: Add new column
ALTER TABLE users ADD COLUMN display_name TEXT;

-- Step 2: Backfill
UPDATE users SET display_name = username WHERE display_name IS NULL;

-- Step 3: Update app to read/write both columns, deploy

-- Step 4: Drop old column
ALTER TABLE users DROP COLUMN username;
```

### Large Data Migrations

Use batch updates with progress tracking. Never update all rows in one transaction.

## Django (Python)

```bash
python manage.py makemigrations   # Generate from model changes
python manage.py migrate          # Apply
python manage.py showmigrations   # Status
python manage.py makemigrations --empty app_name -n description  # Custom SQL
```

### Data Migration

```python
from django.db import migrations

def backfill_display_names(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    batch_size = 5000
    users = User.objects.filter(display_name="")
    while users.exists():
        batch = list(users[:batch_size])
        for user in batch:
            user.display_name = user.username
        User.objects.bulk_update(batch, ["display_name"], batch_size=batch_size)

class Migration(migrations.Migration):
    dependencies = [("accounts", "0015_add_display_name")]
    operations = [migrations.RunPython(backfill_display_names, migrations.RunPython.noop)]
```

## Zero-Downtime Migration Strategy

```
Phase 1: EXPAND
  - Add new column/table (nullable or with default)
  - Deploy: app writes to BOTH old and new
  - Backfill existing data

Phase 2: MIGRATE
  - Deploy: app reads from NEW, writes to BOTH
  - Verify data consistency

Phase 3: CONTRACT
  - Deploy: app only uses NEW
  - Drop old column/table in separate migration
```

## Anti-Patterns

| Anti-Pattern | Better Approach |
|---|---|
| Manual SQL in production | Always use migration files |
| Editing deployed migrations | Create new migration instead |
| NOT NULL without default | Add nullable, backfill, then add constraint |
| Inline index on large table | CREATE INDEX CONCURRENTLY |
| Schema + data in one migration | Separate migrations |
| Dropping column before removing code | Remove code first, drop column next deploy |
