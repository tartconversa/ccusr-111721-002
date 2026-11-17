import argparse
import json

from .service import Exchange


def main():
    parser = argparse.ArgumentParser(description="本地协作台账演练")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    service = Exchange(args.database)
    try:
        service.catalog.register("gate-a", "机构甲", "深圳", "business", [("通行时间", "采集时刻", "string", False)], "2026-11-17")
        service.catalog.register("gate-b", "机构丙", "深圳", "business", [("通行时间", "放行时刻", "string", False)], "2026-11-17")
        service.catalog.ingest("item-1", "gate-a", {"通行时间": "2026-11-17T08:30:00"}, "2026-11-17")
        service.catalog.ingest("item-2", "gate-b", {"通行时间": "2026-11-17T08:31:00"}, "2026-11-17")
        for mapping_id, source_id in (("map-time-a", "gate-a"), ("map-time-b", "gate-b")):
            revision = service.catalog.propose_mapping(mapping_id, "通行时间", source_id, "通行时间", "2026-11-17", "数据管理员")
            service.catalog.decide_mapping(mapping_id, revision, True, "2026-11-17", "审阅人")
        service.grants.issue("g-1", "gate-a", "机构乙", "协作统计", "2026-12-31", "2026-11-17")
        service.grants.issue("g-2", "gate-b", "机构乙", "协作统计", "2026-12-31", "2026-11-17")
        service.policies.set("深圳", "business", "协作统计", "allow", "2026-11-17", "机构甲")
        draft = service.products.assemble("report", "通行汇总", ["item-1", "item-2"], "2026-11-17", "运营")
        try:
            service.delivery.send("d-0", "report", draft, "机构乙", "协作统计", "2026-11-17")
        except PermissionError as exc:
            print(json.dumps({"rejected": exc.args[0]}, ensure_ascii=False, sort_keys=True))
        revision = service.products.assemble("report", "通行汇总", ["item-1", "item-2"], "2026-11-17", "运营", interpretations=["map-time-a", "map-time-b"])
        receipt = service.delivery.send("d-1", "report", revision, "机构乙", "协作统计", "2026-11-17")
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    finally:
        service.close()


if __name__ == "__main__":
    main()
