# V015-M5 R02 改动前基线哈希与备份清单

- 采集时间：2026-11-16（R02 开工前，开发 AI）
- Git HEAD：`e964fb16848de9dece8e729e801b61c7d81dd341`（V0.1.5 全部成果仍未提交，工作区 106 项 M/D/??）
- 外层业务快照 SHA256：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`（与 M1～M5 R01 历史恒定值一致）
- 前置 `tests/verify_isolation.py`：verdict=PASS / exit_code=0 / business_hash_unchanged=true / hard_errors=[] / missing_deps=[]
- R02 改前备份目录：`.local-archive/m5_r02_pre_backup/`（7 个文件，R01 成果态完整副本）
- R01 改前备份仍保留：`.local-archive/m5_r01_pre_backup/`（未触碰）

## R02 拟改文件基线（app / tests）

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| app/excel_boundary.py | 28698 | 8ad2806e04df75754c543d6985236c36fd6324b0a0ba38eb47ca8ee07bc46d4d |
| app/db_manager.py | 48699 | 1ad023ce7f70e724e1ab5d65455ae91d4c7863c5d111b8c51a10e572c41215f0 |
| app/vector_index.py | 19777 | ea38878c117b545688b11896e1e2480ed63a7d922afa754d6ad0ce91af0e991a |
| app/server.py | 149059 | 0be9e2bed7a6ca88f02b0348e87af706003eef513ab7f3256ea9ece2bb23ac6c |
| app/auth_manager.py | 49239 | 0f621d06bc78d4abbc569b221a354b391322dee285ab02ed2091e65ca47472d8 |
| tests/test_m5_file_boundary_and_compat.py | 40482 | 5bdd1c2fc4fd8c8a446c7ef00c0dd725986a2285fd18d35923dbb5d198258cab |
| tests/conftest.py | 12485 | 207a7cf79afd6335170cc270e42302303057b59c1de88c9077711f96466b4033 |

## R02 拟重建证据脚本基线（旧 R01 报告不覆写，新建 m5_r02_*）

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| doc/V0.1.5/evidence/m5_r01_prod_image_verify.py | 13556 | d478f0d44f3136ec24d476c9dcf635e8c6b2dd8da542fc6cb7b8d763f42b3ee4 |
| doc/V0.1.5/evidence/m5_r01_browser_e2e.py | 25183 | a65cc55fc2faf6d8c882d8936b85ea93ba770cd6ad802100d43e3dc28601d4ad |
| doc/V0.1.5/evidence/m5_r01_audit_probes.py | 6756 | 1105c3a39e76a95944647e6496405491aea60462c0241f2925cfab0fa12297f9 |

## 关联不改文件基线（回归对照）

| 文件路径 | 字节大小 | SHA256 |
|---|---:|---|
| web/index.html | 125941 | bf6c68b08054c87e24e41cd1fc3ec038c730f9619268f3452e9d70f976ca6906 |
| web/app.js | 180731 | 2a0b197fa13df5e5b183e41b63c7f3f3070089cd74ae61ed0445149d05e2fb00 |
| Dockerfile | 1534 | bc5e8d54fb8758fd4f0ebdbb7c586d3c6d68cf5386ff570b17b7c7154259413a |
| requirements.txt | 280 | 6f3b73001ffec927671c5496684f203c3bb6c7682bc4da628238a7f1cb2cc15f |
| app/materials.db | — | 9b7be8c2bd8f420f25b12a5394e8470cc2910dd363da875eafd04c1385b78927 |
