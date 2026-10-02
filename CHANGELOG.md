# Changelog

このファイルは、LitLatticeの変更履歴の正本である。

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) を参考にし、バージョン番号は Semantic Versioning の考え方に従う（[開発ガイド](docs/development.md)）。

## Unreleased

### Changed

- DB schemaを大幅に簡素化し、Machine / LibraryEntry / PaperDocument / CitationEvidenceを削除。Library状態をPaperのnullableなUTC timestamp、PDF関連先をDocumentCopyのnullableなpaper_idへ集約した。
- Alembic履歴をinitial migration 1本へ再構築。以前の開発版DBとの互換性を廃止したため、新しいDB pathで `llat init` を実行して再作成が必要。
- AI・script操作用のCLI JSON出力を維持し、独立schema versionを削除。v1.0以前は後方互換性を保証しない。
- 重複した要件・ドメインモデル・アーキテクチャ文書を `docs/spec.md` へ統合し、ADRとTODOを現在の範囲へ整理した。
- 作業branchへのpushでCIを実行し、文書のみの変更では参照チェックに絞る。統合・コード変更はPython matrixで検証。
- 開発用Pythonを `.python-version` の3.13系へ統一し、通常の依存管理をlocked運用とした。対応範囲と依存制約は維持した。

## v0.3.0 - 2026-10-01

### Added

- DOI・arXiv ID・OpenAlex ID を指定して OpenAlex から論文を取り込む `llat paper import` を追加。title と出版年も取得する。既存の論文と識別子が一致すれば、新しく作らずにその論文へ不足分を補う。OpenAlex の record が指定した識別子を報告しない場合は何も保存せず、record が報告しない識別子は論文に付けない。Library には追加しない。
- PDF の同定を解決するための `llat doc show` を追加。PDF から抽出した識別子の手がかり（強い・弱い）、それを持つ既存の論文、自動同定ならどうなるか、次に実行できるコマンドを表示する。ファイルを読むだけで、何も変更しない。
- OpenAlex から論文を取り込み、PDF をその論文に関連付ける `llat doc import` を追加。Library には追加しない。
- タイトル等の自由文で OpenAlex を検索し、候補を表示する `llat search` を追加。
- Web UI に「論文を追加」ページを追加。DOI・arXiv ID・OpenAlex ID からの取り込みと、タイトルでの検索（候補を示すだけで保存しない）ができる。各候補が既存の論文に一致するか、識別子の完全一致で示す。
- Web UI の論文の詳細から、OpenAlex での metadata 取得と expand（深さ 1・2、上限 20・50・100 件）を実行できるようにした。取得した metadata と探索した論文・引用を保存するが、ライブラリには追加しない。
- Web UI にこのマシンの PDF 一覧と未関連付けの絞り込み、詳細画面を追加。抽出した識別子の由来・強さ・候補・自動同定の判定を確認し、候補または UUID を指定した関連付け、識別子からの取り込みと関連付け、関連付けの解除ができる。Library には自動追加せず、解除しても論文・PDF の記録・実ファイルは残す。状態変更は CSRF で保護する。
- `init`、`paper create` / `fetch`、`lib add` / `remove`、`citation add` / `list`、`src add` / `remove`、`doc link` / `unlink` / `identify`、`path` に `--json`（`-j`）を追加。`serve` を除くデータ操作コマンドで成功・想定された Core エラーを既存の JSON envelope に揃え、変更有無と metadata の未確認・競合の警告も返す。

### Fixed

- PDF 詳細の長いハッシュやパスを小さい画面でも折り返し、ナビゲーションの項目を文字の途中で分割せずに行を折り返す。
- Web の使い方ページとデモ手順を、論文の取り込み・検索・metadata 取得・引用探索・PDF の解決に対応する内容へ更新。デモ手順の CSRF 対策に関する古い説明も修正。

### 既知の制限

- Webからのmetadata取得とexpandは同期処理。expandは深さ1・2、上限20・50・100件に限定する。
- OpenAlexのrecordの品質と利用枠、深い探索のnode予算、グラフ表示の制限は引き続き残る。

## v0.2.0 - 2026-10-01

引用グラフを、論文の周辺の探索と、Library全体の把握の両方に使えるようにしたリリース（ADR 0005）。

### Added

- 保存済みの引用関係から、論文の周辺（深さ・方向を指定）のグラフを表示する `llat graph neighborhood` と、Libraryの論文とその間の引用を表示する `llat graph library` を追加。部分グラフ内の in-degree、out-degree、PageRank を示し、`--json` にも対応する。どちらも読み取りだけで、外部と通信しない。
- 引用グラフの解析に NetworkX（BSD-3-Clause）を runtime dependency として追加。
- Web UI に、論文を起点とした周辺グラフ（`/papers/<id>/graph`）を追加。起点を中央に、被引用を左、参考文献を右に並べ、深さ（1・2）と方向を選べる。論文の詳細ページの「周辺グラフを見る」から開く。
- Web UI の引用グラフで、拡大・縮小、ドラッグでの移動、全体表示、タイトル・識別子・ID での絞り込み、ノードのクリックで詳細を開く操作に対応。
- Web UI に JSON API（`GET /api/graph/library`、`GET /api/graph/neighborhood/<id>`）を追加。`data` は `llat graph ... --json` と同じで、エラーも JSON で返す。
- 引用グラフの描画に Cytoscape.js 3.34.3（MIT）を同梱。外部 CDN からは読み込まない。

### Changed

- `--json` の出力を組み立てる処理を CLI から `litlattice.serialization` へ移し、Web UI と共有できるようにした。出力される JSON は変わらない。
- 対応する Python を `>=3.12,!=3.14.1` とした。依存する NetworkX 3.7 が Python 3.14.1 を対応から除外しているため。
- Web UI の引用グラフ（`/graph`）を、DB 内の全論文ではなく、Library の論文とその間の引用を力学モデルで配置する全体ビューにした。ノードの大きさは PageRank。Library が 500 本を超える場合は、PageRank の高い論文ほど中心に来る同心円状に配置する。
- 概要ページの引用数は、引用グラフへのリンクではなくなった（引用グラフは Library 内の引用だけを表示するため）。

### Removed

- サーバー側で SVG を配置していた旧来の引用グラフ（`litlattice.web.graph`）を削除。

### Security

- Web UI の Library への追加・削除の form に CSRF 対策を追加（Flask-WTF 1.3 の `CSRFProtect`）。secret key は起動ごとにランダムに生成する。

### 既知の制限

- 全体ビューは、Library が 500 本を超えると力学モデルではなく、PageRank の高い論文ほど中心に来る同心円状に配置する（Cytoscape.js 本体の力学モデルでは数千本を実用的な時間で配置できないため）。
- 全体ビューの密な部分ではラベルが重なることがある。まとまり（クラスタ）の表示はまだない。
- グラフの指標は in-degree、out-degree、PageRank だけで、どれも表示した部分グラフの中での値である。
- 周辺ビューの深さは 1・2 だけを選べる。
- `llat serve` を再起動した後や、ページを開いてから 1 時間を超えた後は、開いたままのページの form を送信できない（ページを再読み込みすればよい）。

### 検証記録

- 配布wheelを別venvへinstallしてheadless ChromeでグラフとCSRF付きLibrary操作を確認。合成データのLibrary 450本で表示まで約1.7秒、2000本で約2.9秒。`cose`のままでは2000本で88秒かかったため、大きなLibraryは同心円配置とした。

## v0.1.0 - 2026-10-01

PDFフォルダを登録してから引用グラフを確認するまでの一連の流れを成立させた最初のリリース。

### Added

- Paper / LibraryEntry / DocumentCopy を分離したドメインモデルと、SQLAlchemy / Alembic による SQLite への永続化（ADR 0002）。
- Paper と Library の CLI（`llat paper create / list`、`llat show`、`llat lib add / remove`）。
- Citation の記録と探索（`llat citation add / list`、`llat path`）。方向は常に A → B（A が B を引用する）とする。
- ローカルWeb UI（`llat serve`）。論文一覧、論文詳細、引用グラフ、使い方のページがある。日本語で表示する。
- Source の登録（`llat src add / list / remove`）。
- PDF の scan（`llat scan`）。scan は冪等で、消えたファイルは削除せず missing とする。Source 自体が見つからない場合は既存の記録に触れない。scan だけでは Library への追加も外部との通信も行わない。
- Paper Identity Resolution。外部識別子の完全一致だけで同定し、title では統合しない。曖昧な場合は候補を返す。
- PDF からの識別子抽出。ファイル名、PDF metadata、1ページ目から抽出し、1ページ目本文にある DOI は弱い手がかりとして扱う。
- OpenAlex Provider と metadata の取得（`llat paper fetch`）（ADR 0004）。
- citation expand（`llat expand`）。発見した Paper、Citation、CitationEvidence を保存する。Library への追加や PDF の取得は行わない。
- Document 操作（`llat doc list / link / unlink / identify`）。
- `--json` 出力（`show`、`paper list`、`src list`、`doc list`、`scan`、`expand`）。
- Web UI の論文詳細に PDF と出版年を表示。
- GitHub Actions による CI（Python 3.12 / 3.13）。

### Fixed

- OpenAlex の起点の work が起点 Paper と確認できない場合（識別子を共有しない、または別の Paper に一致する）でも、`expand` がその work の引用を起点 Paper に保存していた問題を修正。この場合は探索も保存もしない。
- OpenAlex の応答を読んでいる途中の通信エラー（`ConnectionResetError`、`IncompleteRead` 等）が traceback になり、`--json` でも構造化されたエラーにならなかった問題を修正。

### 検証記録

- 実PDF 13ファイルのscanで、識別子を持つ12本中11本を正しく同定し、誤同定は0件。v0.1以前のschemaからのmigrationと、build済みwheelでのscan・expand・Web UIを確認した。

### 既知の制限

- 1ページ目の本文だけにある DOI は弱い手がかりであり、誤って関連付く可能性がある（`llat doc unlink` で外せる）。
- ambiguous となった PDF を解決する手段は、`llat doc link` による手動の関連付けだけである。
- OpenAlex の record が確認できない論文（arXiv `1810.04805` 等）は、`expand` しても何も保存されない。
- OpenAlex の key なしの日次利用枠は小さい。
- `--depth 2` 以上では、最初に探索した work が node 予算を多く使う。
- Web UI の引用グラフは DB 内の全 Paper を1枚に描く。状態を変更する操作の CSRF 対策もまだない。
- `paper fetch`、`citation list`、`lib add` 等には `--json` がない。
