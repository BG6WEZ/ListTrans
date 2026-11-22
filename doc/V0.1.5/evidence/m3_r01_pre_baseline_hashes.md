# V015-M3 R01 改动前工作区哈希基线

- 采集时间：2026-11-14T12:15:52.7057910+08:00
- Git HEAD：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`
- 采集方式：PowerShell `Get-FileHash -Algorithm SHA256`（输出转小写）
- 外层业务快照 before：`doc/V0.1.5/evidence/m3_r01_outer_snapshot_before.json`（同目录 `.log` 为采集输出）
- 范围说明：以下是 M3 相关实现、前端、测试、夹具、配置及保护工具在任何 M3 代码改动前的当前工作区内容哈希，带 M0～M2 及历史未提交工作，不代表 HEAD 的干净文件内容。
- 保留边界：工作区已有未提交修改、删除和新增全部原样保留；本轮不覆盖、不回退、不删除未识别工作；不换模型、不调召回排序权重/阈值、不改金标、不降门禁。
- 本清单仅记录改动前基线，不作为测试通过、审核通过或版本放行证据；清单自身不做自引用哈希。

## app 实现

| 相对路径 | 改动前 SHA256 |
|---|---|
| `app/admin_api.py` | `46faf848c04c70e2f1537195957115f07e6febe750baee8f7bd51b38f9060fa9` |
| `app/audit_manager.py` | `412b3d99be88b10acb1f58b52c32fc979f8035da234636f841140fdf3d43e437` |
| `app/auth_manager.py` | `8b5caeba98abc398c32f22e60adef2e31761ce9b4da9bc9a3d32f72aefa7dd78` |
| `app/auth_middleware.py` | `5aba3cc84f0dc2203581f29e92b4bd14fc71dfcb2f90732ab1f5c8b8edd449db` |
| `app/binding_decisions.py` | `8b5bc83db8cd0ae90dc565c28f7ccd6c47235a04cd4688f86f6f1006f23c49db` |
| `app/db_manager.py` | `47ed0b50957b8fdcf2d9d58a0ad377447bd05f2e81949c26b3202e940d3dec2c` |
| `app/domain_extractors.py` | `08e66817d065293afe862a96560a35337737aa8a06ffb433ba3b52c6e1965e76` |
| `app/domain_router.py` | `262b3c44b54e24e1332de553495c83fd5322acf743a20b864aca93d6360a8b1e` |
| `app/feedback_miner.py` | `981bede1183da115c6e5b1b8b951f688c9ce442b6ac14e56f82bb5e83fed2ecb` |
| `app/feedback_train.py` | `b13ac9eff30ac0143cad1e51c02a6c893e79f4298f3dedaec0fe07e1df736cbf` |
| `app/flywheel_db.py` | `fb7e262ac643587209b2c749fe3b220f7917795fb04c186d305660d9e5b18044` |
| `app/flywheel_sandbox.py` | `c4bc5e0ff9b6875208ead853ad1cf291717be128ab719fdfb13a4278057ca0d4` |
| `app/llm_client.py` | `e4971eb95647c6c0e1b606145f1f76e120c7ad0d126b17339041001e4859c51b` |
| `app/llm_rule_engine.py` | `f223af4e06c819ca66f3b1342df9187d9b81ac0e005f55d6c30ad476f6e2645f` |
| `app/matcher.py` | `48780dfe4bd799535472b92116139da35a3ac01a87a5ab6803100390e5f1e796` |
| `app/normalizer.py` | `711f221ff7933725e131ecd5f659e674b8dd3cef8c2e6897c437d17d167b46cf` |
| `app/preprocessor.py` | `2e04f3022d5a658a87c6de4da9ddb125c8a5ff0d3fd0bc9db0df1cb6c89a7ac5` |
| `app/query_preprocessor.py` | `8ec776bf186851248fad6e7468edab85d3870b0fde2318f77f2eb53974ffda2e` |
| `app/report_generator.py` | `54ae14d36d894bfc4bcddb428728ac445da3f5158a64acfa6f0746a20d416e07` |
| `app/rule_api.py` | `afecd2cf7942ea75e74ee99ceb1c4d14a549d32df941d88ebc3f81acb272ed5a` |
| `app/server.py` | `a621a00cd31bfb4cff30003d33dcb1a152b548634b7bde0c7afd9386abcca1b3` |
| `app/session_manager.py` | `2a8f7269101127739b395a89749aacdebfaea7de223063285c01217cc3630842` |
| `app/vector_index.py` | `58cc76e35dbae01d3354f58684d5f7b74be72c279ab0fcf72c9cb47a4f95748d` |

## tests 测试与隔离工具

| 相对路径 | 改动前 SHA256 |
|---|---|
| `tests/conftest.py` | `207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033` |
| `tests/verify_isolation.py` | `090ba89a25813f42910bd245b1bd2cd05bf580ffdc888ce3fe19ec523ad1eb62` |
| `tests/eval_golden_set.py` | `8b22c3b950c4accac9c57f9d05c8eeb02eb035aaf75d4bf3171c0751acce6584` |
| `tests/test_admin_dashboard.py` | `21a414db9f939b197b523a283c5ba69d9a2043e8c1dcfe99cc05b3adef8e8752` |
| `tests/test_auth_security_fix.py` | `4071b1d88343c19f65bc01b9889d6dc7a1275de8e284f6b4ccc68bb828d56055` |
| `tests/test_auto_binding.py` | `0b32c687486edbe85bb0383c2435968526fa4db9fc594d97b60c53d611c8629e` |
| `tests/test_binding_decisions.py` | `adb13a41e2a9cd0ed152164e86eb3e8debb43567299b10880a2ac5dda6f05878` |
| `tests/test_feedback_miner.py` | `3c70c6660a0c0ec111903841ddf0888795e6cd81cd6c613e3e364cc3895a4228` |
| `tests/test_flywheel_end2end.py` | `d47af7426ad2828d02725ac4589fc3325bbc30d8276e0b2699c074efb48c18c6` |
| `tests/test_flywheel_sandbox.py` | `8b56971b34c26d0a83d0f9da07419ad99e0699c0e1e0048b67902c65ded87901` |
| `tests/test_llm_integration.py` | `2f7c7bb85a4743a3382385db2f984afb5d22729598d9aab3a62a5c6f7110a091` |
| `tests/test_m0_guardrail_regression.py` | `ece380d56a8899b0c1f9f735f7ed28823470036d7ac32fbe00b784877e55839c` |
| `tests/test_m0_isolation.py` | `92c97d4e7b7d6b68490986feeb79a32e317b7211914258d942d638130acafa3a` |
| `tests/test_phase0_phase1.py` | `891dbe21f0f79dcd65e18e40aa57219862ef1818e27a52063fa72eea19009e3e` |
| `tests/test_query_contract.py` | `b080c7bf69f376ee46482c8466b1e75eab44419c95d86fd86e5c10ad691a9704` |
| `tests/test_query_preprocessor.py` | `159c573db7e3d40d0f6c26436e7e3d05388e0b166d6638077960211b22b73407` |
| `tests/test_query_cleaning_pipeline.py` | `0de3cea544985365eb7667c38c2bda95d1878a8a5c0c80d5d39d61c38b39be1c` |
| `tests/test_rule_deadlock_fix.py` | `561bb448bae14f19744bbe0d09d60bc0ffad30e464b27b7b6871e257d29a4ce6` |
| `tests/test_v0_0_8_llm_rules.py` | `9765eb7ac61882e2fe8334d3103e6352216ec471d8f491c1cb29e45f12493a6c` |

## 夹具 / 前端 / 配置 / 工具

| 相对路径 | 改动前 SHA256 |
|---|---|
| `tests/fixtures/golden_set_v1.json` | `c8909a21b34fc12fd45ee96d625dec5798f670cdd725988beb99b2f2f216eae5` |
| `tests/fixtures/sandbox_materials_v1.json` | `f4fb0d678d38e717d41f2140df1327edeb432f99c03e1f53d6b84a22abf036f4` |
| `tests/fixtures/gen_standard_template.py` | `a66f32f3f3fd432d58ffe310a1612d0658021f122613c875324925250675eb93` |
| `web/app.js` | `4a8eb6204c5e3f27cf7c81081e93dfbd71f3040ba1dea0dfdc86720e3caadc09` |
| `web/index.html` | `f9883bd95c107586c1aec9542c071c066bbbcdf69499895ddb65081b5f08d435` |
| `web/index.css` | `8b924cacbd07d82e43906ec9e3958908f7ab6fa1f84205a6c40faf1340cd0417` |
| `config/auto_binding_rules.json` | `a5338d955b09046ec0b16f3a9625b7955c763aae07dc722e474e6078745f932f` |
| `.local-archive/business_snapshot.py` | `12b2133dd0bb1dd5b30c72a5702f24f5a4d4565cde886a5f9d552df3e28393a7` |
| `pytest.ini` | `4180e186629fc8daf4eac04b8de15081dd277571253f0733b3e5a7422868d62a` |
| `requirements-test.txt` | `1444253aae55a9121e8c549472a24ef2e4eb4e512d18e35a14e7536a34139c2b` |

合计 50 个文件，采集时全部存在、无 MISSING。金标明细 `tests/fixtures/golden_last_run.json` 与隔离根 Golden 路径按既有门禁保持不存在（验证阶段另行实测，不在本基线伪造）。
