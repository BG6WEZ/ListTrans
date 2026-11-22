# V016-M1 R01 交付文件字节与 SHA256 清单

- 基线提交：`e964fb16848de9dece8e729e801b61c7d81dd341`（工作区未提交交付）
- 冻结时间：`2026-11-17 22:31:15`
- 说明：包含本轮新增/修改的代码、测试、台账/开发小结及证据产物（不含本清单自身）。

## code

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `app/rule_context.py` | 12968 | `cd9b499ed00a4e59db929e3580426db603da37ac23092fa4176b07226053f38a` |
| `app/flywheel_db.py` | 26025 | `5ad05e3d6812cdf480c45c37db622649fc448bd76cc9fc4bf2aebbd95fcf3d28` |
| `app/feedback_contract.py` | 71561 | `e052c9359c24d3305fc2bd2e5e2ea096521169501d8024e1a8591988a4cc1c5b` |
| `app/feedback_miner.py` | 29474 | `45632cc952250b58abc4baf3525cf2864abebbf55633e6db0f3014ff61704eda` |
| `app/llm_rule_engine.py` | 86727 | `2299299663aaef867b6418bd4213e0cd649d05d5496e36ab4cc91a227f5c9912` |
| `app/rule_api.py` | 7224 | `d21b2af3b61b8d99817bf0b7fdd38827fed3302d5ba827ce6cf62a2011775137` |
| `app/normalizer.py` | 13001 | `3070ad1415208e789d15245779bbff63be3415bcc7f11b7ff2d4ed3278c2b802` |
| `app/query_preprocessor.py` | 44603 | `35ea58ade4103f21396e12b6fa9f8ab05da649e9dc881a405a9c731edeebe932` |
| `app/matcher.py` | 121083 | `020605650595bc42d9291927476ec1364ba12d191109715ee86131cb173db9e6` |
| `app/preprocessor.py` | 26455 | `e226865b15cbb728404b5c81a06e12386501f922b0d9f53e311d81209f0fb390` |
| `app/db_manager.py` | 71665 | `a39fee995f68353bad7c7a6dcb765ad49442ffbd6aa92711ef09ae2b242bae46` |
| `app/binding_decisions.py` | 31087 | `39b9f4f7a43f398cba8e8455092d4f8a40a30f6fa261c285353a69976713d5bf` |
| `app/server.py` | 192857 | `f9129f3f6dc319f3aa60d3590699b200ce7562523e93ab36b32d154439424389` |

## tests

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `tests/test_feedback_miner.py` | 7226 | `49e8bcc4442b0bed70a070cb9d38d6bbddced5d11f06f04366174d5a74e8b321` |
| `tests/test_v016_m1_mining_and_rule_context.py` | 51390 | `ba061f0861791022cd96caa2332ddcbeffe7c83693b582cf000046f0acd26cf5` |

## docs

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `doc/V0.1.6/02_任务台账.md` | 4065 | `022700c682de8a1a9e2595fde1895e1572ee2c0055445227e790be482cc2f16e` |
| `doc/V0.1.6/rounds/V016-M1_R01_开发小结.md` | 14185 | `5a329d8f8173c7530a68fa7f421ac1e5ce8528947c24e1f4447e5254122eae08` |

## evidence

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `doc/V0.1.6/evidence/m1_r01_3rule_gate.log` | 2770 | `ee891d31984df241c5602f6a28af550e5c8b6b0c9465ab67ade0ff4712e18b81` |
| `doc/V0.1.6/evidence/m1_r01_3rule_gate.py` | 10436 | `1b08c39bf24c492d55fff96eb1fdce85016dae0142d9cacf4990e70fd191ccc9` |
| `doc/V0.1.6/evidence/m1_r01_cm009_proof.log` | 6616 | `4982aa49a8360083fad37f121b4f8c95d2874eabc9a463bac65bd268a7c75eef` |
| `doc/V0.1.6/evidence/m1_r01_full_pytest.log` | 40616 | `8b13e92b359b536239a9ca0cf75a3cd2283e511726f2ad3225ad90fca9f48c79` |
| `doc/V0.1.6/evidence/m1_r01_full_pytest_fixed_root.log` | 40613 | `3f1b417387f61518b85d709ab40faf15bcd414ced37a19a2ea1f5ecec28db322` |
| `doc/V0.1.6/evidence/m1_r01_gate_reports.json` | 52196 | `c7c82e3ab5811aba85e0b28f0f6024e066a0e5f90e3c9fbf634ff68489978b59` |
| `doc/V0.1.6/evidence/m1_r01_isolation_verify_after.json` | 12684 | `4bd853eebe17c64ae4ac98cfc6bb460fd992371c90bd3b60c9aef9b427c4b5d1` |
| `doc/V0.1.6/evidence/m1_r01_isolation_verify_before.json` | 12684 | `344b3a8a62b81fec6ad7975f0ce694631b2a59198f0e64a31cc00f210276a780` |
| `doc/V0.1.6/evidence/m1_r01_m0_regression.log` | 4948 | `7643497ef7b9a0710044d485146442087d0bde0821083eb0e104655d8fd24674` |
| `doc/V0.1.6/evidence/m1_r01_outer_snapshot_after.json` | 3744 | `3478f8b287a43ffbb1df150c49057f2867062c0f4b7e448860362f34ba5f35d2` |
| `doc/V0.1.6/evidence/m1_r01_outer_snapshot_before.json` | 3744 | `c4dcf222dfed676bf900ec71049dcdac919e6162e0e358678f053f04650acc5d` |
| `doc/V0.1.6/evidence/m1_r01_pre_baseline_hashes.md` | 1464 | `1e42388a02de684c17769373279cc6ae5cb8d2336df679e8da475a64645c3add` |
| `doc/V0.1.6/evidence/m1_r01_pre_repro.py` | 6269 | `e312d253e941d05bc641eebcd4bf59efc92b1a98ed4e99d20b23bba639eabfad` |
| `doc/V0.1.6/evidence/m1_r01_pre_reproduction.json` | 836 | `6c3e98a6b71221f3b9e0f632b996b462cf0c6e48c10cf93e0accd461e6a728ee` |
| `doc/V0.1.6/evidence/m1_r01_pre_reproduction.log` | 2372 | `a3826e0bf0b8ae2c5c35e56b31e3e23b124f49ac07bd05c74557812faa27f620` |
| `doc/V0.1.6/evidence/m1_r01_targeted_pytest.log` | 42058 | `359f70f7a583948dbf433ce730270ab210928090491716a505a1d0b9a49341c2` |
