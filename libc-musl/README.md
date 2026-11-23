# libc-musl — TriRV 的 musl 移植层（目录地图，2026-11-24 整理）

```
third_party/musl/        上游 vendor（子模块 → clearnature/musl trirv-vendor）
libc-musl/
├── musl_tri/            三值重写层（TriRV 自研，随主仓语义演进）
│   ├── *.c              重写单元（位图系/qsort/strtol/strtod/ecvt 系…22 文件）
│   └── arch_tri/        arch 层（read8/write8、原子纯 C）
├── preprocess_musl.py   上游源 → TriRV C 子集 预处理器（单文件管线）
├── batch_musl_compile.py  批量编译统计（trirv_cc 列，65/66）
├── batch_musl_tri_tcc.py  批量编译对照（tri-tcc 列，多 TU 管线）
└── README.md            本文件
```

## 上游与重写层的关系

- 上游源在 `third_party/musl`（子模块，fork=clearnature/musl 的 trirv-vendor 分支），
  **只读消费**——预处理器按规则裁剪（含 `&数字` 技巧行、内联汇编等 TriRV 不支持形态）。
- `musl_tri/` 是**三值原生重写**（非上游补丁）：位图系 memset/memcpy、qsort、
  strtol/strtod、ecvt 系等——这些文件的上游版本依赖二进制语义（`|32`、掩码），
  在三值 `|`/`&` 下会静默算错，必须重写（评审 25 条 A-ctype 项的教训面）。
- 预处理后的临时产物在 `_bootstrap.scratch`（不入库）。

## 整理记录（2026-11-24）

- 删 `musl_src/`（上游 6 文件平铺拷贝，与 third_party/musl 重复；
  trirv_musl_real_e2e 改按 文件名→目录 映射直读上游树）。
- 删 `musl_pp/`（运行时 scratch，由 _bootstrap.scratch 重建）。
- 历史管线名 `musl_tri` 保留（tests/ 文档引用密集，改名破坏面大不值）。
