# ADR 0006: 現在のユースケースに合わせたモデルの簡素化

- Status: Accepted
- Date: 2026-10-02

## 判断と理由

外部利用者と既存DBへの互換性要件がない開発段階では、開発速度と理解しやすさを優先する。Library登録はPaperのtimestamp、PDF関連付けはDocumentCopyのnullable FKで表す。引用は辺だけを保持し、複数マシンや情報源の独立モデルを持たない。具体的なモデルと不変条件の正本は [仕様](../spec.md) とする。

Alembicは維持し、過去のmigrationを最終schemaのinitial migration 1本へ置き換える。旧開発版DBは作り直す。現在の判断と矛盾するADR 0001は削除し、経緯はGit履歴へ残す。

## 結果

Paperの発見・Library登録・PDFの存在は引き続き区別できる。不要なEntityとjoinを減らし、現在必要な一意性・FK・自己引用禁止を維持する。JSONはCLI自動化に残すが、v1.0以前の互換性を約束しない。非目標の設計は必要性が発生してから行う。
