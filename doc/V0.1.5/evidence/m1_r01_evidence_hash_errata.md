# V015-M1 R01 证据哈希勘误（A-M1-01）

- 统筹AI；2026-11-13（Asia/Shanghai）。只追加勘误，原交付清单、原证据及审核小结保持原样。
- 15/15代码、测试、README及本机脚本哈希吻合；9份evidence中5份吻合，以下4行各漏写1个字符。下表绑定本轮读取的原文件字节。
- 本勘误只关闭哈希转录问题，不代表M1放行。

| 原清单行 | 文件 | 原记录 | 实测SHA256 |
|---|---|---|---|
| 37 | doc/V0.1.5/evidence/m1_r01_pre_baseline_hashes.md | 907cf521083a6bf1e71be5a6e3dff252ae28d6aa30e4a840d7c25ce0ff29df9 | 907cf521083a6bf1e71be5a6e3dff252aef28d6aa30e4a840d7c25ce0ff29df9 |
| 38 | doc/V0.1.5/evidence/m1_r01_outer_snapshot_before.json | 5768bdabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | 5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e |
| 39 | doc/V0.1.5/evidence/m1_r01_outer_snapshot_after.json | 5768bdabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e | 5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e |
| 41 | doc/V0.1.5/evidence/m1_r01_verify_after.json | 464bf67db4a851b4a63295ecd6473b6ae2d069c9b752f71c2cffdb19fcae373 | 464bf67db4a851b4a63295ecd6473b6ae2d069c9b752f71c2cffd5b19fcae373 |
