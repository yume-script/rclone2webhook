import json
import os
import sqlite3
import time
from collections import deque

import requests
from dotenv import load_dotenv

# rclone2webhook.env 파일 로드
load_dotenv("rclone2webhook.env")

# ── Rclone RC 서버 설정 ──────────────────────────────────────────────
RCLONE_URL = os.getenv("RCLONE_URL", "http://192.168.0.90:5274")
AUTH_USER = os.getenv("RCLONE_AUTH_USER")
AUTH_PASS = os.getenv("RCLONE_AUTH_PASS")
AUTH = (AUTH_USER, AUTH_PASS) if AUTH_USER and AUTH_PASS else None

# 실제 rclone.conf에 등록된 원격(remote) 이름 (예: union_gds)
RCLONE_REMOTE_NAME = os.getenv("RCLONE_REMOTE_NAME", "union_gds")

# 원격 기준 상대 경로. TARGET_DIR과 동일한 하위 구조를 가리켜야 함
# (마운트 경로 접두어(mnt/xxx/)를 뺀 나머지 부분)
RCLONE_REMOTE_PATH = os.getenv("RCLONE_REMOTE_PATH", "GDRIVE/READING")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# DB 매칭/알림 표시용 전체 경로 (기존과 동일한 포맷 유지)
TARGET_DIR = os.getenv("TARGET_DIR", "mnt/gds2/GDRIVE/READING")

# BookOasis 스캔 API 설정
BOOKOASIS_API_URL = os.getenv("BOOKOASIS_API_URL", "http://192.168.0.31:5930/api/webhook/scan")
BOOKOASIS_API_TOKEN = os.getenv("BOOKOASIS_API_TOKEN", "")

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

# 주기 설정 (초 단위) — 새 파일 체크는 기본 5분
try:
  CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))
except ValueError:
  CHECK_INTERVAL = 300

try:
  REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", 3600))
except ValueError:
  REFRESH_INTERVAL = 3600

DISCORD_MAX_LEN = 1900  # 디스코드 2000자 제한에 여유를 둔 값

# 대용량 폴더 + tpslimit 제한 환경에서 operations/list가 오래 걸릴 수 있어 넉넉하게 설정
try:
  LIST_TIMEOUT = int(os.getenv("LIST_TIMEOUT", 900))
except ValueError:
  LIST_TIMEOUT = 900

# 이전 스캔 결과(파일 목록)를 저장해 재시작 후에도 중복 알림을 막기 위한 상태 파일
STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "seen_files_state.json"
)


def log_print(message):
  """시간을 포함하여 로그를 출력하는 함수"""
  current_time = time.strftime("[%Y-%m-%d %H:%M:%S]", time.localtime())
  print(f"{current_time} {message}", flush=True)


def load_previous_state():
  """이전 실행에서 저장한 파일 목록(경로 집합)을 불러옴. 없으면 None 반환(최초 실행)"""
  if not os.path.exists(STATE_FILE):
    return None
  try:
    with open(STATE_FILE, "r", encoding="utf-8") as f:
      data = json.load(f)
    return set(data.get("files", []))
  except Exception as e:
    log_print(f"[-] 상태 파일 로드 실패, 최초 실행으로 간주합니다: {e}")
    return None


def save_current_state(file_paths):
  """현재 스캔된 전체 파일 목록을 상태 파일에 저장"""
  try:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
      json.dump({"files": sorted(file_paths), "updated_at": time.time()}, f)
  except Exception as e:
    log_print(f"[-] 상태 파일 저장 실패: {e}")


def chunk_messages(messages, separator="\n\n----------------------------------\n\n", max_len=DISCORD_MAX_LEN):
  """메시지 리스트를 디스코드 2000자 제한에 맞춰 여러 청크로 분할"""
  chunks = []
  current = ""

  for msg in messages:
    candidate = msg if not current else current + separator + msg

    if len(candidate) > max_len and current:
      chunks.append(current)
      current = msg
    else:
      current = candidate

  if current:
    chunks.append(current)

  return chunks


def send_discord_notification(content):
  """디스코드 웹훅으로 최종 결과 전송 (2000자 제한 대응)"""
  if not DISCORD_WEBHOOK_URL:
    log_print("[에러] DISCORD_WEBHOOK_URL이 설정되지 않았습니다.")
    return

  payload = {"content": content}
  try:
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    if response.status_code == 204:
      log_print("[디스코드 전송 성공]")
    elif response.status_code == 429:
      retry_after = response.json().get("retry_after", 1)
      log_print(f"[디스코드 레이트리밋] {retry_after}초 대기 후 재시도")
      time.sleep(float(retry_after) + 0.5)
      retry_res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
      if retry_res.status_code == 204:
        log_print("[디스코드 전송 성공 (재시도)]")
      else:
        log_print(
            f"[디스코드 재전송 실패] 상태 코드: {retry_res.status_code}, 내용:"
            f" {retry_res.text}"
        )
    else:
      log_print(
          f"[디스코드 전송 실패] 상태 코드: {response.status_code}, 내용:"
          f" {response.text}"
      )
  except Exception as e:
    log_print(f"[디스코드 통신 에러] {e}")


def send_discord_messages(messages):
  """메시지 리스트를 2000자 제한에 맞춰 청크로 나눠 순서대로 전송"""
  for chunk in chunk_messages(messages):
    send_discord_notification(chunk)


def refresh_rclone_vfs():
  """Rclone 마운트/캐시 새로고침 요청 (마운트를 통해 파일에 접근하는 다른 서비스를 위해 유지)

  dir 파라미터는 마운트된 fs 루트 기준 상대경로여야 하므로 TARGET_DIR(표시용 전체경로)이
  아니라 RCLONE_REMOTE_PATH를 사용한다.
  """
  log_print(f"[*] Rclone VFS 새로고침 요청 중... (대상 폴더: {RCLONE_REMOTE_PATH})")
  try:
    payload = {"dir": RCLONE_REMOTE_PATH, "recursive": True}
    res = requests.post(
        f"{RCLONE_URL}/vfs/refresh", json=payload, auth=AUTH, timeout=30
    )
    if res.status_code == 200:
      log_print(f"[*] Rclone VFS 새로고침 완료 성공")
    else:
      log_print(f"[-] Rclone VFS 새로고침 응답 코드: {res.status_code}")
  except Exception as e:
    log_print(f"[-] VFS 새로고침 통신 에러: {e}")


def list_remote_files():
  """
  원격(remote)을 직접 재귀 조회하여 현재 존재하는 전체 파일 목록을 가져옴.
  VFS 캐시를 거치지 않고 백엔드를 직접 조회하므로, 이 rclone 프로세스가
  전송하지 않은(=다른 경로로 업로드된) 파일도 빠짐없이 잡아낸다.
  반환값: { "GDRIVE/READING/화보/.../001.WEBP": size, ... } 형태의 dict
          (경로는 RCLONE_REMOTE_PATH 기준 상대경로)
  """
  body = {
      "fs": f"{RCLONE_REMOTE_NAME}:",
      "remote": RCLONE_REMOTE_PATH,
      "opt": {"recurse": True, "noModTime": True, "filesOnly": True},
  }
  start = time.time()
  # tpslimit 등으로 대용량 폴더는 목록 조회 자체가 오래 걸릴 수 있어 넉넉하게 잡음
  res = requests.post(
      f"{RCLONE_URL}/operations/list", json=body, auth=AUTH, timeout=LIST_TIMEOUT
  )
  res.raise_for_status()
  data = res.json()
  elapsed = time.time() - start
  log_print(f"[*] 원격 목록 조회 완료 ({elapsed:.1f}초 소요)")

  files = {}
  for entry in data.get("list", []):
    if entry.get("IsDir"):
      continue
    # entry["Path"]는 RCLONE_REMOTE_PATH 기준 상대 경로
    files[entry["Path"]] = entry.get("Size", 0)

  return files


def find_library_id_from_db(db_path, folder_path):
  """SQLite DB에서 file_path가 해당 폴더를 포함하는 레코드를 찾아 library_id 반환"""
  if not os.path.exists(db_path):
    return None

  try:
    with sqlite3.connect(db_path) as conn:
      cursor = conn.cursor()
      query = "SELECT library_id FROM books WHERE file_path LIKE ? LIMIT 1"
      cursor.execute(query, (f"%{folder_path}%",))
      row = cursor.fetchone()
      return row[0] if row else None
  except Exception as e:
    log_print(f"[-] DB 조회 에러 ({db_path}): {e}")
    return None


def call_bookoasis_scan_api(library_id, db_type):
  """BookOasis 스캔 웹훅 API 호출"""
  params = {
      "token": BOOKOASIS_API_TOKEN,
      "library_id": library_id,
      "type": db_type,
  }
  return requests.get(BOOKOASIS_API_URL, params=params, timeout=10)


def bundle_new_files_by_folder(new_relative_paths):
  """
  새로 발견된 파일들의 상대 경로 목록을 4단계 상위 폴더 기준으로 묶는다.
  예: 화보/4KHD/01/DSFGDSG/001.WEBP -> TARGET_DIR/화보/4KHD/01/DSFGDSG
  """
  folder_updates = {}

  for rel_path, size in new_relative_paths.items():
    parts = rel_path.split("/")

    if len(parts) >= 4:
      target_folder = TARGET_DIR + "/" + "/".join(parts[:4])
    else:
      # 4단계보다 얕은 경로는 파일의 바로 상위 폴더로 묶음
      parent = "/".join(parts[:-1]) if len(parts) > 1 else ""
      target_folder = TARGET_DIR + ("/" + parent if parent else "")

    if target_folder not in folder_updates:
      folder_updates[target_folder] = {"count": 0, "size": 0}

    folder_updates[target_folder]["count"] += 1
    folder_updates[target_folder]["size"] += size

  return folder_updates


def process_folder_updates(folder_updates):
  """DB 매칭 -> API 호출 -> 디스코드 메시지 구성까지 폴더 단위로 처리"""
  executed_webhooks = set()
  discord_messages = []

  for folder_path, data in folder_updates.items():
    file_count = data["count"]
    total_size_mb = data["size"] / 1024 / 1024

    log_print(f"[*] 처리 중인 폴더: {folder_path} (신규 파일 {file_count}개)")

    matched_db_type = None
    library_id = None

    for db_type, db_path in DB_PATHS.items():
      lib_id = find_library_id_from_db(db_path, folder_path)
      if lib_id is not None:
        matched_db_type = db_type
        library_id = lib_id
        break

    api_status_msg = "DB 매칭 실패 (API 미호출)"
    if library_id is not None and matched_db_type:
      webhook_dedup_key = f"{matched_db_type}_{library_id}_{folder_path}"

      if webhook_dedup_key not in executed_webhooks:
        executed_webhooks.add(webhook_dedup_key)
        try:
          api_res = call_bookoasis_scan_api(library_id, matched_db_type)
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
      log_print(f"[-] 해당 폴더({folder_path})와 일치하는 DB 레코드를 찾지 못했습니다.")

    msg = (
        f"🚨 **[RCLONE & SCAN] 신규 파일 감지**\n"
        f"📂 **폴더:** `{folder_path}`\n"
        f"📄 **신규 파일 수:** `{file_count}개` (`{total_size_mb:.2f} MB`)\n"
        f"🏷️ **DB분류:** `{matched_db_type if matched_db_type else '없음'}` | **Library ID:** `{library_id if library_id else 'N/A'}`\n"
        f"⚙️ **상태:** `{api_status_msg}`"
    )
    discord_messages.append(msg)

  return discord_messages


def monitor_rclone():
  log_print(f"==================================================")
  log_print(f"[*] Rclone 신규 파일 감시 + 북오아시스 DB 연동 시작")
  log_print(f"[*] 서버 주소: {RCLONE_URL}")
  log_print(f"[*] 원격 이름: {RCLONE_REMOTE_NAME}:")
  log_print(f"[*] 감시 경로: {RCLONE_REMOTE_PATH} (표시용: {TARGET_DIR})")
  log_print(f"[*] 체크 주기: {CHECK_INTERVAL}초 ({CHECK_INTERVAL // 60}분)")
  log_print(f"==================================================")

  if not BOOKOASIS_API_TOKEN:
    log_print("[경고] BOOKOASIS_API_TOKEN이 설정되지 않았습니다. .env를 확인하세요.")

  if AUTH_USER and not AUTH_PASS:
    log_print("[경고] RCLONE_AUTH_USER만 설정되고 RCLONE_AUTH_PASS가 없어 인증 없이 요청합니다.")

  previous_files = load_previous_state()
  last_refresh_time = 0

  if previous_files is None:
    # 최초 실행: 현재 상태를 기준선으로만 저장하고 알림은 보내지 않음
    log_print("[*] 상태 파일이 없어 최초 실행으로 판단, 기준선을 설정합니다...")
    try:
      current_files = list_remote_files()
      save_current_state(set(current_files.keys()))
      previous_files = set(current_files.keys())
      log_print(f"[*] 기준선 설정 완료: 총 {len(previous_files)}개 파일. 다음 스캔부터 신규 파일을 감지합니다.")
    except Exception as e:
      log_print(f"[-] 최초 목록 조회 실패: {e}")
      previous_files = set()

  while True:
    current_time = time.time()

    if current_time - last_refresh_time >= REFRESH_INTERVAL:
      refresh_rclone_vfs()
      last_refresh_time = current_time

    log_print(f"[*] 원격 파일 목록 조회 중... (대상: {RCLONE_REMOTE_PATH})")

    try:
      current_listing = list_remote_files()
      current_keys = set(current_listing.keys())

      new_keys = current_keys - previous_files

      if not new_keys:
        log_print("[-] 새로운 파일 없음. (이전과 동일)")
      else:
        log_print(f"[+] 신규 파일 {len(new_keys)}개 감지됨")

        new_files = {k: current_listing[k] for k in new_keys}
        folder_updates = bundle_new_files_by_folder(new_files)

        log_print(f"[+] 총 {len(folder_updates)}개의 폴더에서 변동 사항 감지됨")

        discord_messages = process_folder_updates(folder_updates)
        if discord_messages:
          send_discord_messages(discord_messages)

      # 처리 성공 여부와 무관하게 최신 목록을 상태로 저장
      # (API 실패로 인한 재알림은 executed_webhooks가 아니라 별도 재시도로 다뤄야 하므로,
      #  일단 "감지"는 여기서 완료된 것으로 간주한다)
      previous_files = current_keys
      save_current_state(current_keys)

    except requests.exceptions.ConnectionError:
      log_print(f"[-] Rclone 서버({RCLONE_URL})에 연결할 수 없습니다.")
    except requests.exceptions.HTTPError as e:
      log_print(f"[-] Rclone RC 응답 오류: {e}")
    except Exception as e:
      log_print(f"[-] 에러 발생: {e}")

    next_check_min = CHECK_INTERVAL / 60
    log_print(f"[*] 다음 스캔까지 약 {next_check_min:.1f}분간 대기합니다...\n")
    time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
  try:
    monitor_rclone()
  except KeyboardInterrupt:
    log_print("\n[*] 사용자에 의해 모니터링이 중단되었습니다.")
