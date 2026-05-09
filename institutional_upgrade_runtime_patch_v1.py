# institutional_upgrade_runtime_patch_v1.py
# 목적:
# - 기존 trader.py를 전면 교체하지 않고, GitHub 루트에 추가/import만 해서 운영 구조 업그레이드
# - CORE/WATCH/BLOCK 심볼 등급제
# - 코인별 enter_score / stop_atr / tp_r / max_lev / size_mult 강제 적용
# - 검증 안 된 심볼은 실거래 차단 또는 관찰만
# - /profile 명령으로 현재 적용 상태 확인/간단 변경
#
# 설치:
# main.py에서 trader import 이후, Trader 인스턴스 생성 이전에 아래 1줄 추가:
#   import institutional_upgrade_runtime_patch_v1  # noqa: F401
#
# 안전 원칙:
# - 현재 보유 포지션의 SL/TP는 건드리지 않음.
# - 신규 진입 직전 파라미터만 적용.
# - UNKNOWN 심볼은 기본 차단.

from __future__ import annotations

try:
    import json
    import os
    import time
    from pathlib import Path
    from typing import Any, Dict, List, Tuple

    import trader as _t
except Exception as _boot_e:  # pragma: no cover
    print(f"[INST-UPGRADE V1] boot failed: {_boot_e}", flush=True)
    _t = None  # type: ignore


DEFAULT_PROFILES: Dict[str, Any] = {
    "_meta": {
        "version": "institutional_upgrade_v1",
        "mode": "recommend_first_then_apply",
    },
    "ONDOUSDT": {
        "grade": "CORE",
        "enter_score": 70,
        "stop_atr": 2.0,
        "tp_r": 1.5,
        "max_lev": 8,
        "size_mult": 1.0,
        "short": True,
    },
    "ZECUSDT": {
        "grade": "CORE",
        "enter_score": 70,
        "stop_atr": 1.5,
        "tp_r": 2.0,
        "max_lev": 8,
        "size_mult": 1.0,
        "short": True,
    },
    "BTCUSDT": {
        "grade": "WATCH",
        "enter_score": 78,
        "stop_atr": 1.8,
        "tp_r": 1.5,
        "max_lev": 3,
        "size_mult": 0.25,
        "short": True,
    },
    "ETHUSDT": {
        "grade": "WATCH",
        "enter_score": 78,
        "stop_atr": 1.8,
        "tp_r": 1.5,
        "max_lev": 3,
        "size_mult": 0.25,
        "short": True,
    },
    "SOLUSDT": {
        "grade": "WATCH",
        "enter_score": 80,
        "stop_atr": 1.8,
        "tp_r": 1.5,
        "max_lev": 3,
        "size_mult": 0.20,
        "short": True,
    },
    "XRPUSDT": {
        "grade": "WATCH",
        "enter_score": 80,
        "stop_atr": 1.8,
        "tp_r": 1.5,
        "max_lev": 3,
        "size_mult": 0.20,
        "short": True,
    },
    "DOGEUSDT": {
        "grade": "WATCH",
        "enter_score": 80,
        "stop_atr": 1.8,
        "tp_r": 1.5,
        "max_lev": 3,
        "size_mult": 0.20,
        "short": True,
    },
}

_PROFILES_CACHE: Dict[str, Any] | None = None
_PROFILES_MTIME: float = 0.0
_LAST_NOTIFY: Dict[str, float] = {}


def _env(name: str, default: str = "") -> str:
    try:
        return str(os.getenv(name, default)).strip()
    except Exception:
        return default


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name, str(default)).lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except Exception:
        return float(default)


def _int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except Exception:
        return int(default)


def _clip(x: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


def _profile_path() -> Path:
    raw = _env("SYMBOL_PROFILE_FILE", "symbol_profiles.json") or "symbol_profiles.json"
    return Path(raw)


def _atomic_write(path: Path, obj: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_profiles(force: bool = False) -> Dict[str, Any]:
    global _PROFILES_CACHE, _PROFILES_MTIME
    path = _profile_path()
    try:
        if not path.exists():
            if _bool("INSTITUTIONAL_PROFILE_AUTOCREATE", True):
                _atomic_write(path, DEFAULT_PROFILES)
            _PROFILES_CACHE = dict(DEFAULT_PROFILES)
            _PROFILES_MTIME = 0.0
            return _PROFILES_CACHE

        mt = float(path.stat().st_mtime)
        if (not force) and _PROFILES_CACHE is not None and mt == _PROFILES_MTIME:
            return _PROFILES_CACHE

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("symbol profile root must be object")
        _PROFILES_CACHE = data
        _PROFILES_MTIME = mt
        return data
    except Exception as e:
        print(f"[INST-UPGRADE V1] profile load failed: {e}", flush=True)
        _PROFILES_CACHE = dict(DEFAULT_PROFILES)
        return _PROFILES_CACHE


def _save_profiles(data: Dict[str, Any]) -> bool:
    global _PROFILES_CACHE, _PROFILES_MTIME
    try:
        path = _profile_path()
        _atomic_write(path, data)
        _PROFILES_CACHE = data
        _PROFILES_MTIME = float(path.stat().st_mtime) if path.exists() else time.time()
        return True
    except Exception as e:
        print(f"[INST-UPGRADE V1] profile save failed: {e}", flush=True)
        return False


def _sym(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _profile(symbol: Any) -> Dict[str, Any]:
    sym = _sym(symbol)
    if not sym:
        return {}
    data = _load_profiles()
    raw = data.get(sym) or {}
    if not isinstance(raw, dict):
        raw = {}
    out = dict(raw)
    out["symbol"] = sym
    out["grade"] = str(out.get("grade") or "UNKNOWN").upper()

    # WATCH 기본 보정
    if out["grade"] == "WATCH":
        out.setdefault("enter_score", _int("WATCH_ENTER_SCORE", 78))
        out.setdefault("max_lev", _int("WATCH_MAX_LEV", 3))
        out.setdefault("size_mult", _float("WATCH_SIZE_MULT", 0.25))
    elif out["grade"] == "CORE":
        out.setdefault("size_mult", 1.0)
    elif out["grade"] in ("BLOCK", "UNKNOWN"):
        out.setdefault("size_mult", _float("UNKNOWN_SIZE_MULT", 0.0))
    return out


def _grade_lists() -> Tuple[List[str], List[str], List[str]]:
    data = _load_profiles()
    core, watch, block = [], [], []
    for k, v in data.items():
        if str(k).startswith("_") or not isinstance(v, dict):
            continue
        g = str(v.get("grade") or "UNKNOWN").upper()
        if g == "CORE":
            core.append(_sym(k))
        elif g == "WATCH":
            watch.append(_sym(k))
        elif g == "BLOCK":
            block.append(_sym(k))
    return sorted(set(core)), sorted(set(watch)), sorted(set(block))


def _allowed_symbols() -> List[str]:
    core, watch, _block = _grade_lists()
    return list(dict.fromkeys(core + watch))


def _send(self: Any, msg: str) -> None:
    for name in ("notify", "tg_send", "_notify"):
        fn = getattr(self, name, None)
        if callable(fn):
            try:
                fn(msg)
                return
            except Exception:
                pass
    try:
        print(msg, flush=True)
    except Exception:
        pass


def _send_throttled(self: Any, key: str, msg: str, sec: int = 180) -> None:
    now = time.time()
    last = float(_LAST_NOTIFY.get(key, 0.0) or 0.0)
    if now - last >= sec:
        _LAST_NOTIFY[key] = now
        _send(self, msg)


def _apply_symbol_universe(self: Any) -> None:
    if not _bool("INSTITUTIONAL_PATCH_ON", True):
        return
    if not _bool("SYMBOL_PROFILES_ON", True):
        return
    if _bool("INSTITUTIONAL_ALLOW_UNKNOWN_SCAN", False):
        return
    if not _bool("BLOCK_UNKNOWN_SYMBOLS", True):
        return

    allowed = _allowed_symbols()
    if not allowed:
        return
    try:
        cur = list(getattr(self, "symbols", []) or [])
        merged = []
        # allowed 순서 우선, 기존 순서 보조
        for s in allowed + cur:
            u = _sym(s)
            if u and u in allowed and u not in merged:
                merged.append(u)
        if merged:
            self.symbols = merged
        if isinstance(getattr(self, "state", None), dict):
            self.state["inst_allowed_symbols"] = merged
    except Exception:
        pass


def _apply_profile_to_mp(mp: Dict[str, Any], prof: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(mp, dict):
        mp = {}
    if not prof:
        return mp
    grade = str(prof.get("grade") or "UNKNOWN").upper()
    if grade in ("BLOCK", "UNKNOWN"):
        return mp

    out = dict(mp)
    # threshold / SL / TP
    if prof.get("enter_score") is not None:
        try:
            out["enter_score"] = max(float(out.get("enter_score", 0) or 0), float(prof.get("enter_score")))
        except Exception:
            pass
    if prof.get("stop_atr") is not None:
        try:
            out["stop_atr"] = float(prof.get("stop_atr"))
        except Exception:
            pass
    if prof.get("tp_r") is not None:
        try:
            out["tp_r"] = float(prof.get("tp_r"))
        except Exception:
            pass

    # lev cap
    if prof.get("max_lev") is not None:
        try:
            out["lev"] = int(min(int(float(out.get("lev", 1) or 1)), int(float(prof.get("max_lev")))))
        except Exception:
            pass

    # size multiplier
    if prof.get("size_mult") is not None:
        try:
            base = float(out.get("order_usdt", 0) or 0)
            mult = _clip(float(prof.get("size_mult")), 0.0, 2.0)
            out["order_usdt"] = round(base * mult, 4)
        except Exception:
            pass

    out["profile_symbol"] = str(prof.get("symbol") or "")
    out["profile_grade"] = grade
    return out


def _extract_enter_args(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    # Trader._enter(symbol, side, price, reason, sl, tp, strategy, score, atr)
    def at(idx: int, name: str, default: Any = None) -> Any:
        if name in kwargs:
            return kwargs.get(name)
        if len(args) > idx:
            return args[idx]
        return default

    return {
        "price": at(0, "price"),
        "reason": at(1, "reason"),
        "sl": at(2, "sl"),
        "tp": at(3, "tp"),
        "strategy": at(4, "strategy", ""),
        "score": at(5, "score", 0.0),
        "atr": at(6, "atr", 0.0),
    }


def _rebuild_enter_args(args: Tuple[Any, ...], kwargs: Dict[str, Any], sl: Any, tp: Any) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
    new_args = list(args)
    if len(new_args) > 2:
        new_args[2] = sl
    elif "sl" in kwargs:
        kwargs["sl"] = sl
    if len(new_args) > 3:
        new_args[3] = tp
    elif "tp" in kwargs:
        kwargs["tp"] = tp
    return tuple(new_args), kwargs


def _calc_profile_sl_tp(symbol: str, side: str, price: Any, atr: Any, prof: Dict[str, Any], old_sl: Any, old_tp: Any) -> Tuple[Any, Any]:
    try:
        p = float(price)
        a = float(atr)
        if p <= 0 or a <= 0:
            return old_sl, old_tp
        stop_atr = float(prof.get("stop_atr")) if prof.get("stop_atr") is not None else None
        tp_r = float(prof.get("tp_r")) if prof.get("tp_r") is not None else None
        if stop_atr is None or tp_r is None:
            return old_sl, old_tp
        s = str(side or "").upper()
        if s in ("LONG", "BUY"):
            return p - a * stop_atr, p + a * tp_r
        if s in ("SHORT", "SELL"):
            return p + a * stop_atr, p - a * tp_r
        return old_sl, old_tp
    except Exception:
        return old_sl, old_tp


def _profile_status_text(self: Any = None, verbose: bool = False) -> str:
    core, watch, block = _grade_lists()
    lines = []
    lines.append("🏛 Institutional Profile V1")
    lines.append(f"ON={_bool('INSTITUTIONAL_PATCH_ON', True)} profiles={_bool('SYMBOL_PROFILES_ON', True)} block_unknown={_bool('BLOCK_UNKNOWN_SYMBOLS', True)}")
    lines.append(f"CORE={','.join(core) if core else '-'}")
    lines.append(f"WATCH={','.join(watch) if watch else '-'}")
    if block or verbose:
        lines.append(f"BLOCK={','.join(block) if block else '-'}")
    data = _load_profiles()
    for sym in core + watch:
        p = data.get(sym, {}) if isinstance(data.get(sym), dict) else {}
        lines.append(
            f"- {sym} {str(p.get('grade','?')).upper()} "
            f"score>={p.get('enter_score','-')} sl={p.get('stop_atr','-')} tp={p.get('tp_r','-')} "
            f"lev<={p.get('max_lev','-')} size×{p.get('size_mult','-')}"
        )
    return "\n".join(lines)


def _set_grade(symbol: str, grade: str) -> bool:
    sym = _sym(symbol)
    grade = str(grade or "").upper()
    if not sym or grade not in ("CORE", "WATCH", "BLOCK"):
        return False
    data = _load_profiles(force=True)
    row = data.get(sym)
    if not isinstance(row, dict):
        row = {}
    row["grade"] = grade
    if grade == "CORE":
        row.setdefault("enter_score", 70)
        row.setdefault("stop_atr", 1.5)
        row.setdefault("tp_r", 2.0)
        row.setdefault("max_lev", 8)
        row.setdefault("size_mult", 1.0)
    elif grade == "WATCH":
        row.setdefault("enter_score", _int("WATCH_ENTER_SCORE", 78))
        row.setdefault("stop_atr", 1.8)
        row.setdefault("tp_r", 1.5)
        row.setdefault("max_lev", _int("WATCH_MAX_LEV", 3))
        row.setdefault("size_mult", _float("WATCH_SIZE_MULT", 0.25))
    elif grade == "BLOCK":
        row.setdefault("size_mult", 0.0)
    data[sym] = row
    return _save_profiles(data)


def _patch_commands(Trader: Any, method_name: str) -> None:
    orig = getattr(Trader, method_name, None)
    if not callable(orig):
        return

    def wrapped(self: Any, text: str, *args: Any, **kwargs: Any) -> Any:
        try:
            raw = str(text or "").strip()
            low = raw.lower()
            parts = raw.split()
            cmd = parts[0].lower() if parts else ""
            if cmd in ("/profile", "/profiles", "/grade", "/grades"):
                if len(parts) >= 2 and parts[1].lower() == "reload":
                    _load_profiles(force=True)
                    _apply_symbol_universe(self)
                    _send(self, "✅ profile reloaded\n" + _profile_status_text(self, verbose=True))
                    return
                if len(parts) >= 3 and parts[1].lower() in ("core", "watch", "block"):
                    ok = _set_grade(parts[2], parts[1].upper())
                    _load_profiles(force=True)
                    _apply_symbol_universe(self)
                    _send(self, ("✅" if ok else "❌") + f" {parts[2].upper()} -> {parts[1].upper()}\n" + _profile_status_text(self, verbose=True))
                    return
                if len(parts) >= 3 and parts[1].lower() == "show":
                    sym = _sym(parts[2])
                    _send(self, json.dumps(_profile(sym), ensure_ascii=False, indent=2))
                    return
                _send(self, _profile_status_text(self, verbose=True) + "\n\n사용법: /profile reload | /profile core ZECUSDT | /profile watch BTCUSDT | /profile block LABUSDT")
                return
        except Exception as e:
            try:
                _send(self, f"❌ profile command error: {e}")
                return
            except Exception:
                pass
        return orig(self, text, *args, **kwargs)

    setattr(Trader, method_name, wrapped)


def _install() -> None:
    if _t is None:
        return
    if not _bool("INSTITUTIONAL_PATCH_ON", True):
        print("[INST-UPGRADE V1] disabled by INSTITUTIONAL_PATCH_ON=false", flush=True)
        return

    Trader = getattr(_t, "Trader", None)
    if Trader is None:
        print("[INST-UPGRADE V1] trader.Trader not found", flush=True)
        return

    # Init wrapper: create profile file and restrict universe at startup.
    orig_init = getattr(Trader, "__init__", None)
    if callable(orig_init):
        def init_wrapped(self: Any, *args: Any, **kwargs: Any) -> None:
            orig_init(self, *args, **kwargs)
            try:
                _load_profiles(force=True)
                _apply_symbol_universe(self)
                if isinstance(getattr(self, "state", None), dict):
                    self.state["inst_profile_patch"] = "V1"
            except Exception:
                pass
        Trader.__init__ = init_wrapped

    # _mp wrapper: symbol-aware live params.
    orig_mp = getattr(Trader, "_mp", None)
    if callable(orig_mp):
        def mp_wrapped(self: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
            mp = orig_mp(self, *args, **kwargs) or {}
            try:
                sym = _sym(kwargs.get("symbol") or getattr(self, "_inst_current_symbol", "") or getattr(self, "_iup_current_symbol", ""))
                if sym and _bool("SYMBOL_PROFILES_ON", True):
                    prof = _profile(sym)
                    mp = _apply_profile_to_mp(mp, prof)
            except Exception:
                pass
            return mp
        Trader._mp = mp_wrapped

    # _enter wrapper: final grade gate + score gate + SL/TP override + size/lev context.
    orig_enter = getattr(Trader, "_enter", None)
    if callable(orig_enter):
        def enter_wrapped(self: Any, symbol: str, side: str, *args: Any, **kwargs: Any) -> Any:
            sym = _sym(symbol)
            prof = _profile(sym)
            grade = str(prof.get("grade") or "UNKNOWN").upper()
            info = _extract_enter_args(args, kwargs)
            score = 0.0
            try:
                score = float(info.get("score") or 0.0)
            except Exception:
                score = 0.0

            # Unknown/block gate.
            if _bool("SYMBOL_PROFILES_ON", True):
                if grade in ("BLOCK", "UNKNOWN") and _bool("BLOCK_UNKNOWN_SYMBOLS", True):
                    reason = f"SYMBOL_BLOCK:{sym}:{grade}"
                    try:
                        self.state["last_skip_reason"] = reason
                        self.state["last_event"] = f"대기: {reason}"
                    except Exception:
                        pass
                    _send_throttled(self, f"symblock:{sym}", f"🚫 {reason}\n/profile watch {sym} 로 WATCH 승격 가능", 300)
                    return False

                min_score = prof.get("enter_score")
                if min_score is not None:
                    try:
                        min_score_f = float(min_score)
                        if score and score < min_score_f:
                            reason = f"PROFILE_SCORE_BLOCK:{sym} {score:.1f}<{min_score_f:.1f} grade={grade}"
                            try:
                                self.state["last_skip_reason"] = reason
                                self.state["last_event"] = f"대기: {reason}"
                            except Exception:
                                pass
                            return False
                    except Exception:
                        pass

            # profile-aware mp context + SL/TP correction for new entry.
            old_cur = getattr(self, "_inst_current_symbol", None)
            setattr(self, "_inst_current_symbol", sym)
            try:
                new_sl, new_tp = _calc_profile_sl_tp(sym, side, info.get("price"), info.get("atr"), prof, info.get("sl"), info.get("tp"))
                new_args, new_kwargs = _rebuild_enter_args(args, kwargs, new_sl, new_tp)
                if isinstance(getattr(self, "state", None), dict):
                    self.state["inst_last_profile"] = {
                        "symbol": sym,
                        "grade": grade,
                        "enter_score": prof.get("enter_score"),
                        "stop_atr": prof.get("stop_atr"),
                        "tp_r": prof.get("tp_r"),
                        "max_lev": prof.get("max_lev"),
                        "size_mult": prof.get("size_mult"),
                        "score": score,
                    }
                return orig_enter(self, symbol, side, *new_args, **new_kwargs)
            finally:
                try:
                    if old_cur is None:
                        delattr(self, "_inst_current_symbol")
                    else:
                        setattr(self, "_inst_current_symbol", old_cur)
                except Exception:
                    pass
        Trader._enter = enter_wrapped

    # tick wrapper: keep allowed universe clean.
    orig_tick = getattr(Trader, "tick", None)
    if callable(orig_tick):
        def tick_wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                _apply_symbol_universe(self)
            except Exception:
                pass
            return orig_tick(self, *args, **kwargs)
        Trader.tick = tick_wrapped

    # status wrapper.
    orig_status = getattr(Trader, "status_text", None)
    if callable(orig_status):
        def status_wrapped(self: Any, *args: Any, **kwargs: Any) -> str:
            base = orig_status(self, *args, **kwargs)
            if not _bool("PROFILE_STATUS_LINES", True):
                return base
            try:
                last = {}
                try:
                    last = dict(getattr(self, "state", {}).get("inst_last_profile") or {})
                except Exception:
                    last = {}
                core, watch, block = _grade_lists()
                extra = [
                    f"🏛 INSTv1 profiles={'ON' if _bool('SYMBOL_PROFILES_ON', True) else 'OFF'} block_unknown={_bool('BLOCK_UNKNOWN_SYMBOLS', True)}",
                    f"CORE={','.join(core) if core else '-'} | WATCH={','.join(watch) if watch else '-'}",
                ]
                if last:
                    extra.append(
                        "🧬 last_profile="
                        f"{last.get('symbol','-')} {last.get('grade','-')} "
                        f"score>={last.get('enter_score','-')} sl={last.get('stop_atr','-')} "
                        f"tp={last.get('tp_r','-')} lev<={last.get('max_lev','-')} size×{last.get('size_mult','-')}"
                    )
                return str(base).rstrip() + "\n" + "\n".join(extra)
            except Exception:
                return base
        Trader.status_text = status_wrapped

    # help wrapper.
    orig_help = getattr(Trader, "help_text", None)
    if callable(orig_help):
        def help_wrapped(self: Any, *args: Any, **kwargs: Any) -> str:
            base = orig_help(self, *args, **kwargs)
            return str(base).rstrip() + "\n\n🏛 프로필/등급\n/profile | /profile reload | /profile core ZECUSDT | /profile watch BTCUSDT | /profile block LABUSDT"
        Trader.help_text = help_wrapped

    # command wrappers. Current repo variants use different method names.
    _patch_commands(Trader, "handle_command")
    _patch_commands(Trader, "handle_telegram_command")

    print("[INST-UPGRADE V1] loaded CORE/WATCH/BLOCK + symbol_profiles + /profile", flush=True)


try:
    _install()
except Exception as _e:
    try:
        print(f"[INST-UPGRADE V1] install failed: {_e}", flush=True)
    except Exception:
        pass
