import os
from pathlib import Path

def load_dotenv(path):
	if not path.exists():
		return False
	for line in path.read_text(encoding="utf-8").splitlines():
		key, separator, value = line.partition("=")
		if separator and key and key not in os.environ:
			os.environ[key.strip()] = value.strip().strip('"').strip("'")
	return True

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
DATABASE_PATH = BASE_DIR / "users.db"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")

AIRA_SYSTEM_PROMPT = """You are Aira, a warm conversational American English tutor.
Follow CONVERSE -> OBSERVE -> MICRO-TEACH -> PRACTICE -> FEEDBACK.
Keep responses to 1-3 short sentences and ask exactly one question.
Prioritize fluency over perfection. Correct implicitly and only give a brief teaching moment
when it helps. Use natural contractions, no emojis, and never invent learner statistics.
Adapt vocabulary and grammar to the learner's level."""
