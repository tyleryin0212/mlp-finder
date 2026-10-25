"""数据库和搜索的测试，不需要网络和密钥。

运行：python3 -m unittest tests.test_db -v
"""
import unittest

from app import db

TERMINAL = ("Success", "Failed", "Stopped")


def task(id, name, created, state="Success", creator="tyler"):
    return {"Id": id, "Name": name, "CreateTime": created, "CreatedBy": creator, "Status": {"State": state}}


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init(self.conn)
        db.upsert_tasks(self.conn, [
            task("t1", "ThisIsASampleTask", "2026-06-01T00:00:00Z"),
            task("t2", "this-is-3d-task", "2026-06-02T00:00:00Z"),
            task("t3", "testnnsht2layer-0627-361x720", "2026-06-03T00:00:00Z", state="Running"),
            task("t4", "faircrps_2_1", "2026-06-04T00:00:00Z", creator="amy"),
            task("t5", "faircrpsX2Y1", "2026-06-05T00:00:00Z"),
        ], "2026-10-25T00:00:00Z")

    def names(self, q, **kw):
        rows, _ = db.search(self.conn, q, **kw)
        return [r["name"] for r in rows]

    def test_acceptance_cases(self):
        # PRD 第 6 节的验收标准
        for q in ("Sample", "sample", "ASample"):
            self.assertIn("ThisIsASampleTask", self.names(q), q)
        for q in ("3d", "3D-task", "3d task"):
            self.assertIn("this-is-3d-task", self.names(q), q)

    def test_mid_token_fragment(self):
        # MLP 自带搜索搜不到的情况
        self.assertEqual(self.names("2layer"), ["testnnsht2layer-0627-361x720"])
        self.assertEqual(self.names("nsht2"), ["testnnsht2layer-0627-361x720"])

    def test_all_keywords_must_match(self):
        self.assertEqual(self.names("3d sample"), [])

    def test_underscore_is_literal(self):
        # "_" 不能被当成 LIKE 的单字符通配符，否则会匹配到 faircrpsX2Y1
        self.assertEqual(self.names("faircrps_2"), ["faircrps_2_1"])

    def test_percent_is_literal(self):
        self.assertEqual(self.names("%"), [])

    def test_order_newest_first(self):
        self.assertEqual(self.names("task"), ["this-is-3d-task", "ThisIsASampleTask"])

    def test_filters_and_total(self):
        rows, total = db.search(self.conn, "", creator="amy")
        self.assertEqual(([r["id"] for r in rows], total), (["t4"], 1))
        rows, total = db.search(self.conn, "", state="Running")
        self.assertEqual(total, 1)

    def test_filter_options(self):
        opts = db.filter_options(self.conn)
        self.assertEqual(opts["states"], ["Running", "Success"])
        self.assertEqual(opts["creators"], ["amy", "tyler"])

    def test_pagination(self):
        rows, total = db.search(self.conn, "", limit=2, offset=2)
        self.assertEqual(total, 5)
        self.assertEqual([r["id"] for r in rows], ["t3", "t2"])


class SyncStateTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init(self.conn)

    def test_incremental_since(self):
        self.assertIsNone(db.incremental_since(self.conn, TERMINAL))
        db.upsert_tasks(self.conn, [
            task("a", "a", "2026-06-01T00:00:00Z"),
            task("b", "b", "2026-06-02T00:00:00Z", state="Running"),
            task("c", "c", "2026-06-03T00:00:00Z", state="SomeNewState"),
            task("d", "d", "2026-06-04T00:00:00Z"),
        ], "2026-10-25T00:00:00Z")
        # 最早的未结束任务；未知状态也算未结束
        self.assertEqual(db.incremental_since(self.conn, TERMINAL), "2026-06-02T00:00:00Z")

    def test_incremental_since_all_finished(self):
        db.upsert_tasks(self.conn, [
            task("a", "a", "2026-06-01T00:00:00Z"),
            task("b", "b", "2026-06-02T00:00:00Z"),
        ], "2026-10-25T00:00:00Z")
        self.assertEqual(db.incremental_since(self.conn, TERMINAL), "2026-06-02T00:00:00Z")

    def test_mark_deleted_and_restore(self):
        db.upsert_tasks(self.conn, [task("a", "alpha", "2026-06-01T00:00:00Z"),
                                    task("b", "beta", "2026-06-02T00:00:00Z")], "2026-10-24T00:00:00Z")
        # 新一轮全量只看到 a
        db.upsert_tasks(self.conn, [task("a", "alpha", "2026-06-01T00:00:00Z")], "2026-10-25T00:00:00Z")
        self.assertEqual(db.mark_deleted(self.conn, "2026-10-25T00:00:00Z"), 1)
        self.assertEqual(db.task_count(self.conn), 1)
        self.assertEqual(self.names_all(), ["alpha"])
        # b 再次出现时恢复
        db.upsert_tasks(self.conn, [task("b", "beta", "2026-06-02T00:00:00Z")], "2026-10-26T00:00:00Z")
        self.assertEqual(db.task_count(self.conn), 2)

    def names_all(self):
        return [r["name"] for r in db.search(self.conn, "")[0]]

    def test_sync_runs(self):
        run_id = db.start_run(self.conn, "full", "2026-10-25T00:00:00Z")
        self.assertIsNone(db.last_run(self.conn))  # 还没结束
        db.finish_run(self.conn, run_id, "2026-10-25T00:01:00Z", ok=True, fetched=10, message=None)
        self.assertEqual(db.last_run(self.conn)["fetched"], 10)
        self.assertEqual(db.last_full_sync(self.conn), "2026-10-25T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
