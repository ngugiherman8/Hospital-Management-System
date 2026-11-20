"""Give the website a database account with only the rights it needs.

The owner account (used for migrations) can change the structure. The website account can read and write rows, but
cannot drop or alter tables, create roles, or edit or delete the audit log, payments and stock movements.
"""

from django.db import connection

APPEND_ONLY = ["core_auditlog", "billing_payment", "stock_stockmovement"]  # insert and read only
KEPT = ["sync_outbox", "sync_appliedchange", "accounts_failedlogin"]  # may be updated, never deleted or truncated


def apply(app_user, app_password=None):
    q = connection.ops.quote_name
    with connection.cursor() as cur:
        cur.execute("SELECT current_database(), current_user")
        database, owner = cur.fetchone()
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [app_user])
        if cur.fetchone() is None:
            if not app_password:
                raise ValueError("The website account does not exist yet: give a password to create it.")
            cur.execute(
                f"CREATE ROLE {q(app_user)} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %s", [app_password]
            )
        elif app_password:
            cur.execute(f"ALTER ROLE {q(app_user)} PASSWORD %s", [app_password])
        cur.execute(f"REVOKE ALL ON DATABASE {q(database)} FROM PUBLIC")
        cur.execute(f"GRANT CONNECT ON DATABASE {q(database)} TO {q(app_user)}")
        cur.execute(f"GRANT USAGE ON SCHEMA public TO {q(app_user)}")
        cur.execute(f"REVOKE CREATE ON SCHEMA public FROM {q(app_user)}")
        cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {q(app_user)}")
        cur.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {q(app_user)}")
        cur.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {q(owner)} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {q(app_user)}"
        )
        cur.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {q(owner)} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {q(app_user)}"
        )
        cur.execute(f"REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM {q(app_user)}")
        cur.execute(f"REVOKE UPDATE, DELETE ON {', '.join(q(t) for t in APPEND_ONLY)} FROM {q(app_user)}")
        cur.execute(f"REVOKE DELETE ON {', '.join(q(t) for t in KEPT)} FROM {q(app_user)}")
    return owner
