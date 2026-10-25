"""FastAPI 入口：搜索接口、同步接口，以及进程内的定时同步。

运行：uvicorn app.main:app --port 8000
"""
import asyncio
import logging
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse

from . import db
from .config import get_settings
from .sync import Syncer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

settings = get_settings()
syncer = Syncer(settings)
STATIC = Path(__file__).parent / "static"


def creator_name(creator: str | None) -> str | None:
    """'trn:iam::2107608163:user/lanxiang.xing' -> 'lanxiang.xing'"""
    return creator.rsplit("/", 1)[-1] if creator else None


async def sync_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(syncer.run)
        except Exception:
            log.exception("定时同步出错")
        await asyncio.sleep(settings.sync_interval_sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with closing(db.connect(settings.db_path)) as conn:
        db.init(conn)
    task = asyncio.create_task(sync_loop())
    yield
    task.cancel()


app = FastAPI(title="mlp-finder", lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/tasks")
def list_tasks(q: str = "", state: str = "", creator: str = "", offset: int = Query(0, ge=0)):
    with closing(db.connect(settings.db_path)) as conn:
        rows, total = db.search(conn, q, state=state or None, creator=creator or None, offset=offset)
    items = [{**r, "creator_name": creator_name(r["creator"]), "url": settings.task_url(r["id"])} for r in rows]
    return {"items": items, "total": total}


@app.get("/api/filters")
def filters():
    with closing(db.connect(settings.db_path)) as conn:
        opts = db.filter_options(conn)
    creators = [{"value": c, "name": creator_name(c)} for c in opts["creators"]]
    return {"states": opts["states"], "creators": sorted(creators, key=lambda c: c["name"].lower())}


@app.post("/api/sync")
async def trigger_sync():
    result = await asyncio.to_thread(syncer.run, "incremental")
    if result is None:
        return JSONResponse({"detail": "已有同步正在进行"}, status_code=409)
    return result


@app.get("/api/sync/status")
def sync_status():
    with closing(db.connect(settings.db_path)) as conn:
        return {
            "running": syncer.running,
            "last_run": db.last_run(conn),
            "task_count": db.task_count(conn),
        }
