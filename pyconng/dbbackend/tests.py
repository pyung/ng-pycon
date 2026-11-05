"""
Tests for the schema-creating PostgreSQL backend.

These run without a Postgres server, deliberately: the thing worth pinning is the
bookkeeping, not the DDL. The DDL was proved once by dropping the schema and
watching ``migrate`` rebuild it, and it is proved on every run by the suite itself,
which cannot create its own test database unless this works.

The cache-key test is the important one. It is here because the first version of
this backend keyed on the connection alias, which stays ``default`` while Django
swaps the database name to ``test_pyconng_db`` -- so the schema was reported as
already created in a database nothing had ever connected to, and the test run
failed in precisely the way the backend exists to prevent.
"""

from unittest import mock

from django.test import SimpleTestCase

from pyconng.dbbackend import base


class ConnectionParamsTests(SimpleTestCase):
    def test_create_schema_never_reaches_libpq(self):
        """
        It is our own key, not a connection parameter. Left in place, psycopg
        rejects the whole connection.
        """
        wrapper = base.DatabaseWrapper.__new__(base.DatabaseWrapper)
        with mock.patch.object(
            base.base.DatabaseWrapper,
            "get_connection_params",
            return_value={"dbname": "pyconng_db", "create_schema": "pyconng"},
        ):
            params = base.DatabaseWrapper.get_connection_params(wrapper)
        self.assertNotIn("create_schema", params)
        self.assertEqual(wrapper._schema_to_create, "pyconng")

    def test_no_schema_configured_is_not_an_error(self):
        """SQLite, or a Postgres deployment that just uses ``public``."""
        wrapper = base.DatabaseWrapper.__new__(base.DatabaseWrapper)
        with mock.patch.object(
            base.base.DatabaseWrapper,
            "get_connection_params",
            return_value={"dbname": "pyconng_db"},
        ):
            base.DatabaseWrapper.get_connection_params(wrapper)
        self.assertIsNone(wrapper._schema_to_create)


class EnsureSchemaTests(SimpleTestCase):
    def setUp(self):
        self._saved = set(base._ensured)
        base._ensured.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        base._ensured.clear()
        base._ensured.update(self._saved)

    def _wrapper(self, alias="default"):
        wrapper = base.DatabaseWrapper.__new__(base.DatabaseWrapper)
        wrapper.alias = alias
        return wrapper

    def _connection(self):
        connection = mock.MagicMock()
        connection.autocommit = False
        return connection

    def test_the_schema_is_created_once_per_database(self):
        wrapper, connection = self._wrapper(), self._connection()
        wrapper._ensure_schema(connection, "pyconng", "pyconng_db")
        wrapper._ensure_schema(connection, "pyconng", "pyconng_db")
        cursor = connection.cursor.return_value.__enter__.return_value
        self.assertEqual(cursor.execute.call_count, 1)
        self.assertIn(
            'CREATE SCHEMA IF NOT EXISTS "pyconng"', cursor.execute.call_args[0][0]
        )

    def test_the_test_database_gets_its_own_schema(self):
        """
        The regression. Same alias, different database: the second call must still
        issue the statement, because the test database is empty.
        """
        wrapper, connection = self._wrapper(), self._connection()
        wrapper._ensure_schema(connection, "pyconng", "pyconng_db")
        wrapper._ensure_schema(connection, "pyconng", "test_pyconng_db")
        cursor = connection.cursor.return_value.__enter__.return_value
        self.assertEqual(cursor.execute.call_count, 2)

    def test_the_work_is_committed_when_not_autocommitting(self):
        """An uncommitted CREATE SCHEMA is no schema at all."""
        wrapper, connection = self._wrapper(), self._connection()
        wrapper._ensure_schema(connection, "pyconng", "pyconng_db")
        connection.commit.assert_called_once()

    def test_nothing_is_committed_under_autocommit(self):
        wrapper, connection = self._wrapper(), self._connection()
        connection.autocommit = True
        wrapper._ensure_schema(connection, "pyconng", "pyconng_db")
        connection.commit.assert_not_called()

    def test_a_role_without_create_rights_still_connects(self):
        """
        A legitimate way to run: somebody else owns the schema and this account
        only reads and writes inside it. Refusing the connection would be wrong.
        """
        wrapper, connection = self._wrapper(), self._connection()
        connection.cursor.side_effect = RuntimeError("permission denied for database")
        wrapper._ensure_schema(connection, "pyconng", "pyconng_db")
        self.assertIn(("pyconng_db", "pyconng"), base._ensured)


class QuotingTests(SimpleTestCase):
    def test_an_embedded_quote_is_doubled(self):
        """
        The name comes from settings rather than a request, but a schema name with
        a quote in it should be a syntax error, not an injection point.
        """
        self.assertEqual(base.DatabaseWrapper._quote('pycon"ng'), 'pycon""ng')

    def test_an_ordinary_name_is_untouched(self):
        self.assertEqual(base.DatabaseWrapper._quote("pyconng"), "pyconng")
