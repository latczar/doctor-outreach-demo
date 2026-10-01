"""Settings from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=value lines from .env. Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    campaign_path: Path
    seed_dir: Path
    prompts_dir: Path
    outbox_dir: Path
    exports_dir: Path
    llm_provider: str
    ollama_url: str
    ollama_model: str
    anthropic_model: str
    email_sender: str
    smtp_host: str
    smtp_port: int
    allowed_recipient_suffixes: tuple[str, ...]
    alert_webhook_url: str
    demo_online: bool = False  # the hosted copy: writable files live in /tmp, runs finish in one go
    snapshot_path: Path = ROOT / "data" / "demo-snapshot.db"  # real AI drafts, loaded on a fresh start
    demo_inbox: str = ""  # when set, every email goes here instead of the made-up doctor's address
    resend_api_key: str = ""
    resend_from: str = "onboarding@resend.dev"  # Resend's test sender, until you add your own domain


def get_settings() -> Settings:
    load_dotenv()
    env = os.environ.get
    suffixes = env("ALLOWED_RECIPIENT_SUFFIXES", ".example,.test")
    # Vercel sets VERCEL=1. Its servers only allow writing to /tmp, and they can't reach a local AI model.
    online = bool(env("VERCEL")) or env("DEMO_ONLINE", "").lower() in ("1", "true", "yes")
    writable = Path("/tmp") if online else ROOT
    return Settings(
        db_path=Path(env("DATABASE_PATH", str(writable / "data" / "outreach.db"))),
        campaign_path=Path(env("CAMPAIGN_PATH", str(ROOT / "config" / "campaign.json"))),
        seed_dir=ROOT / "data" / "seed",
        prompts_dir=ROOT / "prompts" / "v1",
        outbox_dir=Path(env("OUTBOX_DIR", str(writable / "outbox"))),
        exports_dir=ROOT / "exports",
        llm_provider=env("LLM_PROVIDER", "fake").strip().lower(),
        ollama_url=env("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=env("OLLAMA_MODEL", "qwen2.5:7b-instruct"),
        anthropic_model=env("ANTHROPIC_MODEL", "claude-opus-5-5"),
        # Real email is for your laptop only. The public copy always saves to the outbox folder, so
        # nobody visiting the link can send anything.
        email_sender="outbox" if online else env("EMAIL_SENDER", "outbox").strip().lower(),
        smtp_host=env("SMTP_HOST", "localhost"),
        smtp_port=int(env("SMTP_PORT", "1025")),
        allowed_recipient_suffixes=tuple(
            s.strip().lower() for s in suffixes.split(",") if s.strip()
        ),
        alert_webhook_url=env("ALERT_WEBHOOK_URL", "").strip(),
        demo_online=online,
        demo_inbox="" if online else env("DEMO_INBOX", "").strip(),
        resend_api_key="" if online else env("RESEND_API_KEY", "").strip(),
        resend_from=env("RESEND_FROM", "onboarding@resend.dev").strip(),
    )
