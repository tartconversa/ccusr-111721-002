import hashlib
import json

from .audit import write


class Catalog:
    def __init__(self, connection):
        self.connection = connection

    def register(self, source_id, owner, jurisdiction, classification, fields, at):
        if not fields or len({field[0] for field in fields}) != len(fields):
            raise ValueError("来源字段缺失或重复")
        with self.connection:
            self.connection.execute(
                "INSERT INTO sources(id,owner,jurisdiction,classification) VALUES(?,?,?,?)",
                (source_id, owner, jurisdiction, classification),
            )
            self.connection.executemany(
                "INSERT INTO fields(source_id,name,meaning,kind,sensitive) VALUES(?,?,?,?,?)",
                [(source_id, name, meaning, kind, int(sensitive)) for name, meaning, kind, sensitive in fields],
            )
            write(self.connection, at, owner, "source.register", source_id, {"jurisdiction": jurisdiction})

    def ingest(self, asset_id, source_id, payload, at):
        source = self.connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None or not source["active"]:
            raise ValueError("来源不可用")
        allowed = {row[0] for row in self.connection.execute("SELECT name FROM fields WHERE source_id=?", (source_id,))}
        if not isinstance(payload, dict) or not payload or set(payload) - allowed:
            raise ValueError("记录字段不符合来源目录")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connection:
            self.connection.execute(
                "INSERT INTO assets(id,source_id,schema_version,payload,digest,received_at) VALUES(?,?,?,?,?,?)",
                (asset_id, source_id, source["schema_version"], canonical, digest, at),
            )
            write(self.connection, at, source["owner"], "asset.ingest", asset_id, {"source": source_id, "digest": digest})
        return digest

    def get(self, asset_id):
        row = self.connection.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return dict(row)

    def propose_mapping(self, mapping_id, canonical, source_id, field_name, at, actor):
        if not canonical:
            raise ValueError("语义映射缺少目标字段名")
        field = self.connection.execute(
            "SELECT f.meaning,s.schema_version FROM fields f JOIN sources s ON s.id=f.source_id "
            "WHERE f.source_id=? AND f.name=?",
            (source_id, field_name),
        ).fetchone()
        if field is None:
            raise KeyError((source_id, field_name))
        revision = self.connection.execute("SELECT COALESCE(MAX(revision),0)+1 FROM field_mappings WHERE id=?", (mapping_id,)).fetchone()[0]
        with self.connection:
            self.connection.execute(
                "INSERT INTO field_mappings(id,revision,canonical,source_id,field_name,meaning,schema_version,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,'pending',?)",
                (mapping_id, revision, canonical, source_id, field_name, field["meaning"], field["schema_version"], at),
            )
            write(self.connection, at, actor, "mapping.propose", mapping_id, {"revision": revision, "canonical": canonical, "source": source_id, "field": field_name})
        return revision

    def decide_mapping(self, mapping_id, revision, approve, at, actor):
        row = self.connection.execute("SELECT status FROM field_mappings WHERE id=? AND revision=?", (mapping_id, revision)).fetchone()
        if row is None:
            raise KeyError(mapping_id)
        if row["status"] != "pending":
            raise ValueError("语义映射已审结")
        status = "approved" if approve else "rejected"
        with self.connection:
            self.connection.execute("UPDATE field_mappings SET status=?,decided_at=? WHERE id=? AND revision=?", (status, at, mapping_id, revision))
            write(self.connection, at, actor, "mapping.decide", mapping_id, {"revision": revision, "status": status})
        return status

    def mapping(self, mapping_id, revision=None):
        if revision is None:
            row = self.connection.execute("SELECT * FROM field_mappings WHERE id=? ORDER BY revision DESC LIMIT 1", (mapping_id,)).fetchone()
        else:
            row = self.connection.execute("SELECT * FROM field_mappings WHERE id=? AND revision=?", (mapping_id, revision)).fetchone()
        if row is None:
            raise KeyError(mapping_id)
        return dict(row)

    def mappings(self, status=None):
        if status is None:
            rows = self.connection.execute("SELECT * FROM field_mappings ORDER BY id,revision").fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM field_mappings WHERE status=? ORDER BY id,revision", (status,)).fetchall()
        return [dict(row) for row in rows]
