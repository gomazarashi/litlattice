# ADR 0003: OpenAlex連携と通信のtransaction境界

- Status: Accepted
- Date: 2026-09-30
- Updated: 2026-10-02

## 判断

現在のProviderはOpenAlexのみ。通信は標準ライブラリで実装し、Coreは単一のPaperProviderとProviderWorkを使う。

通信中にSQLiteのwrite transactionを開かない。必要な識別子を短いread transactionで読み、外部通信の結果を集めてから、write transaction内で同定を再確認してまとめて保存する。通信が失敗した場合は何も保存しない。API keyをログやエラーへ出さない。

OpenAlexがpreprintと出版版を同じworkへ統合していても、Paperを安易に統合しない。

- arXiv以外のDOIを持つworkにはarXiv IDを識別子として付けない
- 入力と識別子を共有しないworkは適用しない
- 起点Paperと確認できないworkや別Paperにも一致するworkからはexpandしない
- Providerが報告しない入力識別子をPaperに追加しない

## 理由と結果

通信待ちのDB lockを避け、識別子の一意性と引用グラフの正確さを守る。通常テストはfakeとHTTP応答の置き換えで確認し、外部ネットワークに依存させない。record品質とOpenAlexの利用枠による制限は残る。
