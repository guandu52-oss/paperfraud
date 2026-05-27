"""Global configuration."""

from dataclasses import dataclass, field


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
