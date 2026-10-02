# ADR 0005: 現在のユースケースに合わせた最小モデル

- Status: Accepted
- Date: 2026-10-02

## 判断と理由

外部利用者と既存DBへの互換性要件がない開発段階では、開発速度と理解しやすさを優先する。Library登録はPaperのtimestamp、PDF関連付けはDocumentCopyのnullable FKで表す。引用は辺だけを保持し、複数マシンや情報源の独立モデルを持たない。具体的なモデルと不変条件の正本は [仕様](../spec.md) とする。

schemaはAlembicのinitial migrationから管理する（ADR 0001）。

## 結果

Paperの発見・Library登録・PDFの存在を区別できる。Entityとjoinを必要最小限に保ち、一意性・FK・自己引用禁止を維持する。JSONはCLI自動化に残すが、v1.0以前の互換性を約束しない。非目標の設計は必要性が発生してから行う。
