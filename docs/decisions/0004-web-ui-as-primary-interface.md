# ADR 0004: ローカルWeb UIとbrowserでのgraph描画

- Status: Accepted
- Date: 2026-09-30
- Updated: 2026-10-02

## 判断

ローカルWeb UIを主要UIの一つとする。人間の日常利用を優先し、CLIとの完全な機能同等性は要求しない。両方が同じCoreを呼ぶ。

server-rendered HTMLを使い、graphだけCytoscape.jsで描画する。部分グラフの抽出とdegree・PageRankの解析はCore、配置と操作はbrowserが担当する。Web内部のgraph用JSON endpointは画面を支えるものであり、外部向けREST APIにはしない。

localhostで待ち受け、debugを無効にする。状態変更はCSRF付きPOST。認証と外部公開は対象外。

## 理由と結果

画面全体のJavaScript frameworkやbuild工程を増やさず、引用グラフの拡大・移動・検索を提供できる。Cytoscape.jsはライセンスと入手元を記録して同梱し、外部CDNやweb fontへ依存しない。CLIとWebは共通serializerでCore結果をJSON値へ変換するが、操作の範囲と表示はそれぞれの用途で決める。
