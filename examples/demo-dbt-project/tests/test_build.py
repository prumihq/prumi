"""The demo build writes the warehouse and the semantic manifest that Prumi reads."""

import json

import duckdb

MODELS = ["customers", "products", "orders", "order_items", "inventory_snapshots"]
METRICS = [
    "revenue",
    "orders",
    "units_sold",
    "average_order_value",
    "customers",
    "gross_margin",
    "gross_margin_pct",
    "units_on_hand",
    "discount_rate",
]


def test_manifest_has_the_semantic_models_and_metrics(demo_project):
    manifest = json.loads(demo_project.manifest_path.read_text())
    relations = {
        m["name"]: m["node_relation"]["relation_name"] for m in manifest["semantic_models"]
    }
    assert relations == {name: f'"demo"."main"."{name}"' for name in MODELS}
    assert set(METRICS) <= {m["name"] for m in manifest["metrics"]}


def test_warehouse_has_the_models(demo_project):
    with duckdb.connect(str(demo_project.db_path), read_only=True) as conn:
        for name in MODELS:
            raw = conn.execute(f"SELECT count(*) FROM raw.{name}").fetchone()[0]
            built = conn.execute(f'SELECT count(*) FROM "demo"."main"."{name}"').fetchone()[0]
            assert built == raw > 0, name
        assert conn.execute("SELECT count(*) FROM main.metricflow_time_spine").fetchone()[0]
