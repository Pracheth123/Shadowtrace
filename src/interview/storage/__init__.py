"""
Storage backends for the SQL-backed report index.

`open_report_store(settings, data_root)` is the single place the backend is
chosen:

  - DATABASE_URL empty  → SQLite at DATA_DIR/reports.sqlite (development, tests);
  - DATABASE_URL set    → PostgreSQL, or a startup error. Never a silent
                          fallback to SQLite.

Production (APP_ENV=prod) refuses SQLite unless ALLOW_SQLITE_IN_PRODUCTION=1,
and refuses PostgreSQL without `sslmode=verify-full` (server certificate and
hostname verified against DB_SSLROOTCERT / the URL's `sslrootcert`).

Only the report index moves. Identities, intakes, transcripts, evaluation jobs
and results, reports, disputes, revisions and practice records remain files
under DATA_DIR (docs/DEPLOYMENT.md, "Storage boundary").
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from interview.storage.reports_pg import (
    EXPECTED_VERSION,
    DatabaseUnavailable,
    PostgresReportStore,
    SchemaNotReady,
)


class StorageConfigError(RuntimeError):
    """The storage configuration is not acceptable for this environment."""


def host_label(url: str) -> str:
    """host:port/dbname for messages and diagnostics; never user or password."""
    parts = urlsplit(url)
    host = parts.hostname or "?"
    port = f":{parts.port}" if parts.port else ""
    return f"{host}{port}{parts.path or ''}"


def sslmode_of(url: str) -> str:
    return (parse_qs(urlsplit(url).query).get("sslmode") or [""])[0]


# One pool per database per process: services are rebuilt (tests, reconfigure)
# without opening a new pool each time.
_PG_STORES: dict[str, PostgresReportStore] = {}


def open_report_store(settings, data_root: Path):
    """The report store for these settings. Raises rather than degrading."""
    if settings.database_backend == "postgres":
        url = settings.database_url.get_secret_value().strip()
        if settings.is_production and sslmode_of(url) != "verify-full":
            raise StorageConfigError(
                "Production PostgreSQL needs sslmode=verify-full (with sslrootcert= pointing "
                "at the RDS CA bundle) in DATABASE_URL."
            )
        cached = _PG_STORES.get(url)
        if cached is not None:
            return cached
        store = _PG_STORES[url] = PostgresReportStore(
            url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            connect_timeout_s=settings.db_connect_timeout_s,
            statement_timeout_ms=settings.db_statement_timeout_ms,
            auto_migrate=settings.auto_migrate,
            host_label=host_label(url),
        )
        return store
    if settings.is_production and not settings.allow_sqlite_in_production:
        raise StorageConfigError(
            "APP_ENV=prod with no DATABASE_URL. Set DATABASE_URL to PostgreSQL, or set "
            "ALLOW_SQLITE_IN_PRODUCTION=1 to run a deliberate single-host SQLite deployment."
        )
    from interview.roadmap.reports import ReportStore

    return ReportStore(Path(data_root) / "reports.sqlite")


__all__ = [
    "DatabaseUnavailable",
    "EXPECTED_VERSION",
    "PostgresReportStore",
    "SchemaNotReady",
    "StorageConfigError",
    "host_label",
    "open_report_store",
    "sslmode_of",
]
