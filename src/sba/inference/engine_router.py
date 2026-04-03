"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【ファイルの役割】
  エンジンルーター（AIの「どの窓口に並ぶか」を決める係）

【ひとことで言うと】
  「この質問はどのAIエンジンに頼むべきか」を
  タスクの種類・文章の長さ・混雑状況を見て自動で判定する。

【3つのAIエンジン（Tier）の使い分け】

  Tier1 = Phi-4:14B（ローカル・無料・VRAM8GB）
    → 普通の質問・推論・評価に使う「メインエンジン」
    → ローカルで動くので完全無料

  Tier2 = Gemini 2.5 Flash（クラウド・API・無料枠あり）
    → 長い文章（8000トークン超）の要約や処理
    → Tier1が混んでいるときの「補助エンジン」
    → 1日に使える上限（クオータ）があるので節約しながら使う

  Tier3 = Qwen2.5-Coder:7B（ローカル・無料・VRAM5GB）
    → コード生成・コードレビュー専用の「コード専門エンジン」
    → Tech系のBrainのときだけ使われる

【ルーティング判定の優先順位（上から順にチェック）】

  優先度1: コード系の質問 AND Tech系のBrain
           → Tier3（コード専門エンジン）

  優先度2: 文章が8000トークンを超えている
           OR Tier1が10秒以上混んでいる
           → Tier2（クラウドエンジン）
           ただしTier2のクオータが残り少ない → Tier1に戻す

  優先度3: それ以外全部
           → Tier1（メインエンジン）

【呼び出し元】
  src/sba/learning/learning_loop.py（学習ループから呼ばれる）
  src/sba/experiment/experiment_engine.py（実験エンジンから呼ばれる）

【呼び出し先】
  src/sba/inference/tier1.py（Phi-4を動かすクラス）
  src/sba/inference/tier2.py（Gemini APIを呼ぶクラス）
  src/sba/inference/tier3.py（Qwen2.5-Coderを動かすクラス）

【関連設計書】
  09.推論エンジン・VRAM運用設定書.md（§3.1 判定フロー）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ──────────────────────────────────────────
# Python 3.10以前で「X | Y型」の型ヒントが使えるようにする
# ──────────────────────────────────────────
from __future__ import annotations

# ──────────────────────────────────────────
# 標準ライブラリのインポート
# ──────────────────────────────────────────
from enum import Enum          # 定数グループを作るためのクラス（タイポ防止）
from typing import Optional    # 「Noneかもしれない値」の型ヒント
from dataclasses import dataclass  # データをまとめて持つシンプルなクラスを作るツール

# ──────────────────────────────────────────
# 同じプロジェクト内のファイルをインポート
# ──────────────────────────────────────────
# 各TierのエンジンクラスとInferenceResult（推論結果）をインポート
# ※InferenceResultは各Tierで別々のクラスだが、中身の構造は似ている
from .tier1 import Tier1Engine, InferenceResult as Tier1Result
from .tier2 import Tier2Engine, InferenceResult as Tier2Result
from .tier3 import Tier3Engine, InferenceResult as Tier3Result


# ══════════════════════════════════════════════════════
# 【定数定義①】TaskType - タスクの種類を表す列挙型
# ══════════════════════════════════════════════════════
class TaskType(Enum):
    """
    ────────────────────────────────────────────────────
    AIに頼むタスクの「種類」を表す定数クラス。

    【なぜEnumで定義するか】
      "code_generation" という文字列を直接使うと
      タイポ（typo）したときに気づきにくい。
      TaskType.CODE_GENERATION と書けば、
      タイポがあればPythonが即エラーを出してくれる。

    【各タスクの説明】
      CODE_GENERATION : コードを新しく書く（例: 「ソート関数を作って」）
      CODE_REVIEW     : 既存コードを評価・改善する（例: 「このコードのバグを見つけて」）
      LONG_TEXT       : 8000トークン超えの長い文章の処理
      SUMMARIZATION   : 文章の要約
      REASONING       : 普通の質問・推論・評価（デフォルト）
    ────────────────────────────────────────────────────
    """
    CODE_GENERATION = "code_generation"  # コード生成（Tier3担当）
    CODE_REVIEW     = "code_review"      # コードレビュー（Tier3担当）
    LONG_TEXT       = "long_text"        # 長文処理（Tier2担当）
    SUMMARIZATION   = "summarization"    # 要約（Tier2担当）
    REASONING       = "reasoning"        # 通常推論（Tier1担当・デフォルト）


# ══════════════════════════════════════════════════════
# 【定数定義②】SelectedTier - 選ばれたTierを表す列挙型
# ══════════════════════════════════════════════════════
class SelectedTier(Enum):
    """
    ────────────────────────────────────────────────────
    ルーティング判定の「結果」として選ばれたTierを表す定数クラス。

    route()メソッドがこの値を返すことで、
    「どのTierで処理するか」が明確にわかる。
    ────────────────────────────────────────────────────
    """
    TIER1 = "tier1"  # Phi-4:14B（メインエンジン）
    TIER2 = "tier2"  # Gemini 2.5 Flash（クラウド補助エンジン）
    TIER3 = "tier3"  # Qwen2.5-Coder:7B（コード専門エンジン）


# ══════════════════════════════════════════════════════
# 【データクラス①】InferenceTask - AIに頼む仕事の「依頼票」
# ══════════════════════════════════════════════════════
@dataclass
class InferenceTask:
    """
    ────────────────────────────────────────────────────
    AIエンジンへの「依頼票」。
    「どんな仕事を・どんな設定で頼むか」をまとめたデータクラス。

    【@dataclass とは】
      __init__（初期化）や __repr__（表示）を
      自動で作ってくれるPythonの便利機能。
      わざわざ def __init__(self, ...) と書かなくていい。

    【各フィールドの説明】
      type              : タスクの種類（TaskType.REASONING など）
      prompt            : AIへの質問・指示文
      estimated_tokens  : promptのだいたいのトークン数（文字数の目安）
                          ※1トークン ≈ 英語1単語 ≈ 日本語0.5〜1文字
      is_tech_brain     : Tech系のBrainが装着されているか（コード系BrainのときTrue）
      max_output_tokens : AIが返す回答の最大トークン数（デフォルト2048）
      temperature       : AIの「創造性」（0.0=決まった答え, 1.0=ランダム, デフォルト0.7）
      timeout_s         : 何秒以内に返答がなければタイムアウトするか（デフォルト30秒）
    ────────────────────────────────────────────────────
    """
    type: TaskType          # タスクの種類
    prompt: str             # AIへの質問・指示文
    estimated_tokens: int   # プロンプトの推定トークン数（ルーティング判定に使用）
    is_tech_brain: bool = False        # Tech系Brain装着中かどうか（デフォルトFalse）
    max_output_tokens: int = 2048      # 最大出力トークン数
    temperature: float = 0.7           # 生成の多様性（0.0〜1.0）
    timeout_s: float = 30.0            # タイムアウト秒数


# ══════════════════════════════════════════════════════
# 【データクラス②】RoutingDecision - ルーティングの「判定結果票」
# ══════════════════════════════════════════════════════
@dataclass
class RoutingDecision:
    """
    ────────────────────────────────────────────────────
    route()メソッドが返す「どのTierを選んだか」の結果票。

    【各フィールドの説明】
      selected_tier        : 選ばれたTier（SelectedTier.TIER1 など）
      reason               : なぜそのTierを選んだかの理由（ログ・デバッグ用）
      estimated_wait_time_s: 処理が始まるまでの推定待機時間（秒）
    ────────────────────────────────────────────────────
    """
    selected_tier: SelectedTier     # 選ばれたTier
    reason: str                     # 選んだ理由（ログ用）
    estimated_wait_time_s: float    # 推定待機時間（秒）


# ══════════════════════════════════════════════════════
# 【メインクラス】EngineRouter
# ══════════════════════════════════════════════════════
class EngineRouter:
    """
    ────────────────────────────────────────────────────
    推論エンジンの自動振り分けを担当するクラス。

    【このクラスの仕事】
      InferenceTask（依頼票）を受け取り、
      タスクの種類・文章の長さ・Tier1の混雑状況・Tier2の残量を見て
      「どのTierに頼むか」を自動で決める。

    【判定フロー（優先順位順）】

      ①コード系 AND Tech系Brain → Tier3
        └ コード生成/レビューで、かつコード系BrainのときはTier3専門家に頼む

      ②長文（8000トークン超）OR Tier1混雑（10秒超） → Tier2検討
        └ ただしTier2のクオータ（残量）が不足 → Tier1にフォールバック

      ③それ以外 → Tier1（デフォルト）

    【なぜTier2はクオータ確認が必要か】
      Tier2（Gemini）は無料枠が「1日1500リクエスト」という上限がある。
      上限を超えると課金が発生する可能性があるため、
      残量を確認してから使うかどうか判断する。
    ────────────────────────────────────────────────────
    """

    # ── ルーティング判定で使う閾値（しきい値）定数 ──
    # 閾値とは「この数値を超えたら別の処理にする」という境界線のこと

    TOKEN_THRESHOLD_TIER2 = 8000
    # ↑ プロンプトが8000トークンを超えたらTier2を検討する
    # ↑ 8000トークン ≈ 英語6000単語 ≈ 日本語約12000文字

    TIER1_WAIT_THRESHOLD_S = 10.0
    # ↑ Tier1が10秒以上混雑していたらTier2を検討する
    # ↑ 10秒待つより、Geminiに頼んだ方が速い場合があるため

    TIER2_MIN_REMAINING_TOKENS = 100
    # ↑ Tier2のクオータ残量がこれ以下になったらTier2を使わない
    # ↑ 100トークン = ほぼ使い切り状態（安全マージン）

    def __init__(
        self,
        tier1: Optional[Tier1Engine] = None,
        tier2: Optional[Tier2Engine] = None,
        tier3: Optional[Tier3Engine] = None,
    ) -> None:
        """
        ────────────────────────────────────────────────────
        【初期化処理】

        【引数】
          tier1: Tier1エンジン（省略すると自動で新規作成）
          tier2: Tier2エンジン（省略すると自動で新規作成）
          tier3: Tier3エンジン（省略すると自動で新規作成）

        【Optional[Tier1Engine] = None の意味】
          「Tier1Engineかもしれないし、Noneかもしれない」という型ヒント。
          = None は「引数を省略した場合の初期値はNone」という意味。
          省略されたらその場で新しいエンジンを作る（or演算子を使っている）。

        【なぜ引数で受け取れるようにしているか（依存性の注入）】
          テストのときに「本物のTier1」の代わりに「偽物のTier1（Mock）」を
          渡せるようにするため。これを「依存性の注入（DI）」という。
          テストで実際にOllamaを動かさなくて済む。
        ────────────────────────────────────────────────────
        """
        # 引数が渡されればそれを使い、省略されれば新規作成する
        self.tier1 = tier1 or Tier1Engine()  # Tier1（Phi-4）エンジン
        self.tier2 = tier2 or Tier2Engine()  # Tier2（Gemini）エンジン
        self.tier3 = tier3 or Tier3Engine()  # Tier3（Qwen2.5-Coder）エンジン

    # ══════════════════════════════════════════════════════
    # 【公開メソッド①】route - どのTierを使うか判定する
    # ══════════════════════════════════════════════════════
    def route(self, task: InferenceTask) -> RoutingDecision:
        """
        ────────────────────────────────────────────────────
        【役割】
          InferenceTask（依頼票）を見て「どのTierに振るか」を決める。
          実際のAI推論はここでは行わない。「判定だけ」する。

        【判定の流れ（if文の順番 = 優先順位）】

          1. コード系タスク AND Tech系Brain → Tier3
             ↓ 違う
          2. 長文 OR Tier1混雑 → Tier2検討
             ↓ Tier2クオータ不足
          2b. Tier1にフォールバック
             ↓ どれにも当てはまらない
          3. デフォルト → Tier1

        【引数】
          task: InferenceTask（依頼票）

        【戻り値】
          RoutingDecision（どのTierを選んだか・理由・推定待機時間）
        ────────────────────────────────────────────────────
        """

        # ── 優先度1: コード系 AND Tech系Brain → Tier3 ──
        # コード生成/レビューで、かつTech系のBrainが装着されているとき
        # コード専門のTier3（Qwen2.5-Coder）が一番得意なので優先的に使う
        if self._is_code_task(task) and task.is_tech_brain:
            return RoutingDecision(
                selected_tier=SelectedTier.TIER3,
                reason="コードタスク + Tech系Brain → Tier3（コード専門エンジン）",
                estimated_wait_time_s=0.0,  # Tier3待機時間は別途取得可能だが現在は0概算
            )

        # ── 優先度2: 長文 or Tier1混雑 → Tier2検討 ──
        # _should_use_tier2() で「Tier2を使うべき状況か」を判断する
        if self._should_use_tier2(task):

            # Tier2（Gemini）のクオータ（残量）を確認する
            quota = self.tier2.get_remaining_quota()

            # クオータが十分に残っているかチェック
            remaining = quota.get("remaining_tokens")
            has_quota = (
                remaining is not None
                and remaining > self.TIER2_MIN_REMAINING_TOKENS
            )

            if has_quota:
                # Tier2のクオータが十分 → Tier2を使う
                return RoutingDecision(
                    selected_tier=SelectedTier.TIER2,
                    reason=(
                        f"長文（{task.estimated_tokens}トークン）または"
                        f"Tier1混雑 → Tier2（クラウドエンジン）"
                    ),
                    estimated_wait_time_s=quota.get("daily_used", 0) * 0.001,
                    # ↑ 概算の待機時間（daily_used × 0.001秒）
                    # ↑ 正確な値ではなく「使った量が多いほど少し遅い」程度の目安
                )
            else:
                # Tier2のクオータが不足 → Tier1にフォールバック
                # フォールバック = 「本命が使えないので次善策を使う」こと
                return RoutingDecision(
                    selected_tier=SelectedTier.TIER1,
                    reason="Tier2クオータ不足 → Tier1にフォールバック",
                    estimated_wait_time_s=self.tier1.get_latest_wait_time(),
                )

        # ── 優先度3: デフォルト → Tier1 ──
        # 上の条件に何も当てはまらなかった普通の質問はTier1が担当
        return RoutingDecision(
            selected_tier=SelectedTier.TIER1,
            reason="デフォルトルーティング → Tier1（メインエンジン）",
            estimated_wait_time_s=self.tier1.get_latest_wait_time(),
        )

    # ══════════════════════════════════════════════════════
    # 【公開メソッド②】infer - 判定してそのまま推論も実行する
    # ══════════════════════════════════════════════════════
    async def infer(
        self, task: InferenceTask
    ) -> Tier1Result | Tier2Result | Tier3Result:
        """
        ────────────────────────────────────────────────────
        【役割】
          route() で判定 → そのまま対応するTierで推論を実行する
          という「判定から実行まで一括でやる」メソッド。

        【async/await とは】
          「非同期処理」のための仕組み。
          AI推論は時間がかかる処理（数秒〜数十秒）。
          asyncを使うと「AIが考えている間に別のことができる」状態にできる。
          await を付けた処理は「終わるまで待つ」という意味。

        【引数】
          task: InferenceTask（依頼票）

        【戻り値】
          InferenceResult（Tier1/Tier2/Tier3のどれかの結果）
          戻り値の型が3種類あるのは、使ったTierによって結果クラスが変わるため。

        【エラー】
          ValueError: 想定外のTierが選ばれた場合（バグ検知用）
        ────────────────────────────────────────────────────
        """
        # まずどのTierを使うか判定する
        decision = self.route(task)

        # 判定結果に従って対応するTierのエンジンを呼び出す
        if decision.selected_tier == SelectedTier.TIER1:
            # Tier1（Phi-4:14B）で通常推論を実行
            return await self.tier1.infer(
                task.prompt,
                temperature=task.temperature,
                max_tokens=task.max_output_tokens,
                timeout_s=task.timeout_s,
            )

        elif decision.selected_tier == SelectedTier.TIER2:
            # Tier2（Gemini 2.5 Flash）で長文処理・要約を実行
            return await self.tier2.infer(
                task.prompt,
                max_tokens=task.max_output_tokens,
                temperature=task.temperature,
                timeout_s=task.timeout_s,
            )

        elif decision.selected_tier == SelectedTier.TIER3:
            # Tier3（Qwen2.5-Coder:7B）でコード生成・レビューを実行
            # ※Tier1/Tier2は infer() だが、Tier3は generate_code() を呼ぶ
            #   コード専用エンジンなのでメソッド名が異なる
            return await self.tier3.generate_code(
                task.prompt,
                temperature=task.temperature,
                max_tokens=task.max_output_tokens,
                timeout_s=task.timeout_s,
            )

        else:
            # ここには通常到達しない（すべてのSelectedTierを網羅しているため）
            # もし到達したらコードにバグがある
            raise ValueError(
                f"想定外のTierが選ばれました: {decision.selected_tier}\n"
                f"RoutingDecisionの値を確認してください。"
            )

    # ══════════════════════════════════════════════════════
    # 【公開メソッド③】get_tier_status - 全Tierの状態を取得
    # ══════════════════════════════════════════════════════
    def get_tier_status(self) -> dict:
        """
        ────────────────────────────────────────────────────
        【役割】
          Tier1・Tier2・Tier3それぞれの現在の状態（ステータス）を
          まとめて返す。

        【使用場面】
          「今どのTierがどのくらい混んでいるか」を確認するときや
          モニタリング・ログ記録のときに使う。

        【戻り値の構造】
          {
            "tier1": {
              "latency_ms":  ... ,  # 最近の処理時間（ミリ秒）
              "wait_time_s": ... ,  # 現在の待機時間（秒）
              "available":   True   # 常にTrue（Tier1は常時利用可）
            },
            "tier2": {
              "status":            ... ,  # OK / WARNING / THROTTLE / STOP
              "remaining_tokens":  ... ,  # クオータ残量
              "daily_used":        ...    # 今日の使用量
            },
            "tier3": {
              "latency_ms":  ... ,  # 最近の処理時間（ミリ秒）
              "wait_time_s": ... ,  # 現在の待機時間（秒）
              "available":   True   # 常にTrue（Tier3は常時利用可）
            }
          }
        ────────────────────────────────────────────────────
        """
        # Tier2のクオータ情報を取得（APIの残量確認）
        tier2_quota = self.tier2.get_remaining_quota()

        return {
            "tier1": {
                # latencyとは「処理にかかった時間」のこと
                # get_current_latency()は秒で返すので×1000してミリ秒に変換
                "latency_ms":  self.tier1.get_current_latency() * 1000,
                "wait_time_s": self.tier1.get_latest_wait_time(),
                "available":   True,  # Tier1はローカルなので常に利用可能
            },
            "tier2": {
                "status":           tier2_quota.get("status"),
                "remaining_tokens": tier2_quota.get("remaining_tokens"),
                "daily_used":       tier2_quota.get("daily_used"),
            },
            "tier3": {
                "latency_ms":  self.tier3.get_latest_latency() * 1000,
                "wait_time_s": self.tier3.get_latest_wait_time(),
                "available":   True,  # Tier3もローカルなので常に利用可能
            },
        }

    # ══════════════════════════════════════════════════════
    # 【内部メソッド①】_is_code_task - コード系タスクか判定
    # ══════════════════════════════════════════════════════
    def _is_code_task(self, task: InferenceTask) -> bool:
        """
        ────────────────────────────────────────────────────
        【役割】
          タスクが「コード系（CODE_GENERATION or CODE_REVIEW）」か
          True/False で返す。

        【なぜ別メソッドにするか】
          route() の中に条件を直接書くと長くなりすぎて読みにくい。
          メソッドに分けることで「コード系かどうかの判定ロジック」が
          1ヶ所にまとまり、将来変更するときも楽になる。
        ────────────────────────────────────────────────────
        """
        return task.type in [TaskType.CODE_GENERATION, TaskType.CODE_REVIEW]

    # ══════════════════════════════════════════════════════
    # 【内部メソッド②】_should_use_tier2 - Tier2を使うべきか判定
    # ══════════════════════════════════════════════════════
    def _should_use_tier2(self, task: InferenceTask) -> bool:
        """
        ────────────────────────────────────────────────────
        【役割】
          「Tier2を使うことを検討すべき状況か」を True/False で返す。
          ※Trueでもクオータ不足ならTier1になることに注意。
           あくまで「検討する入口」の判定。

        【Tier2を検討する3つの条件】

          条件1: プロンプトが8000トークンを超えている
            → Tier1（ローカル）では処理が重すぎる・遅すぎる長さ
            → 例: 長い論文の要約、大量コードのレビュー

          条件2: Tier1の待機時間が10秒を超えている
            → Tier1が混んでいて（他の処理が使用中で）すぐ使えない状態
            → Tier2（クラウド）の方が速く返ってくる場合がある

          条件3: タスクの種類が LONG_TEXT または SUMMARIZATION
            → 明示的に「長文処理・要約」と指定されたタスク
            → これらはTier2（Gemini）が得意な処理

        【戻り値】
          True  → Tier2を検討すべき状況
          False → Tier2を使う必要はない（Tier1で十分）
        ────────────────────────────────────────────────────
        """
        # 条件1: トークン数が閾値（8000）を超えている
        if task.estimated_tokens > self.TOKEN_THRESHOLD_TIER2:
            return True

        # 条件2: Tier1の待機時間が閾値（10秒）を超えている
        if self.tier1.get_latest_wait_time() > self.TIER1_WAIT_THRESHOLD_S:
            return True

        # 条件3: タスク種別が明示的に長文/要約系
        if task.type in [TaskType.LONG_TEXT, TaskType.SUMMARIZATION]:
            return True

        # どの条件にも当てはまらない → Tier2は不要
        return False