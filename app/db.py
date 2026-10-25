"""SQLite 存储：任务表、原始 JSON 表、同步记录表，以及搜索。

时间统一存成 UTC 的 "YYYY-MM-DDTHH:MM:SSZ" 字符串，和 MLP 返回的格式一致，可以直接按字符串比较和排序。
"""
import json
import sqlite3
from pathlib import Path

PAGE_SIZE = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id         TEXT PRIMARY KEY,      -- MLP 任务 ID
  name       TEXT NOT NULL,
  state      TEXT,
  creator    TEXT,
  created_at TIMESTAMP,
  deleted    BOOLEAN DEFAULT FALSE,
  synced_at  TIMESTAMP            -- 最近一次在同步中出现的时间（即那次同步的开始时间）
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS task_raw (
  id  TEXT PRIMARY KEY REFERENCES tasks(id),
  raw JSON                          -- ListJobs 返回的原始条目
);

CREATE TABLE IF NOT EXISTS sync_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL,        -- full / incremental
  started_at  TIMESTAMP NOT NULL,
  finished_at TIMESTAMP,
  ok          BOOLEAN,
  fetched     INTEGER,
  message     TEXT
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL：同步写入时，搜索仍然可以读
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# ---------- 任务 ----------

def upsert_tasks(conn: sqlite3.Connection, items: list[dict], synced_at: str) -> None:
    conn.executemany(
        """
        INSERT INTO tasks (id, name, state, creator, created_at, deleted, synced_at)
        VALUES (?, ?, ?, ?, ?, FALSE, ?)
        ON CONFLICT(id) DO UPDATE SET
          name = excluded.name, state = excluded.state, creator = excluded.creator,
          created_at = excluded.created_at, deleted = FALSE, synced_at = excluded.synced_at
        """,
        [
            (
                it["Id"],
                it.get("Name") or "",
                (it.get("Status") or {}).get("State"),
                it.get("CreatedBy"),
                it.get("CreateTime"),
                synced_at,
            )
            for it in items
        ],
    )
    conn.executemany(
        "INSERT INTO task_raw (id, raw) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET raw = excluded.raw",
        [(it["Id"], json.dumps(it, ensure_ascii=False)) for it in items],
    )


def mark_deleted(conn: sqlite3.Connection, synced_before: str) -> int:
    """把这次全量同步中没出现的任务标记为已删除，返回标记的条数。"""
    cur = conn.execute("UPDATE tasks SET deleted = TRUE WHERE NOT deleted AND synced_at < ?", (synced_before,))
    return cur.rowcount


def incremental_since(conn: sqlite3.Connection, terminal_states: tuple[str, ...]) -> str | None:
    """增量同步的起点：最早一个未结束任务的创建时间；都结束了就用最新任务的创建时间。库为空返回 None。"""
    marks = ",".join("?" * len(terminal_states))
    row = conn.execute(
        f"SELECT MIN(created_at) FROM tasks WHERE NOT deleted AND (state IS NULL OR state NOT IN ({marks}))",
        terminal_states,
    ).fetchone()
    if row[0]:
        return row[0]
    return conn.execute("SELECT MAX(created_at) FROM tasks").fetchone()[0]


def filter_options(conn: sqlite3.Connection) -> dict:
    """列出库里出现过的状态和创建人，作为过滤下拉框的选项。"""
    def distinct(col: str) -> list[str]:
        sql = f"SELECT DISTINCT {col} FROM tasks WHERE NOT deleted AND {col} IS NOT NULL ORDER BY {col}"
        return [r[0] for r in conn.execute(sql)]
    return {"states": distinct("state"), "creators": distinct("creator")}


def task_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM tasks WHERE NOT deleted").fetchone()[0]


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(
    conn: sqlite3.Connection,
    q: str,
    *,
    state: str | None = None,
    creator: str | None = None,
    offset: int = 0,
    limit: int = PAGE_SIZE,
) -> tuple[list[dict], int]:
    """按任务名子串搜索（不区分大小写），空格分隔的多个词按"与"组合。返回 (本页结果, 总数)。"""
    where = ["NOT deleted"]
    params: list = []
    for kw in q.split():
        where.append("name LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(kw)}%")
    if state:
        where.append("state = ?")
        params.append(state)
    if creator:
        where.append("creator = ?")
        params.append(creator)
    clause = " AND ".join(where)

    total = conn.execute(f"SELECT COUNT(*) FROM tasks WHERE {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT id, name, state, creator, created_at FROM tasks
        WHERE {clause}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    return [dict(r) for r in rows], total


# ---------- 同步记录 ----------

def start_run(conn: sqlite3.Connection, kind: str, started_at: str) -> int:
    cur = conn.execute("INSERT INTO sync_runs (kind, started_at) VALUES (?, ?)", (kind, started_at))
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, finished_at: str, ok: bool, fetched: int, message: str | None) -> None:
    conn.execute(
        "UPDATE sync_runs SET finished_at = ?, ok = ?, fetched = ?, message = ? WHERE id = ?",
        (finished_at, ok, fetched, message, run_id),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def last_run(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM sync_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def last_full_sync(conn: sqlite3.Connection) -> str | None:
    """上次成功的全量同步的开始时间。"""
    row = conn.execute("SELECT MAX(started_at) FROM sync_runs WHERE kind = 'full' AND ok").fetchone()
    return row[0]
