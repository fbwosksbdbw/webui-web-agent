import json
from pathlib import Path


def test_base_tables_contains_controlled_product_and_seller_policies():
    path = Path(__file__).resolve().parents[1] / "knowledge" / "config" / "base_tables.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    for table in ["tbtainfo", "tbproduct", "tbdxtrustproduct", "tbdxcpproduct", "tbinsureproduct", "tbclientmap", "tbclient", "tbclientseller"]:
        assert table in data
        assert data[table]["policy"]["kind"] == "controlled_base"
        assert data[table]["schema"]["schema_sources"]

    assert data["tbdxcpproduct"]["policy"]["modules"] == ["商业养老金"]
    assert data["tbinsureproduct"]["policy"]["modules"] == ["保险"]
    assert data["tbinsureproduct"]["schema"]["schema_status"] == "missing_cache"

    seller = data["tbclientseller"]
    assert seller["policy"]["sharded"] is True
    assert seller["policy"]["physical_table_pattern"] == "tbclientseller{1..16}"
    assert seller["schema"]["same_schema_across_shards"] is True
    assert seller["schema"]["reference_physical_table"] == "tbclientseller1"
    assert "open_branch" in seller["schema"]["columns"]
    assert "in_client_no" in seller["records"]["default_branch_customer"]

    clientmap = data["tbclientmap"]
    assert clientmap["policy"]["db"] == "ui_bc"
    assert "bank_acc" in clientmap["schema"]["columns"]
    assert clientmap["records"]["default_customer"]["bank_acc"] == "A33060210021113"

    client = data["tbclient"]
    assert client["policy"]["physical_table_pattern"] == "tbclient{1..16}"
    assert "client_name" in client["schema"]["columns"]
    assert client["records"]["default_customer"]["in_client_no"] == "33060210021113"
