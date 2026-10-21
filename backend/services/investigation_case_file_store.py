"""Case file metadata only. No URLs, credentials or binary content belong here."""
from __future__ import annotations

import copy
from pathlib import PurePath

from psycopg import sql
from psycopg.types.json import Jsonb

FILE_TABLE_DDL = """
    id BIGSERIAL PRIMARY KEY,
    namespace TEXT NOT NULL,
    case_id TEXT NOT NULL,
    source_system TEXT NOT NULL CHECK (source_system IN ('engineer','zendesk','hermes','zac')),
    source_file_id TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    managed_filename TEXT NOT NULL DEFAULT '',
    slack_filename TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 52428800),
    purpose TEXT NOT NULL,
    short_description TEXT NOT NULL DEFAULT '',
    provenance JSONB NOT NULL,
    slack_file_id TEXT,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','available','unavailable')),
    failure_code TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (namespace,case_id,source_system,source_file_id)
"""

MAX_SHORT_DESCRIPTION = 240


def normalize_short_description(value, *, managed_filename: str = '') -> str:
    """Return bounded, display-only metadata; never accept control characters."""
    raw = str(value or '')
    if any(ord(char) < 32 for char in raw if char not in {'\t', '\n', '\r'}):
        raise ValueError('case_file_description_invalid')
    text = ' '.join(raw.split())
    if len(text) > MAX_SHORT_DESCRIPTION:
        raise ValueError('case_file_description_too_long')
    return text or (f'Engineer attachment {managed_filename}'.strip() if managed_filename else '')


def managed_name(case_id, source_system, file_id, original):
    # Preserve a safe final extension, never caller path components.
    extension = PurePath(str(original).replace('\\', '/')).suffix
    if not extension or len(extension) > 16 or not extension[1:].isalnum():
        extension = ''
    return f'{case_id}-{source_system}-{file_id}{extension}'


def _identity(row):
    return tuple(row[k] for k in ('namespace', 'case_id', 'source_system', 'source_file_id'))


class MemoryCaseFileStore:
    def register_case_file(self, **row):
        with self._lock:
            rows = self.__dict__.setdefault('_case_files', {})
            row['namespace'] = self.settings.job_namespace
            existing = next((r for r in rows.values() if _identity(r) == _identity(row)), None)
            if existing:
                return copy.deepcopy(existing)
            fid = len(rows) + 1
            managed = managed_name(row['case_id'], row['source_system'], fid, row['original_filename'])
            row['short_description'] = normalize_short_description(row.get('short_description'), managed_filename=managed)
            row.update(id=fid, managed_filename=managed,
                       state='pending', failure_code=None, version=1, slack_file_id=None, slack_filename='')
            rows[fid] = row
            return copy.deepcopy(row)

    def get_case_file(self, case_id, file_id):
        with self._lock:
            row = self.__dict__.get('_case_files', {}).get(int(file_id))
            return copy.deepcopy(row) if row and row['case_id'] == case_id and row['namespace'] == self.settings.job_namespace else None

    def list_case_files(self, case_id):
        with self._lock:
            return [copy.deepcopy(r) for r in self.__dict__.get('_case_files', {}).values()
                    if r['case_id'] == case_id and r['namespace'] == self.settings.job_namespace]

    def update_case_file(self, case_id, file_id, **fields):
        with self._lock:
            row = self.get_case_file(case_id, file_id)
            if row is None:
                raise ValueError('case_file_not_found')
            row.update(fields)
            self._case_files[int(file_id)] = row
            return copy.deepcopy(row)

    def bind_case_zac_session(self, case_id, session_id):
        with self._lock:
            binding = self._hermes_bindings[(self.settings.job_namespace, case_id)]
            binding['zac_session_id'] = session_id


class PostgresCaseFileStore:
    def register_case_file(self, **row):
        columns = ('case_id', 'source_system', 'source_file_id', 'original_filename', 'content_type', 'size_bytes', 'purpose', 'short_description', 'provenance')
        short_description = normalize_short_description(row.get('short_description'), managed_filename='')
        values = [row[k] for k in columns]
        values[columns.index('short_description')] = short_description
        values[columns.index('provenance')] = Jsonb(row['provenance'])
        with self._connect() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(sql.SQL('INSERT INTO {} (namespace,{}) VALUES (%s,{}) '
                'ON CONFLICT (namespace,case_id,source_system,source_file_id) DO UPDATE SET source_file_id=EXCLUDED.source_file_id RETURNING *').format(
                self._table('investigation_case_files'), sql.SQL(',').join(map(sql.Identifier, columns)),
                sql.SQL(',').join(sql.Placeholder() for _ in columns)),
                (self.settings.job_namespace, *values))
            result = dict(cursor.fetchone())
            if not result['managed_filename']:
                cursor.execute(sql.SQL('UPDATE {} SET managed_filename=%s WHERE id=%s RETURNING *').format(self._table('investigation_case_files')),
                    (managed_name(result['case_id'], result['source_system'], result['id'], result['original_filename']), result['id']))
                result = dict(cursor.fetchone())
            if not result['short_description']:
                cursor.execute(sql.SQL('UPDATE {} SET short_description=%s WHERE id=%s RETURNING *').format(self._table('investigation_case_files')),
                    (normalize_short_description('', managed_filename=result['managed_filename']), result['id']))
                result = dict(cursor.fetchone())
        return result

    def get_case_file(self, case_id, file_id):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('SELECT * FROM {} WHERE namespace=%s AND case_id=%s AND id=%s').format(self._table('investigation_case_files')),
                (self.settings.job_namespace, case_id, int(file_id)))
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_case_files(self, case_id):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('SELECT * FROM {} WHERE namespace=%s AND case_id=%s ORDER BY id').format(self._table('investigation_case_files')),
                (self.settings.job_namespace, case_id))
            return [dict(row) for row in cursor.fetchall()]

    def update_case_file(self, case_id, file_id, **fields):
        allowed = {'slack_file_id', 'slack_filename', 'content_type', 'state', 'failure_code', 'provenance', 'short_description'}
        if not fields or set(fields) - allowed:
            raise ValueError('case_file_update_invalid')
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('UPDATE {} SET {},updated_at=NOW() WHERE namespace=%s AND case_id=%s AND id=%s RETURNING *').format(
                self._table('investigation_case_files'), sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in fields)),
                (*(Jsonb(v) if k == 'provenance' else v for k, v in fields.items()), self.settings.job_namespace, case_id, int(file_id)))
            row = cursor.fetchone()
        if not row:
            raise ValueError('case_file_not_found')
        return dict(row)

    def bind_case_zac_session(self, case_id, session_id):
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL('UPDATE {} SET zac_session_id=%s WHERE namespace=%s AND zendesk_ticket_id=%s').format(self._table('automation_hermes_case_bindings')),
                (session_id, self.settings.job_namespace, case_id))
