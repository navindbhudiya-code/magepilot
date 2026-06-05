"""Read-only MySQL access for database debugging.

Credentials come from `<root>/app/etc/env.php` (the canonical Magento location). ONLY read-only
queries run — `SELECT` / `SHOW` / `DESCRIBE` / `EXPLAIN`. Any write (INSERT/UPDATE/DELETE/DROP/…),
stacked statements, or file I/O (`INTO OUTFILE`, `LOAD_FILE`) is refused. A `LIMIT` is auto-applied
to unbounded `SELECT`s. The password is passed via `MYSQL_PWD`, never on the command line.
"""
import json
import os
import re
import shutil
import subprocess

from agent import config

_READONLY_START = re.compile(r"^\s*(select|show|describe|desc|explain)\b", re.I)
_WRITE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|grant|revoke|rename|"
    r"into\s+outfile|into\s+dumpfile|load_file|load\s+data|set\s+@|call|lock|unlock)\b",
    re.I,
)


def is_read_only(sql: str) -> bool:
    """True only for a single read-only statement with no write/file/stacking constructs."""
    q = (sql or "").strip().rstrip(";").strip()
    if not q:
        return False
    if ";" in q:                      # no stacked statements
        return False
    if not _READONLY_START.match(q):  # must START with a read verb
        return False
    if _WRITE.search(q):              # and contain no write/file constructs
        return False
    return True


def db_credentials(root: str) -> dict | None:
    """Extract the default DB connection from app/etc/env.php (via php). None if unavailable."""
    env_php = os.path.join(os.path.realpath(root), "app", "etc", "env.php")
    if not os.path.isfile(env_php):
        return None
    if not shutil.which("php"):
        return None
    code = (f'$c = include {json.dumps(env_php)}; '
            f'echo json_encode($c["db"]["connection"]["default"] ?? new stdClass());')
    try:
        out = subprocess.run(["php", "-r", code], capture_output=True, text=True, timeout=20)
        data = json.loads(out.stdout or "{}")
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("host"):
        return None
    host, port = data["host"], "3306"
    if ":" in host:
        host, port = host.split(":", 1)
    return {
        "host": config.DB_HOST or host,
        "port": config.DB_PORT or port,
        "db": data.get("dbname", ""),
        "user": data.get("username", ""),
        "password": data.get("password", ""),
    }


def _transport(root: str, q: str):
    """Pick how to reach the DB. Returns (argv, run_kwargs) or (None, error_string).

    auto: host `mysql` client → docker `db` container → `warden db connect`.
    The password is passed via MYSQL_PWD in the child env (never on argv) for host/docker.
    """
    mode = config.DB_MODE
    mysql = shutil.which("mysql")
    warden = shutil.which("warden")
    container = config.DB_CONTAINER

    want_host = mode == "host" or (mode == "auto" and mysql and config.DB_HOST)
    want_docker = mode == "docker" or (mode == "auto" and container and not want_host)
    want_warden = mode == "warden" or (mode == "auto" and warden and not want_host and not want_docker)

    if want_host:
        cred = db_credentials(root)
        if not mysql:
            return None, "the `mysql` client was not found on PATH"
        if not cred:
            return None, "could not read DB credentials from app/etc/env.php"
        argv = [mysql, "-h", cred["host"], "-P", str(cred["port"]), "-u", cred["user"],
                cred["db"], "--table", "-e", q]
        return argv, {"env": {**os.environ, "MYSQL_PWD": cred["password"]}}

    if want_docker:
        cred = db_credentials(root)
        if not container:
            return None, "AGENT_DB_CONTAINER not set (docker mode needs the db container name)"
        if not cred:
            return None, "could not read DB credentials from app/etc/env.php"
        # `-e MYSQL_PWD` (no value) forwards it from our env → keeps the password off argv.
        argv = ["docker", "exec", "-e", "MYSQL_PWD", container, "mysql", "-u", cred["user"],
                "--table", cred["db"], "-e", q]
        return argv, {"env": {**os.environ, "MYSQL_PWD": cred["password"]}}

    if want_warden:
        if not warden:
            return None, "warden not found on PATH"
        # `warden db connect` handles credentials + container; feed the query on stdin.
        return ["warden", "db", "connect"], {"cwd": os.path.realpath(root), "input": q + ";"}

    return None, ("no DB transport available — install the mysql client, set AGENT_DB_CONTAINER "
                  "to your db container, or run inside a Warden project")


def run_query(root: str, sql: str, limit: int = None) -> dict:
    """Run a READ-ONLY query against the store DB. Returns {ok, output} or {ok: False, error}."""
    if not is_read_only(sql):
        return {"ok": False,
                "error": "refused: only read-only queries are allowed "
                         "(SELECT / SHOW / DESCRIBE / EXPLAIN, single statement)"}
    q = sql.strip().rstrip(";").strip()
    if q.lower().startswith("select") and " limit " not in q.lower():
        q += f" LIMIT {limit or config.DB_ROW_LIMIT}"
    argv, kw = _transport(root, q)
    if argv is None:
        return {"ok": False, "error": kw}
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60, **kw)
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": f"query transport failed: {e}"}
    if out.returncode != 0:
        return {"ok": False, "error": (out.stderr or "query failed").strip()}
    return {"ok": True, "output": out.stdout.strip() or "(empty result)"}
