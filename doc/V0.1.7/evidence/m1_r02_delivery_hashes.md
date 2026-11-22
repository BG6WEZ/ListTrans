# V017-M1 / R02 逐文件交付 SHA256

2026-11-19，开发 AI。SHA256 为未提交工作区 R02 整改后最终文件哈希，供独立审核比对。相对 R01 新增 `app/typed_fields.py` 与 `tests/test_v017_m1_typed_fields.py`（R01 漏列，见 A-M1-06）。

## app/ 运行模块（M1 改动，含 R02 整改）

| 文件 | SHA256 | R02 变更 |
|---|---|---|
| app/matcher.py | a92682c82f2a50dacd4ea57ca03f60729f30b06fd42f00fd4d83b430f6e4646f | A-M1-01：通用属性循环排除 box_type/tag_id |
| app/preprocessor.py | f7e54d0405ae1acf0258474b5f6914c53ce7a5dbf0c6676a44ad4363e3949511 | 无 |
| app/query_preprocessor.py | 803e817767cba2c39ecf7c76f463e03b0d1fc02bbba7cce0fdcf830f952de643 | 无 |
| app/normalizer.py | a6006074e80184c1251bca1b9c117c8a5d6e6d7781668376a3135dbff0c8a484 | 无 |
| app/db_manager.py | 937fe80ebb0c36d307fd13b7c5b2eccd416d3894fa22391614fda53ff81d4991 | A-M1-04：补 flags_fill_rate + whitelist_filtered_count 合并 unwhitelisted_attrs |
| app/domain_extractors.py | 76cccdf27551de09e0a886c92240a42a8d977054f80eb651a5ae5c30790d8671 | A-M1-02：按命中子串判别外径前缀 |
| app/typed_fields.py | 784dc580e7f81dd5d7392a0ab5681677713185806f3c835efb254e3665393a09 | R01 漏列，补登记 |

## tests/ 测试与夹具（M1 新增，含 R02 整改）

| 文件 | SHA256 | R02 变更 |
|---|---|---|
| tests/verify_m1_schema_v3.py | 52034eda5e8218d089bbcb5da74f17f7bafc26f46f678c04dacc178781a51181 | OBS-M1-01：补实测 v3→v2 回滚断言 |
| tests/build_structured_extraction_eval.py | f1899d8007d68405eb2edc2e92ce5107a0da824e1cde9586e2cd9bae6d4d2205 | 无 |
| tests/eval_structured_extraction.py | 1c922f1f891393cbc7cd67ae0542ac31d29f96b61ed104972026d7299062da7a | A-M1-03：field_fp 纳入误抽字段 |
| tests/fixtures/structured_extraction_eval_v1.json | 6ae54b7a098d3fb913c6fa7a670d04830fdd3628d2e6401caa791f456016c873 | A-M1-03：补标注覆盖 75/75 + GT-0004 期望域修正 + GT-0008 误抽字段 |
| tests/test_v017_m1_typed_fields.py | 7f42791b3f956ab2b69fb6331a0c840743528ab5bf2d44155d7f0823455723bd | R01 漏列，补登记 |

## 冻结资产（0 字节变化，仅复核）

| 资产 | 说明 |
|---|---|
| app/materials.db | 只读，未写入 |
| config/ 16 份文件 | 只读，未改写 |
| tests/fixtures/golden_set_v1.json | 冻结，未改动 |
| tests/fixtures/golden_baseline.json | 冻结，未改动 |
| tests/fixtures/golden_governance_v1.json | 冻结，未改动 |
