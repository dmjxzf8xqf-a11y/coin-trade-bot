# institutional_upgrade_runtime_patch_v2.py
# Institutional Upgrade V2 runtime patch
# 설치: main.py에서 Trader 인스턴스 생성 전에 import 1줄 추가
#   import institutional_upgrade_runtime_patch_v2  # noqa: F401
# 목적:
# - protection_guard / position_reconciler / execution_journal / health_monitor를 기존 trader.py에 안전하게 연결
# - 기존 파일을 크게 뜯어고치지 않고 신규 진입/상태/명령에만 패치

from __future__ import annotations

import os
import time
from typing import Any, Dict, Tuple

try:
    import trader as _t
except Exception as _e:  # pragma: no cover
    print(f"[INST-UPGRADE V2] trader import failed: {_e}", flush=True)
    _t = None  # type: ignore


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name, str(default)).lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _sym(s: Any) -> str:
    return str(s or "").strip().upper()


def _send(self: Any, msg: str) -> None:
    for name in ("notify", "tg_send", "_notify", "send_message"):
        fn = getattr(self, name, None)
        if callable(fn):
            try:
                fn(msg)
                return
            except Exception:
                pass
    print(msg, flush=True)


def _extract_enter_info(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    # Trader._enter(symbol, side, price, reason, sl, tp, strategy, score, atr) 계열 대응
    def at(idx: int, name: str, default: Any = None) -> Any:
        if name in kwargs:
            return kwargs.get(name)
        if len(args) > idx:
            return args[idx]
        return default
    return {
        "price": at(0, "price"),
        "reason": at(1, "reason", ""),
        "sl": at(2, "sl"),
        "tp": at(3, "tp"),
        "strategy": at(4, "strategy", "unknown"),
        "score": at(5, "score", 0.0),
        "atr": at(6, "atr", 0.0),
    }


def _install() -> None:
    if _t is None:
        return
    if not _bool("INSTITUTIONAL_V2_ON", True):
        print("[INST-UPGRADE V2] disabled by INSTITUTIONAL_V2_ON=false", flush=True)
        return
    Trader = getattr(_t, "Trader", None)
    if Trader is None:
        print("[INST-UPGRADE V2] trader.Trader not found", flush=True)
        return

    # imports are inside installer so bot still boots if individual modules fail
    from protection_guard import get_guard
    from position_reconciler import get_reconciler
    from execution_engine_v2 import get_journal
    from health_monitor import get_monitor
    import telegram_admin_v2_patch

    orig_init = getattr(Trader, "__init__", None)
    if callable(orig_init) and not getattr(Trader, "_inst_v2_init_patched", False):
        def init_wrapped(self: Any, *args: Any, **kwargs: Any) -> None:
            orig_init(self, *args, **kwargs)
            try:
                self._inst_v2_guard = get_guard()
                self._inst_v2_reconciler = get_reconciler()
                self._inst_v2_journal = get_journal()
                self._inst_v2_health = get_monitor()
                if isinstance(getattr(self, "state", None), dict):
                    self.state["institutional_v2"] = "loaded"
            except Exception as e:
                print(f"[INST-UPGRADE V2] init attach failed: {e}", flush=True)
        Trader.__init__ = init_wrapped
        Trader._inst_v2_init_patched = True

    orig_enter = getattr(Trader, "_enter", None)
    if callable(orig_enter) and not getattr(Trader, "_inst_v2_enter_patched", False):
        def enter_wrapped(self: Any, symbol: str, side: str, *args: Any, **kwargs: Any) -> Any:
            sym = _sym(symbol)
            info = _extract_enter_info(args, kwargs)
            strategy = str(info.get("strategy") or "unknown")
            score = _float(info.get("score"), 0.0)
            reason = str(info.get("reason") or "")

            guard = get_guard()
            try:
                guard.update_from_trader_state(self)
                dec = guard.can_enter(sym, strategy=strategy, score=score)
                if not dec.ok:
                    try:
                        if isinstance(getattr(self, "state", None), dict):
                            self.state["last_skip_reason"] = dec.reason
                            self.state["last_event"] = f"대기: {dec.reason}"
                    except Exception:
                        pass
                    _send(self, f"🛡️ 신규진입 차단\n{sym} {side}\n{dec.reason}")
                    return False
            except Exception as e:
                print(f"[INST-UPGRADE V2] guard failed: {e}", flush=True)

            journal = get_journal()
            key = ""
            try:
                key = journal.record_signal(sym, side, score=score, strategy=strategy, reason=reason)
                journal.record_order_attempt(key, sym, side, price=info.get("price"), score=score, strategy=strategy)
            except Exception:
                pass
            try:
                res = orig_enter(self, symbol, side, *args, **kwargs)
                try:
                    journal.record_order_result(key, ok=bool(res is not False), result=res, symbol=sym, side=side)
                except Exception:
                    pass
                return res
            except Exception as e:
                try:
                    journal.record_order_result(key, ok=False, error=e, symbol=sym, side=side)
                except Exception:
                    pass
                raise
        Trader._enter = enter_wrapped
        Trader._inst_v2_enter_patched = True

    orig_tick = getattr(Trader, "tick", None)
    if callable(orig_tick) and not getattr(Trader, "_inst_v2_tick_patched", False):
        def tick_wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                get_guard().update_from_trader_state(self)
            except Exception:
                pass
            try:
                get_reconciler().reconcile(self, force=False)
            except Exception:
                pass
            try:
                get_monitor().run(force=False)
            except Exception:
                pass
            return orig_tick(self, *args, **kwargs)
        Trader.tick = tick_wrapped
        Trader._inst_v2_tick_patched = True

    orig_status = getattr(Trader, "status_text", None)
    if callable(orig_status) and not getattr(Trader, "_inst_v2_status_patched", False):
        def status_wrapped(self: Any, *args: Any, **kwargs: Any) -> str:
            base = str(orig_status(self, *args, **kwargs)).rstrip()
            if not _bool("INSTITUTIONAL_V2_STATUS_LINES", True):
                return base
            lines = []
            try:
                lines.extend(get_guard().status_lines())
            except Exception:
                pass
            try:
                lines.extend(get_reconciler().status_lines())
            except Exception:
                pass
            try:
                lines.extend(get_journal().status_lines())
            except Exception:
                pass
            try:
                get_monitor().run(force=False)
                lines.extend(get_monitor().status_lines())
            except Exception:
                pass
            if lines:
                return base + "\n" + "\n".join(lines)
            return base
        Trader.status_text = status_wrapped
        Trader._inst_v2_status_patched = True

    def _patch_commands(method_name: str) -> None:
        orig = getattr(Trader, method_name, None)
        if not callable(orig):
            return
        flag = f"_inst_v2_{method_name}_patched"
        if getattr(Trader, flag, False):
            return
        def cmd_wrapped(self: Any, text: str, *args: Any, **kwargs: Any) -> Any:
            try:
                if telegram_admin_v2_patch.handle_v2_command(self, text):
                    return None
            except Exception as e:
                try:
                    _send(self, f"❌ inst v2 command error: {e}")
                    return None
                except Exception:
                    pass
            return orig(self, text, *args, **kwargs)
        setattr(Trader, method_name, cmd_wrapped)
        setattr(Trader, flag, True)

    _patch_commands("handle_command")
    _patch_commands("handle_telegram_command")

    # help text add-on
    orig_help = getattr(Trader, "help_text", None)
    if callable(orig_help) and not getattr(Trader, "_inst_v2_help_patched", False):
        def help_wrapped(self: Any, *args: Any, **kwargs: Any) -> str:
            base = str(orig_help(self, *args, **kwargs)).rstrip()
            return base + "\n\n🏛 INST V2\n/v2help | /guard | /health | /reconcile | /orders | /report | /candidate | /applycandidate"
        Trader.help_text = help_wrapped
        Trader._inst_v2_help_patched = True

    print("[INST-UPGRADE V2] loaded: guard + reconciler + execution journal + health + admin commands", flush=True)


try:
    _install()
except Exception as _e:
    print(f"[INST-UPGRADE V2] install failed: {_e}", flush=True)
