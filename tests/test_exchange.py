import json
import tempfile
import unittest
from pathlib import Path

from exchange import Exchange


class ExchangeFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Exchange(Path(self.temp.name) / "exchange.db")
        self.service.catalog.register("flow", "机构甲", "甲地", "restricted", [("count", "通行计数", "integer", False)], "2026-11-01")
        self.service.catalog.ingest("item-1", "flow", {"count": 8}, "2026-11-02")
        self.service.grants.issue("g-1", "flow", "机构乙", "运行协调", "2026-12-31", "2026-11-03")
        self.service.policies.set("甲地", "restricted", "运行协调", "allow", "2026-11-03", "机构甲")
        self.revision = self.service.products.assemble("report", "通行观察", ["item-1"], "2026-11-04", "机构甲")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_delivery_keeps_revision_and_request_identity(self):
        desk = self.service.delivery
        receipt = desk.send("d-1", "report", self.revision, "机构乙", "运行协调", "2026-11-05")
        self.assertEqual(receipt["product_revision"], self.revision)
        self.assertEqual(receipt, desk.send("d-1", "report", self.revision, "机构乙", "运行协调", "2026-11-05"))
        with self.assertRaises(ValueError):
            desk.send("d-1", "report", self.revision, "其他机构", "运行协调", "2026-11-05")

    def test_grant_and_policy_gate_delivery(self):
        with self.assertRaises(PermissionError):
            self.service.delivery.send("d-2", "report", self.revision, "陌生机构", "运行协调", "2026-11-05")
        self.service.policies.set("甲地", "restricted", "运行协调", "deny", "2026-11-06", "机构甲")
        with self.assertRaises(PermissionError):
            self.service.delivery.send("d-3", "report", self.revision, "机构乙", "运行协调", "2026-11-07")
        self.assertTrue(self.service.history("report"))

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.service.catalog.ingest("item-2", "flow", {"unknown": 9}, "2026-11-03")


class SemanticMappingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Exchange(Path(self.temp.name) / "exchange.db")
        self.service.catalog.register(
            "gate-a", "机构甲", "深圳", "business",
            [("通行时间", "采集时刻", "string", False), ("企业所在地", "注册地", "string", False)],
            "2026-11-01",
        )
        self.service.catalog.register(
            "gate-b", "机构丙", "深圳", "business",
            [("通行时间", "放行时刻", "string", False), ("企业所在地", "经营地", "string", False)],
            "2026-11-01",
        )
        self.service.catalog.ingest("item-1", "gate-a", {"通行时间": "2026-11-02T08:30:00", "企业所在地": "南山区"}, "2026-11-02")
        self.service.catalog.ingest("item-2", "gate-b", {"通行时间": "2026-11-02T08:31:00", "企业所在地": "福田区"}, "2026-11-02")
        self.service.grants.issue("g-1", "gate-a", "机构乙", "协作统计", "2026-12-31", "2026-11-03")
        self.service.grants.issue("g-2", "gate-b", "机构乙", "协作统计", "2026-12-31", "2026-11-03")
        self.service.policies.set("深圳", "business", "协作统计", "allow", "2026-11-03", "机构甲")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def approve(self, mapping_id, canonical, source_id, field_name):
        revision = self.service.catalog.propose_mapping(mapping_id, canonical, source_id, field_name, "2026-11-04", "数据管理员")
        self.service.catalog.decide_mapping(mapping_id, revision, True, "2026-11-04", "审阅人")
        return revision

    def approve_all(self):
        self.approve("map-time-a", "通行时间", "gate-a", "通行时间")
        self.approve("map-time-b", "通行时间", "gate-b", "通行时间")
        self.approve("map-loc-a", "企业所在地", "gate-a", "企业所在地")
        self.approve("map-loc-b", "企业所在地", "gate-b", "企业所在地")
        return ["map-time-a", "map-time-b", "map-loc-a", "map-loc-b"]

    def test_conflict_without_mappings_stays_unresolved(self):
        revision = self.service.products.assemble("report", "通行汇总", ["item-1", "item-2"], "2026-11-05", "运营")
        with self.assertRaises(PermissionError) as caught:
            self.service.delivery.send("d-1", "report", revision, "机构乙", "协作统计", "2026-11-06")
        reasons = caught.exception.args[0]
        unresolved = {reason["field"] for reason in reasons if reason["reason"] == "unresolved"}
        self.assertEqual(unresolved, {"通行时间", "企业所在地"})
        rejections = [entry for entry in self.service.history("report") if entry["action"] == "delivery.reject"]
        self.assertTrue(rejections)
        self.assertIn("unresolved", rejections[-1]["details"])

    def test_adopted_mappings_resolve_conflict_and_receipt_records_snapshot(self):
        mappings = self.approve_all()
        revision = self.service.products.assemble("report", "通行汇总", ["item-1", "item-2"], "2026-11-05", "运营", interpretations=mappings)
        receipt = self.service.delivery.send("d-1", "report", revision, "机构乙", "协作统计", "2026-11-06")
        self.assertEqual(receipt["product_revision"], revision)
        snapshot = json.loads(receipt["interpretations"])
        self.assertEqual(len(snapshot), 4)
        passage = {(entry["source_id"], entry["meaning"]) for entry in snapshot if entry["canonical"] == "通行时间"}
        self.assertEqual(passage, {("gate-a", "采集时刻"), ("gate-b", "放行时刻")})
        location = {(entry["source_id"], entry["meaning"]) for entry in snapshot if entry["canonical"] == "企业所在地"}
        self.assertEqual(location, {("gate-a", "注册地"), ("gate-b", "经营地")})

    def test_partial_coverage_still_unresolved(self):
        self.approve("map-time-a", "通行时间", "gate-a", "通行时间")
        revision = self.service.products.assemble("report", "通行汇总", ["item-1", "item-2"], "2026-11-05", "运营", interpretations=["map-time-a"])
        with self.assertRaises(PermissionError) as caught:
            self.service.delivery.send("d-1", "report", revision, "机构乙", "协作统计", "2026-11-06")
        unresolved = {reason["field"] for reason in caught.exception.args[0] if reason["reason"] == "unresolved"}
        self.assertEqual(unresolved, {"通行时间", "企业所在地"})

    def test_pending_or_rejected_mapping_cannot_be_adopted(self):
        revision = self.service.catalog.propose_mapping("map-time-a", "通行时间", "gate-a", "通行时间", "2026-11-04", "数据管理员")
        self.assertEqual(self.service.catalog.mapping("map-time-a")["status"], "pending")
        with self.assertRaises(ValueError):
            self.service.products.assemble("report", "通行汇总", ["item-1"], "2026-11-05", "运营", interpretations=["map-time-a"])
        self.service.catalog.decide_mapping("map-time-a", revision, False, "2026-11-04", "审阅人")
        with self.assertRaises(ValueError):
            self.service.products.assemble("report", "通行汇总", ["item-1"], "2026-11-05", "运营", interpretations=["map-time-a"])
        with self.assertRaises(ValueError):
            self.service.catalog.decide_mapping("map-time-a", revision, True, "2026-11-05", "审阅人")

    def test_mapping_revision_pinned_per_product_revision(self):
        first = self.approve("map-time-a", "通行时间", "gate-a", "通行时间")
        revision1 = self.service.products.assemble("report", "通行汇总", ["item-1"], "2026-11-05", "运营", interpretations=[("map-time-a", first)])
        receipt1 = self.service.delivery.send("d-1", "report", revision1, "机构乙", "协作统计", "2026-11-06")
        second = self.service.catalog.propose_mapping("map-time-a", "通行时刻", "gate-a", "通行时间", "2026-11-07", "数据管理员")
        self.service.catalog.decide_mapping("map-time-a", second, True, "2026-11-07", "审阅人")
        self.assertNotEqual(first, second)
        self.assertEqual(self.service.delivery.send("d-1", "report", revision1, "机构乙", "协作统计", "2026-11-08"), receipt1)
        receipt2 = self.service.delivery.send("d-2", "report", revision1, "机构乙", "协作统计", "2026-11-08")
        adopted1 = json.loads(receipt2["interpretations"])
        self.assertEqual(adopted1[0]["mapping_revision"], first)
        self.assertEqual(adopted1[0]["canonical"], "通行时间")
        revision2 = self.service.products.assemble("report", "通行汇总", ["item-1"], "2026-11-09", "运营", interpretations=["map-time-a"])
        adopted2 = self.service.products.interpretations("report", revision2)
        self.assertEqual(adopted2[0]["mapping_revision"], second)
        self.assertEqual(adopted2[0]["canonical"], "通行时刻")

    def test_mapping_must_match_product_inputs(self):
        self.approve("map-time-b", "通行时间", "gate-b", "通行时间")
        with self.assertRaises(ValueError):
            self.service.products.assemble("report", "通行汇总", ["item-1"], "2026-11-05", "运营", interpretations=["map-time-b"])

    def test_mappings_are_reviewable(self):
        revision = self.service.catalog.propose_mapping("map-time-a", "通行时间", "gate-a", "通行时间", "2026-11-04", "数据管理员")
        pending = self.service.catalog.mappings(status="pending")
        self.assertEqual([(entry["id"], entry["revision"]) for entry in pending], [("map-time-a", revision)])
        self.service.catalog.decide_mapping("map-time-a", revision, True, "2026-11-04", "审阅人")
        self.assertEqual(self.service.catalog.mappings(status="pending"), [])
        record = self.service.catalog.mapping("map-time-a", revision)
        self.assertEqual(record["status"], "approved")
        self.assertEqual(record["meaning"], "采集时刻")
        actions = [entry["action"] for entry in self.service.history("map-time-a")]
        self.assertEqual(actions, ["mapping.propose", "mapping.decide"])

    def test_propose_validates_source_field(self):
        with self.assertRaises(KeyError):
            self.service.catalog.propose_mapping("map-x", "通行时间", "gate-a", "不存在", "2026-11-04", "数据管理员")
        with self.assertRaises(KeyError):
            self.service.catalog.mapping("missing")


if __name__ == "__main__":
    unittest.main()
