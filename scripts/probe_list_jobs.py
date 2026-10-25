"""M0 探测：试调 ListJobs，确认鉴权、返回字段、分页、排序和 NameContains 的匹配方式。

在项目根目录运行：
    python3 -m scripts.probe_list_jobs                  # 基本探测
    python3 -m scripts.probe_list_jobs --contains 3d    # 再测试 NameContains

"""
import argparse
import os
import re
import sys

from app.config import REQUIRED, load_env
from app.volc import VolcError, call

# 只打印路径末段匹配这些名字的标量字段的值
SHOW_VALUE = re.compile(r"^(Id|Name|State|Status|CreateTime|UpdateTime|.*Creator.*|.*User.*)$")
# 路径中出现这些词就一律不打印值
HIDE_VALUE = re.compile(r"Env|Secret|Token|Password|Key|Credential|Command|Args", re.I)


def walk(obj, path=""):
    """遍历 JSON，产出 (路径, 值)。数组只看第一个元素。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        yield f"{path}[]", obj
        if obj:
            yield from walk(obj[0], f"{path}[]")
    else:
        yield path, obj


def describe(item: dict) -> None:
    for path, value in walk(item):
        if isinstance(value, list):
            print(f"  {path:<50} list, {len(value)} 个元素")
            continue
        kind = type(value).__name__
        last = path.split(".")[-1].rstrip("[]")
        if SHOW_VALUE.match(last) and not HIDE_VALUE.search(path):
            print(f"  {path:<50} {kind:<6} = {value!r}")
        else:
            print(f"  {path:<50} {kind}")


def name_of(item: dict) -> str:
    return item.get("Name", "?")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contains", help="用这个片段测试 NameContains，最好取任务名中间的一段")
    args = parser.parse_args()

    load_env()
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print(f".env 中缺少：{', '.join(missing)}")
        return 1
    creds = dict(
        ak=os.environ["VOLC_ACCESS_KEY"],
        sk=os.environ["VOLC_SECRET_KEY"],
        region=os.environ["VOLC_REGION"],
    )

    print(f"== 1. 鉴权与总数（地域 {creds['region']}）")
    try:
        r = call("ListJobs", {"PageSize": 10}, **creds)
    except VolcError as e:
        print(f"调用失败：{e}")
        return 1
    print(f"成功。TotalCount = {r.get('TotalCount')}")
    items = r.get("Items") or []
    if not items:
        print("没有任务，后面的测试跳过。")
        return 0

    print("\n== 2. 一条任务的字段结构")
    describe(items[0])

    print("\n== 3. 哪些 PageSize 可用")
    for size in (1, 5, 20, 50, 100, 200, 500):
        try:
            r = call("ListJobs", {"PageSize": size}, **creds)
            print(f"  PageSize={size}: 返回 {len(r.get('Items') or [])} 条，响应里 PageSize={r.get('PageSize')}")
        except VolcError as e:
            print(f"  PageSize={size}: 失败 {e}")

    print("\n== 4. 按创建时间倒序（前 5 条）")
    r = call("ListJobs", {"PageSize": 10, "SortBy": "CreateTime", "SortOrder": "Descend"}, **creds)
    for it in (r.get("Items") or [])[:5]:
        print(f"  {it.get('CreateTime', '?')}  {name_of(it)}")

    if args.contains:
        print(f"\n== 5. NameContains = {args.contains!r}")
        try:
            r = call("ListJobs", {"PageSize": 20, "NameContains": args.contains}, **creds)
        except VolcError as e:
            print(f"  失败：{e}")
            return 1
        print(f"  TotalCount = {r.get('TotalCount')}")
        for it in r.get("Items") or []:
            print(f"  {name_of(it)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
