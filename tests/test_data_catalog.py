from hui_agent.data_catalog import DataCatalog, DataPolicyError


def test_schema_is_cache_first_and_refreshes_on_miss():
    refreshes = []
    catalog = DataCatalog(
        schema_cache={"tbclientmap": {"columns": {"bank_acc": "varchar"}}},
        db_refresh=lambda table: refreshes.append(table) or {"columns": {"id": "int"}},
    )

    assert catalog.get_schema("tbclientmap")["columns"]["bank_acc"] == "varchar"
    assert refreshes == []

    assert catalog.get_schema("newtable")["columns"]["id"] == "int"
    assert refreshes == ["newtable"]


def test_runtime_route_comes_only_from_current_log():
    catalog = DataCatalog(schema_cache={})
    route = catalog.get_runtime_route(
        "Actual SQL: ds1 ::: select * from tbdxtrustshare16 where bank_acc = ?"
    )

    assert route["physical_tables"]["tbdxtrustshare"] == ["tbdxtrustshare16"]


def test_business_data_lookup_is_forbidden():
    catalog = DataCatalog(schema_cache={})

    try:
        catalog.select_existing_business_data("select * from tbdxtrustshare limit 1")
    except DataPolicyError as exc:
        assert "禁止依赖数据库现有业务数据" in str(exc)
    else:
        raise AssertionError("business data lookup should be forbidden")
