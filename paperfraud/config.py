"""Global configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(env_file: Path | None = None) -> None:
    """Load .env file into os.environ (no-op if file doesn't exist).

    Handles common edge cases from non-technical users:
      - Quoted values: KEY="value" or KEY='value'
      - export prefix: export KEY=value
      - Inline comments: KEY=value  # description
      - Equals in value: KEY=val=ue  (split on first '=' only)
      - Blank lines and # comment lines
    """
    if env_file is None:
        # Default: look for .env in repo root (2 levels up from this file)
        env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return

    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Handle "export KEY=value"
            if line.startswith("export "):
                line = line[7:].lstrip()

            if "=" not in line:
                continue

            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()

            # Strip matching single or double quotes
            if len(val) >= 2:
                if (val.startswith('"') and val.endswith('"')) or \
                   (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]

            # Strip inline comment (only if # is preceded by space, to avoid
            # breaking API keys that contain #)
            comment_idx = val.find(" #")
            if comment_idx != -1:
                val = val[:comment_idx].rstrip()

            if key and key not in os.environ:
                os.environ[key] = val


@dataclass
class Config:
    """Runtime configuration for paperfraud."""

    # PDF acquisition
    scidb_domain: str = ""
    unpaywall_email: str = ""

    # Parser
    grobid_url: str = ""

    # LLM
    llm_provider: str = "noop"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_model: str = "claude-sonnet-4-6"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    deepseek_model: str = "deepseek-chat"

    # LLM qualitative review
    llm_review: bool = False

    # Execution
    timeout: int = 600
    no_external: bool = False
    output_format: str = "terminal"

    # Image output
    image_output_dir: str = "paperfraud_images"
    skip_images: bool = True
    max_pages: int = 0

    # Persistent output
    output_dir: str = ""

    # Streamlit
    launch_web: bool = False

    # External data
    data_file: str = ""
