"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【ファイルの役割】
  VRAM排他制御ガード（VRAMの交通信号機）

【ひとことで言うと】
  RTX3060Ti の VRAM（グラフィックメモリ）は 8GB しかない。
  重いAIモデルを2つ同時に動かすとVRAMが溢れてクラッシュする。
  このファイルはそれを防ぐ「交通信号機」の役割を持つ。

【なぜVRAMが問題になるか】
  使用量の目安：
    Tier1（Phi-4:14B）     ：約8GB ← ほぼ全部使う
    Tier3（Qwen2.5-Coder） ：約5GB
    Whisper（音声認識）    ：約2GB
  → Tier1 + Tier3 を同時起動すると 8+5=13GB → 8GBを超えてクラッシュ
  → だから「1つずつしか動かさない」ルールをコードで強制する

【禁止されている組み合わせ】
  ❌ Tier1 + Tier3   （13GB → オーバー）
  ❌ Tier1 + Whisper  （10GB → オーバー）
  ✅ Tier2はAPIなのでVRAMを使わない → 何と同時でもOK
  ✅ Tier3 + Whisperは7GBなのでギリギリOK

【仕組み】
  「グローバルVRAMロック」という一本橋を作る。
  この橋は一度に1つしか通れない。
  Tier1が橋を渡っている間、Tier3は橋の前で待つ。
  Tier1が橋を渡り終えたら（ロック解放）、次がTier3が渡れる。

【呼び出し元】
  src/sba/inference/tier1.py    （Phi-4利用前後にロック取得・解放）
  src/sba/inference/tier3.py    （Qwen2.5-Coder利用前後に同様）
  src/sba/sources/whisper_transcriber.py （音声認識前後に同様）

【関連設計書】
  09.推論エンジン・VRAM運用設定書.md（§4-5）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ──────────────────────────────────────────
# Python 3.10以前でも「X | Y型」が使えるようにする記述
# （from __future__ import annotations は型ヒントを文字列として扱う）
# ──────────────────────────────────────────
from __future__ import annotations

# ──────────────────────────────────────────
# 標準ライブラリのインポート
# ──────────────────────────────────────────
import threading   # ロック機能（スレッドセーフな「一本橋」）を提供
import time        # 「ロックをどのくらいの時間保持しているか」を計測
from typing import Optional  # 「Noneかもしれない値」の型ヒントに使用
from enum import Enum        # モデルの種類を「定数」として定義するためのクラス

# ──────────────────────────────────────────
# 外部ライブラリのインポート
# ──────────────────────────────────────────
import ollama  # ローカルAIモデル（Phi-4, Qwen2.5-Coderなど）を動かすツール


# ══════════════════════════════════════════════════════
# 【定数定義①】ModelType - モデルの種類を表す列挙型
# ══════════════════════════════════════════════════════
class ModelType(Enum):
    """
    ────────────────────────────────────────────────────
    AIモデルの種類を表す「定数クラス」。

    【なぜEnumを使うか】
      "tier1"という文字列を直接使うと、タイポ（typo）したとき
      エラーに気づきにくい。
      Enum（列挙型）を使うと「ModelType.TIER1」という形になり、
      タイポがあればPythonがすぐにエラーを出してくれる。
      安全に定数を使うための仕組み。

    【各モデルの説明】
      TIER1   : Phi-4:14B（メイン推論担当・VRAM約8GB）
      TIER3   : Qwen2.5-Coder:7B（コード生成専用・VRAM約5GB）
      WHISPER : faster-whisper（音声→テキスト変換・VRAM約2GB）
      NONE    : 現在どのモデルも動いていない状態
    ────────────────────────────────────────────────────
    """
    TIER1   = "tier1"    # Phi-4:14B（一番重い・VRAM約8GB全部使う）
    TIER3   = "tier3"    # Qwen2.5-Coder:7B（コード専用・VRAM約5GB）
    WHISPER = "whisper"  # faster-whisper（音声認識・VRAM約2GB）
    NONE    = "none"     # 誰もVRAMを使っていない状態


# ══════════════════════════════════════════════════════
# 【定数定義②】Ollamaのモデル名マッピング
# ══════════════════════════════════════════════════════
# Ollamaに「このモデルをVRAMから消してください」と命令するときに使うモデル名。
# Ollamaのコマンドラインで「ollama list」を実行したときに出る名前と一致させる。
_OLLAMA_MODEL_NAMES = {
    ModelType.TIER1: "phi4",                 # Phi-4:14B のOllama上の名前
    ModelType.TIER3: "qwen2.5-coder:7b",    # Qwen2.5-Coder:7B のOllama上の名前
}
# ※WhisperはOllama経由ではないため、このマッピングには含まない


# ══════════════════════════════════════════════════════
# 【例外クラス】VRAMGuardError
# ══════════════════════════════════════════════════════
class VRAMGuardError(Exception):
    """
    VRAM制御に関するエラーが起きたときに使うオリジナルエラークラス。
    例：
      - ロック取得がタイムアウトした
      - 禁止されている組み合わせで起動しようとした
      - 自分が持っていないロックを解放しようとした
    """
    pass


# ══════════════════════════════════════════════════════
# 【メインクラス】VRAMGuard
# ══════════════════════════════════════════════════════
class VRAMGuard:
    """
    ────────────────────────────────────────────────────
    VRAMの排他制御（交通信号機）を担当するクラス。

    【基本的な使い方（コードの書き方）】

      # 方法1: acquire/release を手動で呼ぶ
      guard = get_global_vram_guard()
      guard.acquire_lock(ModelType.TIER1)   # 信号を「青」にしてもらう
      try:
          # Tier1を使った処理を書く
          result = tier1.generate("質問文")
      finally:
          guard.release_lock(ModelType.TIER1)  # 終わったら信号を「赤」に戻す

      # 方法2: with文を使う（こちらの方が安全・推奨）
      with guard:
          guard.acquire_lock(ModelType.TIER1)
          result = tier1.generate("質問文")
          # withブロックを出るとき自動でrelease_lockが呼ばれる

    【スレッドセーフとは】
      複数のスレッド（処理の流れ）が同時にVRAMを使おうとしても、
      threading.Lockが「一本橋」として機能し、
      必ず1つずつ順番に処理されることを保証する。

    【タイムアウト】
      デフォルト60秒待ってもロックが取れなければエラーを出す。
      無限に待ち続けてフリーズするのを防ぐ。
    ────────────────────────────────────────────────────
    """

    def __init__(self, timeout_s: float = 60.0) -> None:
        """
        ────────────────────────────────────────────────────
        【初期化処理】
        VRAMGuardクラスを使い始めるときに1回だけ実行される。

        【引数】
          timeout_s: ロック取得を何秒待つか（デフォルト60秒）
                     60秒以上待っても取れなければエラーを出す。

        【作られるもの】
          _lock         : threading.Lock（一本橋）
          _current_model: 今どのモデルがVRAMを使っているか（最初はNONE）
          _timeout_s    : タイムアウトの秒数
          _lock_time    : ロックを取得した時刻（経過時間計算用）
        ────────────────────────────────────────────────────
        """
        self._lock = threading.Lock()          # 一本橋（スレッドセーフなロック）
        self._current_model = ModelType.NONE   # 最初は誰も使っていない
        self._timeout_s = timeout_s            # タイムアウト秒数（デフォルト60秒）
        self._lock_time: Optional[float] = None  # ロック開始時刻（未取得時はNone）

    # ══════════════════════════════════════════════════════
    # 【公開メソッド①】acquire_lock - VRAMロックを取得する
    # ══════════════════════════════════════════════════════
    def acquire_lock(self, model_type: ModelType) -> bool:
        """
        ────────────────────────────────────────────────────
        【役割】
          指定したモデルがVRAMを使えるよう「信号を青にする」処理。

        【処理の流れ】
          1. 禁止組み合わせチェック（例: Tier1が動いてるのにTier3を起動しようとしていないか）
          2. threading.Lock.acquire() で「一本橋」に入る（他が使っていれば待つ）
          3. Whisperの場合は全Ollamaモデルをアンロード（VRAM確保）
          4. 現在使用中のモデルを記録して完了

        【引数】
          model_type: 使いたいモデルの種類（ModelType.TIER1 など）

        【戻り値】
          True（ロック取得成功）

        【エラー】
          VRAMGuardError: タイムアウトまたは禁止組み合わせ
        ────────────────────────────────────────────────────
        """
        # ── Step1: 禁止組み合わせをチェックする ──
        # 例: すでにTier1が動いていてTier3を起動しようとしている → エラーを出す
        self._check_compatibility(model_type)

        # ── Step2: ロックを取得する（一本橋に入る） ──
        # timeout= で指定した秒数以内にロックが取れなければ False が返る
        acquired = self._lock.acquire(timeout=self._timeout_s)
        if not acquired:
            raise VRAMGuardError(
                f"VRAMロックのタイムアウト（{self._timeout_s}秒）: "
                f"{model_type.value} のロック待ちで時間切れになりました。\n"
                f"現在使用中: {self._current_model.value}"
            )

        # ── Step3: 競合するモデルをVRAMから解放する ──
        # Whisper起動前はOllamaモデルを全部アンロードしてVRAMを空ける
        self._unload_conflicting_models(model_type)

        # ── Step4: 現在使用中モデルとロック開始時刻を記録する ──
        self._current_model = model_type
        self._lock_time = time.time()  # 現在の時刻（Unix秒）を記録

        return True

    # ══════════════════════════════════════════════════════
    # 【公開メソッド②】release_lock - VRAMロックを解放する
    # ══════════════════════════════════════════════════════
    def release_lock(self, model_type: ModelType) -> None:
        """
        ────────────────────────────────────────────────────
        【役割】
          AIモデルの使用が終わったら「信号を赤に戻す」処理。
          これを呼ばないと永遠に一本橋が占領されたままになる。

        【安全のポイント】
          「自分が取得したロックしか解放できない」チェックがある。
          例: Tier1がロックを持っているのに
              Tier3が勝手に解放しようとするとエラーになる。

        【引数】
          model_type: 解放するモデルの種類

        【エラー】
          VRAMGuardError: 自分が持っていないロックを解放しようとした場合
        ────────────────────────────────────────────────────
        """
        # 自分が持っていないロックを解放しようとしていないか確認
        if self._current_model != model_type:
            raise VRAMGuardError(
                f"{model_type.value} のロックを解放しようとしましたが、"
                f"現在のロック所有者は {self._current_model.value} です。"
            )

        # 状態をリセットしてからロックを解放する
        self._current_model = ModelType.NONE  # 「誰も使っていない」状態に戻す
        self._lock_time = None                # 計測時刻もリセット
        self._lock.release()                  # 一本橋を空ける（次の待機者が入れる）

    # ══════════════════════════════════════════════════════
    # 【内部メソッド①】_check_compatibility - 禁止組み合わせを確認
    # ══════════════════════════════════════════════════════
    def _check_compatibility(self, model_type: ModelType) -> None:
        """
        ────────────────────────────────────────────────────
        【役割】
          新しいモデルを起動しようとしたとき、
          現在使用中のモデルと「禁止された組み合わせ」でないか確認する。

        【なぜロック取得の前にチェックするか】
          ロックを取得してしまってからエラーを出すと、
          ロックが残ったままになってしまう。
          チェックはロック取得「前」に行うのが安全。

        【禁止ペアの一覧】
          Tier1 + Tier3   → 8GB + 5GB = 13GB → VRAMオーバー ❌
          Tier3 + Tier1   → 上記の逆順も禁止
          Tier1 + Whisper → 8GB + 2GB = 10GB → VRAMオーバー ❌
          Whisper + Tier1 → 上記の逆順も禁止

        【エラー】
          VRAMGuardError: 禁止された組み合わせ
        ────────────────────────────────────────────────────
        """
        # 現在誰もVRAMを使っていない → どんなモデルでも起動OK
        if self._current_model == ModelType.NONE:
            return

        # 禁止ペアの定義（双方向で定義している理由：どちらが先でも防ぎたいため）
        forbidden_pairs = [
            (ModelType.TIER1,   ModelType.TIER3),    # Tier1動作中にTier3起動禁止
            (ModelType.TIER3,   ModelType.TIER1),    # Tier3動作中にTier1起動禁止
            (ModelType.TIER1,   ModelType.WHISPER),  # Tier1動作中にWhisper起動禁止
            (ModelType.WHISPER, ModelType.TIER1),    # Whisper動作中にTier1起動禁止
        ]

        for running_model, new_model in forbidden_pairs:
            if self._current_model == running_model and model_type == new_model:
                raise VRAMGuardError(
                    f"禁止された組み合わせ: {running_model.value} が動作中に "
                    f"{new_model.value} を起動しようとしました。\n"
                    f"VRAMオーバーフローを防ぐため起動できません。"
                )

    # ══════════════════════════════════════════════════════
    # 【内部メソッド②】_unload_conflicting_models - 競合モデルを解放
    # ══════════════════════════════════════════════════════
    def _unload_conflicting_models(self, model_type: ModelType) -> None:
        """
        ────────────────────────────────────────────────────
        【役割】
          起動しようとするモデルのためにVRAMを空ける処理。

        【現在の動作】
          Whisperを起動するとき → 全OllamaモデルをVRAMから消す
          Tier1/Tier3を起動するとき → 何もしない（禁止チェック済みのため）

        【なぜWhisperだけ特別か】
          Tier1とTier3は「ロック」で排他制御されているため、
          片方が動いているとき片方は「待機中」でありVRAMを使っていない。
          Whisperはまれに「Ollamaが古いロードを保持していて消えていない」
          ケースがあるため、念のため全アンロードする。
        ────────────────────────────────────────────────────
        """
        if model_type == ModelType.WHISPER:
            # Whisper起動前は全OllamaモデルをVRAMから解放する
            self._unload_ollama_all()

        # Tier1・Tier3の場合は禁止チェック済みなので追加アンロードは不要

    # ══════════════════════════════════════════════════════
    # 【内部メソッド③】_unload_ollama_all - 全Ollamaモデルを解放
    # ══════════════════════════════════════════════════════
    def _unload_ollama_all(self) -> None:
        """
        ────────────────────────────────────────────────────
        【役割】
          Ollamaで動いている全モデルをVRAMから解放（アンロード）する。
          Whisperを起動する直前に呼ばれる。

        【keep_alive=0 とは】
          Ollamaは通常、モデルを一度ロードするとしばらくVRAMに残し続ける。
          （次の推論を速くするためのキャッシュ）
          「keep_alive=0」を指定すると「推論が終わったら即VRAMから消して」
          という命令になり、VRAMが解放される。

        【なぜ空文字列（""）で呼ぶか】
          アンロードの目的はVRAMを解放することだけ。
          実際には何も推論させたくないため、
          prompt=""（空のプロンプト）でnum_predict=1（1トークンだけ生成）
          という最小コストで呼び出している。

        【失敗しても無視する理由】
          そもそもモデルがロードされていない場合もある。
          失敗しても「すでに解放済み」なので問題ない。
        ────────────────────────────────────────────────────
        """
        for model_type, model_name in _OLLAMA_MODEL_NAMES.items():
            try:
                ollama.generate(
                    model=model_name,
                    prompt="",           # 空プロンプト（実際には何も推論させない）
                    keep_alive=0,        # 0秒後にVRAMから解放（即時解放）
                    options={"num_predict": 1},  # 最小限の処理で呼び出す
                )
            except Exception:
                # モデルがすでにアンロードされている・ロードされていないなど
                # あらゆる失敗は無視してよい（「消えている」のが目的なので）
                pass

    # ══════════════════════════════════════════════════════
    # 【公開メソッド③〜⑤】状態確認系メソッド
    # ══════════════════════════════════════════════════════

    def get_current_model(self) -> ModelType:
        """
        ────────────────────────────────────────────────────
        【役割】
          現在VRAMを使用中のモデルを返す。
          誰も使っていなければ ModelType.NONE が返る。

          デバッグや状態確認のときに使う。
        ────────────────────────────────────────────────────
        """
        return self._current_model

    def get_lock_duration(self) -> Optional[float]:
        """
        ────────────────────────────────────────────────────
        【役割】
          現在のロックを「どのくらいの時間保持しているか」を秒数で返す。
          ロックが取得されていない場合は None を返す。

        【使用場面】
          「Tier1が30分もVRAMを独占している」など、
          異常に長い使用時間を検知するためのモニタリングに使う。
        ────────────────────────────────────────────────────
        """
        if self._lock_time is None:
            return None  # ロックが取得されていない
        return time.time() - self._lock_time  # 現在時刻 - 開始時刻 = 経過秒数

    def is_locked(self) -> bool:
        """
        ────────────────────────────────────────────────────
        【役割】
          今VRAMが誰かに使われているかを True/False で返す。

          True  → 誰かが使用中（ModelType.TIER1, TIER3, WHISPERのどれか）
          False → 誰も使っていない（ModelType.NONE）
        ────────────────────────────────────────────────────
        """
        return self._current_model != ModelType.NONE

    # ══════════════════════════════════════════════════════
    # 【特殊メソッド】with文で使えるようにするための仕組み
    # ══════════════════════════════════════════════════════

    def __enter__(self) -> "VRAMGuard":
        """
        ────────────────────────────────────────────────────
        「with guard:」と書いたときに呼ばれるメソッド。
        自分自身（VRAMGuardオブジェクト）を返すだけ。

        【with文の使い方】
          with get_global_vram_guard() as guard:
              guard.acquire_lock(ModelType.TIER1)
              # ← このブロックの中でTier1を使う
          # ← ブロックを出ると自動でrelease_lockが呼ばれる
        ────────────────────────────────────────────────────
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        ────────────────────────────────────────────────────
        「with」ブロックを抜けるとき（正常終了・例外どちらでも）
        自動的に呼ばれるメソッド。

        【役割】
          もしロックが取得されたままなら、自動的に解放する。
          これにより「例外が発生してもロックが残り続ける」という
          事故を防ぐことができる。

        【引数の意味】
          exc_type: 例外の種類（正常終了の場合はNone）
          exc_val : 例外の内容（正常終了の場合はNone）
          exc_tb  : スタックトレース（正常終了の場合はNone）
        ────────────────────────────────────────────────────
        """
        if self.is_locked():
            try:
                self.release_lock(self._current_model)
            except VRAMGuardError:
                pass  # 解放に失敗してもwithブロックの終了は止めない


# ══════════════════════════════════════════════════════
# 【グローバルシングルトン】
# プロセス全体で「1つだけ」VRAMGuardを共有するための仕組み
# ══════════════════════════════════════════════════════

# プロセス全体で共有するVRAMGuardの実体（最初はNone）
_global_vram_guard: Optional[VRAMGuard] = None


def get_global_vram_guard() -> VRAMGuard:
    """
    ────────────────────────────────────────────────────
    【役割】
      プロセス全体で1つだけ存在するVRAMGuardを返す。
      （シングルトンパターン）

    【シングルトンとは】
      「プログラムが動いている間、このオブジェクトは1個だけ」という設計。
      VRAMGuardが複数存在すると「それぞれが別のロックを管理する」ことになり、
      排他制御の意味がなくなってしまう。
      全員が同じVRAMGuardを使うことで、本当の意味で「一本橋」になる。

    【使い方】
      # Tier1推論の前後にこうやって使う
      guard = get_global_vram_guard()
      guard.acquire_lock(ModelType.TIER1)
      try:
          result = call_phi4_model(prompt)
      finally:
          guard.release_lock(ModelType.TIER1)

    【初回呼び出し時】
      _global_vram_guard が None の場合（まだ作られていない場合）に
      VRAMGuard() を作成して _global_vram_guard に代入する。
      2回目以降は作成済みのものをそのまま返す。
    ────────────────────────────────────────────────────
    """
    global _global_vram_guard  # 関数の外にある変数を変更するためにglobal宣言

    if _global_vram_guard is None:
        # まだ作られていない → 初めてのアクセス時に作成する
        _global_vram_guard = VRAMGuard()

    return _global_vram_guard