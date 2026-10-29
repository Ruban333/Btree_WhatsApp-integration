"""Shared setup for the test scripts: mock WhatsApp, a throwaway copy of the database, signed in as a chosen user.

Run any test with:  .venv\\Scripts\\python -I tests\\test_xxx.py   (nothing is sent anywhere, the live database is not touched)
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
TMP = os.path.join(tempfile.gettempdir(), "btree-crm-tests")
os.makedirs(TMP, exist_ok=True)

_n = [0]


def setup(extra_env=None, db_name="test.db"):
    """Copies the live DB, points the app at the copy, and returns (main, db, auth, current_user_dict)."""
    path = os.path.join(TMP, db_name)
    shutil.copy(os.path.join(BACKEND, "data.db"), path)
    os.environ.update(WA_MODE="mock", DB_PATH=path, **(extra_env or {}))
    os.environ.pop("ANTHROPIC_API_KEY", None) if "ANTHROPIC_API_KEY" not in (extra_env or {}) else None
    sys.path.insert(0, BACKEND)
    import main, db, auth  # noqa: E402
    cur = {"name": "admin"}
    auth.user_from_token = lambda t: db.one("SELECT * FROM users WHERE username=?", (cur["name"],))
    return main, db, auth, cur


def checker():
    results = {"pass": 0, "fail": 0}

    def ok(cond, label):
        results["pass" if cond else "fail"] += 1
        print(("PASS " if cond else "FAIL ") + label)

    return ok, results


def newphone(prefix="9200"):
    _n[0] += 1
    return f"91{prefix}{_n[0]:06d}"   # 12 digits, so it is stored exactly as typed
