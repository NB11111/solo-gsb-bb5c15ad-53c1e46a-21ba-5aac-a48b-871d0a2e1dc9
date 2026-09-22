"""POST /api/rulesets/{id}/compare 接口测试。"""

import pytest


@pytest.fixture()
def two_version_ruleset(client):
    """v1 -> v2：r1 的 score 变化、r3 删除、r4 新增。"""
    v1 = {
        "name": "cmp",
        "rules": [
            {
                "code": "r1",
                "condition": 'doc["x"] >= 10',
                "action": '{"level": "high", "score": 1}',
            },
            {
                "code": "r2",
                "condition": "r1.hit",
                "depends_on": ["r1"],
                "action": '{"bonus": 1}',
            },
            {
                "code": "r3",
                "condition": 'doc["x"] >= 50',
                "action": '{"extra": true}',
            },
        ],
    }
    rid = client.post("/api/rulesets", json=v1).json()["ruleset_id"]
    v2 = {
        "name": "cmp",
        "rules": [
            {
                "code": "r1",
                "condition": 'doc["x"] >= 10',
                "action": '{"level": "high", "score": 2}',
            },
            {
                "code": "r2",
                "condition": "r1.hit",
                "depends_on": ["r1"],
                "action": '{"bonus": 1}',
            },
            {
                "code": "r4",
                "condition": 'doc["x"] >= 10',
                "action": '{"tag": "new"}',
            },
        ],
    }
    resp = client.put(f"/api/rulesets/{rid}", json=v2)
    assert resp.status_code == 200
    return rid


def _compare(client, rid, base_version=1, target_version=2, document=None):
    return client.post(
        f"/api/rulesets/{rid}/compare",
        json={
            "base_version": base_version,
            "target_version": target_version,
            "document": {"x": 100} if document is None else document,
        },
    )


# ---------- 跨版本对比 ----------


def test_compare_across_versions(client, two_version_ruleset):
    resp = _compare(client, two_version_ruleset)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ruleset_id"] == two_version_ruleset
    assert body["base_version"] == 1
    assert body["target_version"] == 2
    assert body["document"] == {"x": 100}

    # 两版各自独立求值的决策与命中摘要
    assert body["base"]["decision"] == {
        "level": "high",
        "score": 1,
        "bonus": 1,
        "extra": True,
    }
    assert body["base"]["hit_rule_codes"] == ["r1", "r2", "r3"]
    assert body["base"]["fired_rule_codes"] == ["r1", "r2", "r3"]
    assert body["base"]["converged"] is True
    assert body["target"]["decision"] == {
        "level": "high",
        "score": 2,
        "bonus": 1,
        "tag": "new",
    }
    assert body["target"]["hit_rule_codes"] == ["r1", "r2", "r4"]

    # 生效规则差异：r4 新增生效，r3 不再生效
    assert body["hit_rule_diff"] == {"added": ["r4"], "removed": ["r3"]}

    # 决策按 JSON 路径归并
    assert body["decision_diff"] == {
        "added": [{"path": "/tag", "value": "new"}],
        "removed": [{"path": "/extra", "value": True}],
        "changed": [{"path": "/score", "base": 1, "target": 2}],
    }


def test_compare_conflict_summary_per_version(client):
    # 同一冲突组在两版中优先级互换 -> 胜者不同，各自给出冲突解释
    v1 = {
        "name": "conf",
        "default_conflict_policy": "priority",
        "rules": [
            {
                "code": "a",
                "priority": 1,
                "conflict_group": "g",
                "condition": "true",
                "action": '{"winner": "a"}',
            },
            {
                "code": "b",
                "priority": 2,
                "conflict_group": "g",
                "condition": "true",
                "action": '{"winner": "b"}',
            },
        ],
    }
    rid = client.post("/api/rulesets", json=v1).json()["ruleset_id"]
    v2 = {
        "name": "conf",
        "default_conflict_policy": "priority",
        "rules": [
            {
                "code": "a",
                "priority": 3,
                "conflict_group": "g",
                "condition": "true",
                "action": '{"winner": "a"}',
            },
            {
                "code": "b",
                "priority": 2,
                "conflict_group": "g",
                "condition": "true",
                "action": '{"winner": "b"}',
            },
        ],
    }
    client.put(f"/api/rulesets/{rid}", json=v2)

    body = _compare(client, rid, document={}).json()
    assert body["base"]["decision"] == {"winner": "b"}
    assert body["target"]["decision"] == {"winner": "a"}

    base_conflict = body["base"]["conflicts"][0]
    assert base_conflict["group"] == "g"
    assert base_conflict["policy"] == "priority"
    assert base_conflict["winner"] == "b"
    assert [s["rule_code"] for s in base_conflict["suppressed"]] == ["a"]
    target_conflict = body["target"]["conflicts"][0]
    assert target_conflict["winner"] == "a"
    assert [s["rule_code"] for s in target_conflict["suppressed"]] == ["b"]

    assert body["hit_rule_diff"] == {"added": ["a"], "removed": ["b"]}
    assert body["decision_diff"]["changed"] == [
        {"path": "/winner", "base": "b", "target": "a"}
    ]


def test_compare_nested_decision_diff(client):
    v1 = {
        "name": "nested",
        "rules": [
            {
                "code": "a",
                "condition": "true",
                "action": '{"cfg": {"limit": 1, "keep": "x"}, "list": [1, 2]}',
            }
        ],
    }
    rid = client.post("/api/rulesets", json=v1).json()["ruleset_id"]
    v2 = {
        "name": "nested",
        "rules": [
            {
                "code": "a",
                "condition": "true",
                # 嵌套字段变化、列表整体变化、新增含 ~ 和 / 的键
                "action": (
                    '{"cfg": {"limit": 2, "keep": "x"}, "list": [1, 3],'
                    ' "a~b/c": 1}'
                ),
            }
        ],
    }
    client.put(f"/api/rulesets/{rid}", json=v2)

    body = _compare(client, rid, document={}).json()
    diff = body["decision_diff"]
    # 新增键按 RFC 6901 转义：~ -> ~0、/ -> ~1
    assert diff["added"] == [{"path": "/a~0b~1c", "value": 1}]
    assert diff["removed"] == []
    # 嵌套 dict 归并到叶子路径；列表作为原子值整体比较
    assert diff["changed"] == [
        {"path": "/cfg/limit", "base": 1, "target": 2},
        {"path": "/list", "base": [1, 2], "target": [1, 3]},
    ]


# ---------- 空差异 ----------


def test_compare_same_version_gives_empty_diff(client, two_version_ruleset):
    body = _compare(client, two_version_ruleset, base_version=1, target_version=1).json()
    assert body["base"] == body["target"]
    assert body["hit_rule_diff"] == {"added": [], "removed": []}
    assert body["decision_diff"] == {"added": [], "removed": [], "changed": []}


def test_compare_different_versions_same_outcome_empty_diff(client):
    # v2 放宽阈值，但对本文档结果完全一致 -> 空差异
    v1 = {
        "name": "same",
        "rules": [
            {
                "code": "r1",
                "condition": 'doc["x"] >= 10',
                "action": '{"level": "high"}',
            }
        ],
    }
    rid = client.post("/api/rulesets", json=v1).json()["ruleset_id"]
    v2 = {
        "name": "same",
        "rules": [
            {
                "code": "r1",
                "condition": 'doc["x"] >= 5',
                "action": '{"level": "high"}',
            }
        ],
    }
    client.put(f"/api/rulesets/{rid}", json=v2)

    resp = _compare(client, rid, document={"x": 100})
    assert resp.status_code == 200
    body = resp.json()
    assert body["base"]["decision"] == body["target"]["decision"] == {
        "level": "high"
    }
    assert body["hit_rule_diff"] == {"added": [], "removed": []}
    assert body["decision_diff"] == {"added": [], "removed": [], "changed": []}


# ---------- 不落库 ----------


def test_compare_does_not_write_evaluations(client, two_version_ruleset):
    resp = _compare(client, two_version_ruleset)
    assert resp.status_code == 200
    assert client.get(f"/api/rulesets/{two_version_ruleset}/evaluations").json() == []
    stats = client.get(f"/api/rulesets/{two_version_ruleset}/evaluation-stats").json()
    assert stats["total_evaluations"] == 0


# ---------- 求值失败隔离 ----------


def test_compare_evaluation_failure_is_isolated(
    client, two_version_ruleset, monkeypatch
):
    from app.engine import EvaluationFailed
    import app.main as main_module

    real_evaluate = main_module.evaluate
    calls = []

    def flaky_evaluate(compiled, document):
        calls.append(1)
        if len(calls) > 1:
            # base 版本求值成功，target 版本失败
            raise EvaluationFailed(
                "NOT_CONVERGED", "模拟不收敛", {"max_iterations": 100}
            )
        return real_evaluate(compiled, document)

    monkeypatch.setattr(main_module, "evaluate", flaky_evaluate)

    resp = _compare(client, two_version_ruleset)
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "NOT_CONVERGED"
    assert error["message"] == "模拟不收敛"
    assert error["details"] == {"max_iterations": 100}

    # 失败被隔离：两版求值都不产生持久化记录，后续求值不受影响
    assert client.get(f"/api/rulesets/{two_version_ruleset}/evaluations").json() == []
    monkeypatch.undo()
    ok = client.post(
        f"/api/rulesets/{two_version_ruleset}/evaluate",
        json={"document": {"x": 100}},
    )
    assert ok.status_code == 200
    assert ok.json()["decision"]["tag"] == "new"


# ---------- 错误结构 ----------


def test_compare_ruleset_not_found(client):
    resp = client.post(
        "/api/rulesets/nope/compare",
        json={"base_version": 1, "target_version": 2, "document": {}},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_compare_version_not_found(client, two_version_ruleset):
    resp = _compare(client, two_version_ruleset, base_version=99)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "VERSION_NOT_FOUND"

    resp = _compare(client, two_version_ruleset, target_version=99)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "VERSION_NOT_FOUND"


def test_compare_validates_request_body(client, two_version_ruleset):
    rid = two_version_ruleset
    # 版本必须 >= 1
    resp = client.post(
        f"/api/rulesets/{rid}/compare",
        json={"base_version": 0, "target_version": 1, "document": {}},
    )
    assert resp.status_code == 422
    resp = client.post(
        f"/api/rulesets/{rid}/compare",
        json={"base_version": 1, "target_version": -2, "document": {}},
    )
    assert resp.status_code == 422

    # 缺少必填版本字段
    resp = client.post(
        f"/api/rulesets/{rid}/compare", json={"target_version": 1}
    )
    assert resp.status_code == 422
    resp = client.post(f"/api/rulesets/{rid}/compare", json={"base_version": 1})
    assert resp.status_code == 422

    # document 缺省为空对象
    resp = client.post(
        f"/api/rulesets/{rid}/compare",
        json={"base_version": 1, "target_version": 2},
    )
    assert resp.status_code == 200
    assert resp.json()["document"] == {}
