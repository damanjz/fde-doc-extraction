"""Settings from environment variables (no pydantic-settings: only the approved packages are installed)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "fusion27b:iq3"))
    num_ctx: int = field(default_factory=lambda: int(_env("LLM_NUM_CTX", "8192")))
    num_predict: int = field(default_factory=lambda: int(_env("LLM_NUM_PREDICT", "1800")))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "300")))
    db_path: str = field(default_factory=lambda: _env("DOCX_DB", str(ROOT / "app.db")))
    upload_dir: str = field(default_factory=lambda: _env("DOCX_UPLOADS", str(ROOT / "uploads")))
    ocr_low_conf: float = field(default_factory=lambda: float(_env("OCR_LOW_CONF", "0.70")))
    max_prompt_chars: int = 9000
    buyer_name: str = field(default_factory=lambda: _env("BUYER_NAME", "Larkfield Coffee Co."))
    buyer_gstin: str = field(default_factory=lambda: _env("BUYER_GSTIN", "36AAKCL4417Q1ZW"))
    # prefix of the buyer's own purchase-order numbers; an invoice number that contains it is a PO number
    po_marker: str = field(default_factory=lambda: _env("PO_MARKER", "LCC/PO"))


settings = Settings()
