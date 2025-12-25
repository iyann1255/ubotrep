import os
import json
import re
import asyncio
import logging
from typing import Dict, Any, Optional
from urllib.parse import urlencode

import aiohttp
from pyrogram import Client, filters
from pyrogram.enums import ChatAction

# =========================
# CONFIG
# =========================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
SESSION = os.getenv("SESSION", "ubot")  # bisa string session / file session
if not API_ID or not API_HASH:
    raise SystemExit("ENV API_ID/API_HASH belum diisi.")

FAST_TIMEOUT = float(os.getenv("FAST_TIMEOUT", "4"))
DATA_FILE = os.getenv("DATA_FILE", "chatbot_data.json")

DEFAULT_ROLE = (
    "Kamu adalah seorang yang lagi ngebalesin chat di dalam group chat. "
    "Balasanmu harus menggunakan bahasa gaul sehari-hari ala anak gen z, "
    "sangat singkat, dan responsif. Balasan tidak perlu selalu formal atau informatif, "
    "fokus saja pada reaksi emosional atau komentar cepat.\n"
    "Jangan pernah menawarkan bantuan dan bertanya.\n"
    "Jangan ngaku bot atau AI.\n"
    "Jawaban singkat Maksimal 10 - 15 karakter.\n"
)

SIPUTZX_GPT3_URL = os.getenv("SIPUTZX_GPT3_URL", "https://api.siputzx.my.id/api/ai/gpt3").strip()
SIPUTZX_GPT_URL = os.getenv("SIPUTZX_GPT_URL", "https://apis-liart.vercel.app/api/gpt").strip()

MAX_SENTENCES = int(os.getenv("MAX_SENTENCES", "1"))
MAX_CHARS = int(os.getenv("MAX_CHARS", "15"))

MENTION_REGEX = re.compile(r"@\w+", re.UNICODE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("ubot-chatbot")

# =========================
# STORAGE
# =========================
data: Dict[str, Any] = {"chats": {}}

def load_data() -> None:
    global data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "chats" not in data or not isinstance(data["chats"], dict):
                data = {"chats": {}}
        except Exception:
            log.exception("Gagal load data, reset.")
            data = {"chats": {}}

def save_data() -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("Gagal save data.")

def get_chat_cfg(chat_id: int) -> Dict[str, Any]:
    cid = str(chat_id)
    if cid not in data["chats"]:
        data["chats"][cid] = {"role": DEFAULT_ROLE, "enabled": False}
        save_data()
    return data["chats"][cid]

# =========================
# RESPONSE LIMITER
# =========================
def limit_response(text: str, max_sentences: int = 1, max_chars: int = 15) -> str:
    if not text:
        return text
    text = text.strip()
    text = re.sub(r"^\s*#{1,6}\s+.*$", "", text, flags=re.MULTILINE).strip()
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE).strip()

    parts = re.split(r"(?<=[.!?])\s+", text)
    short = " ".join(parts[:max_sentences]).strip() or text

    if len(short) > max_chars:
        short = short[:max_chars].rstrip()
    return short

def fallback_reply(_: str) -> str:
    return "wkwk"

# =========================
# HTTP SESSION
# =========================
_session: Optional[aiohttp.ClientSession] = None

async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session and not _session.closed:
        return _session
    _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    return _session

async def call_siputzx(prompt: str, role: str) -> Optional[str]:
    prompt = (prompt or "").strip()
    role = (role or DEFAULT_ROLE).strip()
    if not prompt:
        return None

    session = await get_session()

    # 1) gpt3
    try:
        params = {"prompt": role, "content": prompt}
        url = f"{SIPUTZX_GPT3_URL}?{urlencode(params)}"
        async with session.get(url) as r:
            raw = await r.text()
            if r.status == 200:
                try:
                    js = json.loads(raw)
                except Exception:
                    return raw.strip() or None

                if isinstance(js, dict):
                    val = js.get("data")
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                    if isinstance(val, dict):
                        c = val.get("content")
                        if isinstance(c, str) and c.strip():
                            return c.strip()
                    for v in js.values():
                        if isinstance(v, str) and v.strip():
                            return v.strip()
            else:
                log.warning("Siputzx gpt3 non-200: %s %s", r.status, raw[:200])
    except Exception:
        log.exception("Error call_siputzx (gpt3)")

    # 2) fallback
    try:
        params = {"text": prompt}
        url = f"{SIPUTZX_GPT_URL}?{urlencode(params)}"
        async with session.get(url) as r:
            raw = await r.text()
            if r.status != 200:
                log.warning("Siputzx fallback non-200: %s %s", r.status, raw[:200])
                return None
            js = json.loads(raw)
            if isinstance(js, dict):
                data_obj = js.get("data")
                if isinstance(data_obj, dict):
                    content = data_obj.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                for k in ("result", "answer", "message", "data"):
                    v = js.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
            return None
    except Exception:
        log.exception("Error call_siputzx (fallback)")
        return None

# =========================
# UBot
# =========================
app = Client(SESSION, api_id=API_ID, api_hash=API_HASH)

@app.on_message(filters.command("start") & filters.me)
async def start_cmd(_, m):
    await m.reply_text(
        "On.\n\n"
        "• .chat on|off\n"
        "• .setrole <teks>\n"
        "• .role"
    )

@app.on_message(filters.command("chat", prefixes=".") & filters.me)
async def chat_cmd(_, m):
    cfg = get_chat_cfg(m.chat.id)
    parts = m.text.split(maxsplit=1)
    arg = (parts[1].strip().lower() if len(parts) > 1 else "")
    if arg not in ("on", "off"):
        return await m.reply_text("Pakai: .chat on atau .chat off")

    cfg["enabled"] = (arg == "on")
    save_data()
    await m.reply_text("AKTIF" if cfg["enabled"] else "MATI")

@app.on_message(filters.command("setrole", prefixes=".") & filters.me)
async def setrole_cmd(_, m):
    cfg = get_chat_cfg(m.chat.id)
    parts = m.text.split(maxsplit=1)
    role = (parts[1].strip() if len(parts) > 1 else "")
    if not role:
        return await m.reply_text("Pakai: .setrole ...")

    cfg["role"] = role[:3000]
    save_data()
    await m.reply_text("Ok.")

@app.on_message(filters.command("role", prefixes=".") & filters.me)
async def role_cmd(_, m):
    cfg = get_chat_cfg(m.chat.id)
    role = cfg.get("role") or DEFAULT_ROLE
    await m.reply_text(f"Role:\n\n{role}")

@app.on_message(filters.text | filters.caption)
async def handle_message(client: Client, m):
    if not m.chat:
        return

    cfg = get_chat_cfg(m.chat.id)

    # Auto delete mention (abaikan pesan dari diri sendiri)
    text = (m.text or m.caption or "").strip()
    if text and not m.from_user.is_self and MENTION_REGEX.search(text):
        try:
            await m.delete()
            return
        except Exception:
            # bukan admin / gak punya hak delete
            pass

    if not text:
        return

    # hanya jawab kalau .chat on ATAU user reply ke kamu
    replied_to_me = False
    if m.reply_to_message and m.reply_to_message.from_user:
        replied_to_me = bool(m.reply_to_message.from_user.is_self)

    if not replied_to_me and not cfg.get("enabled", False):
        return

    await client.send_chat_action(m.chat.id, ChatAction.TYPING)

    role = cfg.get("role") or DEFAULT_ROLE

    try:
        answer = await asyncio.wait_for(call_siputzx(text, role), timeout=FAST_TIMEOUT)
    except asyncio.TimeoutError:
        answer = None

    if not answer:
        answer = fallback_reply(text)

    answer = limit_response(answer, MAX_SENTENCES, MAX_CHARS)
    await m.reply_text(answer, disable_web_page_preview=True)

async def _shutdown():
    global _session
    if _session and not _session.closed:
        await _session.close()

def main():
    load_data()
    log.info("Ubot running...")
    app.run()
    # kalau kamu stop pakai KeyboardInterrupt, session akan keburu mati;
    # untuk rapih bisa panggil _shutdown via loop kalau perlu.

if __name__ == "__main__":
    main()
