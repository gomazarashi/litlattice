# エージェント運用

## 役割

このプロジェクトではCodexを上位エージェント、OpenCodeを下位実装エージェントとする。ユーザーの指示を優先し、[AGENTS](../AGENTS.md) と [仕様](spec.md) を守る。

Codexがコードと文書を調べ、schema・API・作業範囲を決める。OpenCodeには狭く具体的な実装を委譲する。Codexは報告だけで完了とせず、実際のdiffと必要な検証を確認する。

branch作成・切替、commit、push、merge、rebase、reset、stash、tag、PR作成などGit履歴操作はCodexだけが行う。OpenCodeに許可するGit操作は読み取り用のstatus・diffなどに限る。

## 委譲

```bash
opencode run --auto --print-logs --log-level INFO "<具体的なタスク>" < /dev/null
```

委譲promptには目的、編集可能・禁止ファイル、決定済みAPI/schema、不変条件、必要なテスト、最後の確認、Git操作の禁止を明記する。未決事項を独断で設計させない。並列実行は編集対象が重ならない場合だけ使い、schemaの変更完了前に依存するCore変更を始めない。

- `--auto` で起動する。使えるオプションは `opencode run --help` で確認する
- stdinを使わない実行では `< /dev/null` でEOFを渡す
- 出力が止まったらログ・プロセス・diffで通信待ちや権限待ちを調べる。同じ指示を無条件に再実行しない
- 長い作業は節目で確認済みの結果と残る作業を日本語で報告する
- 未完了の実作業は [TODO](../TODO.md)、利用者向け変更は [CHANGELOG](../CHANGELOG.md) に記録する

## Skill

Skillは開発の補助でありruntime dependencyではない。簡素化の助言で不要な抽象化を見直しても、ユーザーが指定した不変条件、CSRF、必要な検証は省かない。利用するSkillはタスクに応じて選ぶ。
