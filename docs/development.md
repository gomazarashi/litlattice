# LitLattice 開発ガイド

作業ルールは [AGENTS](../AGENTS.md)、設計は [仕様](spec.md)・[ADR](decisions/)、操作仕様は [利用ガイド](usage.md) を参照する。

## 環境と依存管理

開発用Pythonは `.python-version` に指定した3.13系を使う。利用者向けの対応範囲は `pyproject.toml` の `requires-python` が正本であり、CIは最低対応の3.12系と開発用3.13系を確認する。開発用の指定と対応範囲を同一視しない。

現在の下限にはNetworkXと、Coreで使う `sqlite3.Connection.autocommit` の要件がある。Python 3.14.1の除外はNetworkXの [既知の不具合への対処](https://github.com/networkx/networkx/pull/8372) に合わせたもの。対応範囲は変更せず、例外条件を他の文書へ繰り返し書かない。

```bash
uv sync --locked
uv run llat --help
uv run llat --version
```

通常はlockを変更せず、リポジトリと同じ依存構成を使う。`.venv/` はコミットせず、`uv.lock` と `.python-version` は管理する。CLIとCoreの開発にGUIや特定IDEは必要ない。

- `pyproject.toml`: Pythonの対応範囲、依存の互換性条件、build backendの正本
- `uv.lock`: 開発・CIで実際に使う具体的な依存versionの正本
- `.python-version`: ローカル開発で選ぶPythonのminor version

依存の追加・更新を意図した場合だけ以下を使い、差分と必要な検証を確認する。

```bash
uv add <package>
uv add --dev <package>
uv lock --upgrade-package <package>
uv sync --locked
```

依存の下限は必要なAPI・修正、上限は既知の互換性上の理由で決める。現在の下限は旧版での検証をせずに機械的に下げない。全依存に一律の上限を付けず、導入時のversionだけを理由に更新もしない。追加時は用途、Python対応、ライセンス、保守状況、代替手段を確認する。

現在のProvider通信は標準ライブラリの `urllib` を使う（ADR 0003）。PageRankはnumpy/scipyを追加せず `litlattice.graph` で計算し、NetworkXの参照実装との一致をテストする。これらの実装判断を変更する場合も、CoreとProviderの境界や指標の意味を維持する。

プロジェクトのライセンスは未定。決定時は依存ライブラリとの適合を確認する。

## 検証

変更に応じた確認を行い、機能や振る舞いを変えたら必要なテストを追加・更新する。

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
python scripts/check_docs.py
```

文書だけの変更は `python scripts/check_docs.py` でローカル参照とコードブロックの閉じ忘れを確認し、例・実装との整合性も確認する。コード変更はまず関連テストを実行し、統合・リリースでは全体を確認する。通常のpytestは外部ネットワークの状態に依存させない。

CoreはInterfaceから独立してテストする。Providerにはfakeを注入し、OpenAlexの通信はmonkeypatchで置き換える。ユーザーの実データや通常のXDG保存先は使わず、`tmp_path`等に隔離する。

PDFは通常 `tests/pdf_factory.py` で生成する。生成データでは不具合を再現できない場合は、小さなバイナリfixtureを許可する。不要な内容・個人情報を含めず、出所と再現する問題を記録する。

CIはPRなしでも作業branchへのpushで動く。文書だけの変更は文書チェックを実行し、コード・設定変更とmain・developへの統合ではPython 3.12/3.13の全チェックを実行する。手動実行も全チェックの対象となる（workflowがdefault branchへ反映された後に利用可能）。実行条件とPython matrixの正本は `.github/workflows/ci.yml` とする。ローカルの確認を毎回すべてのPythonで繰り返す必要はない。

## DBとmigration

SQLAlchemyとAlembicを使う。新規DBもAlembic migrationで作り、`create_all()`を使わない（ADR 0001）。migrationはpackage resourceとして配布し、install後も `llat init` で適用できるようにする。

```bash
export LITLATTICE_DB=/tmp/litlattice-dev.db
uv run alembic current
uv run alembic heads
uv run alembic revision --autogenerate -m "..."
uv run alembic upgrade head
```

autogenerateは補助であり、生成したmigrationはレビューする。schema変更時はモデルとの一致と配布物からの新規DB作成を確認する。

## Webの開発

templateとstatic fileは `src/litlattice/web/` にpackage resourceとして置く。画面の操作確認は変更した流れと対象画面で行う。

- 外部CDNやweb fontを使わず、JavaScript依存はversion・ライセンス・入手元を記録して同梱する
- 現在Node.jsのビルド工程は使わない。必要性が生じたら開発・配布の負担と効果を評価する（ADR 0004）
- Cytoscape.jsの更新手順は [同梱ファイルのREADME](../src/litlattice/web/static/vendor/cytoscape/README.md) を参照する。配布ファイルを手で改変しない
- 自身のJavaScriptは `static/` に置く。外部文字列はtextContentやCytoscapeのlabelで表示し、innerHTMLへ入れない
- 状態変更はCSRF付きPOSTでCoreを呼ぶ。認証・外部公開用サーバーは対象外

## Gitとリリース

versionは `pyproject.toml` の `[project] version` が正本。CLIはpackage metadataから表示する。feature branchごとには上げず、リリース単位で更新する。PATCHは互換性を保つ修正、MINORは機能追加、安定版以降の互換性破壊はMAJORとする。

- `main`: リリース済み。annotated tagはmainのcommitに付ける
- `develop`: 次回リリースへ向けた統合先
- 作業branch: 最新developから `feature/*`、`fix/*`、`docs/*`、`chore/*`、`refactor/*` を作る。緊急の `hotfix/*` はmainから作り、mainとdevelopへ反映する
- PRを作る場合は通常develop、hotfixはmainへ向け、merge commitで統合する。PR作成・mergeはユーザーの指示に従う
- main・developへ直接pushせず、削除しない。force pushやtagの上書きは行わない
- commitは論理的な単位に分け、日本語のメッセージを使う
- 作業branchはmerge後に削除する。未pushの変更、open PR、別worktreeでの使用がなく、統合先へ取り込まれていることを確認する

CHANGELOGは利用者向けの振る舞い、互換性、移行、重要な開発環境の変更を記録する。誤字修正・リンク修正・内部的な文書整理はGit履歴で追えるため、変更ごとに追記を要求しない。`Unreleased`を残し、リリース時に日付付きのversionへ移す。分類はAdded / Changed / Fixed / Security等から必要なものだけ使う。GitHub Releasesは任意。

1. リリース内容を確定し、version・lockのproject metadata・CHANGELOG・TODOを更新してdevelopへ統合する。既存の作業PRに含めてよく、専用のrelease準備branchは必須としない
2. developの全CI成功後、develop → mainのリリースPRを作り、主な変更・検証・制限を記載する
3. リリースPRの全CI成功後にmergeする
4. 最新mainのmerge commitへannotated tagを付けてpushする。同名tagがあれば上書きせず状態を調べる

```bash
git checkout main
git pull --ff-only
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

リリース済みのTODOは次の整理時に除き、履歴はCHANGELOGに残す。記録だけのためにpost-release branchを必須としない。

## 文書の更新

各文書の役割は [AGENTS](../AGENTS.md) を参照する。変更した内容の正本文書を更新し、他の文書は参照を使う。将来候補を現在の要件として増やさない。重要で変更しにくい判断はADRに記録し、判断の変更は理由を記録する。現在のモデルと矛盾する旧ADRはGit履歴へ残して削除できる。

OpenCodeなどへの委譲とSkillの運用は [エージェント運用](agent-workflow.md) に記す。
