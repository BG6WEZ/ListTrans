# V015-M5 R02 交付文件清单与精确 SHA256

- 采集时间：2026-11-16（R02 完工交付前，开发 AI，PowerShell `Get-FileHash -Algorithm SHA256` 实算）
- Git 基线：`e964fb16848de9dece8e729e801b61c7d81dd341`（未执行任何 `git commit/push/tag/checkout/reset`，全部成果仍为未提交工作区）
- 生产 Docker 镜像：`listtrans:m5-r02-prod`（`sha256:80d2583b5b6fbd702b0f48aea13854174e66cf2e59ec70c0ef2bdb8f47749a5d`）
- 改前备件目录：`.local-archive/m5_r02_pre_backup/`（7 文件，哈希已与 [R02改前基线清单](m5_r02_pre_baseline_hashes.md) 逐一核验吻合）；R01 备件目录 `.local-archive/m5_r01_pre_backup/` 未触碰
- 外层业务快照前后恒定：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f68bd5c7e`；`app/materials.db` 恒定 `9b7be8c2bd8f420f25b12a5394e8470cc2910dd363da875eafd04c1385b78927`
- 本清单不含自身哈希。

## 实现与测试（7 份拟改文件：5 份实际改动，2 份零改动）

| 文件 | 大小（字节） | R02 当前 SHA256 | 改前基线 SHA256 | 状态 |
|---|---:|---|---|---|
| `app/excel_boundary.py` | 31191 | `a37f1c98167b308fae218685b5da93bdba57cb59b7204056cf3436c0d86fff34` | `8ad2806e04df75754c543d6985236c36fd6324b0a0ba38eb47ca8ee07bc46d4d`（28698） | 已改（A-M5-03） |
| `app/db_manager.py` | 57600 | `d50ae4847fee3b4875318c452faac72b213885235d6bcb51783ff3b85c124284` | `1ad023ce7f70e724e1ab5d65455ae91d4c7863c5d111b8c51a10e572c41215f0`（48699） | 已改（A-M5-01/02） |
| `app/vector_index.py` | 25554 | `30de26fbd63c0285a84839698da9a6ac8ca8599044cdac26e845753cc2a835f4` | `ea38878c117b545688b11896e1e2480ed63a7d922afa754d6ad0ce91af0e991a`（19777） | 已改（A-M5-01） |
| `app/server.py` | 154385 | `5f478a5d730555e91ecb5eceafdd923eac4ee46208014dd50b8682dadf053ff9` | `0be9e2bed7a6ca88f02b0348e87af706003eef513ab7f3256ea9ece2bb23ac6c`（149059） | 已改（A-M5-01/02） |
| `tests/test_m5_file_boundary_and_compat.py` | 70405 | `85570f190d8dc0a26215fe65f69152c76d22fc2061fa0a045a970de97dd9825a` | `5bdd1c2fc4fd8c8a446c7ef00c0dd725986a2285fd18d35923dbb5d198258cab`（40482） | 已改（17 新增测试 + FM5 授权修正，共 27 测试） |
| `app/auth_manager.py` | 49239 | `0f621d06bc78d4abbc569b221a354b391322dee285ab02ed2091e65ca47472d8` | `0f621d06bc78d4abbc569b221a354b391322dee285ab02ed2091e65ca47472d8` | **零改动**（当前=基线，备件目录无需收录） |
| `tests/conftest.py` | 12485 | `207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033` | `207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033` | **零改动**（当前=基线，备件目录无需收录） |

## 开发小结

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `doc/V0.1.5/rounds/V015-M5_R02_开发小结.md` | 17271 | `f6c933197a7d494117746a30e4706bb348a8140ff71a6d3b05b936da86d60f00` |

## R02 证据与脚本（`doc/V0.1.5/evidence/`，全部为新建 `m5_r02_*`，R01 证据未覆写）

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `m5_r02_pre_baseline_hashes.md` | 2577 | `ebe3aa69db41140b0f9667a51da0e211e67fad336ecb5725453532adeff5a0d0` |
| `m5_r02_m5_special_pytest.log` | 4787 | `dfe0456d722feb7af759c5d1ef43f11087368d560e9778dcef215082d86f9394` |
| `m5_r02_m0_m4_regression_pytest.log` | 15917 | `892a21f3be37d7d898f4876bfd3498a9b2b2635d2a84994ce47d09bac610f5f2` |
| `m5_r02_full_pytest.log` | 33259 | `53a03182a0c6a8ea566da4ca3c9143c741494d5477c81b396f3457bf67d388d0` |
| `m5_r02_3rule_gate.py` | 9689 | `8e06aba2c0a8bc67ea35a6b347f77be47653c94aa2aa2e1923c6ce5f6c05e3ce` |
| `m5_r02_3rule_gate.log` | 1360 | `0bdca479492b4ce898608d4d066b2793b4db6b76f6afe776ed40462a513c5d12` |
| `m5_r02_gate_reports.json` | 52080 | `540138598be5647b24a9d0b31d4585f9c0460b1c69d43bdc51bba4ddd00b4022` |
| `m5_r02_browser_e2e.py` | 45536 | `54b97068e057b4ec5b16ba3cba2040cc366f1ae8bb775eb04e63ff73ca062633` |
| `m5_r02_browser_e2e_report.json` | 10936 | `84ddde9de4ce9ef59a4eafc3740d9da239ace65b9874b7ea0ed337940598d8a9` |
| `m5_r02_ui_workbench.png` | 178977 | `a39d2ab03b856970e05c3df1db917e4a37c5643adff35163f79377b2b07465ba` |
| `m5_r02_ui_rejected_restored.png` | 92713 | `4b811f087667df449b383521631544374e3ced374d9efb3c555a36f7dac8556d` |
| `m5_r02_ui_export_done.png` | 179317 | `ec1edf86c82c3d0a5b9db8af6d40c444870e78abfa1c420a003039e6fe34bb63` |
| `m5_r02_prod_image_verify.py` | 63429 | `2b2a56574238848a0940b7f2168fd6364ee52be7780eb3aed91c92972776eeab` |
| `m5_r02_prod_image_build.log` | 25382 | `819583ec2c2b753392bbf43b9531e20d7371d90673c94be40b22c1048ea209d4` |
| `m5_r02_prod_image_verify.json` | 90412 | `f295b9e5e058600dd3601bb64036d3d80dcf00e3ed1b568b8e5fc424494c2941` |
| `m5_r02_venv_verify_after.json` | 12188 | `5c7538a3159c350e6efea7505fa4516d906d20e03802b57b5cb8ac1469f449e9` |
| `m5_r02_venv_verify_after.log` | 342 | `b5de95bf773d912844532f5119630513e406395a0e4370a7c0e1f07a6bc90b9c` |
| `m5_r02_outer_snapshot_before.json` | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f68bd5c7e` |
| `m5_r02_outer_snapshot_after.json` | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f68bd5c7e` |
| `m5_r02_coordinator_handoff_hashes.md` | 821 | `b7c05ba0964814c98f78bfe917116c5b1fd49d9f8f7ea68b446d3047cc312390` |

归类说明：`m5_r02_coordinator_handoff_hashes.md` 为统筹 AI 职责接续时落盘的统筹侧文档（其文内已声明"非开发交付/审核通过"），列入本清单仅为审计完整；其余 19 份为开发 AI R02 交付物。R02 未单独产出 `venv_verify_before` 文件，开工前自检结论记录于 `m5_r02_pre_baseline_hashes.md` 头部。
