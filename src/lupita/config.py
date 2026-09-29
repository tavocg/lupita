from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


def load_env(path: Path = Path(".env")) -> None:
    """Lee KEY=value sin ejecutar código; las variables del entorno prevalecen."""
    if not path.exists():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key.isidentifier():
            raise ValueError(f"{path}:{number}: se esperaba KEY=value")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"{path}:{number}: comillas sin cerrar")
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    ollama_url: str
    model: str
    content_dir: Path
    state_dir: Path
    timeout: int
    draft: bool

    @classmethod
    def from_env(cls):
        url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.query or parts.fragment:
            raise ValueError("OLLAMA_BASE_URL debe ser una URL HTTP(S) base")
        model = os.getenv("OLLAMA_MODEL", "").strip()
        if not model:
            raise ValueError("Configura OLLAMA_MODEL con el nombre de un modelo instalado en tu Ollama")
        timeout = int(os.getenv("OLLAMA_TIMEOUT", "180"))
        if timeout < 1:
            raise ValueError("OLLAMA_TIMEOUT debe ser mayor que cero")
        draft = os.getenv("NEWS_DRAFT", "true").lower()
        if draft not in {"true", "false"}:
            raise ValueError("NEWS_DRAFT debe ser true o false")
        return cls(
            url, model, Path(os.getenv("CONTENT_DIR", "content")),
            Path(os.getenv("STATE_DIR", ".pipeline")), timeout, draft == "true",
        )
