import os
import sqlite3
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

# DB 경로 설정
DB_PATHS = {
    "general": os.getenv(
        "DB_GENERAL_PATH",
        "/root/docker/BookOasis_stable/db/media_general.db",
    ),
    "adult": os.getenv(
        "DB_ADULT_PATH", "/root/docker/BookOasis_stable/db/media_adult.db"
    ),
}

# 주기 설정
try:
  CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 1800))
except ValueError:
  CHECK_INTERVAL = 1800

try:
  REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", 3600))
except ValueError:
  REFRESH_INTERVAL = 3600


def log_print(message):
  """시간을 포함하여 로그를 출력하는 함수"""
  current_time = time.strftime("[%Y-%m-%d %H:%M:%S]", time.localtime())
  print(f"{current_time} {message}", flush=True)


def send_discord_notification(content):
  """디스코드 웹훅으로 최종 결과 전송"""
  if not DISCORD_WEBHOOK_URL:
    log_print("[에러] DISCORD_WEBHOOK_URL이 설정되지 않았습니다.")
    return

  payload = {"content": content}
  try:
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    if response.status_code == 204:
      log_print("[디스코드 전송 성공]")
    else:
      log_print(
          f"[디스코드 전송 실패] 상태 코드: {response.status_code}, 내용:"
          f" {response.text}"
      )
  except Exception as e:
    log_print(f"[디스코드 통신 에러] {e}")


def refresh_rclone_vfs():
  """Rclone 마운트/캐시 새로고침 요청"""
  log_print(f"[*] Rclone VFS 새로고침 요청 중... (대상 폴더: {TARGET_DIR})")
  try:
    payload = {"dir": TARGET_DIR, "recursive": True}
    res = requests.post(
        f"{RCLONE_URL}/vfs/refresh", json=payload, auth=AUTH, timeout=10
    )
    if res.status_code == 200:
      log_print(f"[*] Rclone VFS 새로고침 완료 성공")
    else:
      log_print(f"[-] Rclone VFS 새로고침 응답 코드: {res.status_code}")
  except Exception as e:
    log_print(f"[-] VFS 새로고침 통신 에러: {e}")


def find_library_id_from_db(db_path, folder_path):
  """SQLite DB에서 file_path가 해당 폴더를 포함하는 레코드를 찾아 library_id 반환"""
  if not os.path.exists(db_path):
    return None

  try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # file_path에 폴더 경로가 포함되어 있는지 조회 (LIKE 검색)
    # 예: file_path LIKE '%mnt/gds2/GDRIVE/READING/화보/4KHD/01/DSFGDSG%'
    query = "SELECT library_id FROM books WHERE file_path LIKE ? LIMIT 1"
    cursor.execute(query, (f"%{folder_path}%",))
    row = cursor.fetchone()

    conn.close()
    if row:
      return row[0]  # library_id 반환
  except Exception as e:
    log_print(f"[-] DB 조회 에러 ({db_path}): {e}")

  return None


def monitor_rclone():
  log_print(f"==================================================")
  log_print(f"[*] Rclone 모니터링 + 북오아시스 DB 연동 시작")
  log_print(f"[*] 서버 주소: {RCLONE_URL}")
  log_print(f"[*] 감시 폴더: {TARGET_DIR}")
  log_print(
      f"[*] 체크 주기: {CHECK_INTERVAL}초 ({CHECK_INTERVAL // 60}분)"
  )
  log_print(f"==================================================")

  seen_files = set()
  last_refresh_time = 0

  while True:
    current_time = time.time()

    # 1. VFS 새로고침 주기 체크
    if current_time - last_refresh_time >= REFRESH_INTERVAL:
      refresh_rclone_vfs()
      last_refresh_time = current_time

    log_print(f"[*] Rclone 전송 완료 내역 스캔 중... (대상: {TARGET_DIR})")

    try:
      trans_res = requests.post(
          f"{RCLONE_URL}/core/transferred", auth=AUTH, timeout=5
      )
      if trans_res.status_code == 200:
        transferred_list = trans_res.json().get("transferred", [])
        folder_updates = {}

        for item in transferred_list:
          file_path = item.get("name")
          file_size = item.get("size", 0)
          transfer_time = item.get("time")

          if file_path and file_path.startswith(TARGET_DIR):
            unique_key = f"{file_path}_{transfer_time}"

            if unique_key not in seen_files:
              seen_files.add(unique_key)

              if len(seen_files) > 2000:
                seen_files.pop()

              # 4단계 하위 폴더 경로로 묶기 (예: mnt/gds2/GDRIVE/READING/화보/4KHD/01/DSFGDSG)
              relative_path = file_path[len(TARGET_DIR) :].lstrip("/")
              parts = relative_path.split("/")

              if len(parts) >= 4:
                target_folder = TARGET_DIR + "/" + "/".join(parts[:4])
              else:
                target_folder = os.path.dirname(file_path)

              if target_folder not in folder_updates:
                folder_updates[target_folder] = {"count": 0, "size": 0}

              folder_updates[target_folder]["count"] += 1
              folder_updates[target_folder]["size"] += file_size

        if not folder_updates:
          log_print("[-] 새로운 파일 변경 내역 없음. (이전과 동일)")
        else:
          log_print(
              f"[+] 총 {len(folder_updates)}개의 폴더에서 변동 사항 감지됨"
          )

          # 각 폴더별로 작업 수행 및 중복 방지 리스트 관리
          executed_webhooks = set()  # 중복 API 호출 방지용 세트
          discord_messages = []

          for folder_path, data in folder_updates.items():
            file_count = data["count"]
            total_size_mb = data["size"] / 1024 / 1024

            log_print(f"[*] 처리 중인 폴더: {folder_path}")

            # 2. DB 검색 및 API 트리거 처리 (General 및 Adult 양쪽 확인)
            matched_db_type = None
            library_id = None

            for db_type, db_path in DB_PATHS.items():
              lib_id = find_library_id_from_db(db_path, folder_path)
              if lib_id is not None:
                matched_db_type = db_type
                library_id = lib_id
                break  # 찾으면 중단

            # 3. Webhook API 호출 (중복 체크 포함)
            api_status_msg = "DB 매칭 실패 (API 미호출)"
            if library_id is not None and matched_db_type:
              # 중복 방지를 위한 고유 키 생성 (타입 + 라이브러리ID + 폴더경로)
              webhook_dedup_key = f"{matched_db_type}_{library_id}_{folder_path}"

              if webhook_dedup_key not in executed_webhooks:
                executed_webhooks.add(webhook_dedup_key)

                # API URL 구성
                api_url = f"http://192.168.0.31:5930/api/webhook/scan?token=1234&library_id={library_id}&type={matched_db_type}"

                try:
                  api_res = requests.get(api_url, timeout=10)
                  if api_res.status_code == 200:
                    api_status_msg = f"스캔 API 호출 성공 ({matched_db_type.upper()})"
                    log_print(
                        f"[API 성공] type={matched_db_type}, library_id={library_id}"
                    )
                  else:
                    api_status_msg = f"스캔 API 실패 (코드: {api_res.status_code})"
                    log_print(f"[API 실패] 응답 코드: {api_res.status_code}")
                except Exception as api_err:
                  api_status_msg = "스캔 API 통신 에러"
                  log_print(f"[API 에러] {api_err}")
              else:
                api_status_msg = "중복 방지로 인해 API 스킵됨"

            else:
              log_print(
                  f"[-] 해당 폴더({folder_path})와 일치하는 DB 레코드를 찾지 못했습니다."
              )

            # 4. 디스코드에 보낼 메시지 누적
            msg = (
                f"🚨 **[RCLONE & SCAN] 폴더 변동 알림**\n"
                f"📂 **폴더:** `{folder_path}`\n"
                f"📄 **파일 수:** `{file_count}개` (`{total_size_mb:.2f} MB`)\n"
                f"🏷️ **DB분류:** `{matched_db_type if matched_db_type else '없음'}` | **Library ID:** `{library_id if library_id else 'N/A'}`\n"
                f"⚙️ **상태:** `{api_status_msg}`"
            )
            discord_messages.append(msg)

          # 모든 폴더 처리가 끝난 후 디스코드로 결과 일괄 발송
          if discord_messages:
            final_content = "\n\n----------------------------------\n\n".join(
                discord_messages
            )
            send_discord_notification(final_content)

      else:
        log_print(f"[-] Rclone 서버 응답 오류 (Status Code: {trans_res.status_code})")

    except requests.exceptions.ConnectionError:
      log_print(f"[-] Rclone 서버({RCLONE_URL})에 연결할 수 없습니다.")
    except Exception as e:
      log_print(f"[-] 에러 발생: {e}")

    next_check_min = CHECK_INTERVAL // 60
    log_print(f"[*] 다음 스캔까지 약 {next_check_min}분간 대기합니다...\n")
    time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
  try:
    monitor_rclone()
  except KeyboardInterrupt:
    log_print("\n[*] 사용자에 의해 모니터링이 중단되었습니다.")
