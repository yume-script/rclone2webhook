import os
import time
import requests
from dotenv import load_dotenv

# rclone2webhook.env 파일 로드
load_dotenv("rclone2webhook.env")

# 환경 변수에서 설정값 불러오기
RCLONE_URL = os.getenv("RCLONE_URL", "http://192.168.0.90:5274")
AUTH_USER = os.getenv("RCLONE_AUTH_USER")
AUTH_PASS = os.getenv("RCLONE_AUTH_PASS")
AUTH = (AUTH_USER, AUTH_PASS) if AUTH_USER and AUTH_PASS else None

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TARGET_DIR = os.getenv("TARGET_DIR", "mnt/gds2/GDRIVE/READING")

# 숫자로 변환 (잘못 입력된 경우 기본값 적용)
try:
  CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 1800))
except ValueError:
  CHECK_INTERVAL = 1800

try:
  REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", 3600))
except ValueError:
  REFRESH_INTERVAL = 3600


def send_discord_notification(file_path, file_size):
  """디스코드 웹훅으로 메시지를 전송하는 함수"""
  if not DISCORD_WEBHOOK_URL:
    print("[에러] DISCORD_WEBHOOK_URL이 설정되지 않았습니다.")
    return

  size_mb = file_size / 1024 / 1024

  content = (
      "🚨 **[RCLONE] 새로운 파일 추가/변경 감지**\n"
      f"📂 **경로:** `{file_path}`\n"
      f"📦 **용량:** `{size_mb:.2f} MB`"
  )

  payload = {"content": content}

  try:
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    if response.status_code == 204:
      print(f"[디스코드 전송 성공] {file_path}")
    else:
      print(
          f"[디스코드 전송 실패] 상태 코드: {response.status_code}, 내용:"
          f" {response.text}"
      )
  except Exception as e:
    print(f"[디스코드 통신 에러] {e}")


def refresh_rclone_vfs():
  """Rclone 마운트/캐시 새로고침 요청"""
  try:
    payload = {"dir": TARGET_DIR, "recursive": True}
    res = requests.post(
        f"{RCLONE_URL}/vfs/refresh", json=payload, auth=AUTH, timeout=10
    )
    if res.status_code == 200:
      print(f"[*] Rclone VFS 새로고침 완료 ({TARGET_DIR})")
  except Exception as e:
    pass


def monitor_rclone():
  print(f"[*] Rclone 모니터링 시작: {RCLONE_URL}")
  print(f"[*] 감시 대상 폴더: [{TARGET_DIR}]")
  print(
      f"[*] 체크 주기: {CHECK_INTERVAL}초 ({CHECK_INTERVAL // 60}분) 마다"
      " 실행됩니다.\n"
  )

  seen_files = set()
  last_refresh_time = 0

  while True:
    current_time = time.time()

    # 지정된 주기마다 Rclone VFS 새로고침 수행
    if current_time - last_refresh_time >= REFRESH_INTERVAL:
      refresh_rclone_vfs()
      last_refresh_time = current_time

    try:
      # Rclone 전송 완료 내역 조회
      trans_res = requests.post(
          f"{RCLONE_URL}/core/transferred", auth=AUTH, timeout=5
      )
      if trans_res.status_code == 200:
        transferred_list = trans_res.json().get("transferred", [])

        for item in transferred_list:
          file_path = item.get("name")
          file_size = item.get("size", 0)
          transfer_time = item.get("time")

          # 지정한 하부 폴더(TARGET_DIR)로 시작하는 파일인지 확인
          if file_path and file_path.startswith(TARGET_DIR):
            unique_key = f"{file_path}_{transfer_time}"

            # 중복 알림 방지
            if unique_key not in seen_files:
              seen_files.add(unique_key)

              if len(seen_files) > 1000:
                seen_files.pop()

              # 디스코드 노티 함수 호출
              send_discord_notification(file_path, file_size)

    except requests.exceptions.ConnectionError:
      print(
          f"[-] Rclone 서버({RCLONE_URL})에 연결할 수 없습니다. 재시도 중...",
          end="\r",
      )
    except Exception as e:
      print(f"[-] 에러 발생: {e}")

    time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
  try:
    monitor_rclone()
  except KeyboardInterrupt:
    print("\n[*] 사용자에 의해 모니터링이 중단되었습니다.")
