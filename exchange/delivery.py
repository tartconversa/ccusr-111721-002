import hashlib
import json

from .audit import write
from .grants import Grants
from .policy import Policies
from .products import Products


class Delivery:
    def __init__(self, connection):
        self.connection = connection
        self.grants = Grants(connection)
        self.policies = Policies(connection)
        self.products = Products(connection)

    def send(self, delivery_id, product_id, revision, recipient, purpose, at):
        existing = self.connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        if existing:
            if (existing["product_id"], existing["product_revision"], existing["recipient"], existing["purpose"]) != (product_id, revision, recipient, purpose):
                raise ValueError("交付编号已用于其他请求")
            return dict(existing)
        product = self.connection.execute("SELECT state FROM products WHERE id=? AND revision=?", (product_id, revision)).fetchone()
        if product is None or product["state"] != "draft":
            raise ValueError("数据产品修订不可交付")
        inputs = self.products.inputs(product_id, revision)
        if not inputs:
            raise ValueError("数据产品缺少输入")
        interpretations = self.products.interpretations(product_id, revision)
        reasons, revisions = [], {}
        for asset in inputs:
            source_id = asset["source_id"]
            if asset["state"] != "active" or not asset["source_active"]:
                reasons.append({"source": source_id, "reason": "inactive"})
            if not self.grants.permits(source_id, recipient, purpose, at):
                reasons.append({"source": source_id, "reason": "grant"})
            decision, policy_revision = self.policies.decision(asset["jurisdiction"], asset["classification"], purpose)
            revisions[source_id] = policy_revision
            if decision != "allow":
                reasons.append({"source": source_id, "reason": "policy"})
        reasons.extend(self._unresolved(inputs, interpretations))
        if reasons:
            with self.connection:
                write(self.connection, at, recipient, "delivery.reject", product_id, {"revision": revision, "reasons": reasons})
            raise PermissionError(reasons)
        digest = hashlib.sha256("|".join(asset["digest"] for asset in inputs).encode()).hexdigest()
        with self.connection:
            self.connection.execute(
                "INSERT INTO deliveries(id,product_id,product_revision,recipient,purpose,created_at,input_digest,policy_revisions,interpretations) VALUES(?,?,?,?,?,?,?,?,?)",
                (delivery_id, product_id, revision, recipient, purpose, at, digest, json.dumps(revisions, sort_keys=True), json.dumps(interpretations, ensure_ascii=False, sort_keys=True)),
            )
            write(self.connection, at, recipient, "delivery.accept", delivery_id, {"product": product_id, "revision": revision, "inputs": [a["id"] for a in inputs]})
        return dict(self.connection.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone())

    def _unresolved(self, inputs, interpretations):
        covered = {(item["source_id"], item["field_name"]) for item in interpretations}
        fields = {}
        for asset in inputs:
            for name in json.loads(asset["payload"]):
                fields.setdefault(name, {})[asset["source_id"]] = self._meaning(asset["source_id"], name)
        issues = []
        for name in sorted(fields):
            per_source = fields[name]
            if len(set(per_source.values())) > 1:
                missing = sorted(source for source in per_source if (source, name) not in covered)
                if missing:
                    issues.append({"field": name, "sources": missing, "reason": "unresolved"})
        return issues

    def _meaning(self, source_id, name):
        row = self.connection.execute("SELECT meaning FROM fields WHERE source_id=? AND name=?", (source_id, name)).fetchone()
        return row["meaning"] if row else None
