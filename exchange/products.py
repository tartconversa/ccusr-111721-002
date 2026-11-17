import json

from .audit import write


class Products:
    def __init__(self, connection):
        self.connection = connection

    def assemble(self, product_id, name, asset_ids, at, actor, interpretations=None):
        asset_ids = list(dict.fromkeys(asset_ids))
        if not asset_ids:
            raise ValueError("数据产品缺少输入")
        placeholders = ",".join("?" for _ in asset_ids)
        assets = self.connection.execute(
            "SELECT id,source_id,payload FROM assets WHERE id IN (" + placeholders + ") AND state='active'",
            asset_ids,
        ).fetchall()
        if len(assets) != len(asset_ids):
            raise ValueError("输入记录不可用")
        adopted = self._adopt(interpretations or [], assets)
        revision = self.connection.execute("SELECT COALESCE(MAX(revision),0)+1 FROM products WHERE id=?", (product_id,)).fetchone()[0]
        with self.connection:
            self.connection.execute("INSERT INTO products(id,revision,name,created_at) VALUES(?,?,?,?)", (product_id, revision, name, at))
            self.connection.executemany(
                "INSERT INTO product_inputs(product_id,product_revision,asset_id) VALUES(?,?,?)",
                [(product_id, revision, asset_id) for asset_id in asset_ids],
            )
            self.connection.executemany(
                "INSERT INTO product_interpretations(product_id,product_revision,canonical,source_id,field_name,mapping_id,mapping_revision) VALUES(?,?,?,?,?,?,?)",
                [(product_id, revision, m["canonical"], m["source_id"], m["field_name"], m["id"], m["revision"]) for m in adopted],
            )
            details = {"revision": revision, "inputs": asset_ids}
            if adopted:
                details["interpretations"] = [
                    {"mapping": m["id"], "mapping_revision": m["revision"], "canonical": m["canonical"], "source": m["source_id"], "field": m["field_name"]}
                    for m in adopted
                ]
            write(self.connection, at, actor, "product.assemble", product_id, details)
        return revision

    def _adopt(self, interpretations, assets):
        available = set()
        for asset in assets:
            for field in json.loads(asset["payload"]):
                available.add((asset["source_id"], field))
        adopted, seen = [], set()
        for entry in interpretations:
            mapping_id, revision = (entry, None) if isinstance(entry, str) else entry
            if revision is None:
                mapping = self.connection.execute(
                    "SELECT * FROM field_mappings WHERE id=? AND status='approved' ORDER BY revision DESC LIMIT 1",
                    (mapping_id,),
                ).fetchone()
                if mapping is None:
                    raise ValueError("语义映射未通过审核")
            else:
                mapping = self.connection.execute(
                    "SELECT * FROM field_mappings WHERE id=? AND revision=?", (mapping_id, revision)
                ).fetchone()
                if mapping is None:
                    raise KeyError(mapping_id)
                if mapping["status"] != "approved":
                    raise ValueError("语义映射未通过审核")
            key = (mapping["canonical"], mapping["source_id"])
            if key in seen:
                raise ValueError("语义映射重复")
            seen.add(key)
            if (mapping["source_id"], mapping["field_name"]) not in available:
                raise ValueError("语义映射与产品输入不符")
            adopted.append(mapping)
        return adopted

    def inputs(self, product_id, revision):
        rows = self.connection.execute(
            "SELECT a.*,s.owner,s.jurisdiction,s.classification,s.active AS source_active FROM product_inputs p "
            "JOIN assets a ON a.id=p.asset_id JOIN sources s ON s.id=a.source_id "
            "WHERE p.product_id=? AND p.product_revision=? ORDER BY a.id",
            (product_id, revision),
        ).fetchall()
        return [dict(row) for row in rows]

    def interpretations(self, product_id, revision):
        rows = self.connection.execute(
            "SELECT p.canonical,p.source_id,p.field_name,m.meaning,p.mapping_id,p.mapping_revision "
            "FROM product_interpretations p JOIN field_mappings m ON m.id=p.mapping_id AND m.revision=p.mapping_revision "
            "WHERE p.product_id=? AND p.product_revision=? ORDER BY p.canonical,p.source_id",
            (product_id, revision),
        ).fetchall()
        return [dict(row) for row in rows]
