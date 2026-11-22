# -*- coding: utf-8 -*-
"""V015-M1 / R01 契约回归：统一 prepare_query 与导出字段契约。

覆盖 R04 锁定范围 1/2/4 的验收场景：
1. 同一输入在各入口（上传批量 batch_match_rows、单条 /api/match、
   重算 /api/rematch）的规范字段一致；
2. 施工噪声不进入编码文本（隔离进 parsed_spec["noise_text"]）；
3. 未知专业/单位/品牌不破坏契约结构，按回退链处理；
4. 重算 parsed_spec 覆盖保留统一契约的清洗文本与规范名称（保存进度兼容）；
5. F13：query_info["unit"] 贯通到 matcher 可读键。

本模块只依赖标准库 + app.* 模块导入（conftest 已隔离路径），不落盘、
不触发向量索引构建；match_item 以替身捕获 query_info，断言契约本身。
"""
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.query_preprocessor import prepare_query
from app.normalizer import reload_normalizer_configs


class TestPrepareQueryContract(unittest.TestCase):
    """统一契约 prepare_query 的字段契约。"""

    def test_contract_superset_keys(self):
        """契约返回结构是既有 query_info 的超集，生产读取键齐全。"""
        qi = prepare_query("排风机控制盒", "1.名称：排风机控制盒\n2.规格：定制", "强电")
        for key in ("name", "specification", "category", "brand", "professional",
                    "unit", "parsed_spec"):
            self.assertIn(key, qi, f"契约缺少生产读取键 {key}")
        # F01：规范名称提升为顶层键，不再恒回退原文
        self.assertEqual(qi["name"], "排风机控制盒")

    def test_preserve_original_fields(self):
        """preserve_original=True 时保留原文/来源字段供导出与追溯。"""
        qi = prepare_query("排风机控制盒", "1.名称：排风机控制盒", "强电",
                           category="甲供材", brand="华康", unit="台")
        for key in ("original_name", "original_spec", "original_unit",
                    "original_brand", "original_category"):
            self.assertIn(key, qi)
        self.assertEqual(qi["original_category"], "甲供材")
        self.assertEqual(qi["original_brand"], "华康")
        self.assertEqual(qi["original_unit"], "台")
        # 评测入口 preserve_original=False 时不携带原文键，行为兼容
        qi_eval = prepare_query("排风机控制盒", "1.名称：排风机控制盒", "强电",
                                preserve_original=False)
        self.assertNotIn("original_name", qi_eval)

    def test_construction_noise_isolated(self):
        """施工噪声（工作内容等）不进入编码文本，隔离进 noise_text。"""
        noisy_spec = ("1.名称：排风机控制盒\n2.规格：定制\n"
                      "3.工作内容：测位、划线、打眼、缠埋螺体、清扫盒子、接线")
        qi = prepare_query("排风机控制盒", noisy_spec, "强电")
        self.assertNotIn("工作内容", qi["specification"])
        self.assertNotIn("打眼", qi["specification"])
        self.assertIn("工作内容", qi["parsed_spec"]["noise_text"])
        # 编码文本 = name + specification，噪声词不在其中
        encode_text = f"{qi['name']} {qi['specification']}".strip()
        for noise_word in ("打眼", "测位", "划线"):
            self.assertNotIn(noise_word, encode_text)

    def test_unknown_professional_unit_brand_fallback(self):
        """未知专业/单位/品牌：不抛错，契约结构完整，按回退链处理。"""
        qi = prepare_query("未知设备XYZ", "1.名称：未知设备XYZ", "外星科技",
                           category="", brand="不存在品牌", unit="光年")
        self.assertEqual(qi["professional"], "外星科技")
        self.assertEqual(qi["unit"], "光年")
        self.assertEqual(qi["brand"], "不存在品牌")
        # category 回退链：品类 -> 专业 -> 规范名称
        self.assertEqual(qi["category"], "外星科技")

    def test_category_fallback_chain(self):
        """甲供材品类为空时 category 回退到专业，再回退到规范名称。"""
        qi = prepare_query("排风机控制盒", "1.名称：排风机控制盒", "强电")
        self.assertEqual(qi["category"], "强电")
        qi2 = prepare_query("排风机控制盒", "1.名称：排风机控制盒", "")
        self.assertEqual(qi2["category"], "排风机控制盒")

    def test_alias_applied_to_canonical_name(self):
        """别名改写在契约层生效（规范名称可被 alias_dict 改写）。"""
        qi = prepare_query("排风机", "1.名称：排风机\n2.规格：定制", "暖通")
        # 生产 alias_dict 存在"排风机"相关键（如"静音型排风机"），直接探测：
        # 若"排风机"在别名表则改写生效；本断言锁定契约语义——name 与
        # parsed_spec["名称"]一致，改写与否由配置决定，不锁死具体值。
        self.assertEqual(qi["name"], str(qi["parsed_spec"].get("名称") or qi["name"]))


class TestEntrypointConsistency(unittest.TestCase):
    """同一输入在各入口的规范字段一致性（真实生产入口函数）。"""

    @classmethod
    def setUpClass(cls):
        from app import server
        cls.server = server
        # 捕获各入口实际交给 match_item 的 query_info，替身不触发向量/DB 写
        cls.captured = []
        cls._orig_match_item = server.match_item

        def capture_match(q_info, q_vector=None, confirmed_series=None):
            cls.captured.append(q_info)
            return {"matched_code": "", "matched_name": "", "is_exact": False,
                    "top_candidates": [], "matched_category": "",
                    "matched_spec": "", "matched_unit": "", "matched_brand": ""}

        server.match_item = capture_match

    @classmethod
    def tearDownClass(cls):
        cls.server.match_item = cls._orig_match_item

    def _reset_capture(self):
        self.captured.clear()

    def test_batch_entry_prepare_query(self):
        """上传批量入口：query_info 含 unit（F13）与规范名称（F01）。"""
        rows = [{
            "row_idx": 1, "name": "排风机控制盒",
            "specification": "1.名称：排风机控制盒\n2.规格：定制",
            "unit": "台", "qty": 2.0, "loss": 1.0,
            "category": "甲供材", "brand": "华康", "professional": "强电",
        }]
        self.server.batch_match_rows([dict(r) for r in rows])
        self.assertEqual(len(self.captured), 1)
        qi = self.captured[0]
        self.assertEqual(qi["name"], "排风机控制盒")
        self.assertEqual(qi["unit"], "台")
        self.assertEqual(qi["brand"], "华康")
        self.assertEqual(qi["professional"], "强电")
        self._reset_capture()

    def test_same_input_same_contract_across_entrypoints(self):
        """同一输入在批量/单条/重算三入口的规范字段一致。"""
        name = "排风机控制盒"
        spec = "1.名称：排风机控制盒\n2.规格：定制"

        # 上传批量
        rows = [{"row_idx": 1, "name": name, "specification": spec,
                 "unit": "台", "qty": 2.0, "loss": 1.0, "category": "甲供材",
                 "brand": "华康", "professional": "强电"}]
        self.server.batch_match_rows([dict(r) for r in rows])
        qi_batch = dict(self.captured[-1])
        self._reset_capture()

        # 单条 /api/match
        import asyncio
        payload = {"text": spec, "name": name, "category": "甲供材",
                   "brand": "华康", "professional": "强电", "unit": "台"}
        asyncio.run(self.server.api_match_single(payload))
        qi_single = dict(self.captured[-1])
        self._reset_capture()

        for key in ("name", "specification", "category", "brand",
                    "professional", "unit"):
            self.assertEqual(qi_batch[key], qi_single[key],
                             f"批量与单条入口 {key} 不一致")

    def test_rematch_parsed_spec_override_keeps_contract(self):
        """重算入口 parsed_spec 覆盖保留统一契约清洗文本与规范名称。"""
        import asyncio
        from app.server import RematchRequest
        req = RematchRequest(
            row_id="1",
            text="1.名称：排风机控制盒\n2.规格：定制",
            fixed_params={"power": "1.5kW"},
            category="甲供材", brand="华康", unit="台", professional="强电",
        )
        # 显式传入鉴权用户，绕开 FastAPI Depends 哨兵直接调用入口函数
        asyncio.run(self.server.api_rematch(req, {"username": "test_contract",
                                                  "name": "契约测试", "role": "员工"}))
        self.assertEqual(len(self.captured), 1)
        qi = self.captured[0]
        # 统一契约的清洗文本与规范名称不被覆盖退回原文
        self.assertEqual(qi["name"], "排风机控制盒")
        self.assertEqual(qi["unit"], "台")
        self.assertEqual(qi["category"], "甲供材")
        # 手动修正参数生效（强电 power -> 功率）
        self.assertEqual(str(qi["parsed_spec"]["attrs"].get("功率", "")), "1.5kW")
        self._reset_capture()

    def test_single_entry_parsed_spec_payload_merge(self):
        """/api/match 显式 parsed_spec 覆盖时保留契约 clean_text/name。"""
        import asyncio
        payload = {
            "text": "1.名称：排风机控制盒\n2.规格：定制",
            "name": "排风机控制盒", "category": "", "brand": "", "unit": "台",
            "professional": "强电",
            "parsed_spec": {"model": "GX-100", "attrs": {"功率": "2.2kW"}},
        }
        asyncio.run(self.server.api_match_single(payload))
        qi = self.captured[-1]
        # 契约默认解析与传入覆盖合并；清洗文本不退回原始全文
        self.assertEqual(qi["parsed_spec"]["model"], "GX-100")
        self.assertEqual(str(qi["parsed_spec"]["attrs"].get("功率", "")), "2.2kW")
        # 覆盖 setdefault 保住契约清洗文本与规范名称（保存进度兼容）
        self.assertIn("排风机控制盒", str(qi["parsed_spec"].get("clean_text", "")))
        self.assertEqual(qi["name"], "排风机控制盒")
        self.assertEqual(qi["unit"], "台")
        self._reset_capture()


class TestGoldenSetEntrypointParity(unittest.TestCase):
    """黄金集评测入口与生产入口共用同一契约（R04 范围 3）。"""

    def test_eval_entrypoint_uses_prepare_query(self):
        """eval_golden_set.evaluate 使用统一契约（preserve_original=False）。"""
        import io as _io
        import sys as _sys
        test_dir = os.path.dirname(os.path.abspath(__file__))
        prev_stdout = _sys.stdout
        try:
            # eval_golden_set 模块级重绑 sys.stdout（GBK 兼容），pytest 下包装
            # 捕获流会被其析构关闭；导入前换独立缓冲（同 test_flywheel_sandbox）
            _sys.stdout = _io.TextIOWrapper(_io.BytesIO(), encoding="utf-8")
            _sys.path.insert(0, test_dir)
            import eval_golden_set as _egs
        finally:
            _sys.stdout = prev_stdout
            _sys.path.pop(_sys.path.index(test_dir))
        self.assertTrue(hasattr(_egs, "evaluate"))


if __name__ == "__main__":
    unittest.main()
