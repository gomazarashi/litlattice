# ADR 0001: SQLAlchemy 2.x と Alembic による永続化

- Status: Accepted
- Date: 2026-09-30
- Updated: 2026-10-04

## Context

LitLatticeはSQLiteを永続データの正本としている。

schemaは機能の追加に伴って今後も継続的に変化する。v0.1.0のリリース後は、ユーザーが蓄積したデータを後続versionへ引き継ぐ方針と、更新に失敗した場合の復旧手順が必要になる。

Paper、PaperIdentifier、Citation、DocumentCopy等では、一意性、外部キー、CHECK制約といったrelational constraintsがドメイン上の不変条件を守るうえで重要である。

永続化の方式として、以下が候補となった。

- 標準ライブラリの `sqlite3` と手書きSQL migration
- ORMとmigration toolの組み合わせ

## Decision

- SQLiteを永続データの正本とする。
- persistence mappingにはSQLAlchemy 2.x（typed declarative mapping）を使用する。
- schema migrationにはAlembicを使用する。
- 新規DBもAlembicの `upgrade head` によって作成する。`Base.metadata.create_all()` をschema初期化の正規ルートにしない。
- SQLiteのmigrationのため、Alembicのbatch modeを利用可能にする。
- autogenerateはmigration作成の補助ツールとして扱い、生成されたmigrationは必ずレビューする。

### リリース済みDBの更新と復旧

- v0.1.0以降の正式リリースで作成したDBは、原則としてAlembic migrationで後続の対応versionへ更新できるようにする。通常のupgradeでDBの削除・再作成を要求しない。自動migrationできない重大な互換性破壊は、そのversionの移行方針とrelease noteで明示する。
- 通常のCLI・Web操作はrevisionが現在のheadと一致しなければ利用を拒否し、暗黙にmigrationしない。ユーザーがバックアップ後、同じDB pathに対して `llat init` を実行し、`upgrade head` を適用する。未知のrevisionをstampで上書きしたり、自動downgradeしたりしない。
- migrationは既存の `engine.begin()` とAlembicを使う。ただし現在のsqlite3のlegacy transaction modeでは、DDLが実際のtransactionを開始しない場合がある。AlembicのSQLite実装も `transactional_ddl = False` であり、migration全体の完全rollbackは保証しない。失敗したDBは一部変更済みの可能性があるため、エラーとDBを保全して原因を調べ、必要なら更新前のバックアップへ戻す。独自のtransaction管理は今回追加しない。
- バックアップの基本は、DBを利用するすべてのプロセスを正常終了させ、DBファイルをコピーすることとする。LitLatticeはWAL等を設定せず、通常のSQLiteのDELETE journal modeを使う。異常終了後のjournalや、外部からWALに変更したDBには単一ファイルコピーの手順を適用しない。
- 復元も停止中のファイルコピーで行う。オンラインbackup・自動backup・世代管理と専用コマンドは追加しない。利用者向け手順の正本は [利用ガイド](../usage.md#dbの更新バックアップ復元)、migrationの検証方針は [開発ガイド](../development.md#dbとmigration) とする。

調査時の一時DBでは、2つ目のテーブル作成前に例外を発生させると、最初のテーブルが残った。確認環境はPython 3.13、SQLite 3.49.1、SQLAlchemy 2.1.1、Alembic 1.20.0であり、この結果から完全rollbackを契約に含めない。

参考: [SQLAlchemyのsqlite3 transaction mode](https://docs.sqlalchemy.org/en/21/dialects/sqlite.html#legacy-transaction-mode-with-the-sqlite3-driver)、[AlembicのSQLite実装](https://alembic.sqlalchemy.org/en/latest/api/ddl.html#alembic.ddl.sqlite.SQLiteImpl.transactional_ddl)、[SQLiteのjournal mode](https://www.sqlite.org/pragma.html#pragma_journal_mode)、[SQLiteのbackupと復元時の注意](https://www.sqlite.org/howtocorrupt.html#backup_or_restore_while_a_transaction_is_active)。

## Consequences

- SQLAlchemyとAlembicへの依存が増える。
- schemaの変更履歴をrevisionとして明示的に管理できる。
- SQLite固有の制約があるALTER操作にもbatch modeで対応しやすい。
- ORM modelとdomain modelを将来必ず同一にする必要はない。ORM modelは永続化の表現であり、ドメインの唯一の表現とは位置付けない。

## Alternatives Considered

### `sqlite3` + 手書きSQL migration

依存は少ないが、migrationの適用状態管理、SQLiteのテーブル再作成を伴う変更、modelとschemaの差分確認を自前で維持する必要がある。

継続的なschema変更を想定すると保守コストが大きいため採用しない。

### `Base.metadata.create_all()` のみ

既存DBに対するschema変更を表現できず、schema evolutionの正規ルートにならないため採用しない。
