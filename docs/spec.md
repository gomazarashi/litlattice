# LitLattice 仕様

## 目的

個人がローカルで研究論文とPDFを管理し、OpenAlexから周辺文献を発見して引用関係を探索する。SQLiteを永続データの正本とし、CLIとローカルWeb UIでCoreを共有する。

## 非目標

複数マシン、Collection、引用Annotation、FullTextLocator、第2Provider・Provider統合、cache戦略、汎用batch request、短縮Paper ID、community detection、betweenness centrality、追加のgraph layout、MCP server、REST API、AI専用Interface、RAG、vector databaseは対象外。AIや自作scriptはCLIのJSON出力を使う。

## データモデル

| モデル | 主なフィールドと意味 |
| --- | --- |
| Paper | UUID、title、publication_year、created_at、nullableなlibrary_added_at。NULLならLibrary外、UTC timestampならLibrary内 |
| PaperIdentifier | UUID、paper_id、scheme、入力value、normalized_value。schemeとnormalized_valueの組は一意 |
| Source | UUID、path、一意なnormalized_path、created_at、last_scanned_at。scan対象のディレクトリ |
| DocumentCopy | UUID、path、一意なnormalized_path、nullableなpaper_id、content_hash、file_size、file_mtime_ns、last_seen_at、missing_since、created_at |
| Citation | UUID、citing_paper_id、cited_paper_id、created_at。A → BはAがBを引用することを表す |

DocumentCopyは最大1つのPaperに関連付く。paper_idがNULLなら未同定。Paperは複数のDocumentCopyを持てる。手動linkは関連先を指定したPaperへ変更し、同じ関連先なら変更なし。unlinkは指定したPaperとの関連だけを解除する。

Sourceの削除はDocumentCopyや実ファイルを削除しない。パスは絶対・字句正規化し、symlinkを解決しない。内容hashは一意ではない。

## 不変条件

- 新規DBとschema変更はSQLAlchemyのモデルとAlembic migrationで管理する
- Entity IDはUUID v4。運用日時はtimezone付きUTCとし、naive datetimeを保存しない
- Paperの発見とLibrary登録は区別する。scan・import・fetch・expand・PDF関連付けでLibraryへ暗黙に登録せず、PDFも取得しない
- 外部識別子の正規化後の完全一致で同定する。同じ外部識別子を複数Paperへ割り当てない。タイトル類似だけで自動統合しない
- preprintと出版版は原則別Paper。複数Paperへ一致した結果は候補として返し、統合しない。Providerが報告しない入力識別子を追加しない
- scanは冪等。新規・変更された未関連付けPDFだけを同定する。消えたファイルはmissingとして保持し、Source自体が利用できないときは配下を変更しない
- Citationの向きは常にciting → cited。重複と自己引用は禁止
- Library解除、PDF関連付け解除はPaper・DocumentCopy・実ファイルの削除と区別する
- graphはSQLiteから作る解析用表現。degreeとPageRankは返した部分グラフ内で計算する

## Core / CLI / Webの責務

Coreは同定、スキャン、取り込み、Library、引用、グラフ解析とDBトランザクションを担当する。結果はsession終了後も使えるデータとして返す。transaction境界は [ADR 0002](decisions/0002-core-transaction-boundary.md) に従う。

CLIは初期化、Source、scan、Paper、Library、Citation、import/search/fetch/expand、graphとJSON自動化を提供する。JSONは共通serializerでCore結果を変換し、成功時はok・data・warnings、失敗時はok・error.type・error.messageを返す。stdoutへJSON以外を混ぜず、適切なexit codeを使う。v1.0以前は後方互換性を保証せず、独立schema versionは持たない。

Webは日常的に行うPaper閲覧、検索・取り込み、PDFの同定・関連付け、Library操作、メタデータ取得、expand、グラフ探索を優先する。CLIとWebの機能同等性は要求しない。localhostでのみ利用するHTML画面をサーバー側で生成し、グラフ描画だけをブラウザ上のCytoscape.jsで行う。状態変更はCSRF付きPOSTでCoreを呼ぶ。

## OpenAlexとの境界

現在のProviderはOpenAlexのみ。PaperProviderはlookup_work・search_works・fetch_references・fetch_citationsをまとめる。ProviderWorkは通信結果であり、Paperの同定と永続化はCoreが行う。OpenAlex固有の通信処理はOpenAlexClientにまとめ、通信中にwrite transactionを開かない。

入力と共有する識別子がないrecordは適用しない。preprintと出版版の誤統合を避ける。詳細は [ADR 0003](decisions/0003-openalex-first-provider.md) と [利用ガイド](usage.md) を参照する。
