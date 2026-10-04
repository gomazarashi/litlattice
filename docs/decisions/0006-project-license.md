# ADR 0006: プロジェクトライセンスにMITを採用する

- Status: Accepted
- Date: 2026-10-04

## 判断と理由

LitLattice自身のコードと文書には [MIT License](../../LICENSE) を採用する。copyrightは `2026 gomazarashi` とし、[標準MIT本文](https://opensource.org/license/mit) に独自条項を加えない。

利用・改変・再配布の障壁が低く、小規模OSSとして条件を理解しやすい。以下の固定versionと現在の配布形態では、MIT採用を妨げるcopyleft依存やソース公開義務、追加のApache NOTICE対象の同梱物は確認されなかった。依存のライセンスは依存自身に適用され、LitLatticeにも同じライセンスを要求するものではない。各依存のcopyright・licenseをMITで置換しない。

## 調査範囲と方法

2026-10-04時点の [uv.lock](../../uv.lock) を基準に、直接runtime依存から `dependencies` を再帰的にたどった。Windows条件付きColoramaを含むclosureは23パッケージ。未選択のextrasや各依存を開発するための依存は、このruntime closureに含めない。

表のupstream tagのLICENSE本文を読み、lockのSHA-256と一致するsdist内の本文と照合した。versionとlicense metadataは同versionのPyPI情報・配布物でも確認した。表は今回の選定時点の記録であり、version固定の正本はuv.lockとする。

### 直接runtime依存

| Package | 確認version | License | Upstream本文 |
| --- | --- | --- | --- |
| alembic | 1.20.0 | MIT | [LICENSE](https://github.com/sqlalchemy/alembic/blob/rel_1_20_0/LICENSE) |
| flask | 3.1.3 | BSD-3-Clause | [LICENSE](https://github.com/pallets/flask/blob/3.1.3/LICENSE.txt) |
| flask-wtf | 1.3.0 | BSD-3-Clause | [LICENSE](https://github.com/pallets-eco/flask-wtf/blob/v1.3.0/LICENSE.rst) |
| networkx | 3.7 | BSD-3-Clause | [LICENSE](https://github.com/networkx/networkx/blob/networkx-3.7/LICENSE.txt) |
| pypdf | 6.19.0 | BSD-3-Clause | [LICENSE](https://github.com/py-pdf/pypdf/blob/6.19.0/LICENSE) |
| sqlalchemy | 2.1.1 | MIT | [LICENSE](https://github.com/sqlalchemy/sqlalchemy/blob/rel_2_1_1/LICENSE) |
| typer | 0.27.2 | MIT | [LICENSE](https://github.com/fastapi/typer/blob/0.27.2/LICENSE) |

### 推移runtime依存

| Package | 確認version | License | Upstream本文 |
| --- | --- | --- | --- |
| mako | 1.4.3 | MIT | [LICENSE](https://github.com/sqlalchemy/mako/blob/rel_1_4_3/LICENSE) |
| typing-extensions | 4.16.0 | PSF-2.0（本文に歴史的Python licenseも収録） | [LICENSE](https://github.com/python/typing_extensions/blob/4.16.0/LICENSE) |
| blinker | 1.9.0 | MIT | [LICENSE](https://github.com/pallets-eco/blinker/blob/1.9.0/LICENSE.txt) |
| click | 8.5.0 | BSD-3-Clause | [LICENSE](https://github.com/pallets/click/blob/8.5.0/LICENSE.txt) |
| itsdangerous | 2.2.0 | BSD-3-Clause | [LICENSE](https://github.com/pallets/itsdangerous/blob/2.2.0/LICENSE.txt) |
| jinja2 | 3.1.6 | BSD-3-Clause | [LICENSE](https://github.com/pallets/jinja/blob/3.1.6/LICENSE.txt) |
| markupsafe | 3.0.3 | BSD-3-Clause | [LICENSE](https://github.com/pallets/markupsafe/blob/3.0.3/LICENSE.txt) |
| werkzeug | 3.1.9 | BSD-3-Clause | [LICENSE](https://github.com/pallets/werkzeug/blob/3.1.9/LICENSE.txt) |
| wtforms | 3.2.2 | BSD-3-Clause | [LICENSE](https://github.com/pallets-eco/wtforms/blob/3.2.2/LICENSE.rst) |
| annotated-doc | 0.0.5 | MIT | [LICENSE](https://github.com/fastapi/annotated-doc/blob/0.0.5/LICENSE) |
| colorama | 0.4.6 | BSD-3-Clause | [LICENSE](https://github.com/tartley/colorama/blob/0.4.6/LICENSE.txt) |
| rich | 15.0.0 | MIT | [LICENSE](https://github.com/Textualize/rich/blob/v15.0.0/LICENSE) |
| markdown-it-py | 4.2.0 | MIT | [LICENSE](https://github.com/executablebooks/markdown-it-py/blob/v4.2.0/LICENSE) |
| mdurl | 0.1.2 | MIT | [LICENSE](https://github.com/executablebooks/mdurl/blob/0.1.2/LICENSE) |
| pygments | 2.21.0 | BSD-2-Clause | [LICENSE](https://github.com/pygments/pygments/blob/2.21.0/LICENSE) |
| shellingham | 1.5.4 | ISC | [LICENSE](https://github.com/sarugaku/shellingham/blob/1.5.4/LICENSE) |

AlembicはMako・SQLAlchemy・typing-extensions、FlaskはBlinker・Click・itsdangerous・Jinja2・MarkupSafe・Werkzeug、Flask-WTFはFlask・itsdangerous・WTFormsを引き込む。Mako・Jinja2・Werkzeug・WTFormsはMarkupSafe、SQLAlchemyはtyping-extensionsに依存する。Typerはannotated-doc・Rich・Shellingham・Windows限定Colorama、Richはmarkdown-it-py・Pygments、markdown-it-pyはmdurlに依存する。

Typer 0.27.2には [vendored ClickのBSD-3-Clause notice](https://github.com/fastapi/typer/blob/0.27.2/typer/_click/LICENSE.txt) もある。markdown-it-pyの [元JavaScript実装のMIT notice](https://github.com/executablebooks/markdown-it-py/blob/v4.2.0/LICENSE.markdown-it) とmdurlのNode由来コードのMIT noticeも確認した。typing-extensionsのPSF本文はnotice保持と改変時の変更概要を求めるが、ソース公開を要求しない。

### Build / dev依存

| Package | 確認version | License | Upstream本文 |
| --- | --- | --- | --- |
| uv-build | 0.11.18 | MIT OR Apache-2.0 | [LICENSE](https://github.com/astral-sh/uv/blob/0.11.18/LICENSE-MIT) |
| pytest | 9.1.1 | MIT | [LICENSE](https://github.com/pytest-dev/pytest/blob/9.1.1/LICENSE) |
| ruff | 0.16.9 | MIT | [LICENSE](https://github.com/astral-sh/ruff/blob/0.16.9/LICENSE) |
| iniconfig | 2.3.0 | MIT | [LICENSE](https://github.com/pytest-dev/iniconfig/blob/v2.3.0/LICENSE) |
| pluggy | 1.6.0 | MIT | [LICENSE](https://github.com/pytest-dev/pluggy/blob/1.6.0/LICENSE) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | [LICENSE](https://github.com/pypa/packaging/blob/26.3/LICENSE) |

uv-buildはuv.lockに含まれず、build-systemの `>=0.11.18,<0.12.0` で指定される。今回の `uv build` はuv 0.11.18内蔵のbuild backendを使用するため、0.11.18を確認した。pytest・Ruffとdev専用のiniconfig・pluggy・packagingはlockのversion。pytestが使うPygments・Windows限定Coloramaはruntimeとも共通である。

uv-buildの [Apache本文](https://github.com/astral-sh/uv/blob/0.11.18/LICENSE-APACHE)、packagingのApache/BSD選択、Ruff内のthird-party noticeも確認した。これらはbuild・検証用ツールであり、ツール本体やそのvendoredコードをLitLatticeのwheel・sdistへコピーしない。ツールでbuild・lint・testすることを理由に、出力へツールのライセンスを適用しない。

標準ライブラリのurllib・sqlite3等とPython実行環境は、利用者の環境で提供され、LitLatticeのwheel・sdistには同梱しない。

## 同梱コードと配布時の扱い

Cytoscape.js 3.34.3は [upstreamのMIT License](https://github.com/cytoscape/cytoscape.js/blob/v3.34.3/LICENSE) に従う。npm公式tarballのintegrityを検証し、同梱JSとLICENSEがnpm配布物・GitHub tagとそれぞれbyte単位で一致することを確認した。取得元・hashの正本は [同梱README](../../src/litlattice/web/static/vendor/cytoscape/README.md) とする。

JS冒頭のcopyright・permission noticeと既存のupstream LICENSEを保持し、package resourceとしてwheel・sdistに同梱する。LitLattice自身のLICENSEで上書きしない。既存の同梱noticeで対応できるため、THIRD_PARTY_NOTICESは新設しない。

Python依存はwheelのRequires-Distによって別の配布物として取得され、LitLatticeのwheel・sdistには依存の実装を同梱しない。依存を含む別のbundleを将来配布する場合、そのbundleで各依存のlicense・notice保持を確認する必要がある。

[現行packaging仕様](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#license-and-license-files) に従って `project.license = "MIT"` と `project.license-files` を使用する。uv-build 0.11.18はPEP 639に対応しており、deprecatedなlicense tableやlicense classifierは追加しない。license-filesにはルートLICENSEとCytoscapeのLICENSEを明示する。wheelのdist-info内にも両方のnoticeを収録し、MetadataのLicense-ExpressionとLicense-File、およびsdist内の両本文を実物で確認する。

既存のv0.1.0リリース記録とtagは変更しない。
