import time
import json
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from config import *

# ✅ CloudFront/WAF 회피용 기본 헤더
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ✅ Bybit 차단 회피: config.py에 뭐가 있든 여기서 강제 치환
try:
    BYBIT_BASE_URL = (BYBIT_BASE_URL or "").strip()
except:
    BYBIT_BASE_URL = "https://api.bybit.com"

if "api.bybit.com" in BYBIT_BASE_URL:
    BYBIT_BASE_URL = BYBIT_BASE_URL.replace("https://api.bybit.com", "https://api.bytick.com").replace(
        "http://api.bybit.com", "https://api.bytick.com"
    )

# 가격 소스(다중)
BINANCE = "https://api.binance.com/api/v3/ticker/price"
COINGECKO = "https://api.coingecko.com/api/v3/simple/price"
COINBASE = "https://api.coinbase.com/v2/prices/BTC-USD/spot"


class Trader:
    """
    ✅ 핵심 기능
    - 텔레그램 명령으로 거래 ON/OFF
    - /status로 현재 상태/잔고/포지션/PnL 확인
    - 잔고 기반 복리 수량 자동 계산
    - 거래소 포지션 조회로 중복 진입 방지
    - /panic 강제청산
    - /buy 수동진입, /sell 수동청산
    - /risk, /lev로 동적 설정 변경(런타임)
    """

    def __init__(self, state):
        self.state = state

        # 런타임 설정(환경변수 기본값 -> 텔레그램 명령으로 변경 가능)
        self.trading_enabled = TRADING_ENABLED_DEFAULT
        self.leverage = LEVERAGE_DEFAULT
        self.risk_pct = RISK_PCT_DEFAULT

        # 내부 상태
        self.position = None         # "LONG" or None
        self.entry_price = None
        self.consec_losses = 0

        # 스팸 방지
        self._last_alert_ts = 0
        self._last_bybit_err_ts = 0

        # 처음 1회 레버리지 설정 여부
        self.lev_set = False

    # ---------- Telegram send ----------
    def tg_send(self, msg):
        print(msg)
        if BOT_TOKEN and CHAT_ID:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data={"chat_id": CHAT_ID, "text": msg},
                    timeout=10,
                )
            except:
                pass

    def tg_send_throttled(self, msg):
        if time.time() - self._last_alert_ts >= ALERT_COOLDOWN_SEC:
            self._last_alert_ts = time.time()
            self.tg_send(msg)

    def tg_send_bybit_err_throttled(self, msg):
        if time.time() - self._last_bybit_err_ts >= max(ALERT_COOLDOWN_SEC, 120):
            self._last_bybit_err_ts = time.time()
            self.tg_send(msg)

    # ---------- price ----------
    def _price_binance(self):
        r = requests.get(BINANCE, params={"symbol": SYMBOL}, headers=HEADERS, timeout=10)
        return float(r.json()["price"])

    def _price_gecko(self):
        r = requests.get(COINGECKO, params={"ids": "bitcoin", "vs_currencies": "usd"}, timeout=10)
        return float(r.json()["bitcoin"]["usd"])

    def _price_coinbase(self):
        r = requests.get(COINBASE, timeout=10)
        return float(r.json()["data"]["amount"])

    def get_price(self):
        for f in (self._price_binance, self._price_gecko, self._price_coinbase):
            try:
                return f()
            except:
                pass
        self.tg_send_throttled("⚠️ 가격 조회 실패")
        return None

    # ---------- Bybit signing (v5) ----------
    def _safe_json(self, r: requests.Response):
        text = r.text or ""
        if not text.strip():
            return {"_non_json": True, "raw": "", "status": r.status_code}
        try:
            return r.json()
        except Exception:
            return {"_non_json": True, "raw": text[:500], "status": r.status_code}

    def _sign_post(self, body: dict):
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body_str = json.dumps(body, separators=(",", ":"))
        pre = ts + BYBIT_API_KEY + recv + body_str
        sign = hmac.new(BYBIT_API_SECRET.encode(), pre.encode(), hashlib.sha256).hexdigest()
        headers = {
            **HEADERS,  # ✅ UA/Accept 항상 포함
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": BYBIT_API_KEY,
            "X-BAPI-SIGN": sign,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
        }
        return headers, body_str

    def _sign_get(self, params: dict):
        ts = str(int(time.time() * 1000))
        recv = "5000"
        query = urlencode(sorted(params.items()))
        pre = ts + BYBIT_API_KEY + recv + query
        sign = hmac.new(BYBIT_API_SECRET.encode(), pre.encode(), hashlib.sha256).hexdigest()
        headers = {
            **HEADERS,  # ✅ UA/Accept 항상 포함
            "X-BAPI-API-KEY": BYBIT_API_KEY,
            "X-BAPI-SIGN": sign,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
        }
        return headers, query

    def _bybit_post(self, path: str, body: dict):
        if DRY_RUN:
            return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}
        h, b = self._sign_post(body)
        url = BYBIT_BASE_URL + path
        r = requests.post(url, headers=h, data=b, timeout=15)
        data = self._safe_json(r)

        # ✅ 403 CloudFront 차단 감지 메시지 강화
        if r.status_code == 403:
            raise Exception(
                f"Bybit 403 blocked (LTE IP). base={BYBIT_BASE_URL} raw={data.get('raw')}"
            )

        if data.get("_non_json"):
            raise Exception(f"Bybit non-JSON status={data.get('status')} raw={data.get('raw')}")
        return data

    def _bybit_get(self, path: str, params: dict):
        if DRY_RUN:
            return {"retCode": 0, "retMsg": "DRY_RUN", "result": {}}
        h, query = self._sign_get(params)
        url = BYBIT_BASE_URL + path + ("?" + query if query else "")
        r = requests.get(url, headers=h, timeout=15)
        data = self._safe_json(r)

        # ✅ 403 CloudFront 차단 감지 메시지 강화
        if r.status_code == 403:
            raise Exception(
                f"Bybit 403 blocked (LTE IP). base={BYBIT_BASE_URL} raw={data.get('raw')}"
            )

        if data.get("_non_json"):
            raise Exception(f"Bybit non-JSON status={data.get('status')} raw={data.get('raw')}")
        return data

    # ---------- balance ----------
    def get_usdt_balance(self):
        if DRY_RUN:
            return float(self.state.get("paper_usdt", 30.0))

        res = self._bybit_get("/v5/account/wallet-balance", {"accountType": ACCOUNT_TYPE})
        if res.get("retCode") != 0:
            raise Exception(f"wallet-balance retCode={res.get('retCode')} retMsg={res.get('retMsg')}")

        lst = (((res.get("result") or {}).get("list")) or [])
        if not lst:
            return 0.0

        coins = (lst[0].get("coin") or [])
        for c in coins:
            if c.get("coin") == "USDT":
                for k in ("availableToWithdraw", "walletBalance", "equity"):
                    v = c.get(k)
                    if v is not None and str(v).strip() != "":
                        return float(v)
        return 0.0

    # ---------- position / pnl ----------
    def get_position_info(self):
        """
        실제 포지션 및 손익(가능하면) 같이 가져옴
        /v5/position/list
        """
        if DRY_RUN:
            if self.position == "LONG" and self.entry_price:
                return {
                    "has_pos": True,
                    "side": "Buy",
                    "size": 1.0,
                    "avgPrice": float(self.entry_price),
                    "unrealisedPnl": 0.0,
                    "cumRealisedPnl": 0.0,
                }
            return {"has_pos": False}

        res = self._bybit_get("/v5/position/list", {"category": CATEGORY, "symbol": SYMBOL})
        if res.get("retCode") != 0:
            raise Exception(f"position/list retCode={res.get('retCode')} retMsg={res.get('retMsg')}")

        items = (((res.get("result") or {}).get("list")) or [])
        if not items:
            return {"has_pos": False}

        p = items[0]
        size = float(p.get("size") or 0)
        side = p.get("side")
        avg = p.get("avgPrice") or p.get("entryPrice") or "0"
        upnl = p.get("unrealisedPnl") or 0
        rpnL = p.get("cumRealisedPnl") or p.get("curRealisedPnl") or 0

        return {
            "has_pos": size > 0,
            "side": side,
            "size": size,
            "avgPrice": float(avg),
            "unrealisedPnl": float(upnl),
            "cumRealisedPnl": float(rpnL),
        }

    def sync_position(self):
        try:
            info = self.get_position_info()
            if info.get("has_pos") and info.get("side") == "Buy":
                self.position = "LONG"
                self.entry_price = float(info.get("avgPrice") or self.entry_price or 0)
            else:
                self.position = None
                self.entry_price = None
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"❌ 포지션 조회 실패: {e}")

    # ---------- leverage ----------
    def set_leverage(self):
        body = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "buyLeverage": str(self.leverage),
            "sellLeverage": str(self.leverage),
        }
        res = self._bybit_post("/v5/position/set-leverage", body)
        self.tg_send(f"⚙️ 레버리지 {self.leverage}x 설정: {res.get('retMsg')} ({res.get('retCode')})")

    # ---------- sizing ----------
    def calc_qty(self, usdt_balance: float, price: float):
        margin = max(usdt_balance * self.risk_pct, 0.0)
        notional = margin * self.leverage
        qty = notional / price if price > 0 else 0.0
        qty = max(qty, 0.0001)
        return float(f"{qty:.6f}")

    # ---------- order ----------
    def order_market(self, side: str, qty: float, reduce_only=False):
        if DRY_RUN:
            self.tg_send(f"🧪 테스트 주문: {side} qty={qty} reduceOnly={reduce_only}")
            return {"retCode": 0, "retMsg": "DRY_RUN"}

        body = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "side": side,          # "Buy" / "Sell"
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "IOC",
        }
        if reduce_only:
            body["reduceOnly"] = True

        res = self._bybit_post("/v5/order/create", body)
        self.tg_send(f"✅ 주문: {res.get('retMsg')} ({res.get('retCode')}) / qty={qty}")
        return res

    # ---------- telegram command handler ----------
    def handle_command(self, text: str):
        """
        텔레그램에서 들어온 메시지 처리
        """
        cmd = (text or "").strip()

        if cmd == "/start":
            self.trading_enabled = True
            self.tg_send("✅ 거래 ON (TRADING_ENABLED=true)")
            return

        if cmd == "/stop":
            self.trading_enabled = False
            self.tg_send("🛑 거래 OFF (TRADING_ENABLED=false)")
            return

        if cmd == "/status":
            self.tg_send(self.status_text())
            return

        if cmd.startswith("/risk "):
            # 예: /risk 0.2
            try:
                v = float(cmd.split()[1])
                if not (0.01 <= v <= 1.0):
                    self.tg_send("❌ risk 범위: 0.01 ~ 1.0")
                    return
                self.risk_pct = v
                self.tg_send(f"✅ RISK_PCT 변경: {self.risk_pct}")
            except:
                self.tg_send("❌ 사용법: /risk 0.2")
            return

        if cmd.startswith("/lev "):
            # 예: /lev 5
            try:
                v = int(cmd.split()[1])
                if not (1 <= v <= 20):
                    self.tg_send("❌ lev 범위: 1 ~ 20")
                    return
                self.leverage = v
                self.lev_set = False  # 다음 tick에 다시 set_leverage 하게
                self.tg_send(f"✅ LEVERAGE 변경: {self.leverage} (다음 루프에서 적용)")
            except:
                self.tg_send("❌ 사용법: /lev 5")
            return

        if cmd == "/buy":
            # 수동 LONG 진입 (포지션 없을 때만)
            self.sync_position()
            if self.position is not None:
                self.tg_send("⚠️ 이미 포지션 있음. /status 확인")
                return
            price = self.get_price()
            if not price:
                self.tg_send("❌ 가격 실패")
                return
            try:
                bal = self.get_usdt_balance()
                qty = self.calc_qty(bal, price)
                if not self.lev_set:
                    self.set_leverage()
                    self.lev_set = True
                self.order_market("Buy", qty)
                self.position = "LONG"
                self.entry_price = price
                self.tg_send(f"📈 수동 LONG 진입: {price} / USDT={bal:.2f} / qty={qty}")
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ /buy 실패: {e}")
            return

        if cmd == "/sell":
            # 수동 청산 (포지션 있을 때)
            self.sync_position()
            if self.position is None:
                self.tg_send("⚠️ 포지션 없음")
                return
            price = self.get_price()
            if not price:
                self.tg_send("❌ 가격 실패")
                return
            try:
                bal = self.get_usdt_balance()
                qty = self.calc_qty(bal, price)
                self.order_market("Sell", qty, reduce_only=True)
                self.position = None
                self.entry_price = None
                self.tg_send("✅ 수동 청산 완료")
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ /sell 실패: {e}")
            return

        if cmd == "/panic":
            # 무조건 reduceOnly Sell 시도
            price = self.get_price() or 0
            try:
                bal = self.get_usdt_balance()
                qty = self.calc_qty(bal, price if price else 1)
                self.order_market("Sell", qty, reduce_only=True)
                self.position = None
                self.entry_price = None
                self.trading_enabled = False
                self.tg_send("🚨 PANIC: 강제청산 시도 + 거래 OFF")
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ /panic 실패: {e}")
            return

        # 도움말
        if cmd in ("/help", "help"):
            self.tg_send(self.help_text())
            return

        # 알 수 없는 명령
        if cmd.startswith("/"):
            self.tg_send("❓ 명령을 모르겠음. /help")
            return

    def help_text(self):
        return (
            "📌 명령어\n"
            "/start  거래 ON\n"
            "/stop   거래 OFF\n"
            "/status 상태/잔고/포지션/PnL\n"
            "/buy    수동 LONG 진입\n"
            "/sell   수동 청산\n"
            "/panic  강제청산 + 거래OFF\n"
            "/risk 0.2  (잔고의 20% 증거금)\n"
            "/lev 5     (레버리지)\n"
        )

    def status_text(self):
        price = self.state.get("last_price")
        last = self.state.get("last_event")
        try:
            bal = self.get_usdt_balance()
        except:
            bal = None

        # 포지션/PnL
        try:
            p = self.get_position_info()
        except:
            p = {"has_pos": False}

        lines = []
        lines.append(f"🧠 DRY_RUN={DRY_RUN} | ON={self.trading_enabled}")
        lines.append(f"⚙️ lev={self.leverage} | risk={self.risk_pct}")
        if price is not None:
            lines.append(f"💵 price={price}")
        if bal is not None:
            lines.append(f"💰 USDT={bal:.2f}")
        if p.get("has_pos"):
            lines.append(f"📍 POS=LONG size={p.get('size')} avg={p.get('avgPrice')}")
            lines.append(f"📈 uPnL={p.get('unrealisedPnl')} | rPnL={p.get('cumRealisedPnl')}")
        else:
            lines.append("📍 POS=None")
        if last:
            lines.append(f"📝 last={last}")
        return "\n".join(lines)

    # ---------- strategy loop ----------
    def tick(self):
        # 상태 노출
        self.state["trading_enabled"] = self.trading_enabled
        self.state["leverage"] = self.leverage
        self.state["risk_pct"] = self.risk_pct

        if not self.trading_enabled:
            self.state["last_event"] = "거래 OFF"
            return

        if self.consec_losses >= MAX_CONSEC_LOSSES:
            self.tg_send_throttled("🛑 연속 손실 제한 도달 (거래 중지)")
            self.trading_enabled = False
            return

        # 포지션 동기화
        self.sync_position()

        # 레버리지 설정(최초 1회 or /lev 변경 후)
        if not self.lev_set:
            try:
                self.set_leverage()
                self.lev_set = True
            except Exception as e:
                self.tg_send_bybit_err_throttled(f"❌ 레버리지 설정 실패: {e}")
                return

        # 가격
        price = self.get_price()
        if not price:
            return
        self.state["last_price"] = price
        self.state["last_event"] = f"Price: {price}"

        # 잔고
        try:
            usdt_balance = self.get_usdt_balance()
        except Exception as e:
            self.tg_send_bybit_err_throttled(f"❌ 잔고 조회 실패: {e}")
            return

        qty = self.calc_qty(usdt_balance, price)
        self.state["usdt_balance"] = usdt_balance
        self.state["calc_qty"] = qty

        # 진입(포지션 없을 때만)
        if self.position is None and self.entry_price is None:
            self.position = "LONG"
            self.entry_price = price
            try:
                self.order_market("Buy", qty)
                self.tg_send(f"📈 LONG 진입: {price} / USDT={usdt_balance:.2f} / qty={qty} (복리)")
            except Exception as e:
                self.position = None
                self.entry_price = None
                self.tg_send_bybit_err_throttled(f"❌ 주문 실패: {e}")
            return

        # 관리(손절/익절)
        if self.position == "LONG" and self.entry_price:
            change = (price - self.entry_price) / self.entry_price * 100

            if change <= -CRASH_PROTECT_PERCENT:
                self.tg_send("🚨 급락 보호")
                try:
                    self.order_market("Sell", qty, reduce_only=True)
                except Exception as e:
                    self.tg_send_bybit_err_throttled(f"❌ 청산 실패: {e}")
                    return
                self.position = None
                self.entry_price = None
                self.consec_losses += 1
                return

            if change <= -MAX_LOSS_PERCENT:
                self.tg_send(f"🛑 손절 {change:.2f}%")
                try:
                    self.order_market("Sell", qty, reduce_only=True)
                except Exception as e:
                    self.tg_send_bybit_err_throttled(f"❌ 손절 실패: {e}")
                    return
                self.position = None
                self.entry_price = None
                self.consec_losses += 1
                return

            if change >= TAKE_PROFIT_PERCENT:
                self.tg_send(f"💰 익절 {change:.2f}%")
                try:
                    self.order_market("Sell", qty, reduce_only=True)
                except Exception as e:
                    self.tg_send_bybit_err_throttled(f"❌ 익절 실패: {e}")
                    return
                self.position = None
                self.entry_price = None
                self.consec_losses = 0
                return

    def public_state(self):
        return {
            "dry_run": DRY_RUN,
            "trading_enabled": self.trading_enabled,
            "leverage": self.leverage,
            "risk_pct": self.risk_pct,
            "price": self.state.get("last_price"),
            "position": self.position,
            "entry_price": self.entry_price,
            "usdt_balance": self.state.get("usdt_balance"),
            "calc_qty": self.state.get("calc_qty"),
            "consec_losses": self.consec_losses,
            "last_event": self.state.get("last_event"),
        }
