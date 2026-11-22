# V015-M5 R04 交付文件清单与精确 SHA256

- 采集时间：2026-11-16（R04 完工交付前，开发 AI，Python `hashlib.sha256` 实算并程序断言 64 位小写十六进制匹配）
- Git 基线：`e964fb16848de9dece8e729e801b61c7d81dd341`（未执行任何 `git commit/push/tag/checkout/reset`，全部成果仍为未提交工作区）
- 生产 Docker 镜像：`listtrans:m5-r04-prod`（`sha256:9fc7e627bd0b42b9ab901738a5671770669dd4e1fc9432cab737ae69518d08e3`，11 层；R03 镜像 `listtrans:m5-r03-prod` `sha256:69c5a4a6cf1ba783c24399adc09b38432510efbddebecdc0fa8d7a3e8782b6b7` 未覆写）
- 改前备件目录：`.local-archive/m5_r04_pre_backup/`（5 文件，哈希见 [R04 改前基线清单](m5_r04_pre_baseline_hashes.md)）；R01～R03 备件目录未触碰
- 外层业务快照前后恒定：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`；`app/materials.db` 恒定 `9b7be8c2bd8f420f25b12a5394e8470cc2910dd363da875eafd04c1385b78927`
- 本清单不含自身哈希。

## 实现与测试（3 份改动源文件 + 1 份新增专项测试 + 1 份仅夹具适配 + 4 份零改动对照）

| 文件 | 大小（字节） | R04 当前 SHA256 | R04 改前基线 SHA256 | 状态 |
|---|---:|---|---|---|
| `app/server.py` | 177530 | `cb6423ae4e2f74ab683f033ae15f3cf6be6063a0361f45bde73e97f5605dc999` | `2b0e418504d70eaed2aaa39c06d8a7ab108a5edcb8e0e7f8cbf8cc23d9b4ca1c` | 已改（A-M5-07/OBS-R03-1：RECOVERY_FAILED 仲裁、持久化阻断与全入口拒写、verify_and_clear_recovery_block、importlib 模块对齐） |
| `app/db_manager.py` | 70317 | `8aa774bd1f1d8db07a647486144f9798c570a68d953d010e9a2a8f1ec39af01c` | `6c76cee35baedcb5eeb4a816189d3747286a0fc5b31644a98e1fd4293b74d53b` | 已改（A-M5-07/OBS-R03-1：DB 持久化恢复阻断标记、写入口保护与 inspect_db_recovery_state） |
| `app/vector_index.py` | 37932 | `b339523deda9f1b90c4cb81a8590048346edf20740d46a6052d16844517e92b5` | `3e4df3066748906da7fe0f1ca5ff902fb2cc195fa09d5994d672c835dc03900a` | 已改（A-M5-06/07/OBS-R03-1：backup_moved/promoted_files 分离、缓存持久化阻断与全写入口保护） |
| `tests/test_m5_r04_recovery_isolation.py` | 31717 | `2c00ea3de365308fe255bd6b0c3438d6d72c3b3399b3882e41d38080eb9e9853` | （R04 新增） | 新增（14 个确定性用例：A-M5-06 0～3 备份/提升/缺件/双失败；A-M5-07 DB/内存失败、重启不绕过、全入口拒绝、核验恢复） |
| `tests/test_m5_r03_commit_arbitration.py` | 44064 | `00b27ab2b29adfcecdedd863e95a72e1eed6b0692af672a4760d120e2d513e73` | `bed12a55a5cb25206cc82a3bf8df723319606688e049c246221c432a55b7bd7f` | 仅夹具适配（L142-170 补 DB 阻断复位与 _new_db_import_lock_manager；24 项既有 R03 断言零改动） |
| `tests/test_m5_file_boundary_and_compat.py` | 70405 | `85570f190d8dc0a26215fe65f69152c76d22fc2061fa0a045a970de97dd9825a` | `85570f190d8dc0a26215fe65f69152c76d22fc2061fa0a045a970de97dd9825a` | 零改动（保留 R02 27 个用例与授权 FM5 断言） |
| `app/excel_boundary.py` | 31191 | `a37f1c98167b308fae218685b5da93bdba57cb59b7204056cf3436c0d86fff34` | `a37f1c98167b308fae218685b5da93bdba57cb59b7204056cf3436c0d86fff34` | 零改动（当前=R02/R03 基线） |
| `app/auth_manager.py` | 49239 | `0f621d06bc78d4abbc569b221a354b391322dee285ab02ed2091e65ca47472d8` | `0f621d06bc78d4abbc569b221a354b391322dee285ab02ed2091e65ca47472d8` | 零改动（当前=R02/R03 基线） |
| `tests/conftest.py` | 12485 | `207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033` | `207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033` | 零改动（当前=R02/R03 基线） |

## 轮次小结

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `doc/V0.1.5/rounds/V015-M5_R04_开发小结.md` | 16648 | `211a1a66b262e18f5f4a06545b879f7c5dd647c70a64baf07e337ecec2807a86` |

## R04 开发侧证据与脚本（`doc/V0.1.5/evidence/`，全部为新建 `m5_r04_*`，R01～R03 证据未覆写）

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `m5_r04_3rule_gate.log` | 2628 | `bbfac6a0683c42a345e4ecd30e63e82a88caac653a47deb5b46b29e74b76d3a3` |
| `m5_r04_3rule_gate.py` | 9933 | `52ce85600fb44182e270eb530e484076bcaad60990f97528ba891e1176745a83` |
| `m5_r04_browser_e2e.log` | 2630 | `a217cf07e0a89f2758fd5be971f0268b080860a1e8e2328121f0cbcf6e85f876` |
| `m5_r04_browser_e2e.py` | 47750 | `d89ba155b34c0f5ed75e5de651e0f68311eca29c811487a57d8e46f071431fd6` |
| `m5_r04_browser_e2e_report.json` | 11568 | `12c6893d38eb1538c2d182d47e9cb10d133b1b0c1fc962945447b41277f3cbcd` |
| `m5_r04_full_pytest.log` | 75594 | `c976c292e4766bd0c67087d40e2ca15b11cabbae60f8512ba92d76b6022e966b` |
| `m5_r04_gate_reports.json` | 52080 | `a923c595277caa8b42d9a910551588bb1f9bf3ee54e9cb5b33db57118c5d039a` |
| `m5_r04_isolation_verify_before.json` | 12188 | `a04558b6f761edc5da0fcf1b2d325f69ce5f25cc134683929cc33069d41e3202` |
| `m5_r04_m4_m5_regression_pytest.log` | 20108 | `1e9bd67482490d090ca0948b1bd225bef45a83f8b393d9bb5c93b6d24e7fdc4f` |
| `m5_r04_outer_snapshot_after.json` | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `m5_r04_outer_snapshot_before.json` | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `m5_r04_pre_baseline_hashes.md` | 7191 | `76de00c4947705d6e3269314df8750a8054c903bdf9aff0f50e3fc082c926a46` |
| `m5_r04_prod_image_build.log` | 25040 | `595f63c77144f5fa19345cd9f1090bb00dd63019133a48cd0db4bb23b5cfa992` |
| `m5_r04_prod_image_verify.json` | 91242 | `642fb323006742974c26f4acd70ce4a4f5a2bf702d9bdea01f261f8aa30eba94` |
| `m5_r04_prod_image_verify.log` | 3450 | `6b3c533341f4ee78831ab760ed6c2d774891c104ba4e469f4395d7bfe102a870` |
| `m5_r04_prod_image_verify.py` | 65970 | `8c42ebaec2b233a0afac233433094252782da48b758a64acc2fc178309a27c4f` |
| `m5_r04_special_pytest.log` | 4960 | `f20a8acce4ac5f701f9b46e799a45c9606125d62e2b079a637aacf6084f4d9c3` |
| `m5_r04_ui_export_done.png` | 179353 | `c3144b09eb67281f896f5e5c9a95c8fc4311758d8907f015fed8956b0dccca31` |
| `m5_r04_ui_rejected_restored.png` | 92713 | `2e47b17c4654db4f233eced3ba0c3378979bc4a6ebf7454dbc84dbaead8c2cce` |
| `m5_r04_ui_workbench.png` | 179323 | `aeee931622f7816c9fae9dd2e11298fd915380d0764b17b9e8fbb2a27a9ea194` |
| `m5_r04_venv_verify_after.json` | 12188 | `7b573a11578f81cdd6e7a588a3e5bcd1d3598613ac3c5ce2df05071e71a97aa9` |
| `m5_r04_venv_verify_after.log` | 680 | `ff172e67e0da943e8678f9e6346b00e7d0c9885ad6cb4c49573bc504f35fda0b` |

## 业务冻结对象对照

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `app/materials.db`（业务冻结库） | 19759104 | `9b7be8c2bd8f420f25b12a5394e8470cc2910dd363da875eafd04c1385b78927` |
