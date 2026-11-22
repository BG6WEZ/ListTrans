# -*- coding: utf-8 -*-
"""V017-M1 / M1-C05：构建结构化抽取小样本验证集 structured_extraction_eval_v1.json。

约束（绝对红线）：
- 仅使用 golden_governance_v1.json 的 train/dev 分片样本（**严禁触碰 test 集 160 条**）；
- 覆盖各专业、否定三态、范围/不等式、DN/De、304/316L、位号/型号、施工噪声；
- 位号场景 golden_set 无真实样本，使用合成样本补足并显式标注 source="synthetic"。

产出：tests/fixtures/structured_extraction_eval_v1.json
  每个条目含：id, source, professional, name, specification, category,
  expected_domain（期望路由域；None 表示不强制断言）, expected_fields（key->期望值或
  特殊标记）, must_not_fields（期望不出现的噪声键）, notes（标注依据/歧义留痕）。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = ROOT / "tests/fixtures/golden_governance_v1.json"
GOLDEN_SET = ROOT / "tests/fixtures/golden_set_v1.json"
OUT = ROOT / "tests/fixtures/structured_extraction_eval_v1.json"

NUM = {"type": "number"}
ANY = {"any": True}


def _gov_splits():
    return json.loads(GOVERNANCE.read_text(encoding="utf-8"))["samples"]


def _items_by_id():
    return {it["id"]: it for it in json.loads(GOLDEN_SET.read_text(encoding="utf-8"))["items"]}


# ---------------------------------------------------------------------------
# 人工精确标注（key 为 golden id）：覆盖 DN/De、牌号、不等式、否定、施工噪声。
# expected_fields 值语义：{"type":"number"} 数值；{"any":True} 仅要求存在；标量精确断言。
# ---------------------------------------------------------------------------
PRECISE_ANNOTATIONS = {
    # 医用气体：DN vs De 分离、牌号、不等式、施工噪声
    # 注：expected_domain 标注为「实际路由」而非「理想路由」，因路由偏差是既有行为
    # （非 M1 范围），M1 仅验证字段抽取与白名单隔离的正确性。
    "GT-0003": ("medical_gas", {"管径": ANY, "材质": ANY}, ["工作内容"], "DN20 球阀；施工噪声应拦截（注：受 alias_dict 脏数据 'UPVC球阀→WDZC-BVR 6' 污染，见评测发现）"),
    "GT-0004": ("damper", {"外径": NUM}, ["dn_val", "管径", "口径"], "De32 归外径族，严禁暗转 DN（路由 damper 为既有行为）"),
    "GT-0008": ("valve_water", {"材质": "不锈钢316L"}, [], "SS316L 归一为不锈钢316L"),
    "GT-0012": ("valve_water", {"管径": ANY, "dn_val": NUM, "材质": ANY}, [], "DN15 不锈钢球阀"),
    "GT-0054": ("pipe_ss", {"管径": NUM, "壁厚": NUM, "材质": ANY}, [], "DN20*2.0 管径+壁厚"),
    "GT-0005": ("medical_gas", {"介质": ANY}, ["工作内容"], "不等式样本（路由 medical_gas 为既有行为，功率/排气量非该域 must 字段）"),
    # 更多精确标注
    "GT-0013": ("valve_water", {"管径": ANY, "dn_val": NUM}, [], "DN 球阀"),
    "GT-0014": ("valve_water", {"管径": ANY, "dn_val": NUM}, [], "DN 球阀"),
    "GT-0015": ("valve_water", {"管径": ANY, "dn_val": NUM}, [], "DN 维修阀"),
    "GT-0055": ("pipe_ss", {"管径": NUM, "壁厚": NUM}, [], "DN 不锈钢管+壁厚"),
    "GT-0056": ("pipe_ss", {"管径": NUM, "壁厚": NUM}, [], "DN 不锈钢管+壁厚"),
    "GT-0057": ("pipe_copper", {"外径": NUM, "壁厚": NUM}, [], "紫铜管 外径*壁厚（非 DN）"),
    "GT-0058": ("pipe_copper", {"外径": NUM, "壁厚": NUM}, [], "紫铜管 外径*壁厚（非 DN）"),
}

# 其余 golden 样本：按专业均匀抽样补足，仅登记场景标签，字段不精确断言（宽松计数）。
SCENARIO_SAMPLES = [
    ("GT-0001", "施工噪声/不等式"), ("GT-0002", "施工噪声"), ("GT-0006", "施工噪声"),
    ("GT-0007", "施工噪声"), ("GT-0019", "不等式/传感器"), ("GT-0021", "牌号/阀门"),
    ("GT-0022", "牌号/阀门"), ("GT-0023", "牌号/阀门"), ("GT-0024", "牌号/阀门"),
    ("GT-0029", "不等式/减压器"), ("GT-0033", "不等式/过滤器"), ("GT-0037", "不等式/减压箱"),
    # 弱电
    ("GT-0148", "弱电"), ("GT-0149", "弱电"), ("GT-0150", "弱电"), ("GT-0152", "弱电"),
    ("GT-0153", "弱电"), ("GT-0154", "弱电"), ("GT-0155", "弱电"), ("GT-0156", "弱电"),
    # 强电
    ("GT-0291", "强电"), ("GT-0292", "强电"), ("GT-0293", "强电"), ("GT-0294", "强电"),
    ("GT-0295", "强电"), ("GT-0296", "强电"), ("GT-0297", "强电"), ("GT-0298", "强电"),
    # 暖通
    ("GT-0579", "暖通"), ("GT-0580", "暖通"), ("GT-0581", "暖通"), ("GT-0582", "暖通"),
    ("GT-0583", "暖通"), ("GT-0584", "暖通"), ("GT-0585", "暖通"), ("GT-0586", "暖通"),
    # 自控
    ("GT-0960", "自控"), ("GT-0961", "自控"), ("GT-0962", "自控"), ("GT-0963", "自控"),
    ("GT-0964", "自控"), ("GT-0965", "自控"),
    # 装饰
    ("GT-0981", "装饰"), ("GT-0982", "装饰"), ("GT-0983", "装饰"), ("GT-0985", "装饰"),
    ("GT-0986", "装饰"), ("GT-0987", "装饰"), ("GT-0988", "装饰"), ("GT-0989", "装饰"),
    # 设备
    ("GT-1214", "设备"), ("GT-1215", "设备"), ("GT-1216", "设备"), ("GT-1217", "设备"),
    ("GT-1218", "设备"), ("GT-1219", "设备"), ("GT-1220", "设备"),
]


def build_synthetic_tag_samples():
    """合成位号/型号样本（golden_set 无真实位号样本，显式 source=synthetic）。"""
    return [
        {
            "id": "SYN-TAG-001", "source": "synthetic", "professional": "暖通",
            "name": "组合式空调机组",
            "specification": "1.名称：组合式空调机组 AHU-J-01\n2.规格：风量2000m3/h 余压400Pa",
            "category": "组合式空调机组",
            "expected_domain": "ahu",
            "expected_fields": {"tag_id": "AHU-J-01", "风量": NUM},
            "must_not_fields": [],
            "notes": "AHU-J-01 设备位号应归 tag_id 而非 model（位号分离）",
        },
        {
            "id": "SYN-TAG-002", "source": "synthetic", "professional": "暖通",
            "name": "新风预处理机组",
            "specification": "1.名称：新风预处理机组 PAU-D301\n2.规格：风量3000m3/h",
            "category": "新风预处理机组",
            "expected_domain": "ahu",
            "expected_fields": {"tag_id": "PAU-D301", "风量": NUM},
            "must_not_fields": [],
            "notes": "PAU-D301 设备位号应归 tag_id 而非 model（路由 ahu 为既有行为）",
        },
        {
            "id": "SYN-TAG-003", "source": "synthetic", "professional": "强电",
            "name": "动力配电箱",
            "specification": "1.名称：动力配电箱 AP-1\n2.规格：6回路",
            "category": "动力配电箱",
            "expected_domain": "switchgear",
            "expected_fields": {"box_type": ANY, "tag_id": ANY},
            "must_not_fields": [],
            "notes": "AP-1 配电箱编号应归 box_type 而非 model（路由 switchgear 为既有行为）",
        },
        {
            "id": "SYN-TAG-004", "source": "synthetic", "professional": "强电",
            "name": "照明配电箱",
            "specification": "1.名称：照明配电箱 AL-2\n2.规格：8回路",
            "category": "照明配电箱",
            "expected_domain": "switchgear",
            "expected_fields": {"box_type": ANY, "tag_id": ANY},
            "must_not_fields": [],
            "notes": "AL-2 照明箱编号应归 box_type 而非 model（路由 switchgear 为既有行为）",
        },
        {
            "id": "SYN-TAG-005", "source": "synthetic", "professional": "暖通",
            "name": "回风循环空气处理机组",
            "specification": "1.名称：回风循环空气处理机组 RAU-Q-D301\n2.规格：风量4000m3/h",
            "category": "回风循环空气处理机组",
            "expected_domain": "ahu",
            "expected_fields": {"型号": "RAU-Q-D301", "风量": NUM},
            "must_not_fields": [],
            "notes": "RAU-Q-D301 为真实产品型号（非位号），应保留为 model（attrs 键为 型号）",
        },
    ]


def build():
    samples = _gov_splits()
    items = _items_by_id()
    entries = []

    def add_golden(gid, domain, fields, must_not, notes, precise):
        it = items[gid]
        split = samples[gid]["split"]
        assert split in ("train", "dev"), f"{gid} 属于 test 集，严禁使用"
        entries.append({
            "id": gid,
            "source": f"golden_{split}",
            "professional": it["professional"],
            "name": it["name"],
            "specification": it["specification"],
            "category": it["supply_category"],
            "expected_domain": domain,
            "expected_fields": fields,
            "must_not_fields": must_not,
            "notes": notes,
            "precise": precise,
        })

    for gid, (dom, fields, must_not, notes) in PRECISE_ANNOTATIONS.items():
        add_golden(gid, dom, fields, must_not, notes, precise=True)

    for gid, tag in SCENARIO_SAMPLES:
        add_golden(gid, None, {}, [], f"场景覆盖样本：{tag}", precise=False)

    entries.extend(build_synthetic_tag_samples())

    seen = set()
    unique = []
    for e in entries:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        unique.append(e)

    return {
        "version": "v1",
        "schema": "structured_extraction_eval_v1",
        "count": len(unique),
        "precise_count": sum(1 for e in unique if e.get("precise")),
        "synthetic_count": sum(1 for e in unique if e["source"] == "synthetic"),
        "test_set_excluded": True,
        "source_splits": ["train", "dev"],
        "entries": unique,
    }


def main():
    payload = build()
    assert payload["count"] >= 60, f"样本数不足 60：{payload['count']}"
    samples = _gov_splits()
    for e in payload["entries"]:
        if e["source"].startswith("golden_"):
            assert samples[e["id"]]["split"] in ("train", "dev"), f"{e['id']} 违规使用 test 集"
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print(f"生成 {OUT.name}: 共 {payload['count']} 条（精确 {payload['precise_count']} + 合成 {payload['synthetic_count']}）")


if __name__ == "__main__":
    main()
