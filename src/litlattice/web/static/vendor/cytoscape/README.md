# Cytoscape.js（同梱）

LitLatticeのWeb UIが引用グラフの描画に使う、Cytoscape.jsの配布ファイルである（ADR 0004）。外部CDNから読み込まず、package resourceとして同梱する。このディレクトリのファイルは編集しない。

| 項目 | 値 |
| --- | --- |
| version | 3.34.3（2026-09-07、stable） |
| license | MIT（`LICENSE` は upstream のものをそのまま置いている） |
| 公式サイト | https://js.cytoscape.org/ |
| ソース | https://github.com/cytoscape/cytoscape.js （tag `v3.34.3`） |
| 取得元 | npm の公式 package `cytoscape@3.34.3`（https://registry.npmjs.org/cytoscape/-/cytoscape-3.34.3.tgz） の `dist/cytoscape.min.js` と `LICENSE` |
| tarball の integrity | `sha512-yfYGhRcGAntq6YBD583j4n0Eg3jIxvWmZtz/5uz9UYkeIStSlMxuUja+ec5j3iBD8nv1rwaOAYMW09tBdkSeaQ==` |
| `cytoscape.min.js` の SHA-256 | `5f3b5b529546d5af1fc5628590af033b74511a5b6f789f5f4682845863228b91` |

`cytoscape.min.js` は UMD 版で、読み込むと `window.cytoscape` を定義する。GitHub の tag `v3.34.3` の `dist/cytoscape.min.js` と同一であることを確認した。

## 更新する場合

1. 公式の stable release を確認する（pre-release は使わない）
2. npm の tarball を取得し、registry の integrity と一致することを確認する
3. `dist/cytoscape.min.js` と `LICENSE` を置き換え、この表の version、integrity、SHA-256 を更新する

```bash
sha256sum src/litlattice/web/static/vendor/cytoscape/cytoscape.min.js
```
