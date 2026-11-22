# V015-M2 R01 改动前工作区哈希基线

- 采集时间：2026-11-13T22:34:45.9663800+08:00
- Git HEAD：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`
- 采集方式：PowerShell `Get-FileHash -Algorithm SHA256`
- 范围说明：以下是 M2 相关实现、前端、测试及保护文件在任何 M2 代码改动前的当前工作区内容哈希，不代表 HEAD 的干净文件内容。
- 保留边界：工作区已有未提交修改、删除和新增全部保留；本轮不覆盖、不回退、不删除未识别工作，不修改 matcher 权重、M1 门禁或金标。

| 相对路径 | 改动前 SHA256 |
|---|---|
| `app/server.py` | `9ce124244f724e7f9fb46173afab8572c3084720791adc0bcf1a5cd3df34e690` |
| `app/session_manager.py` | `3a1c31b3059606c44a9495e49d1af607acc8184643ede34f6944c6a76b9c445c` |
| `app/db_manager.py` | `47ed0b50957b8fdcf2d9d58a0ad377447bd05f2e81949c26b3202e940d3dec2c` |
| `app/report_generator.py` | `54ae14d36d894bfc4bcddb428728ac445da3f5158a64acfa6f0746a20d416e07` |
| `app/matcher.py` | `48780dfe4bd799535472b92116139da35a3ac01a87a5ab6803100390e5f1e796` |
| `app/flywheel_sandbox.py` | `c4bc5e0ff9b6875208ead853ad1cf291717be128ab719fdfb13a4278057ca0d4` |
| `app/llm_rule_engine.py` | `f223af4e06c819ca66f3b1342df9187d9b81ac0e005f55d6c30ad476f6e2645f` |
| `web/app.js` | `c8cafc92684ffd71aca135e7a263472a75acdcfcc405ce47807bf72a6a6c0041` |
| `web/index.html` | `f9883bd95c107586c1aec9542c071c066bbbcdf69499895ddb65081b5f08d435` |
| `web/index.css` | `8b924cacbd07d82e43906ec9e3958908f7ab6fa1f84205a6c40faf1340cd0417` |
| `tests/test_auto_binding.py` | `ab4749f2b75336abc75e57ec42ae6091685e1454a60cc0923656f11f31e049c5` |
| `tests/conftest.py` | `207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033` |
| `tests/test_query_contract.py` | `b080c7bf69f376ee46482c8466b1e75eab44419c95d86fd86e5c10ad691a9704` |
| `tests/eval_golden_set.py` | `8b22c3b950c4accac9c57f9d05c8eeb02eb035aaf75d4bf3171c0751acce6584` |
| `tests/README.md` | `82b82d27b33667291e6d9d766c8635fdfdd7170d0a4a3e7332064d9ad320723a` |
| `config/auto_binding_rules.json` | `a5338d955b09046ec0b16f3a9625b7955c763aae07dc722e474e6078745f932f` |
| `tests/fixtures/golden_set_v1.json` | `c8909a21b34fc12fd45ee96d625dec5798f670cdd725988beb99b2f2f216eae5` |

本清单仅记录改动前基线，不作为测试通过、审核通过或版本放行证据；清单自身不做自引用哈希。
