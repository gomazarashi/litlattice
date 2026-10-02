# LT デモ手順

CLI とローカル Web UI の両方から、同じ SQLite データベースを操作・閲覧するデモの手順。通常の操作・設定・制限は [利用ガイド](usage.md) を参照する。

- 1〜7: 架空データを手で登録するデモ（ネットワーク不要）
- 8: 実際の PDF から scan、論文同定、citation expand までを行うデモ（OpenAlex と通信する）

- デモ専用のDBを使う。普段のDB（`~/.local/share/litlattice/`）は使わない。
- Paper、DOI、引用関係はすべて **デモ用の架空データ** である。実在論文の引用関係を示すものではない。DOI は例示用の `10.5555/...` を使う。

## 1. 準備

```bash
uv sync --locked

DEMO_DB=/tmp/litlattice-lt.db
rm -f "$DEMO_DB"

uv run llat --db "$DEMO_DB" init
```

`llat` の繰り返し入力を減らすため、以降は次の関数を使う。

```bash
llat() { uv run llat --db "$DEMO_DB" "$@"; }
new_paper() { llat paper create "$@" | sed -n 's/^ID: *//p'; }
```

## 2. Paper を作成する

Paper の作成だけでは Library に追加されないことに注目する。

```bash
A=$(new_paper --title "Graph-Aware Literature Discovery"     --doi 10.5555/litlattice-demo.1)
B=$(new_paper --title "Citation-Centered Research Navigation" --doi 10.5555/litlattice-demo.2)
C=$(new_paper --title "Local-First Paper Libraries"           --doi 10.5555/litlattice-demo.3)
D=$(new_paper --title "Document Identity from Local Files"     --doi 10.5555/litlattice-demo.4)
E=$(new_paper --title "Human-in-the-Loop Paper Resolution"    --doi 10.5555/litlattice-demo.5 --arxiv 2401.00005)
F=$(new_paper --title "Survey of Personal Research Tooling"    --doi 10.5555/litlattice-demo.6)
G=$(new_paper --title "Incremental Citation Graph Construction" --doi 10.5555/litlattice-demo.7)
H=$(new_paper --title "Provenance for Bibliographic Metadata"  --doi 10.5555/litlattice-demo.8)
I=$(new_paper --title "Offline-Capable Reference Managers"     --doi 10.5555/litlattice-demo.9)
J=$(new_paper --title "Deduplication Without Automatic Merging" --doi 10.5555/litlattice-demo.10)
K=$(new_paper --title "Directed Path Queries over Citations"   --doi 10.5555/litlattice-demo.11)
L=$(new_paper --title "Lightweight Views of Scholarly Graphs"  --doi 10.5555/litlattice-demo.12)

llat paper list
```

`-` は Library 外、`L` は Library 内を表す。

## 3. 一部だけ Library へ追加する

```bash
llat lib add "$A"
llat lib add "$C"
llat lib add "$F"
llat lib add "$H"
llat paper list
```

## 4. Citation を登録する

`A → B` は「A が B を引用している」という意味である。

```bash
llat citation add "$A" "$B"
llat citation add "$A" "$E"
llat citation add "$B" "$C"
llat citation add "$E" "$C"
llat citation add "$C" "$D"
llat citation add "$E" "$D"
llat citation add "$E" "$J"
llat citation add "$F" "$A"
llat citation add "$F" "$G"
llat citation add "$F" "$I"
llat citation add "$G" "$B"
llat citation add "$G" "$K"
llat citation add "$H" "$D"
llat citation add "$H" "$J"
llat citation add "$I" "$C"
llat citation add "$I" "$H"
llat citation add "$J" "$D"
llat citation add "$K" "$E"
llat citation add "$K" "$L"
llat citation add "$L" "$C"
```

同じ Citation をもう一度登録しても増えない（冪等）。

```bash
llat citation add "$A" "$B"
```

## 5. CLI で references / cited-by / path を見る

```bash
llat citation list "$C" --direction references   # -> : C が引用している Paper
llat citation list "$C" --direction citations    # <- : C を引用している Paper
llat citation list "$C"                          # 両方

llat path "$A" "$D"    # 引用の向きに沿った最短経路（A → E → D）
llat path "$D" "$A"    # 逆向きは辿らないのでエラーになる
```

## 6. ブラウザで見る

```bash
uv run llat --db "$DEMO_DB" serve
```

ブラウザで <http://127.0.0.1:8000/> を開く。8000番が使用中なら `serve --port 8080` のように変更する。

- 概要: 論文数、ライブラリ件数、引用数、被引用数の多い論文
- 論文: ライブラリ未登録も含む論文一覧
- 引用グラフ: ライブラリに登録した論文と、それらの間の引用を表示する（ライブラリ外の論文は出ない）。ノードの大きさは PageRank。拡大・縮小、ドラッグでの移動、「全体を表示」、論文を探す操作ができ、ノードをクリックすると詳細を開く
- 論文の詳細: 参考文献（この論文が引用している論文）、被引用、ライブラリ状態。ここからライブラリへの追加・削除ができる。「周辺グラフを見る」で、その論文を中央に、被引用を左、参考文献を右に並べたグラフを開ける（深さ1〜2、方向を選べる）

## 7. CLI の変更がブラウザに反映されることを見せる

サーバーを動かしたまま、別のターミナルで同じDBを操作する（`DEMO_DB` と関数は再定義する）。

```bash
llat lib add "$D"
llat citation add "$B" "$D"
```

ブラウザを再読み込みすると、同じ SQLite の状態が反映される。逆に、ブラウザで Library から外した結果は `llat paper list` に現れる。

## 8. 実際の PDF から引用グラフまで

PDF フォルダを登録し、scan、論文同定、metadata 取得、citation expand、Web UI での確認までを通す。OpenAlex（https://api.openalex.org）と通信するため、ネットワークが必要である。API key は不要だが、keyなしの日次利用枠は小さい（`OPENALEX_API_KEY` を設定すると増える）。

### PDF を用意する

arXiv からダウンロードしたPDFは、ファイル名（例: `2005.14165v4.pdf`）や1ページ目の arXiv stamp から同定できる。出版社のPDFは、埋め込み metadata や1ページ目の DOI から同定できることが多い。

```bash
PDF_DIR=/tmp/litlattice-lt-pdfs
mkdir -p "$PDF_DIR"
curl -sL -o "$PDF_DIR/2005.14165v4.pdf" https://arxiv.org/pdf/2005.14165v4
curl -sL -o "$PDF_DIR/1512.03385v1.pdf" https://arxiv.org/pdf/1512.03385v1
```

手元の論文PDFのフォルダを使ってもよい。scan はファイルを読むだけで、移動・変更・削除はしない。

### Source を登録して scan する

```bash
DEMO_DB=/tmp/litlattice-lt-real.db
rm -f "$DEMO_DB"
llat() { uv run llat --db "$DEMO_DB" "$@"; }

llat init
llat src add "$PDF_DIR"
llat scan
llat doc list
```

scan の出力には PDF ごとの同定結果が出る。

- `new paper` / `linked`: 識別子から論文を同定し、PDF と関連付けた
- `ambiguous`: arXiv ID と出版社 DOI が混在する等で、1つの論文に決められなかった（preprint と出版版を統合しないため）
- `unidentified`: 識別子が見つからなかった。PDF の記録は残る

どの場合も Library には追加されない。もう一度 `llat scan` を実行しても、PDF や論文は増えない。

`ambiguous` や `unidentified` になった PDF は、`llat doc show <document-id>` で、抽出した識別子、それを持つ既存の論文、次に実行できるコマンドを確認できる。

```bash
llat doc list --unlinked                         # 論文と関連付いていない PDF
llat doc show <document-id>                      # 手がかりと候補、次の手順
llat doc link <document-id> <paper-id>           # 既存の論文に関連付ける
llat doc import <document-id> --arxiv 2401.12345 # OpenAlex から取り込んで関連付ける
```

### metadata を取得し、Library に入れ、引用を探索する

```bash
P=$(llat doc list | grep 2005.14165 | sed 's/.*-> //')

llat paper fetch "$P"      # OpenAlex から title / 出版年などを補完
llat lib add "$P"          # 管理対象にするのは明示的な操作
llat expand "$P" --direction both --max-nodes 60

llat paper list            # 発見した論文は `-`（Library 外）のまま
llat citation list "$P" --direction references
```

`expand` は発見した論文と引用関係を保存するが、Library への追加や PDF のダウンロードはしない。

OpenAlex の一部の record には、別の論文の DOI や title が混入している（2026-09 時点で、arXiv の `1706.03762`、`1810.04805`、`2303.08774` 等）。このような record は論文と識別子を共有しないため `unconfirmed` となり、`paper fetch` は何も適用しない。`expand` も、確認できないworkの引用を起点の論文へ付けないよう、探索も保存もせずに警告する。

### ブラウザで確認する

```bash
llat serve --port 8080
```

論文詳細ページでは、関連付いた PDF のパス、出版年、参考文献・被引用を確認できる。expand した論文の詳細から「周辺グラフを見る」を開くと、発見した参考文献と被引用を左右に分けて表示する。

### Web から論文と PDF を管理する

- 「論文を追加」で DOI / arXiv ID / OpenAlex ID を指定して取り込む。タイトル検索では候補を確認してから取り込む（検索だけでは保存しない）。
- 「PDF」でDocumentCopy を表示し、未解決のものだけに絞り込む。詳細では抽出した識別子と候補を確認し、既存の論文との関連付け、識別子からの取り込みと関連付けができる。
- タイトル検索から新たに追加した論文に関連付けるには、その論文の詳細にある UUID を PDF の詳細に入力する。
- PDF の関連付けを解除しても、論文・DocumentCopy・実ファイルは残る。
- 論文の詳細から metadata 取得と expand を実行する。深さは 1・2、上限は 20・50・100 件で、通信中は送信ボタンに表示する。

いずれも Library へ自動追加しない。管理したい論文だけを詳細画面から明示的に Library へ追加する。

### JSON で出力する

AI エージェント等から使う場合は `--json`（`-j`）を付ける。stdout には JSON だけが出る。

```bash
llat show "$P" --json
llat expand "$P" --max-nodes 10 --json
llat paper fetch "$P" --json
llat citation list "$P" --json
llat lib add "$P" --json
```

## 注意

`llat serve` は Flask の組み込みサーバーを使う、ローカル利用・デモ用のサーバーである。既定では `127.0.0.1:8000` で待ち受け、外部には公開しない。状態変更のフォームは CSRF で保護しているが、認証はないため、外部ネットワークへ公開しないこと。
