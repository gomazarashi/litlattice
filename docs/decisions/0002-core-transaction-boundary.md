# ADR 0002: Core use caseをtransaction boundaryとする

- Status: Accepted
- Date: 2026-09-30
- Updated: 2026-10-04

## Context

CLIとローカルWeb UIが同じCoreを利用する。

Interfaceごとにtransactionの扱いが異なると、一部だけが保存される操作や、Interfaceへの永続化詳細の漏出が起こりやすい。

DocumentCopyへの取り込みでPaperの保存と関連付けを別々にcommitすると、関連付けが失敗してもPaperや識別子が残る。原子性の対象を永続化変更と明記し、外部通信前のreadと外部通信後のwriteを区別する必要がある。

一方、初期段階でRepositoryやUnit of Work等の抽象層を導入すると、実際の必要性が明確になる前に構造が複雑になる。

## Decision

- 変更系のpublic Core use caseは、そのuse caseによる永続化変更を一つのwrite transactionで原子的にcommitする。write処理の途中で失敗した場合、そのuse caseによる変更は残さない。
- 外部I/O前の短いread transactionは許容する。外部I/O中にwrite transactionを保持せず、外部I/O後のwrite transaction内で必要な前提条件を再確認してから変更をまとめて適用する。通信との境界は [ADR 0003](0003-openalex-first-provider.md) に従う。
- この原則は、public Core use caseにつき物理的なtransactionを厳密に一つに制限するものではない。DocumentCopyへの取り込みでは、存在確認のread、Provider通信、存在・同定の再確認とPaper・識別子・metadata・関連付けのatomic writeに分ける。
- CLI等のInterfaceはSQLAlchemyの `Session` を直接操作しない。Interfaceは使用するDBを指定してCore use caseを呼び出す。
- 現段階ではRepository abstractionを導入しない。Core use caseの内部でSQLAlchemyを直接使用してよい。
- ORM modelとdomain modelの完全な二重モデルはまだ導入しない。
- Session外で使えない状態（attached ORM object、lazy load）をInterfaceへ返さない。必要な場合だけ小さなresult object（immutableなdataclass等）を返す。
- 予期された失敗は、DBの例外をそのまま露出させずCore-levelのerrorとして返す。

schema migrationの失敗時の保証は、通常のデータ変更とは区別し、[ADR 0001](0001-use-sqlalchemy-alembic-persistence.md#リリース済みdbの更新と復旧) に従う。

## Consequences

- Interfaceは永続化の詳細を知らずにCoreを利用できる。
- 各use caseの原子性が明確になる。
- 複数のuse caseを一つのtransactionにまとめる必要が生じた場合は、その時点で構造を見直す。
- Repository abstractionや独立したdomain modelは、テスト容易性や永続化方式の変更などの具体的な必要が生じた段階で導入を検討する。

## Alternatives Considered

### InterfaceがSessionを管理する

Interfaceごとにtransactionの扱いが分散し、CLI以外のInterfaceで同じ規則を再実装する必要が生じるため採用しない。

### Repository / Unit of Workを最初から導入する

現時点の機能規模に対して抽象層が過剰であり、実際の必要性が明確になるまで採用しない。
