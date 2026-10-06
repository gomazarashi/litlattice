# Changelog

このファイルは、LitLatticeの変更履歴の正本である。

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) を参考にし、バージョン番号は Semantic Versioning の考え方に従う（[開発ガイド](docs/development.md)）。

## Unreleased

## v0.1.1 - 2026-10-06

### Added

- プロジェクトのライセンスをMIT Licenseとして明確化し、wheel・sdistにもライセンス情報を同梱。

### Changed

- リリース済みDBをmigrationで引き継ぐ方針と、停止中のバックアップ・復元手順を明記。DB更新失敗時の再作成案内を廃止し、原因と復旧手順を案内。

- Webのメタデータ取得・PDF関連付け・使い方の案内文を読みやすい日本語に整理。

### Fixed

- DocumentCopyへの論文取り込みと関連付けを原子的に保存し、関連付け失敗時にPaper・識別子・metadataの変更が残る問題を修正。

## v0.1.0 - 2026-10-02

PDFフォルダの登録から、論文の同定・取り込み、引用探索とグラフ解析までをCLIとローカルWeb UIで扱える最初のリリース。

### Added

- SQLAlchemy / Alembic によるSQLiteへの永続化と `llat init`（ADR 0001）。Paperの発見、Library登録、PDF（DocumentCopy）の存在を区別する。
- Sourceの登録と冪等なPDFのscan（`llat src`、`llat scan`）。消えたファイルは削除せずmissingとし、scanだけではLibraryへの追加も外部との通信も行わない。
- ファイル名、PDF metadata、1ページ目から抽出した識別子の完全一致による論文の同定。titleでは統合せず、曖昧な場合は候補を返す。PDFの手がかりの確認と関連付け・解除（`llat doc list / show / link / unlink / identify / import`）。
- Paper・Libraryの操作（`llat paper create / list / import / fetch`、`llat show`、`llat lib add / remove`）と、OpenAlexでのタイトル検索（`llat search`）（ADR 0003）。
- Citationの記録と探索（`llat citation add / list`、`llat path`、`llat expand`）。方向は常に A → B（A が B を引用する）とし、expandはLibraryへの追加やPDFの取得を行わない。
- 引用グラフの解析（`llat graph neighborhood / library`）。部分グラフ内の in-degree、out-degree、PageRankを示す。
- ローカルWeb UI（`llat serve`、ADR 0004）。論文の追加・検索、metadata取得とexpand、PDFの解決、Libraryの操作、周辺グラフと全体グラフ（Cytoscape.js 同梱）を日本語で提供し、状態変更はCSRFで保護する。
- `serve` を除くデータ操作コマンドの `--json`（`-j`）出力。
- GitHub Actions によるCI（Python 3.12 / 3.13）。

### 既知の制限

- Webの外部通信は同期処理。expandは深さ1・2、上限20・50・100件に限定する。
- Libraryが500本を超えると、全体グラフは力学モデルではなくPageRank順の同心円配置になる。密な部分ではラベルが重なることがある。
- OpenAlexのrecordが入力の論文と確認できなければ何も保存しない。record品質と利用枠、深い探索のnode予算による制限が残る。
- PDFの1ページ目の本文だけにあるDOIは弱い手がかりであり、誤って関連付く可能性がある（`llat doc unlink` で外せる）。
- v1.0以前はJSON出力の後方互換性を保証しない。
