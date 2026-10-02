# LitLattice 利用ガイド

CLIとローカルWeb UIは同じSQLiteデータベースを操作する。操作の再現例は [デモ手順](lt-demo.md)、開発環境は [開発ガイド](development.md) を参照する。

## 起動とデータの場所

リポジトリから利用する場合は `uv sync --locked` で準備し、以下のコマンドに `uv run` を付ける。install済みのCLI名は `llat` と `litlattice`。`python -m litlattice` でも同じCLIを起動できる。

```bash
llat --help
llat --version
llat init
```

DBの場所は `--db`、環境変数 `LITLATTICE_DB`、XDGのデフォルトの順で決まる。デフォルトは `$XDG_DATA_HOME/litlattice/litlattice.db`（未設定・相対パスの場合は `~/.local/share/litlattice/litlattice.db`）。通常のコマンドはDBを暗黙に作らず、初期化が必要なら `llat init` を案内する。

```bash
llat --db /path/to/library.db init
llat --db /path/to/library.db paper list
```

初期化済みのDBに対する `llat init` は再実行できる。

## PDFを認識して論文に関連付ける

```bash
llat src add ~/papers
llat scan
llat doc list --unlinked
llat doc show <document-id>
llat doc link <document-id> <paper-id>
llat doc unlink <document-id> <paper-id>
```

`<paper-id>` と `<document-id>` は実際のUUIDに置き換える。scanは冪等で、外部通信やPDFのダウンロードは行わない。Sourceが利用可能で、以前見つかったPDFだけが消えていればmissingとして保持する。Source自体が利用できない場合は配下の記録を変更しない。

PDFの手がかりと候補は `doc show` またはWebの「PDF」で確認できる。1つのPDFは最大1つの論文に関連付く。linkで関連先を変更でき、unlinkは指定した論文との関連だけを解除する。関連付けの解除で論文・PDFの記録・実ファイルは削除しない。Sourceの登録を外す `src remove` でもDocumentCopyと実ファイルは残る。

## 論文の取り込み・検索とLibrary

```bash
llat search "deep learning"
llat paper import --doi 10.1038/nature14539
llat doc import <document-id> --doi 10.1038/nature14539
llat paper list
llat show <paper-id>
llat paper fetch <paper-id>
llat lib add <paper-id>
llat lib remove <paper-id>
```

import、search、fetch、expandとそれらに対応するWeb操作はOpenAlexと通信する。`OPENALEX_API_KEY` を設定できる。keyなしの利用枠は小さいため、大きな探索ではkeyが必要になる場合がある。keyをログやリポジトリに残さない。

searchは候補を表示するだけで保存しない。importは論文を取り込み、doc importはPDFとの関連付けも行う。Libraryへの登録は `lib add` またはWebで明示的に行う。fetchは既存のtitle・出版年を上書きせず、不足分を補う。

## 引用探索とグラフ

`A → B` は「AがBを引用する」を意味する。

```bash
llat expand <paper-id> --depth 1 --direction both --max-nodes 200
llat citation list <paper-id> --direction references
llat path <source-paper-id> <target-paper-id>
llat graph neighborhood <paper-id> --depth 1
llat graph library
```

expandは論文・引用を保存し、Libraryへの追加やPDF取得は行わない。graphとpathは保存済みデータを読み、外部とは通信しない。Libraryグラフは管理対象の論文と、その間の引用だけを表示する。指標は返した部分グラフ内の値である。

## ローカルWeb UI

```bash
llat serve                  # http://127.0.0.1:8000/
llat serve --port 8080
```

常に `127.0.0.1` で待ち受け、debugは無効。認証を持たないローカル用サーバーであり、外部ネットワークへ公開しない。

「論文を追加」で識別子から取り込むか、タイトル検索の候補を確認して追加する。「PDF」で手がかりを確認し、候補やUUIDから関連付け、取り込み、解除を行う。論文詳細からmetadata取得、expand、Library操作と周辺グラフの表示ができる。Source登録とscanはCLIで行う。

状態変更はCSRFで保護したPOSTを使う。サーバー再起動後や、ページを開いて1時間を超えた後は、form送信前にページを再読み込みする。

## 現在の制限

- Webの外部通信は同期処理。expandは深さ1・2、上限20・50・100件。バックグラウンド実行と進捗表示はない
- Libraryグラフは500本まで力学モデル、それより多い場合はPageRank順の同心円配置。密な部分ではラベルが重なることがある
- Webの周辺グラフは深さ1・2。グラフの指標はDB全体の被引用数とは異なる
- OpenAlexのrecordが入力の論文と確認できなければ何も保存しない。record品質と利用枠、深い探索で最初のworkがnode予算を多く使う制限が残る
- PDFの1ページ目のDOIだけによる同定は誤って関連付く可能性がある。手がかりを確認し、必要なら関連付けを解除する

## JSON出力

`serve` を除くデータ操作コマンドは `--json`（`-j`）を持つ。stdoutは1つのJSON envelopeだけとし、従来の人間向け表示は引数を付けない場合に維持する。

- 成功: `ok: true`、`data`、`warnings`
- 想定されたCoreエラー: `ok: false`、`error.type`、`error.message` とexit code 1
- CLIの引数解析エラーはstderrとexit code 2。JSON envelopeにはしない
- 曖昧・未確認の結果は成功envelopeの `data.status` と `warnings` で表す。成功が必ず保存を意味するわけではない

v1.0以前はJSONの後方互換性を保証しない。独立したschema versionは持たず、同じ製品version内では一貫した構造にする。各コマンドの実際の出力を確認してscriptで使う。

UUID・日時・enumは共通serializerで文字列へ変換し、Core resultのフィールド名を使う。

```json
{"ok": true, "data": {"paper_id": "<UUID>", "added": true}, "warnings": []}
```

```json
{"ok": false, "error": {"type": "PaperNotFound", "message": "Paper not found: <UUID>"}}
```

```bash
llat paper fetch <paper-id> --json
llat citation list <paper-id> --json
llat lib add <paper-id> --json
```
