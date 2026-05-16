# Systemd + Watchdog 운영 패치

이 패치는 매매 로직을 건드리지 않습니다. 목적은 서버 재부팅/프로세스 죽음/중복 실행을 줄이는 운영 안정화입니다.

## GitHub에 올릴 파일

```text
bot_watchdog.py
scripts/install_systemd_service.sh
scripts/install_watchdog_cron.sh
scripts/service_status.sh
PATCH_README_SERVICE.md
```

## 서버 적용

```bash
cd ~/coin-trade-bot && git pull && chmod +x scripts/*.sh && sudo bash scripts/install_systemd_service.sh
```

## 상태 확인

```bash
cd ~/coin-trade-bot && bash scripts/service_status.sh
```

또는:

```bash
sudo systemctl status coin-trade-bot --no-pager
journalctl -u coin-trade-bot -n 100 --no-pager
```

## Watchdog 크론 설치

5분마다 봇 상태를 확인하고, 서비스가 죽었으면 재시작합니다.

```bash
cd ~/coin-trade-bot && bash scripts/install_watchdog_cron.sh
```

수동 점검:

```bash
cd ~/coin-trade-bot && venv/bin/python bot_watchdog.py --once
```

강제 재시작 허용 점검:

```bash
cd ~/coin-trade-bot && WATCHDOG_RESTART_ON_FAIL=true venv/bin/python bot_watchdog.py --once
```

## 주의

- systemd 적용 후에는 `nohup python main.py`를 다시 치지 마세요. 중복 실행 위험이 있습니다.
- 재시작은 `sudo systemctl restart coin-trade-bot`만 쓰는 게 안전합니다.
- `.env`는 GitHub에 올리면 안 됩니다.
