"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【ファイルの役割】
  Brain Hot-Swap Manager（脳の入れ替え係）

【ひとことで言うと】
  「本棚（brain_bank）」と「作業机（[active]）」の間で
  Brainファイルを安全に運ぶ司令塔。

【このファイルがやること】
  1. save  : 作業机のBrainを本棚に保存する
  2. load  : 本棚のBrainを作業机に取り出す
  3. list  : 本棚にどんなBrainが入っているか一覧表示
  4. status: 今どのBrainが机の上にあるか確認

【最重要設計ポイント】
  「アトミック操作」を徹底している。
  アトミックとは「全部成功か、全部失敗か」どちらかしかない状態のこと。
  例：保存中にパソコンが落ちても「半分だけ保存された壊れたBrain」が
  残らないようにする仕組み。

【呼び出し元】
  src/sba/cli/brain_cmds.py（CLIコマンドから呼ばれる）

【呼び出し先】
  src/sba/brain/brain_package.py（BrainPackageクラス）

【関連設計書】
  03.Brain Hot-Swap設定書.md
  10.Brain Package・保存形式設定書.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ──────────────────────────────────────────
# 標準ライブラリのインポート
# ──────────────────────────────────────────
import shutil      # ファイル・フォルダをコピー/移動/削除するための道具
import json        # metadata.jsonなどのJSONファイルを読み書きするための道具
import uuid        # 世界に1つだけのIDを生成するための道具（Brain識別に使用）
import os          # ファイルのパーミッション（読み取り専用解除）に使用
from pathlib import Path          # ファイルパスをわかりやすく扱うためのクラス
from datetime import datetime     # 「いつ保存したか」の日時記録に使用
from typing import Optional, Dict, List, Any  # 型ヒント（変数の型を明示するための道具）
import hashlib     # （将来的な整合性チェック用。現時点では未使用）
import tempfile    # 一時的な作業用フォルダを作るための道具
import threading   # 同時アクセスを防ぐロック機能のための道具

# ──────────────────────────────────────────
# 同じプロジェクト内のファイルをインポート
# ──────────────────────────────────────────
from .brain_package import BrainPackage  # Brainフォルダの中身を扱うクラス


# ══════════════════════════════════════════════════════
# 【例外クラス】
# Brain管理に関するエラーが起きたときに使うオリジナルエラー
# 例: 「指定したBrainが見つからない」「保存に失敗した」など
# ══════════════════════════════════════════════════════
class BrainManagerError(Exception):
    """
    Brain管理操作専用のエラークラス。
    Pythonのデフォルトエラーではなく、このエラーを使うことで
    「SBAのBrain管理で何かがおかしい」と一目でわかるようにしている。
    """
    pass


# ══════════════════════════════════════════════════════
# 【メインクラス】BrainHotSwapManager
# ══════════════════════════════════════════════════════
class BrainHotSwapManager:
    """
    ────────────────────────────────────────────────────
    Brainの「入れ替え操作（Hot-Swap）」を管理するクラス。

    【本棚と作業机のたとえ】
      brain_bank/ フォルダ  = 本棚（保存済みBrainが並んでいる）
      [active]/   フォルダ  = 作業机（今使っているBrainが置いてある）

    【アトミック操作の保証】
      保存や読み込み中にエラーが発生した場合、
      完全に元の状態に戻す（ロールバック）機能を持つ。
      「途中まで保存された壊れたファイル」が残らない設計。

    【スレッドセーフ】
      threading.Lockを使っているので、
      仮に2つの処理が同時に保存しようとしても
      順番を守って安全に処理される。
    ────────────────────────────────────────────────────
    """

    def __init__(self, brain_bank_path: Path | str, active_path: Path | str):
        """
        ────────────────────────────────────────────────────
        【初期化処理】
        このクラスを使い始めるときに最初に1回だけ実行される。

        【引数】
          brain_bank_path : 本棚フォルダのパス（例: C:/SBA/brain_bank/）
          active_path     : 作業机フォルダのパス（例: C:/SBA/brain_bank/[active]/）

        【やること】
          1. パスをPathオブジェクトに変換して保存
          2. スレッドセーフ用のロックを作成
          3. 両方のフォルダが実際に存在するか確認
        ────────────────────────────────────────────────────
        """
        # パスをPathオブジェクトに統一する（文字列でもPathでも受け付けるため）
        self.brain_bank_path = Path(brain_bank_path)
        self.active_path = Path(active_path)

        # スレッドロック：同時に2つの操作が走らないようにする「一本橋」
        # このロックを取得した処理だけが先に進める
        self._lock = threading.Lock()

        # フォルダが存在するか確認（存在しなければエラーを出して止まる）
        self._validate_directories()

    def _validate_directories(self):
        """
        ────────────────────────────────────────────────────
        【フォルダ存在確認】
        brain_bankフォルダと[active]フォルダが
        正しく存在しているか確認するメソッド。

        【なぜ必要か】
          パスが間違っていたり、フォルダが消えていたりすると
          後の処理で意味不明なエラーが出る。
          最初に「フォルダが存在するか」を確認することで、
          問題の原因を早い段階で教えられる。
        ────────────────────────────────────────────────────
        """
        # brain_bankフォルダの確認
        if not self.brain_bank_path.exists():
            raise BrainManagerError(
                f"brain_bankフォルダが見つかりません: {self.brain_bank_path}"
            )
        if not self.brain_bank_path.is_dir():
            raise BrainManagerError(
                f"brain_bankパスがフォルダではありません: {self.brain_bank_path}"
            )

        # [active]フォルダの確認
        if not self.active_path.exists():
            raise BrainManagerError(
                f"[active]フォルダが見つかりません: {self.active_path}"
            )
        if not self.active_path.is_dir():
            raise BrainManagerError(
                f"[active]パスがフォルダではありません: {self.active_path}"
            )

    # ══════════════════════════════════════════════════════
    # 【公開メソッド①】save - Brainを本棚に保存する
    # ══════════════════════════════════════════════════════
    def save(
        self,
        brain_name: Optional[str] = None,
        description: str = ""
    ) -> Dict[str, Any]:
        """
        ────────────────────────────────────────────────────
        【役割】
          作業机（[active]）のBrainを本棚（brain_bank）に保存する。

        【処理の流れ】
          1. [active]フォルダのmetadata.jsonを読んでドメイン名とバージョンを取得
          2. 保存先フォルダ名を決める（例: Python開発_v1.1）
          3. まず「一時フォルダ」にコピーする（これが安全設計のポイント！）
          4. コピー成功後、一時フォルダを本棚の正しい場所に「移動」する
          5. 失敗した場合は一時フォルダを消すだけ（元データは無傷）

        【なぜ一時フォルダを使うか】
          直接コピーしていると、コピー中にパソコンが落ちたとき
          「半分だけ書かれた壊れたフォルダ」が残ってしまう。
          一時フォルダに全部コピーしてから「移動（リネーム）」すれば
          移動は一瞬で終わるので壊れたままになるリスクがほぼゼロになる。

        【引数】
          brain_name  : 保存するときの名前（省略すると自動命名）
          description : 保存時のメモ（任意）

        【戻り値】
          保存結果の情報が入ったDict（辞書）
          例: {'success': True, 'version': '1.1', 'saved_path': 'C:/SBA/...'}
        ────────────────────────────────────────────────────
        """
        # ロックを取得してから実際の処理を呼び出す
        # withブロックを抜けると自動的にロックが解放される
        with self._lock:
            return self._save_impl(brain_name, description)

    def _save_impl(self, brain_name: Optional[str], description: str) -> Dict[str, Any]:
        """
        ────────────────────────────────────────────────────
        【save()の実際の処理】
        ロックの中で呼ばれる内部メソッド。
        外部から直接呼んではいけない（_ で始まるのは「内部専用」の印）。
        ────────────────────────────────────────────────────
        """

        # ── Step1: [active]フォルダのBrainメタデータを読み込む ──
        # BrainPackageはフォルダ内のファイルをまとめて扱うクラス（brain_package.py参照）
        try:
            active_brain = BrainPackage.from_directory(self.active_path)
        except Exception as e:
            raise BrainManagerError(f"[active]フォルダの読み込みに失敗しました: {e}")

        # metadata.jsonから情報を取り出す
        metadata_dict = active_brain.get_metadata_dict()
        current_version = metadata_dict.get('version', '1.0')  # バージョン（なければ1.0）
        domain = metadata_dict.get('domain', 'Unknown')          # ドメイン名（なければUnknown）
        brain_id = metadata_dict.get('brain_id', str(uuid.uuid4()))  # Brain固有ID

        # ── Step2: 保存先フォルダ名を決める ──
        # 形式: "ドメイン名_vバージョン"（例: Python開発_v1.0）
        target_dirname = f"{domain}_v{current_version}"
        target_path = self.brain_bank_path / target_dirname

        # 同じバージョンがすでに存在する場合はバージョンを自動的に上げる
        # 例: Python開発_v1.0がすでにある → Python開発_v1.1として保存
        if target_path.exists():
            current_version = self._increment_version(current_version)
            target_dirname = f"{domain}_v{current_version}"
            target_path = self.brain_bank_path / target_dirname

        # ── Step3: 一時フォルダにコピーする（安全設計の核心部分） ──
        temp_dir = None
        try:
            # brain_bankフォルダの中に一時フォルダを作る
            # prefix='brain_save_'を付けることで「保存中の作業フォルダ」と識別できる
            temp_dir = tempfile.mkdtemp(prefix='brain_save_', dir=self.brain_bank_path)
            temp_path = Path(temp_dir)

            # [active]の全ファイルを一時フォルダにコピー
            self._copy_brain_files(self.active_path, temp_path)

            # コピーしたファイルのmetadata.jsonを更新（バージョン・日時を上書き）
            self._update_saved_metadata(
                temp_path,
                current_version,
                brain_name,
                description,
                brain_id
            )

            # ── Step4: 一時フォルダを正式な場所に「移動」する（アトミック操作） ──
            # 移動は「名前を変えるだけ」なので一瞬で終わる → 壊れる可能性がほぼゼロ
            if target_path.exists():
                shutil.rmtree(target_path)  # 古いものがあれば先に消す
            shutil.move(str(temp_path), str(target_path))

            # ── Step5: 保存結果の情報をまとめて返す ──
            registry_entry = {
                'brain_id': brain_id,
                'domain': domain,
                'version': current_version,
                'name': brain_name or f"{domain} v{current_version}",
                'description': description,
                'saved_at': datetime.utcnow().isoformat() + 'Z',
                'saved_path': str(target_path),
                'size_bytes': self._calculate_dir_size(target_path),
            }

            return {
                'success': True,
                'message': f"Brainを保存しました: {target_dirname}",
                'brain_id': brain_id,
                'domain': domain,
                'version': current_version,
                'saved_path': str(target_path),
                'registry': registry_entry,
            }

        except Exception as e:
            # ── 失敗した場合: 一時フォルダを削除する ──
            # 元の[active]フォルダは一切触っていないので無傷
            if temp_dir and Path(temp_dir).exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise BrainManagerError(f"保存中にエラーが発生しました: {e}")

    # ══════════════════════════════════════════════════════
    # 【公開メソッド②】load - 本棚からBrainを取り出す
    # ══════════════════════════════════════════════════════
    def load(
        self,
        brain_name: str,
        rollback_on_error: bool = True
    ) -> Dict[str, Any]:
        """
        ────────────────────────────────────────────────────
        【役割】
          本棚（brain_bank）の指定したBrainを
          作業机（[active]）に取り出してセットする。

        【処理の流れ】
          1. 指定したBrainが本棚に存在するか確認
          2. 現在の[active]を「バックアップ」として一時保存
          3. [active]を空にする
          4. 指定したBrainの全ファイルを[active]にコピー
          5. 成功 → バックアップを削除
          6. 失敗 → バックアップから元に戻す（ロールバック）

        【ロールバックとは】
          失敗したときに「なかったこと」にする処理。
          例: 読み込み中にエラーが出ても
          元々[active]にあったBrainが元通りに戻る。

        【引数】
          brain_name       : 取り出すBrainのフォルダ名（例: Python開発_v1.1）
          rollback_on_error: 失敗時に元に戻すか（デフォルトTrue）

        【戻り値】
          読み込み結果の情報が入ったDict（辞書）
        ────────────────────────────────────────────────────
        """
        with self._lock:
            return self._load_impl(brain_name, rollback_on_error)

    def _load_impl(
        self,
        brain_name: str,
        rollback_on_error: bool
    ) -> Dict[str, Any]:
        """
        ────────────────────────────────────────────────────
        【load()の実際の処理】
        ロックの中で呼ばれる内部メソッド。
        ────────────────────────────────────────────────────
        """

        # ── Step1: 指定されたBrainを本棚の中から探す ──
        target_brain_path = self.brain_bank_path / brain_name
        if not target_brain_path.exists():
            # 完全一致で見つからない場合は「あいまい検索」を試みる
            # 例: "Python"と入力 → "Python開発_v1.1"にマッチ
            matches = self._find_brain_fuzzy(brain_name)
            if not matches:
                raise BrainManagerError(
                    f"Brainが見つかりません: {brain_name}\n"
                    f"利用可能なBrain: {self.list_brains_names()}"
                )
            if len(matches) > 1:
                # 複数マッチした場合は「どれですか？」とエラーで教える
                raise BrainManagerError(
                    f"'{brain_name}'に複数のBrainがマッチしました: {matches}\n"
                    f"フォルダ名を完全に指定してください。"
                )
            target_brain_path = self.brain_bank_path / matches[0]

        # ── Step2: 対象Brainが正常なファイル構成か確認する ──
        try:
            target_brain = BrainPackage.from_directory(target_brain_path)
            target_metadata = target_brain.get_metadata_dict()
        except Exception as e:
            raise BrainManagerError(f"指定されたBrainのファイルが壊れています: {e}")

        # ── Step3: 現在の[active]をバックアップする（ロールバック準備） ──
        backup_dir = None
        try:
            # システムの一時フォルダにバックアップを作成
            # brain_bank外に作ることでlist_brains()などに影響しない
            backup_dir = tempfile.mkdtemp(prefix='brain_load_backup_')
            backup_path = Path(backup_dir)

            # [active]の全ファイルをバックアップにコピー
            self._copy_brain_files(self.active_path, backup_path)

            # ── Step4: [active]を空にして新しいBrainをコピー ──
            self._clear_active_directory()  # まず机の上を全部片付ける
            self._copy_brain_files(target_brain_path, self.active_path)  # 新しいBrainを机に置く

            # [active]のmetadata.jsonに「いつ読み込んだか」を記録
            self._update_loaded_metadata(self.active_path)

            # ── Step5: 読み込みが成功したか最終確認 ──
            # BrainPackageで読み込めれば「正常なBrainが[active]にある」と判断
            active_brain = BrainPackage.from_directory(self.active_path)

            # 成功：バックアップは不要なので削除
            shutil.rmtree(backup_path, ignore_errors=True)

            return {
                'success': True,
                'message': f"Brainを読み込みました: {brain_name}",
                'brain_id': target_metadata.get('brain_id'),
                'domain': target_metadata.get('domain'),
                'version': target_metadata.get('version'),
                'loaded_at': datetime.utcnow().isoformat() + 'Z',
                'active_path': str(self.active_path),
            }

        except Exception as e:
            # ── 失敗した場合: バックアップから元に戻す（ロールバック） ──
            if rollback_on_error and backup_dir and Path(backup_dir).exists():
                try:
                    # [active]を再び空にしてバックアップから復元
                    self._clear_active_directory()
                    self._copy_brain_files(Path(backup_dir), self.active_path)
                    shutil.rmtree(Path(backup_dir), ignore_errors=True)
                    raise BrainManagerError(
                        f"読み込みに失敗しました（[active]は元に戻しました）: {e}"
                    )
                except BrainManagerError:
                    raise  # BrainManagerErrorはそのまま上に投げる
                except Exception as rollback_error:
                    raise BrainManagerError(
                        f"読み込みに失敗し、さらにロールバックも失敗しました。\n"
                        f"読み込みエラー: {e}\n"
                        f"ロールバックエラー: {rollback_error}\n"
                        f"[active]フォルダが壊れている可能性があります。"
                    )
            else:
                # rollback_on_error=Falseの場合はそのまま失敗を返す
                if backup_dir:
                    shutil.rmtree(Path(backup_dir), ignore_errors=True)
                raise BrainManagerError(f"読み込みに失敗しました: {e}")

    # ══════════════════════════════════════════════════════
    # 【公開メソッド③】list_brains - 本棚の一覧を返す
    # ══════════════════════════════════════════════════════
    def list_brains(self) -> List[Dict[str, Any]]:
        """
        ────────────────────────────────────────────────────
        【役割】
          brain_bankフォルダの中にある全Brainの情報を
          リストとして返す。

        【スキップするフォルダ】
          _ で始まるフォルダ    → テンプレートなど（例: _blank_template）
          [ で始まるフォルダ    → [active]など
          brain_save_* フォルダ → 保存途中の一時フォルダ
          brain_load_backup_*  → 読み込み途中の一時フォルダ

        【戻り値】
          各BrainのDict（辞書）のリスト。
          各Dictには名前・ドメイン・バージョン・レベル・サイズなどが入る。
        ────────────────────────────────────────────────────
        """
        brains = []

        try:
            # brain_bankフォルダの中身をフォルダ名でソートしながらループ
            for brain_dir in sorted(self.brain_bank_path.iterdir()):

                # 対象外フォルダをスキップする条件
                if (
                    brain_dir.name.startswith('_')             # _blank_templateなど
                    or brain_dir.name.startswith('[')          # [active]など
                    or brain_dir.name == "blank_template"      # 旧名テンプレート
                    or brain_dir.name.startswith("brain_save_")  # 保存中一時フォルダ
                    or brain_dir.name.startswith("brain_load_backup_")  # バックアップ一時フォルダ
                ):
                    continue

                # フォルダ以外（ファイル等）もスキップ
                if not brain_dir.is_dir():
                    continue

                try:
                    # BrainPackageでフォルダを読み込んでメタデータを取得
                    brain = BrainPackage.from_directory(brain_dir)
                    metadata = brain.get_metadata_dict()

                    brains.append({
                        'name': brain_dir.name,                      # フォルダ名
                        'domain': metadata.get('domain'),            # ドメイン名
                        'version': metadata.get('version'),          # バージョン
                        'brain_id': metadata.get('brain_id'),        # 固有ID
                        'saved_at': metadata.get('last_saved_at'),   # 最終保存日時
                        'created_at': metadata.get('created_at'),    # 作成日時
                        'level': metadata.get('level', 1),           # 育成レベル（デフォルト1）
                        'size_bytes': self._calculate_dir_size(brain_dir),  # フォルダサイズ（バイト）
                    })
                except Exception:
                    # 個別のBrainが壊れていてもリスト全体は止めない
                    # （1つ問題があっても他のBrainは表示する）
                    pass

        except Exception as e:
            raise BrainManagerError(f"Brain一覧の取得に失敗しました: {e}")

        return brains

    def list_brains_names(self) -> List[str]:
        """
        ────────────────────────────────────────────────────
        【役割】
          保存済みBrainのフォルダ名だけをリストで返す。
          エラーメッセージで「どんなBrainがあるか」を
          ユーザーに教えるために使う。
        ────────────────────────────────────────────────────
        """
        return [b['name'] for b in self.list_brains()]

    # ══════════════════════════════════════════════════════
    # 【公開メソッド④】get_active_brain - 現在のBrain情報を返す
    # ══════════════════════════════════════════════════════
    def get_active_brain(self) -> Dict[str, Any]:
        """
        ────────────────────────────────────────────────────
        【役割】
          現在作業机（[active]）に乗っているBrainの
          メタデータ情報を返す。

          「今どのBrainが動いているか」を確認するために使う。
        ────────────────────────────────────────────────────
        """
        try:
            active_brain = BrainPackage.from_directory(self.active_path)
            metadata = active_brain.get_metadata_dict()
            return {
                'name': "[active]",
                'domain': metadata.get('domain'),
                'version': metadata.get('version'),
                'brain_id': metadata.get('brain_id'),
                'level': metadata.get('level', 1),
                'size_bytes': self._calculate_dir_size(self.active_path),
            }
        except Exception as e:
            raise BrainManagerError(f"現在のBrain情報の取得に失敗しました: {e}")

    # ══════════════════════════════════════════════════════
    # 【内部ヘルパーメソッド群】
    # （_ で始まる = 外から呼んではいけない内部専用メソッド）
    # ══════════════════════════════════════════════════════

    def _increment_version(self, version_str: str) -> str:
        """
        ────────────────────────────────────────────────────
        【役割】
          バージョン番号を1つ増やす。
          例: "1.0" → "1.1"、"2.3" → "2.4"

        【なぜ必要か】
          同じバージョンのBrainが本棚にすでにあるとき、
          上書きせずに新しいバージョンとして保存するため。
        ────────────────────────────────────────────────────
        """
        try:
            parts = version_str.split('.')  # "1.0" → ["1", "0"]
            if len(parts) >= 2:
                parts[-1] = str(int(parts[-1]) + 1)  # 末尾の数字を1増やす
                return '.'.join(parts)  # ["1", "1"] → "1.1"
            return f"{version_str}.1"   # "1" → "1.1"（ドットがない場合）
        except Exception:
            return "1.0"  # 変換に失敗した場合はデフォルト値に戻す

    def _copy_brain_files(self, src: Path, dst: Path):
        """
        ────────────────────────────────────────────────────
        【役割】
          Brainフォルダの中身（ファイルとサブフォルダ）を
          コピー先にコピーする。

        【注意】
          フォルダ自体ではなく「中身」をコピーする。
          例: src/metadata.json → dst/metadata.json

        【スキップするもの】
          . で始まるファイル（.gitignoreなどの隠しファイル）

        【copy2とは】
          ファイルの「更新日時」なども一緒にコピーする
          shutil.copyの強化版。
        ────────────────────────────────────────────────────
        """
        dst.mkdir(parents=True, exist_ok=True)  # コピー先フォルダがなければ作る

        for item in src.iterdir():
            # 隠しファイル（.gitignoreなど）はスキップ
            if item.name.startswith('.'):
                continue

            if item.is_dir():
                # サブフォルダは再帰的にコピー（dirs_exist_ok=Trueで上書きも可）
                shutil.copytree(
                    item,
                    dst / item.name,
                    dirs_exist_ok=True,
                    copy_function=shutil.copy2
                )
            else:
                # 通常ファイルはcopy2でコピー
                shutil.copy2(item, dst / item.name)

            # コピー後に読み取り専用を解除（Windowsで必要になる場合がある）
            self._make_writable(dst / item.name)

    def _clear_active_directory(self):
        """
        ────────────────────────────────────────────────────
        【役割】
          [active]フォルダの中身を全部削除する（空にする）。
          ただし .で始まる隠しファイルは残す。

        【なぜ隠しファイルを残すか】
          .gitなどのバージョン管理ファイルは
          Brainの中身ではないため削除しない。
        ────────────────────────────────────────────────────
        """
        for item in self.active_path.iterdir():
            # 隠しファイルはスキップ（残す）
            if item.name.startswith('.'):
                continue

            # 削除前に読み取り専用を解除（Windowsで権限エラーになるのを防ぐ）
            self._make_writable(item)

            if item.is_dir():
                shutil.rmtree(item)  # フォルダごと削除
            else:
                item.unlink()        # ファイルを削除

    def _make_writable(self, path: Path):
        """
        ────────────────────────────────────────────────────
        【役割】
          ファイルやフォルダの「読み取り専用」属性を解除する。

        【なぜ必要か】
          Windowsでは_blank_templateなど一部のファイルが
          読み取り専用になっている場合がある。
          読み取り専用のまま削除・コピーしようとするとエラーになるため、
          事前にパーミッション（権限）を変更する。

        【0o777とは】
          Unix系の「全員が読み書き実行できる」権限を表す8進数。
          Windowsでは実質的に読み取り専用フラグを解除する効果がある。
        ────────────────────────────────────────────────────
        """
        if not path.exists():
            return  # 存在しなければ何もしない

        try:
            os.chmod(path, 0o777)  # このファイル/フォルダ自身の権限を変更
        except OSError:
            pass  # 失敗しても処理を止めない

        # フォルダの場合は中のファイル全部に権限変更を再帰的に適用
        if path.is_dir():
            for child in path.rglob('*'):  # rglob('*')で全サブファイルを取得
                try:
                    os.chmod(child, 0o777)
                except OSError:
                    pass

    def _update_saved_metadata(
        self,
        brain_path: Path,
        version: str,
        brain_name: Optional[str],
        description: str,
        brain_id: str
    ):
        """
        ────────────────────────────────────────────────────
        【役割】
          保存先フォルダのmetadata.jsonを更新する。

        【更新する内容】
          - version        : 新しいバージョン番号
          - brain_id       : Brain固有ID（変わらない場合もある）
          - last_saved_at  : 今の日時（UTC）
          - name           : 保存時の名前（指定があれば）
          - save_description: 保存時のメモ（指定があれば）

        【utf-8-sigとは】
          Windows環境でBOMありUTF-8で書かれたJSONを読む際に使うエンコーディング。
          BOM（バイトオーダーマーク）がついていても正しく読める。
          書き込みは通常のutf-8で行う。
        ────────────────────────────────────────────────────
        """
        metadata_path = brain_path / 'metadata.json'
        self._make_writable(metadata_path)  # 読み取り専用の場合に備えて権限を解除

        # 既存のmetadata.jsonを読み込む
        with open(metadata_path, 'r', encoding='utf-8-sig') as f:
            metadata = json.load(f)

        # 更新する項目を上書き
        metadata['version'] = version
        metadata['brain_id'] = brain_id
        metadata['last_saved_at'] = datetime.utcnow().isoformat() + 'Z'
        if brain_name:
            metadata['name'] = brain_name
        if description:
            metadata['save_description'] = description

        # 更新したmetadata.jsonを書き込む（ensure_ascii=Falseで日本語をそのまま保存）
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    def _update_loaded_metadata(self, brain_path: Path):
        """
        ────────────────────────────────────────────────────
        【役割】
          [active]にコピーしたBrainのmetadata.jsonに
          「いつ読み込んだか（last_loaded_at）」を記録する。

        【なぜ必要か】
          「このBrainを最後に読み込んだのはいつか」を
          追跡できるようにするための記録。
        ────────────────────────────────────────────────────
        """
        metadata_path = brain_path / 'metadata.json'
        self._make_writable(metadata_path)

        with open(metadata_path, 'r', encoding='utf-8-sig') as f:
            metadata = json.load(f)

        # 読み込み日時を現在時刻（UTC）で記録
        metadata['last_loaded_at'] = datetime.utcnow().isoformat() + 'Z'

        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    def _find_brain_fuzzy(self, brain_name: str) -> List[str]:
        """
        ────────────────────────────────────────────────────
        【役割】
          入力された名前で始まるBrainを大文字小文字を無視して探す。

        【なぜ必要か】
          ユーザーが "python" と入力しても
          "Python開発_v1.1" にマッチさせたい場合のため。

        【戻り値】
          マッチしたフォルダ名のリスト（0件・1件・複数件がある）
        ────────────────────────────────────────────────────
        """
        matches = []
        search_name = brain_name.lower()  # 小文字に変換して比較

        for brain_dir in self.brain_bank_path.iterdir():
            if brain_dir.is_dir() and brain_dir.name.lower().startswith(search_name):
                matches.append(brain_dir.name)

        return sorted(matches)  # アルファベット順で返す

    def _calculate_dir_size(self, path: Path) -> int:
        """
        ────────────────────────────────────────────────────
        【役割】
          指定フォルダの中にある全ファイルの合計サイズ（バイト）を計算する。
          一覧表示で「このBrainは何MBか」を表示するために使う。
        ────────────────────────────────────────────────────
        """
        total = 0
        try:
            for item in path.rglob('*'):  # フォルダ内の全ファイルを再帰的に取得
                if item.is_file():
                    total += item.stat().st_size  # stat().st_sizeでファイルサイズを取得
        except Exception:
            pass  # 計算失敗しても0を返す（表示が崩れないように）
        return total

    # ══════════════════════════════════════════════════════
    # 【CLI表示用メソッド群】
    # ターミナルに見やすく情報を表示するためのメソッド
    # ══════════════════════════════════════════════════════

    def format_brain_list_table(self) -> str:
        """
        ────────────────────────────────────────────────────
        【役割】
          保存済みBrainの一覧を「表形式のテキスト」にして返す。
          「sba brain list」コマンドの出力に使われる。

        【出力例】
          Name           | Domain  | Version | Level | Saved At   | Size
          -------------------------------------------------------
          Python開発_v1.1 | Python開発 | 1.1     | 1     | 2026-04-01 | 12.3MB
        ────────────────────────────────────────────────────
        """
        brains = self.list_brains()

        if not brains:
            return "brain_bankにBrainが保存されていません。"

        # 表のヘッダー（列名）
        headers = ['Name', 'Domain', 'Version', 'Level', 'Saved At', 'Size']
        rows = []

        for brain in brains:
            # 日時は「YYYY-MM-DD」の日付部分だけ表示（長いので短縮）
            saved_at = brain.get('saved_at', brain.get('created_at', 'N/A'))
            if saved_at and isinstance(saved_at, str):
                saved_at = saved_at[:10]  # "2026-04-01T12:34:56Z" → "2026-04-01"
            else:
                saved_at = 'N/A'

            # バイトをMBに変換して表示
            size_mb = brain['size_bytes'] / (1024 * 1024)
            size_str = f"{size_mb:.1f}MB"

            rows.append([
                brain['name'],
                brain['domain'] or '(blank)',  # ドメインなしは(blank)と表示
                brain['version'] or 'N/A',
                str(brain['level']),
                saved_at,
                size_str,
            ])

        # 各列の最大文字数を計算（列幅を合わせるため）
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, col in enumerate(row):
                col_widths[i] = max(col_widths[i], len(col))

        # ヘッダー行を生成
        lines = []
        header_row = ' | '.join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
        lines.append(header_row)
        lines.append('-' * len(header_row))  # 区切り線

        # データ行を生成
        for row in rows:
            data_row = ' | '.join(col.ljust(col_widths[i]) for i, col in enumerate(row))
            lines.append(data_row)

        return '\n'.join(lines)

    def get_brain_stats(self) -> Dict[str, Any]:
        """
        ────────────────────────────────────────────────────
        【役割】
          brain_bank全体の統計情報を返す。
          - 保存されているBrainの総数
          - 全体の合計サイズ
          - ドメインの種類数
          - ドメインごとのBrain数

        【使用場面】
          「sba brain status」コマンドや管理レポートで使用。
        ────────────────────────────────────────────────────
        """
        brains = self.list_brains()

        total_size = sum(b['size_bytes'] for b in brains)
        domains = set(b['domain'] for b in brains if b['domain'])

        # ドメインごとに何個のBrainがあるかカウント
        versions: Dict[str, int] = {}
        for brain in brains:
            domain = brain['domain'] or '(blank)'
            versions[domain] = versions.get(domain, 0) + 1

        return {
            'total_brains': len(brains),
            'total_size_bytes': total_size,
            'total_size_mb': total_size / (1024 * 1024),
            'unique_domains': len(domains),
            'domains': sorted(list(domains)),
            'brains_per_domain': versions,
        }

    def format_brain_stats(self) -> str:
        """
        ────────────────────────────────────────────────────
        【役割】
          get_brain_stats()の結果を見やすいテキスト形式にして返す。

        【出力例】
          ==================================================
          Brain Bank Statistics
          ==================================================
          Total Brains: 3
          Total Size: 45.2 MB
          Unique Domains: 2

          Brains per Domain:
            Python開発: 2
            青色申告: 1
        ────────────────────────────────────────────────────
        """
        stats = self.get_brain_stats()

        lines = [
            "=" * 50,
            "Brain Bank Statistics",
            "=" * 50,
            f"Total Brains: {stats['total_brains']}",
            f"Total Size: {stats['total_size_mb']:.1f} MB",
            f"Unique Domains: {stats['unique_domains']}",
            "",
            "Brains per Domain:",
        ]

        for domain, count in sorted(stats['brains_per_domain'].items()):
            lines.append(f"  {domain}: {count}")

        return '\n'.join(lines)