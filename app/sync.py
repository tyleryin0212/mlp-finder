"""从 MLP 同步训练任务到本地 SQLite。

- 全量：翻完全部任务，并把这次没出现的任务标记为已删除。库为空，或距上次成功的全量超过 24 小时时执行。
- 增量：只拉"最早一个未结束任务"之后创建的任务（ListJobs 的 CreateTimeAfter），
  既能拿到新任务，也能刷新运行中任务的状态。
"""
import datetime
import logging
import sqlite3
import threading
import time
from contextlib import closing

from . import db
from .config import Settings
from .volc import VolcError, call

log = logging.getLogger(__name__)

PAGE_SIZE = 100            # ListJobs 只接受 10～100
PAGE_INTERVAL_SEC = 0.2    # 翻页间隔，避免触发限流
FULL_SYNC_EVERY = datetime.timedelta(hours=24)
# 已结束的状态（2026-10-25 首次全量同步中实际出现的）。不在这里的都按"未结束"处理：
# 漏了某个结束状态只会让增量多翻几页，不会漏数据
TERMINAL_STATES = ("Completed", "Failed", "Stopped")

TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(TIME_FMT)


def _parse(ts: str) -> datetime.datetime:
    return datetime.datetime.strptime(ts, TIME_FMT).replace(tzinfo=datetime.timezone.utc)


class Syncer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def run(self, kind: str | None = None) -> dict | None:
        """执行一次同步，返回本次的同步记录。kind 为 None 时自动选择全量或增量。
        已有同步在进行时直接返回 None。"""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            with closing(db.connect(self.settings.db_path)) as conn:
                return self._run(conn, kind)
        finally:
            self._lock.release()

    def _run(self, conn: sqlite3.Connection, kind: str | None) -> dict | None:
        since = db.incremental_since(conn, TERMINAL_STATES)
        if since is None:
            kind = "full"
        elif kind is None:
            last_full = db.last_full_sync(conn)
            due = last_full is None or _parse(last_full) < datetime.datetime.now(datetime.timezone.utc) - FULL_SYNC_EVERY
            kind = "full" if due else "incremental"

        started_at = now_iso()
        run_id = db.start_run(conn, kind, started_at)
        try:
            if kind == "full":
                fetched = self._full(conn, started_at)
            else:
                fetched = self._fetch(conn, {"CreateTimeAfter": since}, started_at)[0]
        except Exception as e:
            log.exception("%s 同步失败", kind)
            db.finish_run(conn, run_id, now_iso(), ok=False, fetched=0, message=str(e))
        else:
            log.info("%s 同步完成，拉取 %d 条", kind, fetched)
            db.finish_run(conn, run_id, now_iso(), ok=True, fetched=fetched, message=None)
        return db.get_run(conn, run_id)

    def _full(self, conn: sqlite3.Connection, synced_at: str) -> int:
        fetched, total = self._fetch(conn, {}, synced_at)
        # 中途有任务被删除时，翻页会错位漏掉几条；没拿全就不标记删除，等下一次全量
        if fetched >= total:
            n = db.mark_deleted(conn, synced_at)
            conn.commit()
            if n:
                log.info("标记 %d 个已删除的任务", n)
        else:
            log.warning("全量同步只拿到 %d/%d 条，本次不标记删除", fetched, total)
        return fetched

    def _fetch(self, conn: sqlite3.Connection, body: dict, synced_at: str) -> tuple[int, int]:
        """按创建时间倒序翻完所有页并写库，返回 (去重后的条数, 接口报告的总数)。"""
        seen: set[str] = set()
        page = 1
        while True:
            result = self._call({
                **body,
                "PageNumber": page,
                "PageSize": PAGE_SIZE,
                "SortBy": "CreateTime",
                "SortOrder": "Descend",
            })
            items = result.get("Items") or []
            total = result.get("TotalCount") or 0
            db.upsert_tasks(conn, items, synced_at)
            conn.commit()
            seen.update(it["Id"] for it in items)
            if len(items) < PAGE_SIZE or page * PAGE_SIZE >= total:
                return len(seen), total
            page += 1
            time.sleep(PAGE_INTERVAL_SEC)

    def _call(self, body: dict, attempts: int = 3) -> dict:
        for i in range(attempts):
            try:
                return call("ListJobs", body, **self.settings.creds())
            except (VolcError, OSError) as e:
                if i == attempts - 1:
                    raise
                log.warning("ListJobs 失败，%d 秒后重试：%s", 2 ** i, e)
                time.sleep(2 ** i)
        raise AssertionError("unreachable")
