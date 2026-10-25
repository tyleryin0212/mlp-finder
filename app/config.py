"""从环境变量和项目根目录的 .env 读取配置。"""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ["VOLC_ACCESS_KEY", "VOLC_SECRET_KEY", "VOLC_REGION"]


def load_env(path: Path = ROOT / ".env") -> None:
    """把 .env 里的 KEY=VALUE 读进环境变量（已存在的不覆盖）。"""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


@dataclass(frozen=True)
class Settings:
    # repr=False：打印 Settings 时不会带出密钥
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    region: str
    sync_interval_sec: int
    task_url_template: str
    db_path: Path

    def creds(self) -> dict:
        return {"ak": self.access_key, "sk": self.secret_key, "region": self.region}

    def task_url(self, task_id: str) -> str | None:
        if not self.task_url_template:
            return None
        return self.task_url_template.replace("{region}", self.region).replace("{id}", task_id)


def get_settings() -> Settings:
    load_env()
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"缺少环境变量：{', '.join(missing)}。见 README 的「配置」一节。")
    return Settings(
        access_key=os.environ["VOLC_ACCESS_KEY"],
        secret_key=os.environ["VOLC_SECRET_KEY"],
        region=os.environ["VOLC_REGION"],
        sync_interval_sec=int(os.environ.get("SYNC_INTERVAL_SEC", "300")),
        task_url_template=os.environ.get("TASK_URL_TEMPLATE", ""),
        db_path=Path(os.environ.get("DB_PATH", ROOT / "data" / "mlp-finder.db")),
    )
