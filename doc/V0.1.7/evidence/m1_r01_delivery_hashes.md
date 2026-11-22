# V017-M1 / R01 逐文件交付 SHA256

2026-11-19，开发 AI。SHA256 为未提交工作区最终文件哈希，供独立审核比对。

## app/ 运行模块（M1 改动）

| 文件 | SHA256 |
|---|---|
| app/matcher.py | 25439276c103136c5fac8620b681ac4694a1094aa6e0d89149a523616cfc57db |
| app/preprocessor.py | f7e54d0405ae1acf0258474b5f6914c53ce7a5dbf0c6676a44ad4363e3949511 |
| app/query_preprocessor.py | 803e817767cba2c39ecf7c76f463e03b0d1fc02bbba7cce0fdcf830f952de643 |
| app/normalizer.py | a6006074e80184c1251bca1b9c117c8a5d6e6d7781668376a3135dbff0c8a484 |
| app/db_manager.py | 4867116f7b0e77dc93bc9f6e8db11a3c40797445a7f1114a089a2159df352dc6 |
| app/domain_extractors.py | 8065230359ae0b39994ba9b2b193e2be0b5f25ecd6a5004f58c732e357fcfd8f |

## tests/ 测试与夹具（M1 新增）

| 文件 | SHA256 |
|---|---|
| tests/verify_m1_schema_v3.py | 6f110b280fbb3f690a8bd91f15bd47d056aba5eb399368fa5e9d8b8d4c4ecdf3 |
| tests/build_structured_extraction_eval.py | f1899d8007d68405eb2edc2e92ce5107a0da824e1cde9586e2cd9bae6d4d2205 |
| tests/eval_structured_extraction.py | feb39688850445d2e72ab5833577c1b7d974500e3533c68db234229df72f693a |
| tests/fixtures/structured_extraction_eval_v1.json | ecb04475aa1f91d93b3f7bb45ec6a4622aef7732dac1cb2ad6d0a89fd62c56de |

## 冻结资产（0 字节变化，仅复核）

| 资产 | 说明 |
|---|---|
| app/materials.db | 只读，未写入 |
| config/ 16 份文件 | 只读，未改写 |
| tests/fixtures/golden_set_v1.json | 冻结，未改动 |
| tests/fixtures/golden_baseline.json | 冻结，未改动 |
| tests/fixtures/golden_governance_v1.json | 冻结，未改动 |
