"""
The PostgreSQL backend, plus one thing: it creates its own schema.

Production runs in a named schema rather than ``public`` (``search_path=pyconng``),
which works fine on a database where somebody has already run ``CREATE SCHEMA``.
On a database where nobody has, *nothing* works -- and in a way that reads as a
mystery rather than a missing step:

    MigrationSchemaMissing: Unable to create the django_migrations table
    (no schema has been selected to create in)

That is the first statement ``migrate`` issues, so a fresh database cannot be
bootstrapped at all: not a new staging environment, not a restore into an empty
cluster, and not Django's own test database, which is built from nothing every run.
It is also why the whole suite had only ever been run against SQLite.

It cannot be fixed with a migration, because the migration recorder is the thing
that fails. It has to happen when the connection opens, before any other SQL, which
is what this does. ``CREATE SCHEMA IF NOT EXISTS`` is issued once per process per
schema; the name is qualified, so it works even though ``search_path`` is at that
moment pointing at nothing.

The alternative -- ``search_path=pyconng,public`` -- is worse, and quietly: with the
schema missing, every table lands in ``public`` and the deployment ends up split
across two schemas with nothing reporting it.
"""

import logging

from django.db.backends.postgresql import base

logger = logging.getLogger(__name__)

#: Schemas already ensured in this process, keyed by (database name, schema). A
#: second ``CREATE SCHEMA IF NOT EXISTS`` would be harmless but it is a round trip
#: on every new connection, and behind pgbouncer there are a lot of those.
#:
#: Keyed on the database *name* rather than the alias, which is the bug this
#: comment exists to stop somebody reintroducing: the alias stays ``default``
#: while Django swaps NAME from ``pyconng_db`` to ``test_pyconng_db``, so an
#: alias-keyed cache reports the schema as ensured in a database it has never
#: connected to -- and the test run fails exactly as it did before the fix.
_ensured = set()


class DatabaseWrapper(base.DatabaseWrapper):
    """PostgreSQL, with the configured schema created on first connect."""

    def get_connection_params(self):
        params = super().get_connection_params()
        # Consumed here and never handed to libpq, which would reject it.
        self._schema_to_create = params.pop("create_schema", None)
        return params

    def get_new_connection(self, conn_params):
        connection = super().get_new_connection(conn_params)
        schema = getattr(self, "_schema_to_create", None)
        if schema:
            self._ensure_schema(connection, schema, conn_params.get("dbname"))
        return connection

    def _ensure_schema(self, connection, schema, dbname):
        """
        Create the schema if it is not there. Never fails the connection.

        A role without CREATE on the database cannot do this, and that is a
        legitimate way to run: somebody else provisioned the schema and this
        account only reads and writes inside it. In that case the statement fails,
        the schema is already present, and carrying on is correct. If it is
        genuinely absent the next statement fails anyway, with its own message.
        """
        key = (dbname, schema)
        if key in _ensured:
            return
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'CREATE SCHEMA IF NOT EXISTS "{self._quote(schema)}"'
                )
            if not getattr(connection, "autocommit", False):
                connection.commit()
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.debug("Could not ensure schema %r: %s", schema, exc)
        _ensured.add(key)

    @staticmethod
    def _quote(schema):
        """
        Escape a schema name for a quoted identifier.

        Interpolated rather than parameterised because DDL cannot take a bind
        parameter for an identifier. The value comes from settings, not a request,
        but doubling any quote costs nothing and means a surprising schema name is
        a syntax error rather than an injection.
        """
        return str(schema).replace('"', '""')
