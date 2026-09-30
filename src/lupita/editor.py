"""Genera y valida un resumen editorial mediante la API local de Ollama."""

import json
from pathlib import Path
import re

from .http import RequestError, request
from .models import Article, Editorial, clean_text


CATEGORIES = (
    "Ambiente", "Educación", "Seguridad", "Tecnología", "Finanzas",
    "Política", "Deportes", "Mercado", "Salud", "Sociedad", "Transporte",
)
EXCLUDED_CATEGORY = "Excluir"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 1600},
        "category": {"type": "string", "enum": [*CATEGORIES, EXCLUDED_CATEGORY]},
        "topics": {
            "type": "array", "minItems": 1, "maxItems": 5, "uniqueItems": True,
            "items": {"type": "string", "minLength": 2, "maxLength": 60},
        },
    },
    "required": ["summary", "category", "topics"],
}
SYSTEM = Path(__file__).with_name("editor_instructions.md").read_text(encoding="utf-8")


def word_tokens(value: str) -> list[str]:
    return re.findall(r"\w+", value.casefold())


def validate(data: dict, article: Article) -> Editorial | None:
    if not isinstance(data, dict) or set(data) != set(SCHEMA["required"]):
        raise ValueError("Ollama devolvió campos incompletos o inesperados")
    if data["category"] == EXCLUDED_CATEGORY:
        return None
    if not isinstance(data["summary"], str) or not 1 <= len(data["summary"].strip()) <= 1600:
        raise ValueError("Ollama devolvió un resumen inválido")
    if data["category"] not in CATEGORIES:
        raise ValueError("Ollama devolvió una categoría fuera del catálogo")
    topics = data["topics"]
    if not isinstance(topics, list) or not 1 <= len(topics) <= 5 or any(
        not isinstance(topic, str) or not 2 <= len(topic.strip()) <= 60 for topic in topics
    ):
        raise ValueError("Ollama devolvió temas inválidos")
    if len({clean_text(topic).casefold() for topic in topics}) != len(topics):
        raise ValueError("Ollama devolvió temas repetidos")
    summary = clean_text(data["summary"])
    if len(summary.split()) > 130:
        raise ValueError("El resumen supera las 130 palabras")
    # Un control práctico de copia literal; no es una evaluación jurídica.
    source_words = word_tokens(article.summary + " " + article.body)
    source_spans = {tuple(source_words[i:i + 12]) for i in range(len(source_words) - 11)}
    result_words = word_tokens(summary)
    if any(tuple(result_words[i:i + 12]) in source_spans for i in range(len(result_words) - 11)):
        raise ValueError("El resumen reproduce una secuencia de 12 palabras de la fuente")
    return Editorial(article.title, summary, data["category"], [clean_text(t) for t in topics])


class OllamaEditor:
    def __init__(self, base_url: str, model: str, timeout: int = 180):
        self.url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.timeout = timeout

    def generate(self, article: Article) -> Editorial | None:
        reference = article.to_dict()
        reference["body"] = reference["body"][:18000]
        reference["summary"] = reference["summary"][:3000]
        payload = {
            "model": self.model,
            "stream": False,
            "format": SCHEMA,
            "options": {"temperature": 0.2},
            "messages": [
                {"role": "system", "content": SYSTEM + "\nEsquema: " + json.dumps(SCHEMA, ensure_ascii=False)},
                {"role": "user", "content": json.dumps(reference, ensure_ascii=False)},
            ],
        }
        try:
            raw = request(self.url, payload=payload, timeout=self.timeout)
        except RequestError as error:
            raise RequestError(f"Error al consultar Ollama (OLLAMA_BASE_URL): {error}") from error
        response = json.loads(raw)
        if not isinstance(response, dict) or not response.get("done"):
            raise ValueError("Ollama no completó la generación")
        message = response.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("Ollama devolvió una respuesta sin contenido")
        return validate(json.loads(message["content"]), article)
