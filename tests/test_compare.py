"""跨版本决策对比：decision_diff 的 JSON 路径归并与 compare_evaluations 汇总。"""

from app.engine import compare_evaluations, decision_diff


def test_decision_diff_empty_when_equal():
    decision = {"a": 1, "b": {"c": [1, 2]}, "d": "x"}
    assert decision_diff(decision, dict(decision)) == {
        "added": [],
        "removed": [],
        "changed": [],
    }
    assert decision_diff({}, {}) == {"added": [], "removed": [], "changed": []}


def test_decision_diff_top_level_added_removed_changed():
    diff = decision_diff(
        {"keep": 1, "drop": True, "score": 1},
        {"keep": 1, "score": 2, "tag": "new"},
    )
    assert diff["added"] == [{"path": "/tag", "value": "new"}]
    assert diff["removed"] == [{"path": "/drop", "value": True}]
    assert diff["changed"] == [{"path": "/score", "base": 1, "target": 2}]


def test_decision_diff_recurses_into_nested_dicts():
    diff = decision_diff(
        {"cfg": {"limit": 1, "keep": "x"}},
        {"cfg": {"limit": 2, "keep": "x", "extra": None}},
    )
    assert diff["added"] == [{"path": "/cfg/extra", "value": None}]
    assert diff["removed"] == []
    assert diff["changed"] == [{"path": "/cfg/limit", "base": 1, "target": 2}]


def test_decision_diff_missing_subtree_reported_at_root_path():
    # 仅一侧存在的键以其根路径整体记入，不再展开子树
    diff = decision_diff(
        {"a": {"x": 1, "y": {"z": 2}}},
        {"b": [{"n": 1}]},
    )
    assert diff["added"] == [{"path": "/b", "value": [{"n": 1}]}]
    assert diff["removed"] == [{"path": "/a", "value": {"x": 1, "y": {"z": 2}}}]
    assert diff["changed"] == []


def test_decision_diff_lists_compared_atomically():
    diff = decision_diff({"list": [1, 2]}, {"list": [1, 3]})
    assert diff["changed"] == [{"path": "/list", "base": [1, 2], "target": [1, 3]}]
    # 类型不一致同样记为 changed
    diff = decision_diff({"v": {"a": 1}}, {"v": [1]})
    assert diff["changed"] == [{"path": "/v", "base": {"a": 1}, "target": [1]}]


def test_decision_diff_rfc_6901_escaping_and_sorting():
    diff = decision_diff(
        {"z": 1, "a~b/c": 1},
        {"z": 2, "a~b/c": 2, "m": {"n": 1}},
    )
    assert diff["added"] == [{"path": "/m", "value": {"n": 1}}]
    assert diff["removed"] == []
    # 路径按 RFC 6901 转义后的字典序排列
    assert diff["changed"] == [
        {"path": "/a~0b~1c", "base": 1, "target": 2},
        {"path": "/z", "base": 1, "target": 2},
    ]


def test_compare_evaluations_hit_rule_diff():
    base_result = {
        "hit_rule_codes": ["r1", "r2", "r3"],
        "decision": {"level": "high", "extra": True},
    }
    target_result = {
        "hit_rule_codes": ["r1", "r4", "r2"],
        "decision": {"level": "high", "tag": "new"},
    }
    diff = compare_evaluations(base_result, target_result)
    # added 按 target 顺序、removed 按 base 顺序
    assert diff["hit_rule_diff"] == {"added": ["r4"], "removed": ["r3"]}
    assert diff["decision_diff"]["added"] == [{"path": "/tag", "value": "new"}]
    assert diff["decision_diff"]["removed"] == [{"path": "/extra", "value": True}]
    assert diff["decision_diff"]["changed"] == []


def test_compare_evaluations_identical_results_give_empty_diff():
    result = {"hit_rule_codes": ["r1"], "decision": {"a": 1}}
    assert compare_evaluations(result, dict(result)) == {
        "hit_rule_diff": {"added": [], "removed": []},
        "decision_diff": {"added": [], "removed": [], "changed": []},
    }
