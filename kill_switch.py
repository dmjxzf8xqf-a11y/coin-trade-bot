# kill_switch.py
import os
import time

class KillSwitch:
    """
    “더 이상 업그레이드할 게 없게” 만드는 핵심 안전장치:
    - 오류 연속, 손실 연속, 일일 손실 한도, 슬리피지 한도(옵션) 등으로 자동 중단/쿨다운
    """
    def __init__(self):
        self.cooldown_until = 0
        self.consec_errors = 0

        # env로 조절 가능
        self.max_consec_errors = int(os.getenv("KS_MAX_CONSEC_ERRORS", "5"))
        self.max_consec_losses = int(os.getenv("KS_MAX_CONSEC_LOSSES", "4"))
        self.cooldown_seconds = int(os.getenv("KS_COOLDOWN_SECONDS", "900"))  # 15m

        # 일일 손실 한도(USDT). 0이면 비활성
        self.daily_loss_limit = float(os.getenv("DAILY_LOSS_LIMIT", "0") or 0)

    def in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until

    def trip(self, reason: str) -> str:
        self.cooldown_until = time.time() + self.cooldown_seconds
        return f"🛑 KILL SWITCH: {reason} (cooldown {self.cooldown_seconds}s)"

    def on_loop_error(self):
        self.consec_errors += 1
        if self.consec_errors >= self.max_consec_errors:
            return self.trip(f"too many errors ({self.consec_errors})")
        return None

    def reset_errors(self):
        self.consec_errors = 0

    def check_losses(self, consec_losses: int):
        if consec_losses >= self.max_consec_losses:
            return self.trip(f"too many consecutive losses ({consec_losses})")
        return None

    def check_daily_pnl(self, daily_pnl: float):
        if self.daily_loss_limit > 0 and daily_pnl <= -abs(self.daily_loss_limit):
            return self.trip(f"daily loss limit hit (PnL={daily_pnl:.4f} <= -{abs(self.daily_loss_limit):.4f})")
        return None
