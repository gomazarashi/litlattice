# ADR 0002: Core use caseをtransaction boundaryとする

- Status: Accepted
- Date: 2026-09-30

## Context

CLIとローカルWeb UIが同じCoreを利用する。

Interfaceごとにtransactionの扱いが異なると、一部だけが保存される操作や、Interfaceへの永続化詳細の漏出が起こりやすい。

一方、初期段階でRepositoryやUnit of Work等の抽象層を導入すると、実際の必要性が明確になる前に構造が複雑になる。

## Decision

- 一つのpublic Core use caseが一つのtransaction boundaryを所有する。use caseは成功時にcommitし、失敗時には何もcommitしない。
- CLI等のInterfaceはSQLAlchemyの `Session` を直接操作しない。Interfaceは使用するDBを指定してCore use caseを呼び出す。
- 現段階ではRepository abstractionを導入しない。Core use caseの内部でSQLAlchemyを直接使用してよい。
- ORM modelとdomain modelの完全な二重モデルはまだ導入しない。
- Session外で使えない状態（attached ORM object、lazy load）をInterfaceへ返さない。必要な場合だけ小さなresult object（immutableなdataclass等）を返す。
- 予期された失敗は、DBの例外をそのまま露出させずCore-levelのerrorとして返す。

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
