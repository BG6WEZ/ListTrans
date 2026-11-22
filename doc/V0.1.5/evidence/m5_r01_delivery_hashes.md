# V015-M5 R01 交付文件清单与精确 SHA256

- Git 基线：`e964fb16848de9dece8e729e801b61c7d81dd341`（未执行任何 `git commit/push/reset/checkout`）
- 生产 Docker 镜像：`listtrans:m5-r01-prod`（`sha256:7943d8d8984fc4dd045009d9aa676c7dd688a498bf54c50d9f0de672baf32877`，`docker save` tar SHA256 `0847a56bea6c31d23f00c7e2a5da35eb773a201fc1ede0aa0286ba1cf5af1237`）
- 改动前备件目录：`.local-archive/m5_r01_pre_backup/`（12 份原文件完整保留）

## 实现与依赖

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `requirements.txt` | 280 | `6f3b73001ffec927671c5496684f203c3bb6c7682bc4da628238a7f1cb2cc15f` |
| `app/excel_boundary.py` | 28698 | `8ad2806e04df75754c543d6985236c36fd6324b0a0ba38eb47ca8ee07bc46d4d` |
| `app/audit_manager.py` | 28631 | `a12adb525a00ef5d46b853506f17ac0d689d67320cf2ddb948886cf65ac8c50d` |
| `app/admin_api.py` | 15345 | `5c2bb8256c8017d0678bea8e2c9d38e7d8207a6b056f67ed813f957059c5c2f3` |
| `app/vector_index.py` | 19777 | `ea38878c117b545688b11896e1e2480ed63a7d922afa754d6ad0ce91af0e991a` |
| `app/db_manager.py` | 48699 | `1ad023ce7f70e724e1ab5d65455ae91d4c7863c5d111b8c51a10e572c41215f0` |
| `app/auth_manager.py` | 49239 | `0f621d06bc78d4abbc569b221a354b391322dee285ab02ed2091e65ca47472d8` |
| `app/server.py` | 149059 | `0be9e2bed7a6ca88f02b0348e87af706003eef513ab7f3256ea9ece2bb23ac6c` |
| `web/index.html` | 125941 | `bf6c68b08054c87e24e41cd1fc3ec038c730f9619268f3452e9d70f976ca6906` |
| `web/app.js` | 180731 | `2a0b197fa13df5e5b183e41b63c7f3f3070089cd74ae61ed0445149d05e2fb00` |
| `tests/test_m5_file_boundary_and_compat.py` | 40482 | `5bdd1c2fc4fd8c8a446c7ef00c0dd725986a2285fd18d35923dbb5d198258cab` |

## 项目管理与小结

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `doc/V0.1.5/02_任务台账.md` | 55689 | `0ca8069823f926072feb63d2f873d36ad8f14100c9e97515fac0a9694e612e2e` |
| `doc/V0.1.5/rounds/V015-M5_R01_开发小结.md` | 11729 | `f2fc4abd1900dc7cb53c439fdc22891cffd3cc5358d7baf4cc4e97e0b1a24f82` |

## M5 R01 证据与脚本

| 文件 | 大小（字节） | SHA256 |
|---|---:|---|
| `doc/V0.1.5/evidence/m5_r01_pre_baseline_hashes.md` | 1429 | `1f4e58c84eb3e555b4b177fafd2fc09510d1beb2c38b550d7a6be8aa0ff4d72c` |
| `doc/V0.1.5/evidence/m5_r01_pip_check.log` | 64 | `6326999b2d47daff6338070efad9139a62dd802f33019eab621859ade33fc907` |
| `doc/V0.1.5/evidence/m5_r01_full_pytest.log` | 62346 | `dd2f51f67aac4cdfb7fcb5e401febf8af87c3f05c11063a022dddb33e31cacae` |
| `doc/V0.1.5/evidence/m5_r01_browser_e2e.py` | 25183 | `a65cc55fc2faf6d8c882d8936b85ea93ba770cd6ad802100d43e3dc28601d4ad` |
| `doc/V0.1.5/evidence/m5_r01_browser_e2e_report.json` | 1502 | `fb788a7613fc0352581427faa70c53b39efb51702d1e7956f63198e507cbaa33` |
| `doc/V0.1.5/evidence/m5_r01_ui_workbench.png` | 178106 | `989b57d72c7713abf2d484a58c3d3ce70f09311b43715bc411db70da31f16ec6` |
| `doc/V0.1.5/evidence/m5_r01_ui_export_done.png` | 178481 | `37864c66847fc6d0a91f60eab557164edb55dce0ffa638688c6b73398ffb0b04` |
| `doc/V0.1.5/evidence/m5_r01_3rule_gate.py` | 9607 | `4994d84dcf807d1dd48d700b14059f2da6b39a95c23293b99ca8d83a9c9f30b1` |
| `doc/V0.1.5/evidence/m5_r01_3rule_gate.log` | 1726 | `e6fedeb53f58e0cd18c3a7fdd5b351c344a4d8b189748b10706f45d0d8822f14` |
| `doc/V0.1.5/evidence/m5_r01_gate_reports.json` | 52080 | `dbc42412b818b4732aa45c0bb4190e98cf76d91f786a3ddbfbe7493d63dacf07` |
| `doc/V0.1.5/evidence/m5_r01_docker_build.log` | 24996 | `1ff50c90204443c9279d4772868201fa30212393412edab9a73a84fa4617cdec` |
| `doc/V0.1.5/evidence/m5_r01_prod_image_verify.py` | 13556 | `d478f0d44f3136ec24d476c9dcf635e8c6b2dd8da542fc6cb7b8d763f42b3ee4` |
| `doc/V0.1.5/evidence/m5_r01_prod_image_report.json` | 1117 | `13405ebc1542bd14ecf0433eee88dd55c09f8bca11e8d480e765390d8b6eb559` |
| `doc/V0.1.5/evidence/m5_r01_venv_verify_before.json` | 12188 | `f402ce5597ae1f6fee000b3be8bfacc52aaff331f1215d0b383184dda967648f` |
| `doc/V0.1.5/evidence/m5_r01_venv_verify_after.json` | 12188 | `58d573f758423345e67e799db5db47ec7e86c60092216e2480c40bc322924391` |
| `doc/V0.1.5/evidence/m5_r01_outer_snapshot_before.json` | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `doc/V0.1.5/evidence/m5_r01_outer_snapshot_after.json` | 3496 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
