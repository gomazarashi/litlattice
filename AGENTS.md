# AGENTS.md

LitLatticeは研究論文を管理し、引用関係を探索するシステムである。CLIとローカルWeb UIは共通Coreを使う。

## 作業の進め方

- ユーザーの明示的な指示を優先する。作業範囲、commit・push・PR作成の指示は会話を通じて引き継ぐ
- 必要な最小構成を実装し、使わない抽象層・設定・将来機能を先回りして追加しない
- 変更を論理的な単位へ分け、日本語のコミットメッセージを使う。なるべく体言止めとする
- 振る舞いを変えたら必要なテストを追加・更新し、変更に応じた確認を行う
- 設計・要件を変えたら、その正本文書を更新する。重要で変更しにくい判断はADRに記録する
- 未完了の作業は [TODO](TODO.md)、利用者に関係する変更は [CHANGELOG](CHANGELOG.md) に記録する

## 守るべき不変条件

詳細の正本は [仕様](docs/spec.md) とaccepted ADRである。実装の都合やテストを通すためだけに変更しない。

- 永続データの正本はSQLite。グラフは解析用の表現とする
- 永続化はSQLAlchemy、schema変更と新規DB作成はAlembic migrationを使う（ADR 0002）
- 独立Entityは原則UUID v4。運用timestampはUTCで、naive datetimeを永続化しない
- Paperの発見、Library登録、DocumentCopyの存在は区別する。発見・scan・import・expandでLibrary登録やPDF取得を暗黙に行わない
- 外部識別子をPaperの主キーにせず、scheme・入力値・正規化値を区別する。同じ外部識別子を複数Paperへ割り当てない
- タイトル類似だけでPaperを自動統合しない。preprintと出版版は原則別Paper。曖昧な結果は候補として返す
- Citationは常に「AがBを引用する」をA → Bで表す。重複と自己引用は禁止する
- DocumentCopyは未同定でも保持する。nullableなpaper_idで最大1つのPaperに関連付け、見つからなくなったファイルをscanだけで削除しない
- scanは冪等。Sourceは正規化したパスで一意に識別する
- 同定処理は複数のuse caseで共有し、現在のOpenAlex連携は単一のPaperProvider interfaceにまとめる
- InterfaceはCoreを呼び、同定・scan・外部通信・DB操作の詳細を持たない
- Library解除、PDF関連付け解除、記録削除、実ファイル削除、Paper完全削除を区別する
- JSONモードのstdoutへログや進捗を混ぜず、構造化した結果と適切なexit codeを返す

## 文書と確認

| 文書 | 役割 |
| --- | --- |
| [README](README.md) | プロジェクトへの入口 |
| [利用ガイド](docs/usage.md) | 操作・設定・JSON出力・現在の制限 |
| [仕様](docs/spec.md) | データモデル・不変条件・責務境界・非目標 |
| [ADR](docs/decisions/) | 重要な判断と理由 |
| [開発ガイド](docs/development.md) | 環境・検証・依存管理・Git運用 |
| [エージェント運用](docs/agent-workflow.md) | CodexとOpenCodeの役割・委譲とSkillの扱い |

実データをテストに使わない。Core・CLIの通常テストは外部ネットワークへ依存させず、Webの状態変更はCSRFで保護する。コード変更はRuffと関連テスト、文書変更は参照・例・実装との整合性を確認する。
