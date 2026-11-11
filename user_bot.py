import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, Update, User
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, ContextTypes, TypeHandler


SELL_TEXT = "💸USDT ‌ရောင်းမယ်"
BUY_TEXT = "💲USDT ဝယ်မယ်"
ADMIN_TEXT = "💬admin နဲ့ပြောမယ်"
ORDER_TEXT = "🧾 order တင်ထားပါတယ်"
SOCIAL_TEXT = "Social Media မှာ follow လုပ်မယ်"
APP_GUIDE_TEXT = "📖 အသုံးပြုနည်း"
SOCIAL_CALLBACK = "socials"
ADMIN_CALLBACK = "admin_request"
ORDER_CALLBACK = "order_placed"
SET_ADMIN_COMMAND = "setadmin_infinity889K"

ADMIN_WAIT_MESSAGE = (
	"Please wait. Admin is coming soon.\n"
	"ကျေးဇူးပြု၍ ခဏလေးစောင့်ပါ။ Admin မကြာခင် လာပါမယ်။"
)
ORDER_WAIT_MESSAGE = "ပြန်လည်ကြိုဆိုပါတယ် Screenshot ပို့ပြီးခဏလေးစောင့်ပေးပါ"
IMMEDIATE_ATTENTION_PREVIEW = "[Immediate] User requested admin support."
REQUEST_ATTENTION_PREVIEW = "[Attention] User requested admin support."
ATTENTION_PREVIEW = "[Attention] Needs follow-up."

ADMIN_USERNAME = "infinityadmin001"

# Keep URLs configurable via environment variables.
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://blockinfiniti.com")
FACEBOOK_URL = os.getenv("FACEBOOK_URL", "https://www.facebook.com/profile.php?id=61586373337153")
TELEGRAM_CHANNEL_URL = os.getenv("TELEGRAM_CHANNEL_URL", "https://t.me/infinityexchange8")

BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
MESSAGES_DIR = STORAGE_DIR / "messages"
MEDIA_INCOMING_DIR = STORAGE_DIR / "media" / "incoming"
MEDIA_OUTGOING_DIR = STORAGE_DIR / "media" / "outgoing"
CHATS_FILE = STORAGE_DIR / "chats.json"
ADMIN_CONFIG_FILE = STORAGE_DIR / "admin_config.json"

storage_lock = threading.Lock()


FEEDBACK_CALLBACK = "feedback"
FEEDBACK_BUTTON_TEXT = "📝 Feedback "
USDT_BUTTON_TEXT = "💲USDT "
FEEDBACK_REPLY_TEXT = (
	"We are constantly working to improve. We'd love to hear your thoughts and suggestions.\n\n"
	"ကျွန်ုပ်တို့အနေဖြင့် ဝန်ဆောင်မှုများကို စဉ်ဆက်မပြတ် တိုးတက်ကောင်းမွန်အောင် အမြဲကြိုးပမ်းနေပါသည်။ သင်၏အကြံပြုချက်များကို ဝမ်းမြောက်စွာ ကြိုဆိုပါသဖြင့် လွတ်လပ်စွာ မျှဝေပေးစေလိုပါသည်။"
)


logging.basicConfig(
	format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
	level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Load local .env for development; hosted env vars remain primary in production.
load_dotenv()


def ensure_storage_layout() -> None:
	for directory in (STORAGE_DIR, MESSAGES_DIR, MEDIA_INCOMING_DIR, MEDIA_OUTGOING_DIR):
		directory.mkdir(parents=True, exist_ok=True)

	if not CHATS_FILE.exists():
		_write_json(CHATS_FILE, [])


def _read_json(path: Path, default: Any) -> Any:
	if not path.exists():
		return default

	try:
		with path.open("r", encoding="utf-8") as file_handle:
			return json.load(file_handle)
	except (OSError, json.JSONDecodeError):
		logger.warning("Could not read JSON from %s. Falling back to default.", path)
		return default


def _write_json(path: Path, data: Any) -> None:
	temp_path = path.with_suffix(f"{path.suffix}.tmp")
	with temp_path.open("w", encoding="utf-8") as file_handle:
		json.dump(data, file_handle, ensure_ascii=False, indent=2)
	temp_path.replace(path)


def _messages_file(chat_id: int) -> Path:
	return MESSAGES_DIR / f"{chat_id}.json"


def _extension_from_filename(file_name: Optional[str], fallback: str) -> str:
	ext = Path(file_name or "").suffix.lower()
	return ext if ext else fallback


def _iso_datetime(value: Optional[datetime]) -> str:
	if value is None:
		return datetime.now(timezone.utc).isoformat()

	if value.tzinfo is None:
		value = value.replace(tzinfo=timezone.utc)

	return value.astimezone(timezone.utc).isoformat()


def _build_preview(message_record: dict[str, Any]) -> str:
	text = (message_record.get("text") or "").strip()
	if text:
		return text[:120]

	type_to_preview = {
		"photo": "[Photo]",
		"video": "[Video]",
		"document": "[Document]",
		"audio": "[Audio]",
		"voice": "[Voice]",
		"animation": "[Animation]",
		"sticker": "[Sticker]",
		"other": "[Unsupported message]",
	}
	return type_to_preview.get(message_record.get("type", "other"), "[Message]")


def _chat_title_from_message(message: Message) -> str:
	if message.chat.type == "private" and message.from_user is not None:
		return message.from_user.full_name

	if message.chat.title:
		return message.chat.title

	if message.chat.full_name:
		return message.chat.full_name

	return str(message.chat.id)


def _message_type_from_telegram_message(message: Message) -> str:
	if message.photo:
		return "photo"
	if message.video:
		return "video"
	if message.document:
		return "document"
	if message.audio:
		return "audio"
	if message.voice:
		return "voice"
	if message.animation:
		return "animation"
	if message.sticker:
		return "sticker"
	if message.text or message.caption:
		return "text"
	return "other"


def _persist_message(
	chat_id: int,
	title: str,
	username: str,
	chat_type: str,
	message_record: dict[str, Any],
	*,
	incoming: bool,
) -> None:
	with storage_lock:
		messages_path = _messages_file(chat_id)
		messages = _read_json(messages_path, [])
		if not isinstance(messages, list):
			messages = []
		messages.append(message_record)
		_write_json(messages_path, messages)

		chats = _read_json(CHATS_FILE, [])
		if not isinstance(chats, list):
			chats = []

		chat_index = next((idx for idx, chat in enumerate(chats) if chat.get("chat_id") == chat_id), None)
		preview = _build_preview(message_record)
		last_message_at = message_record.get("timestamp")

		if chat_index is None:
			chat_entry = {
				"chat_id": chat_id,
				"title": title,
				"username": username,
				"chat_type": chat_type,
				"last_message_preview": preview,
				"last_message_at": last_message_at,
				"unread_count": 1 if incoming else 0,
			}
			chats.append(chat_entry)
		else:
			chat_entry = chats[chat_index]
			chat_entry["title"] = title or chat_entry.get("title", str(chat_id))
			if username:
				chat_entry["username"] = username
			elif "username" not in chat_entry:
				chat_entry["username"] = ""
			chat_entry["chat_type"] = chat_type
			chat_entry["last_message_preview"] = preview
			chat_entry["last_message_at"] = last_message_at
			if incoming:
				chat_entry["unread_count"] = int(chat_entry.get("unread_count", 0)) + 1

		_write_json(CHATS_FILE, chats)


def _read_admin_chat_id() -> Optional[int]:
	env_value = os.getenv("ADMIN_CHAT_ID", "").strip()
	if env_value:
		try:
			return int(env_value)
		except ValueError:
			logger.warning("Invalid ADMIN_CHAT_ID in environment; value must be an integer.")

	with storage_lock:
		config_payload = _read_json(ADMIN_CONFIG_FILE, {})

	if not isinstance(config_payload, dict):
		return None

	raw_chat_id = config_payload.get("admin_chat_id")
	if raw_chat_id is None:
		return None

	try:
		return int(raw_chat_id)
	except (TypeError, ValueError):
		return None


def _save_admin_chat_id(admin_chat_id: int, set_by_user_id: Optional[int], set_by_name: str) -> None:
	payload = {
		"admin_chat_id": admin_chat_id,
		"set_by_user_id": set_by_user_id,
		"set_by_name": set_by_name,
		"updated_at": _iso_datetime(None),
	}

	with storage_lock:
		_write_json(ADMIN_CONFIG_FILE, payload)


def _pin_chat_attention(request_context: dict[str, Any], reason: str) -> None:
	chat_id = int(request_context["chat_id"])
	attention_time = _iso_datetime(None)

	with storage_lock:
		chats = _read_json(CHATS_FILE, [])
		if not isinstance(chats, list):
			chats = []

		chat_index = next((idx for idx, chat in enumerate(chats) if chat.get("chat_id") == chat_id), None)
		if chat_index is None:
			chat_entry = {
				"chat_id": chat_id,
				"title": request_context["title"],
				"username": request_context["username"],
				"chat_type": request_context["chat_type"],
				"last_message_preview": REQUEST_ATTENTION_PREVIEW,
				"last_message_at": attention_time,
				"unread_count": 1,
			}
			chats.append(chat_entry)
		else:
			chat_entry = chats[chat_index]
			chat_entry["title"] = request_context["title"] or chat_entry.get("title", str(chat_id))
			if request_context["username"]:
				chat_entry["username"] = request_context["username"]
			elif "username" not in chat_entry:
				chat_entry["username"] = ""
			chat_entry["chat_type"] = request_context["chat_type"]
			chat_entry["last_message_preview"] = REQUEST_ATTENTION_PREVIEW
			chat_entry["last_message_at"] = attention_time
			chat_entry["unread_count"] = max(1, int(chat_entry.get("unread_count", 0)))

		chat_entry["is_pinned"] = True
		chat_entry["attention_level"] = "attention"
		chat_entry["attention_reason"] = reason
		chat_entry["attention_requested_at"] = attention_time

		_write_json(CHATS_FILE, chats)


async def _notify_admin_for_request(
	context: ContextTypes.DEFAULT_TYPE,
	request_context: dict[str, Any],
	*,
	request_source: str,
) -> bool:
	admin_chat_id = _read_admin_chat_id()
	if admin_chat_id is None:
		logger.warning("Admin notification chat is not configured. Run /%s to set it.", SET_ADMIN_COMMAND)
		return False

	username_line = f"@{request_context['requester_username']}" if request_context["requester_username"] else "N/A"
	notification_text = (
		"ATTENTION REQUEST\n"
		"A user requested to talk to admin.\n\n"
		f"Name: {request_context['requester_name']}\n"
		f"Username: {username_line}\n"
		f"Chat ID: {request_context['chat_id']}\n"
		f"Chat Type: {request_context['chat_type']}\n"
		f"Source: {request_source}\n\n"
		"Please respond as soon as possible."
	)

	try:
		sent_message = await context.bot.send_message(chat_id=admin_chat_id, text=notification_text)
		_store_outgoing_telegram_message(sent_message)
		return True
	except Exception:
		logger.exception("Failed to send admin attention notification to chat_id=%s", admin_chat_id)
		return False


async def _download_incoming_media(
	message: Message,
	context: ContextTypes.DEFAULT_TYPE,
) -> tuple[str, Optional[dict[str, Any]]]:
	message_type = _message_type_from_telegram_message(message)
	if message_type not in {"photo", "video", "document", "audio", "voice", "animation", "sticker"}:
		return message_type, None

	chat_id = message.chat.id
	message_id = message.message_id

	try:
		if message.photo:
			photo = message.photo[-1]
			file_name = f"{chat_id}_{message_id}_{photo.file_unique_id}.jpg"
			storage_path = MEDIA_INCOMING_DIR / file_name
			telegram_file = await context.bot.get_file(photo.file_id)
			await telegram_file.download_to_drive(custom_path=str(storage_path))
			return "photo", {
				"file_id": photo.file_id,
				"file_unique_id": photo.file_unique_id,
				"mime_type": "image/jpeg",
				"file_size": photo.file_size,
				"file_name": file_name,
				"storage_path": f"media/incoming/{file_name}",
			}

		if message.video:
			video = message.video
			ext = _extension_from_filename(video.file_name, ".mp4")
			file_name = f"{chat_id}_{message_id}_{video.file_unique_id}{ext}"
			storage_path = MEDIA_INCOMING_DIR / file_name
			telegram_file = await context.bot.get_file(video.file_id)
			await telegram_file.download_to_drive(custom_path=str(storage_path))
			return "video", {
				"file_id": video.file_id,
				"file_unique_id": video.file_unique_id,
				"mime_type": video.mime_type or "video/mp4",
				"file_size": video.file_size,
				"file_name": video.file_name or file_name,
				"storage_path": f"media/incoming/{file_name}",
			}

		if message.document:
			document = message.document
			ext = _extension_from_filename(document.file_name, ".bin")
			file_name = f"{chat_id}_{message_id}_{document.file_unique_id}{ext}"
			storage_path = MEDIA_INCOMING_DIR / file_name
			telegram_file = await context.bot.get_file(document.file_id)
			await telegram_file.download_to_drive(custom_path=str(storage_path))
			return "document", {
				"file_id": document.file_id,
				"file_unique_id": document.file_unique_id,
				"mime_type": document.mime_type or "application/octet-stream",
				"file_size": document.file_size,
				"file_name": document.file_name or file_name,
				"storage_path": f"media/incoming/{file_name}",
			}

		if message.audio:
			audio = message.audio
			ext = _extension_from_filename(audio.file_name, ".mp3")
			file_name = f"{chat_id}_{message_id}_{audio.file_unique_id}{ext}"
			storage_path = MEDIA_INCOMING_DIR / file_name
			telegram_file = await context.bot.get_file(audio.file_id)
			await telegram_file.download_to_drive(custom_path=str(storage_path))
			return "audio", {
				"file_id": audio.file_id,
				"file_unique_id": audio.file_unique_id,
				"mime_type": audio.mime_type or "audio/mpeg",
				"file_size": audio.file_size,
				"file_name": audio.file_name or file_name,
				"storage_path": f"media/incoming/{file_name}",
			}

		if message.voice:
			voice = message.voice
			file_name = f"{chat_id}_{message_id}_{voice.file_unique_id}.ogg"
			storage_path = MEDIA_INCOMING_DIR / file_name
			telegram_file = await context.bot.get_file(voice.file_id)
			await telegram_file.download_to_drive(custom_path=str(storage_path))
			return "voice", {
				"file_id": voice.file_id,
				"file_unique_id": voice.file_unique_id,
				"mime_type": voice.mime_type or "audio/ogg",
				"file_size": voice.file_size,
				"file_name": file_name,
				"storage_path": f"media/incoming/{file_name}",
			}

		if message.animation:
			animation = message.animation
			ext = _extension_from_filename(animation.file_name, ".mp4")
			file_name = f"{chat_id}_{message_id}_{animation.file_unique_id}{ext}"
			storage_path = MEDIA_INCOMING_DIR / file_name
			telegram_file = await context.bot.get_file(animation.file_id)
			await telegram_file.download_to_drive(custom_path=str(storage_path))
			return "animation", {
				"file_id": animation.file_id,
				"file_unique_id": animation.file_unique_id,
				"mime_type": animation.mime_type or "video/mp4",
				"file_size": animation.file_size,
				"file_name": animation.file_name or file_name,
				"storage_path": f"media/incoming/{file_name}",
			}

		if message.sticker:
			sticker = message.sticker
			ext = ".webm" if sticker.is_video else ".webp"
			file_name = f"{chat_id}_{message_id}_{sticker.file_unique_id}{ext}"
			storage_path = MEDIA_INCOMING_DIR / file_name
			telegram_file = await context.bot.get_file(sticker.file_id)
			await telegram_file.download_to_drive(custom_path=str(storage_path))
			return "sticker", {
				"file_id": sticker.file_id,
				"file_unique_id": sticker.file_unique_id,
				"mime_type": "video/webm" if sticker.is_video else "image/webp",
				"file_size": sticker.file_size,
				"file_name": file_name,
				"storage_path": f"media/incoming/{file_name}",
			}
	except Exception:
		logger.exception("Unable to download incoming media for message %s", message.message_id)

	return message_type, None


async def _store_incoming_telegram_message(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
	if message.chat is None:
		return

	message_type, media = await _download_incoming_media(message, context)
	text_value = (message.text or message.caption or "").strip()

	record = {
		"message_id": message.message_id,
		"chat_id": message.chat.id,
		"direction": "incoming",
		"sender_id": message.from_user.id if message.from_user else None,
		"sender_name": message.from_user.full_name if message.from_user else "Unknown",
		"type": message_type,
		"text": text_value,
		"timestamp": _iso_datetime(message.date),
		"media": media,
	}

	_persist_message(
		chat_id=message.chat.id,
		title=_chat_title_from_message(message),
		username=message.from_user.username if message.from_user and message.from_user.username else "",
		chat_type=message.chat.type,
		message_record=record,
		incoming=True,
	)


def _store_outgoing_telegram_message(message: Message) -> None:
	if message.chat is None:
		return

	record = {
		"message_id": message.message_id,
		"chat_id": message.chat.id,
		"direction": "outgoing",
		"sender_id": message.from_user.id if message.from_user else None,
		"sender_name": "Bot",
		"type": _message_type_from_telegram_message(message),
		"text": (message.text or message.caption or "").strip(),
		"timestamp": _iso_datetime(message.date),
		"media": None,
	}

	chat_title = message.chat.title or message.chat.full_name or str(message.chat.id)
	chat_username = message.chat.username or ""

	_persist_message(
		chat_id=message.chat.id,
		title=chat_title,
		username=chat_username,
		chat_type=message.chat.type,
		message_record=record,
		incoming=False,
	)


def build_socials_inline_keyboard() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(
		[
			[InlineKeyboardButton("Facebook", url=FACEBOOK_URL)],
			[InlineKeyboardButton("Telegram Channel", url=TELEGRAM_CHANNEL_URL)],
		]
	)


def _load_auto_start_idle_seconds() -> Optional[float]:
	raw_value = os.getenv("AUTO_START_IDLE_HOURS", "0.25").strip()
	if not raw_value:
		return None

	try:
		hours = float(raw_value)
	except ValueError:
		logger.warning("Invalid AUTO_START_IDLE_HOURS=%s. Using default 24 hours.", raw_value)
		hours = 24.0

	if hours <= 0:
		return None

	return hours * 3600


AUTO_START_IDLE_SECONDS = _load_auto_start_idle_seconds()


def _load_float_env(name: str, default_value: float) -> float:
	raw_value = os.getenv(name, str(default_value)).strip()
	if not raw_value:
		return default_value

	try:
		value = float(raw_value)
	except ValueError:
		logger.warning("Invalid %s=%s. Using default %s.", name, raw_value, default_value)
		return default_value

	if value <= 0:
		logger.warning("Non-positive %s=%s. Using default %s.", name, raw_value, default_value)
		return default_value

	return value


def _load_int_env(name: str, default_value: int) -> int:
	raw_value = os.getenv(name, str(default_value)).strip()
	if not raw_value:
		return default_value

	try:
		return int(raw_value)
	except ValueError:
		logger.warning("Invalid %s=%s. Using default %s.", name, raw_value, default_value)
		return default_value


TELEGRAM_CONNECT_TIMEOUT_SECONDS = _load_float_env("TELEGRAM_CONNECT_TIMEOUT_SECONDS", 30.0)
TELEGRAM_READ_TIMEOUT_SECONDS = _load_float_env("TELEGRAM_READ_TIMEOUT_SECONDS", 30.0)
TELEGRAM_WRITE_TIMEOUT_SECONDS = _load_float_env("TELEGRAM_WRITE_TIMEOUT_SECONDS", 30.0)
TELEGRAM_POOL_TIMEOUT_SECONDS = _load_float_env("TELEGRAM_POOL_TIMEOUT_SECONDS", 30.0)
TELEGRAM_STARTUP_RETRY_DELAY_SECONDS = _load_float_env("TELEGRAM_STARTUP_RETRY_DELAY_SECONDS", 5.0)
TELEGRAM_STARTUP_RETRIES = _load_int_env("TELEGRAM_STARTUP_RETRIES", -1)


def _extract_message(update: Update) -> Optional[Message]:
	return update.message or update.business_message


def _is_message_from_admin(message: Message) -> bool:
	sender = message.from_user
	if sender is None or not sender.username:
		return False

	return sender.username.lstrip("@").lower() == ADMIN_USERNAME.lstrip("@").lower()


def _parse_timestamp(value: Any) -> Optional[datetime]:
	if not isinstance(value, str):
		return None

	normalized_value = value.replace("Z", "+00:00")
	try:
		parsed = datetime.fromisoformat(normalized_value)
	except ValueError:
		return None

	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=timezone.utc)

	return parsed.astimezone(timezone.utc)


def _message_datetime_utc(message: Message) -> datetime:
	if message.date.tzinfo is None:
		return message.date.replace(tzinfo=timezone.utc)

	return message.date.astimezone(timezone.utc)


def _should_auto_start(chat_id: int, incoming_message: Message) -> bool:
	with storage_lock:
		messages = _read_json(_messages_file(chat_id), [])

	if not isinstance(messages, list):
		return True

	has_bot_reply = any(message.get("direction") == "outgoing" for message in messages)
	if not has_bot_reply:
		return True

	if AUTO_START_IDLE_SECONDS is None:
		return False

	last_activity_at: Optional[datetime] = None
	for message_record in reversed(messages):
		parsed = _parse_timestamp(message_record.get("timestamp"))
		if parsed is not None:
			last_activity_at = parsed
			break

	if last_activity_at is None:
		return False

	current_message_at = _message_datetime_utc(incoming_message)
	idle_seconds = (current_message_at - last_activity_at).total_seconds()
	return idle_seconds >= AUTO_START_IDLE_SECONDS


def _command_matches(text: str, command_name: str) -> bool:
	if not text.startswith("/"):
		return False

	head = text.split(maxsplit=1)[0]
	raw_command = head[1:]
	base_command = raw_command.split("@", maxsplit=1)[0]
	return base_command.lower() == command_name.lower()


async def _send_message_compatible(
	message: Message,
	text: str,
	*,
	reply_markup: Optional[InlineKeyboardMarkup | ReplyKeyboardMarkup] = None,
) -> Message:
	kwargs: dict[str, Any] = {}
	if message.business_connection_id:
		kwargs["business_connection_id"] = message.business_connection_id

	sent_message = await message.get_bot().send_message(
		chat_id=message.chat.id,
		text=text,
		reply_markup=reply_markup,
		**kwargs,
	)
	_store_outgoing_telegram_message(sent_message)
	return sent_message


def _build_attention_request_context_from_message(
	message: Message,
	requester_override: Optional[User] = None,
) -> dict[str, Any]:
	chat = message.chat
	requester = requester_override or message.from_user

	if chat.type == "private" and requester is not None:
		title = requester.full_name
	else:
		title = chat.title or chat.full_name or str(chat.id)

	username = ""
	if chat.type == "private" and requester is not None and requester.username:
		username = requester.username
	elif chat.username:
		username = chat.username

	return {
		"chat_id": chat.id,
		"chat_type": chat.type,
		"title": title,
		"username": username,
		"requester_name": requester.full_name if requester is not None else title,
		"requester_username": requester.username if requester is not None and requester.username else "",
		"requester_id": requester.id if requester is not None else None,
	}


async def _queue_admin_attention_from_message(
	message: Message,
	context: ContextTypes.DEFAULT_TYPE,
	*,
	request_source: str,
	requester_override: Optional[User] = None,
) -> None:
	request_context = _build_attention_request_context_from_message(
		message,
		requester_override=requester_override,
	)
	_pin_chat_attention(request_context, reason="User requested admin support")
	await _notify_admin_for_request(context, request_context, request_source=request_source)


def _build_business_safe_reply_keyboard() -> ReplyKeyboardMarkup:
	return ReplyKeyboardMarkup(
		keyboard=[
			[KeyboardButton(SELL_TEXT), KeyboardButton(BUY_TEXT)],
			[KeyboardButton(ADMIN_TEXT)],
			[KeyboardButton(ORDER_TEXT)],
			[KeyboardButton(SOCIAL_TEXT)],
			[KeyboardButton(APP_GUIDE_TEXT)],
		],
		resize_keyboard=True,
		is_persistent=True,
	)


def _build_business_safe_start_inline_keyboard() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(
		[
			[
				InlineKeyboardButton(FEEDBACK_BUTTON_TEXT, callback_data=FEEDBACK_CALLBACK),
				InlineKeyboardButton(USDT_BUTTON_TEXT, url=MINI_APP_URL),
			],
			[InlineKeyboardButton(ADMIN_TEXT, callback_data=ADMIN_CALLBACK)],
			[InlineKeyboardButton(ORDER_TEXT, callback_data=ORDER_CALLBACK)],
			[InlineKeyboardButton(SOCIAL_TEXT, callback_data=SOCIAL_CALLBACK)],
		]
	)


def _build_mini_app_link_keyboard() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(
		[
			[InlineKeyboardButton("Open Mini App", url=MINI_APP_URL)],
		]
	)


async def _send_start_messages(message: Message) -> None:
	start_keyboard = _build_business_safe_start_inline_keyboard()

	await _send_message_compatible(
		message,
		"Mingalarpar 🙏 Welcome to BLOCK INFINITI.\nမင်္ဂလာပါ၊ BLOCK INFINITI မှကြိုဆိုပါတယ်။",
		reply_markup=start_keyboard,
	)



async def _set_admin_notification_chat_for_message(
	message: Message,
) -> None:
	if message.from_user is None:
		await _send_message_compatible(message, "Unable to set admin chat from this sender.")
		return

	_save_admin_chat_id(
		admin_chat_id=message.chat.id,
		set_by_user_id=message.from_user.id,
		set_by_name=message.from_user.full_name,
	)

	note = (
		"Admin notification chat is now configured.\n"
		f"Command: /{SET_ADMIN_COMMAND}\n"
		f"Target Chat ID: {message.chat.id}\n"
		"New Talk to Admin requests will be pinned as immediate attention and notified here."
	)
	await _send_message_compatible(message, note)


async def _handle_menu_text(
	message: Message,
	context: ContextTypes.DEFAULT_TYPE,
	text: str,
) -> None:
	if _command_matches(text, "start"):
		await _send_start_messages(message)
		return

	if _command_matches(text, SET_ADMIN_COMMAND):
		await _set_admin_notification_chat_for_message(message)
		return

	if text in {SELL_TEXT, BUY_TEXT}:
		await _send_message_compatible(
			message,
			"Tap below to open the Mini App.",
			reply_markup=_build_mini_app_link_keyboard(),
		)
		return

	if text == ADMIN_TEXT:
		await _send_message_compatible(message, ADMIN_WAIT_MESSAGE)
		await _queue_admin_attention_from_message(message, context, request_source="menu_text")
		return

	if text == ORDER_TEXT:
		await _send_message_compatible(message, ORDER_WAIT_MESSAGE)
		await _queue_admin_attention_from_message(message, context, request_source="order_placed")
		return

	if text == SOCIAL_TEXT:
		await _send_message_compatible(
			message,
			"rate နဲ့ update news တွေ အချိန်နှင့်တပြေးညီ သိရှိနိုင်ရန် channel နဲ့ page လေးတွေကို join ထားပေးပါ",
			reply_markup=build_socials_inline_keyboard(),
		)
		return

	if text == APP_GUIDE_TEXT:
		await _send_message_compatible(
			message,
			"📖 အသုံးပြုနည်း\n\n"
			"1) Buy/Sell button ကိုနှိပ်ပြီး Mini App ထဲဝင်ပါ။\n"
			f"2) အရောင်းအဝယ်မလုပ်ခင် \"{ADMIN_TEXT}\" ကိုနှိပ်ပြီး username တစ်ခုလုပ်ပေးပါလို့ admin ကိုပြောပါ။\n"
			"3) အကူအညီလိုရင် admin ကို တိုက်ရိုက်ဆက်သွယ်ပါ။",
		)


async def _handle_callback_query(update: Update) -> None:
	query = update.callback_query
	if query is None or query.message is None:
		return

	await query.answer()

	if query.data == FEEDBACK_CALLBACK:
		await _send_message_compatible(query.message, FEEDBACK_REPLY_TEXT)
		return

	if query.data == SOCIAL_CALLBACK:
		await _send_message_compatible(
			query.message,
			"rate နဲ့ update news တွေ အချိန်နှင့်တပြေးညီ သိရှိနိုင်ရန် channel နဲ့ page လေးတွေကို join ထားပေးပါ",
			reply_markup=build_socials_inline_keyboard(),
		)
		return

	if query.data == ADMIN_CALLBACK:
		await _send_message_compatible(query.message, ADMIN_WAIT_MESSAGE)

	if query.data == ORDER_CALLBACK:
		await _send_message_compatible(query.message, ORDER_WAIT_MESSAGE)


async def handle_any_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
	if update.callback_query is not None:
		await _handle_callback_query(update)
		if update.callback_query.data == ADMIN_CALLBACK and update.callback_query.message is not None:
			await _queue_admin_attention_from_message(
				update.callback_query.message,
				context,
				request_source="inline_button",
				requester_override=update.callback_query.from_user,
			)
		if update.callback_query.data == ORDER_CALLBACK and update.callback_query.message is not None:
			await _queue_admin_attention_from_message(
				update.callback_query.message,
				context,
				request_source="order_placed",
				requester_override=update.callback_query.from_user,
			)
		return

	message = _extract_message(update)
	if message is None:
		return

	if _is_message_from_admin(message):
		_store_outgoing_telegram_message(message)
		return

	text_value = (message.text or "").strip()
	should_auto_start = (
		message.chat.type == "private"
		and _should_auto_start(message.chat.id, message)
		and not _command_matches(text_value, SET_ADMIN_COMMAND)
	)

	await _store_incoming_telegram_message(message, context)

	if should_auto_start:
		await _send_start_messages(message)
		return

	if not text_value:
		return

	await _handle_menu_text(message, context, text_value)


def _build_user_application() -> Application:
	token = os.getenv("TELEGRAM_BOT_TOKEN")
	if not token:
		raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable.")

	telegram_app = (
		Application.builder()
		.token(token)
		.connect_timeout(TELEGRAM_CONNECT_TIMEOUT_SECONDS)
		.read_timeout(TELEGRAM_READ_TIMEOUT_SECONDS)
		.write_timeout(TELEGRAM_WRITE_TIMEOUT_SECONDS)
		.pool_timeout(TELEGRAM_POOL_TIMEOUT_SECONDS)
		.get_updates_connect_timeout(TELEGRAM_CONNECT_TIMEOUT_SECONDS)
		.get_updates_read_timeout(TELEGRAM_READ_TIMEOUT_SECONDS)
		.get_updates_write_timeout(TELEGRAM_WRITE_TIMEOUT_SECONDS)
		.get_updates_pool_timeout(TELEGRAM_POOL_TIMEOUT_SECONDS)
		.build()
	)
	telegram_app.add_handler(TypeHandler(Update, handle_any_update), group=0)
	return telegram_app


def run_user_bot_polling() -> None:
	attempt = 0
	while True:
		attempt += 1
		loop = asyncio.new_event_loop()
		asyncio.set_event_loop(loop)
		telegram_app = _build_user_application()
		logger.info("Starting business-compatible user bot polling loop (attempt %s).", attempt)

		try:
			telegram_app.run_polling(
				drop_pending_updates=False,
				allowed_updates=Update.ALL_TYPES,
				bootstrap_retries=-1,
				stop_signals=None,
			)
			return
		except (TimedOut, NetworkError) as exc:
			can_retry = TELEGRAM_STARTUP_RETRIES < 0 or attempt < TELEGRAM_STARTUP_RETRIES
			if not can_retry:
				logger.exception(
					"Telegram startup failed after %s attempts due to network errors.",
					attempt,
				)
				raise

			logger.warning(
				"Telegram startup network error (%s). Retrying in %.1f seconds.",
				exc.__class__.__name__,
				TELEGRAM_STARTUP_RETRY_DELAY_SECONDS,
			)
			time.sleep(TELEGRAM_STARTUP_RETRY_DELAY_SECONDS)
		finally:
			if not loop.is_closed():
				loop.close()


def main() -> None:
	ensure_storage_layout()
	run_user_bot_polling()


if __name__ == "__main__":
	main()
