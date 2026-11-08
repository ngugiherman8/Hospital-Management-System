"""Build-time check of DATABASE_URL. Prints what was received (never the password) and tests the login."""

import os
import re
import sys
from urllib.parse import unquote, urlparse

raw = os.environ.get("DATABASE_URL", "")
if not raw:
    sys.exit("DATABASE_URL is not set (Vercel: Settings > Environment Variables, enabled for Production).")

u = urlparse(raw)
pw = unquote(u.password or "")
print("DB check -> user:", u.username, "| host:", u.hostname, "| port:", u.port, "| database:", u.path.lstrip("/"))
print("DB check -> password length:", len(pw), "| only letters/digits:", bool(re.fullmatch(r"[A-Za-z0-9]+", pw)))
if raw != raw.strip() or any(c in raw for c in "\"' \n"):
    print("DB check -> WARNING: the value contains a space, quote or line break. Remove it.")

try:
    import psycopg

    with psycopg.connect(
        host=u.hostname,
        port=u.port or 5432,
        user=unquote(u.username or ""),
        password=pw,
        dbname=u.path.lstrip("/") or "postgres",
        sslmode="require",
        connect_timeout=15,
        prepare_threshold=None,
    ) as c:
        c.execute("select 1")
    print("DB check -> login OK")
except Exception as e:  # noqa: BLE001
    last = str(e).strip().splitlines()[-1]
    sys.exit(
        f"DB check -> login FAILED: {last}\n"
        "The username and host above are what Vercel sent. If they look right, the password is wrong."
    )
