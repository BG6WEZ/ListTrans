# V018-M1 / R01 交付文件 SHA256 清单

- 基线：Git HEAD `e964fb16848de9dece8e729e801b61c7d81dd341` + `m0_r01_dispatch_baseline.json` + `m0_r01_coord_release.json`
- 较 M0 放行基线未变动：77 份；本包修改既有文件：2 份；新增源码/候选配置/测试/评测脚本：4 份。

| 文件 | 类别 | SHA256 |
|---|---|---|
| `app/sparse_recall.py` | 新增 | `7aeb4a121b922df35f279f51655e9e5dfe6f4eb4cc2a8b6ab531d039f7fcaa1c` |
| `app/recall_candidates/v018_m1_aliases.json` | 新增 | `9c69c06ba5d245b44192c5f572f78ed18d003a79d103065db0e32f5c413055f0` |
| `app/db_manager.py` | 修改 | `fefebb24125717e225a6588fa01c60e8f27d49c3fab31a13a102e0df08ddac88` |
| `app/matcher.py` | 修改 | `add2ef11733ed070acd38688f2f868e3bd25f50d33e6fda64a961e1d17f2886c` |
| `tests/test_v018_m1_sparse_recall.py` | 新增 | `d074cb59aae4bd145a8627d72b44ec7ee9a82137eef4f1f45645eceaa4db2f0d` |
| `doc/V0.1.8/evidence/m1_r01_eval.py` | 新增 | `f9e08c440a49b80af8217a20ee36164397f8dcf6fa0efaea8c1c7c0a92052b20` |
| `doc/V0.1.8/evidence/m1_r01_comparison.json` | 证据 | `cc5ccf55d9cda7fde22421f10da8d1e5e6fd1f88d2201e2231dba4067f141697` |
| `doc/V0.1.8/evidence/m1_r01_gate_exact.json` | 证据 | `d4b0faee01fed8ff6df4e3efd9ba52160ce348a95be133555980f89c861de761` |
