# ADR 0001: SQLAlchemy 2.x と Alembic による永続化

- Status: Accepted
- Date: 2026-09-30

## Context

LitLatticeはSQLiteを永続データの正本としている。

schemaは機能の追加に伴って今後も継続的に変化する。

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
