# LitLattice

LitLattice は、研究論文を整理・管理し、論文間の引用関係をグラフとして探索・解析するための文献管理システムです。

名前は **Literature + Lattice** に由来します。

> 現在開発中です。

## 概要

LitLattice は、自分で収集した論文を管理するとともに、その周辺文献や引用関係を探索できる環境を提供することを目的としています。

主な用途:

- 論文の整理・管理
- PDFの検出と論文への関連付け
- 論文メタデータの管理
- 引用・被引用文献の探索
- 引用ネットワークの探索・解析
- Libraryへの明示的な登録と解除

## 開発状況

v0.1.0のリリースに向けて準備中です。CLIとローカルWeb UIから、論文の取り込み・検索、PDFの同定解決、引用探索とグラフ解析を利用できます。

## Quick Start

```bash
uv sync --locked
export LITLATTICE_DB=/tmp/litlattice-demo.db
uv run llat init

# PDFを置いたディレクトリをSourceとして登録し、scanする
uv run llat src add ~/papers
uv run llat scan            # PDFを認識し、ファイル名・PDF metadata等から論文を同定
uv run llat doc list        # 認識したPDFと、関連付いた論文

# 論文を探して取り込む（OpenAlexと通信。Libraryには自動で追加しない）
uv run llat search "language models are few-shot learners"   # 候補を表示するだけで、保存はしない
uv run llat paper import --doi 10.48550/arXiv.2005.14165

# 論文のmetadataを取得し、引用関係を探索する（OpenAlexと通信）
uv run llat paper list
uv run llat paper fetch <paper-id>
uv run llat lib add <paper-id>                     # 管理対象にするかは明示的に選ぶ
uv run llat expand <paper-id> --depth 1 --max-nodes 200

uv run llat graph neighborhood <paper-id> --depth 1   # 保存済みの引用から周辺のグラフと指標を表示
uv run llat graph library                            # Libraryの論文の間の引用グラフ

uv run llat serve           # http://127.0.0.1:8000/ で論文の追加・PDFの解決・引用探索
```

scanとexpandは、発見した論文をLibraryへ自動では追加せず、PDFをダウンロードしません。

Web UIでは、論文の取り込み・タイトル検索、PDFの手がかりの確認と関連付け・解除、metadata取得、引用探索ができます。PDFフォルダの登録とscanはCLIで行います。

`serve` を除くデータ操作コマンドは `--json`（`-j`）で機械可読な結果を出力します。v1.0以前はJSONの後方互換性を保証しません。

通常の操作とJSON出力は [利用ガイド](docs/usage.md)、再現用のシナリオは [デモ手順](docs/lt-demo.md) を参照してください。

## ドキュメント

詳細な仕様と設計は `docs/` 以下で管理します。

- `docs/usage.md` — 利用手順・設定・JSON出力
- `docs/spec.md` — データモデル・不変条件・責務境界
- `docs/decisions/` — Architecture Decision Records
- `TODO.md` — 実際に次に行う作業
- `docs/development.md` — 開発環境・検証・Git運用
- `docs/agent-workflow.md` — OpenCodeなどのAI開発ツールの使い方
- `CHANGELOG.md` — 変更履歴

## License

未定
