"""跨版本决策对比：同一份文档在两版规则下的结果归并。

配合 ``POST /api/rulesets/{id}/compare`` 的发布前预演：文档分别按
base / target 两版规则求值（不落库）后，本模块把两次 ``evaluate()``
结果归并为

- ``hit_rule_diff``：生效规则集合差异（``added`` = 仅 target 生效，
  ``removed`` = 仅 base 生效），各自按对应版本的 evaluation_order 排列；
- ``decision_diff``：两份 decision 按 JSON 路径（RFC 6901，与
  input-footprint 一致）递归归并的 ``added`` / ``removed`` / ``changed``。

决策归并规则：

- 两侧都是 dict：按键递归；仅一侧存在的键以其所在路径为根，整个子树
  记入 added / removed（不再向下展开）；
- 其余情况（标量、列表、两侧类型不一致）：值不等即在该路径记一条
  changed。列表不做元素级对齐，整体作为原子值比较；
- 每类差异按路径字典序排列，保证输出确定；两版决策完全一致时三类
  差异均为空列表。
"""

from __future__ import annotations

from typing import Any

from .footprint import json_pointer


def decision_diff(base: Any, target: Any) -> dict[str, list[dict[str, Any]]]:
    """把两份决策结果按 JSON 路径归并为 added/removed/changed 三类差异。"""
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []

    def walk(base_node: Any, target_node: Any, segments: tuple) -> None:
        if isinstance(base_node, dict) and isinstance(target_node, dict):
            for key in base_node:
                if key not in target_node:
                    removed.append(
                        {
                            "path": json_pointer((*segments, key)),
                            "value": base_node[key],
                        }
                    )
            for key in target_node:
                if key not in base_node:
                    added.append(
                        {
                            "path": json_pointer((*segments, key)),
                            "value": target_node[key],
                        }
                    )
            for key in base_node:
                if key in target_node:
                    walk(base_node[key], target_node[key], (*segments, key))
        elif base_node != target_node:
            changed.append(
                {
                    "path": json_pointer(segments),
                    "base": base_node,
                    "target": target_node,
                }
            )

    walk(base, target, ())
    added.sort(key=lambda entry: entry["path"])
    removed.sort(key=lambda entry: entry["path"])
    changed.sort(key=lambda entry: entry["path"])
    return {"added": added, "removed": removed, "changed": changed}


def compare_evaluations(
    base_result: dict[str, Any], target_result: dict[str, Any]
) -> dict[str, Any]:
    """归并两次 ``evaluate()`` 结果的生效规则差异与决策差异。"""
    base_hits = base_result["hit_rule_codes"]
    target_hits = target_result["hit_rule_codes"]
    base_hit_set = set(base_hits)
    target_hit_set = set(target_hits)
    return {
        "hit_rule_diff": {
            "added": [code for code in target_hits if code not in base_hit_set],
            "removed": [code for code in base_hits if code not in target_hit_set],
        },
        "decision_diff": decision_diff(
            base_result["decision"], target_result["decision"]
        ),
    }
