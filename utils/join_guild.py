# =============================================================================
#  SahilOps :: GUILD & SPAM HYBRID ENGINE v4.4
#  JOIN  •  LEAVE  •  OWNER SPAM (GUILD JOIN SUCCESS + BD FRIEND REQUEST ENGINE)
#  AUTO FAILED ACCOUNTS EXPORT (FAILED_ACCOUNTS.TXT)
#  TAG: SahilOps
# =============================================================================

import os, sys, re, time, json, csv, threading, random
import urllib.parse, urllib.request, urllib.error, ssl, concurrent.futures
from datetime import datetime

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_DIR = os.path.join(BASE_DIR, "SahilOps")
FAILED_FILE = os.path.join(EXPORT_DIR, "failed_accounts.txt")

try:
    import my_pb2
    import output_pb2
except ImportError:
    pass

# =============================================================================
#  THEME ENGINE
# =============================================================================
class T:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    ITALIC = "\033[3m"

    RED    = "\033[38;5;203m"
    DEEP_R = "\033[38;5;160m"
    BLOOD  = "\033[38;5;196m"
    GOLD   = "\033[38;5;220m"
    AMBER  = "\033[38;5;214m"
    CYAN   = "\033[38;5;80m"
    TEAL   = "\033[38;5;44m"
    GREEN  = "\033[38;5;78m"
    LIME   = "\033[38;5;154m"
    PINK   = "\033[38;5;212m"
    VIOLET = "\033[38;5;141m"
    WHITE  = "\033[38;5;255m"
    GREY   = "\033[38;5;245m"
    DARK   = "\033[38;5;238m"

def _vis(s):  return len(re.sub(r"\033\[[0-9;]*m", "", s))
def _pad(s, w, al="l"):
    v = _vis(s)
    if v >= w: return s
    g = w - v
    if al == "l": return s + " " * g
    if al == "r": return " " * g + s
    return " " * (g // 2) + s + " " * (g - g // 2)

def term_w():
    try:    return max(56, os.get_terminal_size().columns)
    except: return 90

def term_c(s, width=None):
    w = width or term_w()
    v = _vis(s)
    if v >= w: return s
    return " " * ((w - v) // 2) + s

def clr():  os.system("cls" if os.name == "nt" else "clear")

# =============================================================================
#  HEADER
# =============================================================================
LOGO = [
    " ██████╗ ██████╗ ███████╗ █████╗ ████████╗   ██╗ ██████╗ ██╗  ██╗██╗██████╗ ",
    "██╔════╝ ██╔══██╗██╔════╝██╔══██╗╚══██╔══╝   ██║██╔═══██╗██║  ██║██║██╔══██╗",
    "██║  ███╗██████╔╝█████╗  ███████║   ██║      ██║██║   ██║███████║██║██████╔╝",
    "██║   ██║██╔══██╗██╔══╝  ██╔══██║   ██║ ██   ██║██║   ██║██╔══██║██║██╔══██╗",
    "╚██████╔╝██║  ██║███████╗██║  ██║   ██║ ╚█████╔╝╚██████╔╝██║  ██║██║██║  ██║",
    " ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝   ╚═╝  ╚════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝",
]

def show_header():
    clr()
    W = term_w()
    grad = [T.BLOOD, T.RED, T.AMBER, T.GOLD, T.CYAN, T.BLOOD]
    print()
    for i, ln in enumerate(LOGO):
        print(term_c(f"{grad[i % len(grad)]}{T.BOLD}{ln}{T.RESET}"))
    rule = f"{T.DEEP_R}{'─' * min(60, W - 4)}{T.RESET}"
    print(term_c(rule))
    print(term_c(f"{T.GOLD}{T.BOLD}✦  SahilOps  ·  GUILD & SPAM HYBRID (BD)  ✦{T.RESET}"))
    print(term_c(f"{T.CYAN}{T.ITALIC}join  •  leave  •  owner spam  •  smart retry{T.RESET}"))
    print(term_c(rule))
    print()

# =============================================================================
#  LAYOUT PRIMITIVES
# =============================================================================
def _box_width(): return min(74, term_w() - 4)

def frame_top(title, w=None, col=None):
    w = w or _box_width(); col = col or T.RED
    inner = w - 2
    bar = f"┌─[ {T.BOLD}{title}{T.RESET}{col} ]" + "─" * max(0, inner - _vis(title) - 6) + "┐"
    return f"{col}{bar}{T.RESET}"

def frame_bot(w=None, col=None, label="SahilOps"):
    w = w or _box_width(); col = col or T.RED
    inner = w - 2
    lbl = f"[ {label} ]"
    return f"{col}└" + "─" * max(0, inner - len(lbl) - 1) + lbl + f"┘{T.RESET}"

def frame_row(content, w=None, col=None):
    w = w or _box_width(); col = col or T.RED
    inner = w - 2
    return f"{col}│{T.RESET}{_pad(content, inner)}{col}│{T.RESET}"

# =============================================================================
#  HTTP LAYER & GUILD API
# =============================================================================
_ssl = ssl.create_default_context()
_ssl.check_hostname = False
_ssl.verify_mode    = ssl.CERT_NONE

BASE = "https://danger-guild-management-web.vercel.app"
JOIN_ENDPOINTS  = [f"{BASE}/join"]
LEAVE_ENDPOINTS = [
    f"{BASE}/leave",
    f"{BASE}/guild/leave",
    f"{BASE}/quit",
    f"{BASE}/guild/quit",
    f"{BASE}/exit",
]
UA = "Mozilla/5.0 (Linux; Android 10) SahilOps/4.4"

def _get(url, timeout=20):
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Connection": "close",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl) as r:
            return True, r.status, r.read().decode("utf-8", "replace"), url, None
    except urllib.error.HTTPError as e:
        try:  body = e.read().decode("utf-8", "replace")
        except: body = ""
        return False, e.code, body, url, None
    except Exception as e:
        return False, 0, "", url, str(e)

def _call(endpoints, guild_id, uid, pwd):
    last = (False, 0, "", endpoints[0])
    for ep in endpoints:
        q = urllib.parse.urlencode({"guild_id": guild_id, "uid": uid, "password": pwd})
        ok, code, body, used, err = _get(f"{ep}?{q}")
        if ok: return True, code, body, used
        last = (False, code, body, used)
    return last

def do_join(g, u, p):  return _call(JOIN_ENDPOINTS,  g, u, p)
def do_leave(g, u, p): return _call(LEAVE_ENDPOINTS, g, u, p)

def call_with_retry(fn, gid, uid, pwd, max_retries=3):
    backoff = 0.6
    last = None
    for attempt in range(max_retries + 1):
        try:
            ok, code, body, ep = fn(gid, uid, pwd)
            last = (ok, code, body, ep)
            if ok: return ok, code, body, ep, attempt
            if code == 429 or (500 <= code < 600):
                time.sleep(backoff + random.uniform(0, 0.3))
                backoff *= 2
                continue
            return ok, code, body, ep, attempt
        except Exception as e:
            last = (False, 0, str(e), "")
            time.sleep(backoff)
            backoff *= 2
    return (*last, max_retries)

def get_guild_owner_uid(guild_id):
    endpoints = [
        f"{BASE}/guild/info?guild_id={guild_id}",
        f"{BASE}/info?guild_id={guild_id}",
        f"https://danger-guild-management-web.vercel.app/guild/details?guild_id={guild_id}"
    ]
    for ep in endpoints:
        ok, code, body, _, _ = _get(ep, timeout=12)
        if ok and body:
            try:
                data = json.loads(body)
                owner = (
                    data.get("owner_id") or data.get("leader_id") or
                    data.get("owner_uid") or data.get("leader_uid") or
                    data.get("data", {}).get("owner_id") or
                    data.get("guild_info", {}).get("owner_id")
                )
                if owner and str(owner).isdigit():
                    return int(owner)
            except Exception:
                continue
    return None

# =============================================================================
#  SPAM ENGINE & CRYPTO (BD SERVER ROUTE)
# =============================================================================
JWT_API_ENDPOINTS = [
    ("AGS COMMUNITY JWT API", "https://ags-community-jwt-api.vercel.app/token"),
]

BD_FRIEND_URLS = [
    "https://clientbp.ggblueshark.com/RequestAddingFriend",
    "https://client.bd.freefiremobile.com/RequestAddingFriend",
    "https://client.ind.freefiremobile.com/RequestAddingFriend",
]

AES_KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
AES_IV  = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])

FRIEND_HEADERS = {
    "User-Agent": "UnityPlayer/2022.3.47f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
    "X-GA": "v1 1",
    "ReleaseVersion": "OB54",
    "Content-Type": "application/octet-stream",
    "X-Unity-Version": "2022.3.47f1",
    "Connection": "Keep-Alive"
}

def encrypt_data(data: bytes) -> bytes:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return cipher.encrypt(pad(data, AES.block_size))

def encode_varint(n: int) -> bytes:
    result = []
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            byte |= 0x80
        result.append(byte)
        if not n:
            break
    return bytes(result)

def build_friend_request_payload(target_uid: int) -> bytes:
    body  = encode_varint(8) + encode_varint(target_uid)
    body += encode_varint(16) + encode_varint(target_uid)
    body += encode_varint(24) + encode_varint(26)
    return body

def jwt_from_external_api(api_name: str, api_url: str, uid: str, password: str, retries: int = 1):
    err_msg = ""
    for attempt in range(retries + 1):
        try:
            url = f"{api_url}?uid={uid}&password={password}"
            resp = requests.get(url, timeout=12, verify=False)
            if resp.status_code == 200:
                data = resp.json()
                token = (
                    data.get("token") or 
                    data.get("jwt") or 
                    data.get("access_token") or 
                    data.get("data", {}).get("token") or 
                    data.get("data", {}).get("jwt")
                )
                if token and len(token) > 50:
                    return token, None
                err_msg = "API json missing Token"
            else:
                err_msg = f"API HTTP {resp.status_code}"
        except Exception as e:
            err_msg = str(e)[:30]
        if attempt < retries: time.sleep(0.3)
    return None, err_msg

def get_jwt_with_reason(uid: str, password: str, retries: int = 1):
    for mname, url in JWT_API_ENDPOINTS:
        jwt, err = jwt_from_external_api(mname, url, uid, password, retries)
        if jwt:
            return jwt, mname, None
    return None, None, err

def send_friend_request(jwt: str, target_uid: int, retries: int = 1):
    plain = build_friend_request_payload(target_uid)
    encrypted = encrypt_data(plain)
    headers = FRIEND_HEADERS.copy()
    headers["Authorization"] = f"Bearer {jwt}"
    
    last_err = "Max retries exceeded"
    last_code = 0

    for target_url in BD_FRIEND_URLS:
        for attempt in range(retries + 1):
            try:
                resp = requests.post(target_url, headers=headers, data=encrypted, timeout=10, verify=False)
                if resp.status_code == 200:
                    return True, resp.status_code, None
                last_code = resp.status_code
                last_err = f"HTTP_{resp.status_code}"
            except Exception as e:
                last_err = str(e)[:25]
            if attempt < retries: time.sleep(0.2)
        if last_code == 200:
            break

    return False, last_code, last_err

# =============================================================================
#  ACCOUNT LOADER & FAILED RECORDER
# =============================================================================
def load_accounts(path):
    if not os.path.isfile(path):
        return None, f"file not found: {path}"
    out, seen = [], set()
    if path.endswith(".json"):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for item in data:
                u, p = item.get('uid'), item.get('password')
                if u and p and str(u) not in seen:
                    seen.add(str(u)); out.append((str(u), str(p)))
            return out, None
        except Exception as e:
            return None, str(e)

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            s = raw.strip()
            if not s or s.startswith("#"): continue
            for sep in (":", "|", ",", " "):
                if sep in s:
                    a, b = s.split(sep, 1)
                    a, b = a.strip(), b.strip()
                    if a and b and a not in seen:
                        seen.add(a); out.append((a, b))
                    break
    return out, None

def record_failed_account(uid, pwd):
    os.makedirs(EXPORT_DIR, exist_ok=True)
    failed_file = FAILED_FILE
    with open(failed_file, "a", encoding="utf-8") as f:
        f.write(f"{uid}:{pwd}\n")

# =============================================================================
#  PROGRESS & RESULTS
# =============================================================================
class Progress:
    def __init__(self, total):
        self.total = total
        self.done  = 0
        self.ok    = 0
        self.fail  = 0
        self.lock  = threading.Lock()
        self._last_len = 0

    def tick(self, success):
        with self.lock:
            self.done += 1
            if success: self.ok += 1
            else:       self.fail += 1
            self._render()

    def _render(self):
        W = term_w()
        width = min(40, W - 40)
        filled = int(width * self.done / self.total) if self.total else 0
        bar = "█" * filled + "░" * (width - filled)
        pct = int(100 * self.done / self.total) if self.total else 0
        line = (
            f"{T.CYAN}[{T.LIME}{bar}{T.CYAN}]{T.RESET} "
            f"{T.WHITE}{self.done:>3}/{self.total:<3}{T.RESET} "
            f"{T.DIM}({pct:>3}%){T.RESET}  "
            f"{T.GREEN}✔{self.ok:<3}{T.RESET} "
            f"{T.BLOOD}✘{self.fail:<3}{T.RESET}"
        )
        pad_len = max(0, (W - _vis(line)) // 2)
        sys.stdout.write("\r" + " " * self._last_len + "\r" + " " * pad_len + line)
        sys.stdout.flush()
        self._last_len = pad_len + _vis(line)

    def finish(self):
        self._render()
        print()

def result_card(idx, total, uid, pwd, ok, code, action, note="", retries=0, extra="", reason=""):
    W = _box_width()
    col = T.GREEN if ok else T.BLOOD
    tag = "✔ SUCCESS" if ok else "✘ FAILED"
    title = f"{tag}   ·   {idx}/{total}   ·   {action}"

    def masked(p):
        return p[:6] + "•" * 8 + p[-4:] if len(p) > 12 else p[:2] + "•" * 3 + p[-2:]

    rows = [
        f" {T.CYAN}🆔{T.RESET}  UID        {T.WHITE}{T.BOLD}{uid}{T.RESET}",
        f" {T.AMBER}🔒{T.RESET}  PASSWORD   {T.GREY}{masked(pwd)}{T.RESET}",
        f" {T.VIOLET}🎯{T.RESET}  ACTION     {T.PINK}{action}{T.RESET}",
        f" {T.GOLD}📡{T.RESET}  HTTP       {T.WHITE}{code}{T.RESET}",
        f" {T.LIME if ok else T.BLOOD}●{T.RESET}  STATUS     {col}{T.BOLD}{'SUCCESS' if ok else 'FAILED'}{T.RESET}",
    ]
    if extra:
        rows.append(f" {T.TEAL}🪄{T.RESET}  INFO       {T.WHITE}{extra}{T.RESET}")
    if reason:
        rows.append(f" {T.AMBER}⚠{T.RESET}  FR REASON  {T.AMBER}{reason[:44]}{T.RESET}")
    if retries:
        rows.append(f" {T.DIM}↻{T.RESET}  RETRIES    {T.DIM}{retries}{T.RESET}")
    if note:
        rows.append(f" {T.GREY}📝{T.RESET}  NOTE       {T.DIM}{note[:44]}{T.RESET}")

    print()
    print(term_c(frame_top(title, W, col)))
    for r in rows:
        print(term_c(frame_row(r, W, col)))
    print(term_c(frame_bot(W, col, label="SahilOps")))

def summary(succ, fail, total, secs, gid, action, src, extra_label=""):
    W = _box_width(); col = T.AMBER
    rate = round(total / secs, 2) if secs else 0
    print()
    print(term_c(frame_top(f"SESSION REPORT  ·  {action}", W, col)))
    print(term_c(frame_row(f" {T.CYAN}🏰{T.RESET}  GUILD ID      {T.WHITE}{gid}{T.RESET}", W, col)))
    if extra_label:
        print(term_c(frame_row(f" {T.TEAL}👤{T.RESET}  OWNER UID     {T.WHITE}{extra_label}{T.RESET}", W, col)))
    print(term_c(frame_row(f" {T.LIME}✔{T.RESET}  SUCCESS       {T.GREEN}{succ}{T.RESET} / {total}", W, col)))
    print(term_c(frame_row(f" {T.BLOOD}✘{T.RESET}  FAILED        {T.BLOOD}{fail}{T.RESET} / {total}", W, col)))
    print(term_c(frame_row(f" {T.GOLD}⏱{T.RESET}  ELAPSED       {T.WHITE}{secs}s  ({rate} acc/s){T.RESET}", W, col)))
    print(term_c(frame_row(f" {T.CYAN}📂{T.RESET}  SOURCE        {T.DIM}{os.path.basename(src)}{T.RESET}", W, col)))
    print(term_c(frame_row(f" {T.BLOOD}💾{T.RESET}  FAILED SAVED  {T.WHITE}{os.path.relpath(FAILED_FILE, BASE_DIR)}{T.RESET}", W, col)))
    print(term_c(frame_row(f" {T.PINK}👑{T.RESET}  TAG           {T.PINK}SahilOps{T.RESET}", W, col)))
    print(term_c(frame_bot(W, col, label="SahilOps")))

def main_menu():
    W = _box_width()
    print(term_c(frame_top("MAIN MENU", W, T.RED)))
    print(term_c(frame_row("", W, T.RED)))
    print(term_c(frame_row(f"   {T.LIME}{T.BOLD}[1]{T.RESET}  🏰   {T.WHITE}Guild Join{T.RESET}    {T.DIM}add all accounts to a guild{T.RESET}", W, T.RED)))
    print(term_c(frame_row(f"   {T.BLOOD}{T.BOLD}[2]{T.RESET}  🚪   {T.WHITE}Guild Leave{T.RESET}   {T.DIM}remove all accounts from guild{T.RESET}", W, T.RED)))
    print(term_c(frame_row(f"   {T.GOLD}{T.BOLD}[3]{T.RESET}  👑   {T.WHITE}OWNER SPAM{T.RESET}    {T.AMBER}[Guild Join + BD Owner Friend Spam]{T.RESET}", W, T.RED)))
    print(term_c(frame_row("", W, T.RED)))
    print(term_c(frame_row(f"   {T.GREY}{T.BOLD}[0]{T.RESET}  ❌   {T.GREY}Exit{T.RESET}", W, T.RED)))
    print(term_c(frame_bot(W, T.RED, label="SahilOps")))
    print()

def ask(prompt, default=None):
    W = term_w(); BW = _box_width()
    indent = " " * ((W - BW) // 2 + 2)
    suffix = f" {T.DIM}[{default}]{T.RESET}" if default else ""
    return input(f"{indent}{T.GOLD}▸{T.RESET} {T.WHITE}{prompt}{T.RESET}{suffix} {T.PINK}›{T.RESET} ").strip() or default or ""

# =============================================================================
#  EXECUTION ENGINES
# =============================================================================
def run_batch(action, gid, accounts, delay, workers):
    fn = do_join if action == "JOIN" else do_leave
    total = len(accounts)
    results = []
    lock = threading.Lock()
    prog = Progress(total)

    def worker(uid, pwd):
        ok, code, body, ep, retries = call_with_retry(fn, gid, uid, pwd, max_retries=3)
        note = ""
        try:
            if body:
                j = json.loads(body)
                note = str(j.get("message") or j.get("msg") or j.get("error") or "")[:60]
        except Exception:
            note = (body or "")[:60].replace("\n", " ")

        if not ok:
            record_failed_account(uid, pwd)

        with lock:
            results.append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "uid": uid, "password": pwd, "ok": ok, "code": code,
                "endpoint": ep, "retries": retries, "response": (body or "")[:400],
            })
            idx = len(results)

        result_card(idx, total, uid, pwd, ok, code, action, note, retries)
        prog.tick(ok)
        time.sleep(delay)

    print(term_c(f"{T.DIM}─ running ─{T.RESET}"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(worker, u, p) for u, p in accounts]
        concurrent.futures.wait(futs)
    prog.finish()
    return results

def run_owner_spam_batch(gid, owner_uid, accounts, delay, workers):
    total = len(accounts)
    results = []
    lock = threading.Lock()
    prog = Progress(total)

    def owner_spam_worker(uid, pwd):
        g_ok, g_code, g_body, ep, retries = call_with_retry(do_join, gid, uid, pwd, max_retries=2)

        f_status = "Skipped"
        fail_reason = ""
        jwt, method, jwt_err = get_jwt_with_reason(uid, pwd, retries=1)
        if jwt:
            ok_f, scode, f_err = send_friend_request(jwt, owner_uid, retries=1)
            if ok_f:
                f_status = f"Sent ({scode})"
            else:
                f_status = f"Fail ({scode})"
                fail_reason = f"Request: {f_err}"
        else:
            f_status = "JWT Failed"
            fail_reason = f"JWT: {jwt_err}"

        overall_ok = g_ok
        extra_info = f"Guild: {'OK' if g_ok else 'FAIL'} | Owner FR: {f_status}"

        if not g_ok:
            record_failed_account(uid, pwd)

        note = ""
        try:
            if g_body:
                j = json.loads(g_body)
                note = str(j.get("message") or j.get("msg") or j.get("error") or "")[:60]
        except Exception:
            note = (g_body or "")[:60].replace("\n", " ")

        with lock:
            results.append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "uid": uid, "password": pwd, "ok": overall_ok,
                "code": g_code, "endpoint": ep, "retries": retries,
                "response": extra_info,
            })
            idx = len(results)

        result_card(idx, total, uid, pwd, overall_ok, g_code, "OWNER SPAM", note, retries, extra=extra_info, reason=fail_reason)
        prog.tick(overall_ok)
        time.sleep(delay)

    print(term_c(f"{T.DIM}─ launching OWNER SPAM engine ─{T.RESET}"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(owner_spam_worker, u, p) for u, p in accounts]
        concurrent.futures.wait(futs)
    prog.finish()
    return results

# =============================================================================
#  MAIN ENTRY POINT
# =============================================================================
def main():
    show_header()
    main_menu()
    choice = ask("Select option (0-3)", default="3")

    if choice == "0":
        print(term_c(f"{T.GOLD}bye 👋{T.RESET}\n")); sys.exit(0)
    elif choice == "1":
        action = "JOIN"
    elif choice == "2":
        action = "LEAVE"
    elif choice == "3":
        action = "OWNER SPAM"
    else:
        print(term_c(f"{T.BLOOD}invalid choice{T.RESET}\n")); sys.exit(1)

    src = ask("📂  Account file path (.txt or .json)", default=os.path.join(EXPORT_DIR, "BD_results.txt"))
    accounts, err = load_accounts(src)
    if err:
        print(term_c(f"{T.BLOOD}✘ {err}{T.RESET}\n")); sys.exit(1)
    if not accounts:
        print(term_c(f"{T.BLOOD}✘ no accounts found{T.RESET}\n")); sys.exit(1)
    print(term_c(f"{T.GREEN}✔ loaded {len(accounts)} accounts{T.RESET}"))

    gid = ask("🏰  Guild ID")
    if not gid.strip():
        print(term_c(f"{T.BLOOD}✘ guild id required{T.RESET}\n")); sys.exit(1)

    owner_uid = None
    if action == "OWNER SPAM":
        print(term_c(f"{T.CYAN}Fetching Guild Owner details for ID {gid}...{T.RESET}"))
        owner_uid = get_guild_owner_uid(gid)
        if not owner_uid:
            print(term_c(f"{T.AMBER}⚠ Auto-detect owner failed or API is offline.{T.RESET}"))
            manual_owner = ask("🎯 Enter Guild Owner UID manually")
            try:
                owner_uid = int(manual_owner)
            except:
                print(term_c(f"{T.BLOOD}✘ Valid owner UID required for OWNER SPAM mode.{T.RESET}\n"))
                sys.exit(1)
        else:
            print(term_c(f"{T.GREEN}✔ Guild Owner Detected: {owner_uid}{T.RESET}"))

    try:    workers = max(1, min(10, int(ask("⚡  Workers (1-10)", default="2"))))
    except: workers = 2

    try:    delay = max(0.05, float(ask("⏱  Delay seconds (per worker)", default="0.8")))
    except: delay = 0.8

    print()
    print(term_c(f"{T.CYAN}{'─' * _box_width()}{T.RESET}"))
    print(term_c(f"{T.BOLD}{T.GOLD}▶  RUNNING {action}  ·  TAG: SahilOps  ·  workers={workers}  ·  delay={delay}s{T.RESET}"))
    print(term_c(f"{T.CYAN}{'─' * _box_width()}{T.RESET}"))

    t0 = time.time()
    if action == "OWNER SPAM":
        results = run_owner_spam_batch(gid, owner_uid, accounts, delay, workers)
    else:
        results = run_batch(action, gid, accounts, delay, workers)
    elapsed = round(time.time() - t0, 2)

    succ = sum(1 for r in results if r["ok"])
    fail = len(results) - succ

    summary(succ, fail, len(results), elapsed, gid, action, src, extra_label=str(owner_uid) if owner_uid else "")
    print(term_c(f"{T.GOLD}{T.BOLD}✦  DONE  ✦{T.RESET}"))
    print(term_c(f"{T.PINK}TAG :: SahilOps{T.RESET}\n"))

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt:
        print(term_c(f"\n{T.AMBER}stopped by user{T.RESET}\n"))