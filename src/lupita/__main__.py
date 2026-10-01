import argparse
import json
import logging
import os
from pathlib import Path
import signal
import sys
from threading import Event
from xml.etree.ElementTree import ParseError

from .config import Config, load_env
from .date_window import DateWindow
from .editor import CATEGORIES, OllamaEditor
from .git_workflow import run_remote
from .http import RequestError
from .images import search_recent
from .models import canonical_url
from .news_index import read_index, write_index
from .scrapers import (
    delfino, elfinanciero, elmundo, nacion, ncrnoticias,
    observador, repretel, semanario, teletica,
)
from .storage import destination, known_urls, render
from .workflow import process, stage


LOG = logging.getLogger("lupita")
SCRAPERS = {
    "nacion": nacion, "delfino": delfino, "semanario": semanario, "teletica": teletica,
    "elfinanciero": elfinanciero, "observador": observador,
    "ncrnoticias": ncrnoticias, "elmundo": elmundo, "repretel": repretel,
}


def run(articles, editor, config, *, limit: int | None = None, dry_run: bool = False) -> dict:
    if not dry_run:
        articles = list(articles)
        imported = stage(articles, config, limit=limit)
        totals = process(editor, config, limit=limit,
                         urls={canonical_url(article.source_url) for article in articles})
        totals["duplicates"] += imported["duplicates"]
        totals["failed"] += imported["failed"]
        return totals
    totals = {"written": 0, "previewed": 0, "duplicates": 0, "excluded": 0, "failed": 0}
    seen = known_urls(config.content_dir)
    attempted = 0
    for article in articles:
        url = canonical_url(article.source_url)
        if url in seen or os.path.lexists(destination(config.content_dir, article, None)):
            totals["duplicates"] += 1
            continue
        if limit is not None and attempted >= limit:
            break
        # El límite cuenta consultas, incluidas las que terminan en exclusión.
        attempted += 1
        try:
            editorial = editor.generate(article)
            if editorial is None or editorial.category not in CATEGORIES:
                totals["excluded"] += 1
                LOG.info("Noticia omitida por tema fuera del catálogo (%s)", url)
                seen.add(url)
                continue
            path = destination(config.content_dir, article, editorial)
            markdown = render(article, editorial, draft=config.draft)
            print(json.dumps({"path": str(path), "markdown": markdown}, ensure_ascii=False))
            totals["previewed"] += 1
            seen.add(url)
        except (RequestError, ValueError, OSError) as error:
            totals["failed"] += 1
            LOG.error("Noticia omitida (%s): %s", url, error)
    return totals


def execute(args, config, *, stop=None) -> int:
    """Una ejecución; el servicio renueva la ventana relativa antes de cada ciclo."""
    limit = args.limit
    try:
        window = DateWindow.parse(args.date_from, args.until)
        if args.command == "images":
            totals = search_recent(config.content_dir, config.state_dir,
                                   os.getenv("PEXELS_API_KEY"), limit=args.image_limit,
                                   timeout=config.timeout)
            LOG.info("Imágenes: %s", json.dumps(totals, ensure_ascii=False))
            return 1 if totals["failed"] else 0
        if args.command == "process":
            editor = OllamaEditor(config.ollama_url, config.model, config.timeout)
            totals = process(editor, config, limit=limit, dry_run=args.dry_run, window=window)
            LOG.info("Resultado: %s", json.dumps(totals, ensure_ascii=False))
            return 1 if totals["failed"] else 0
        failed_sources = 0
        if args.input:
            articles = read_index(args.input)
        elif args.feed_file:
            articles = SCRAPERS[args.source].parse_feed(args.feed_file.read_bytes())
        else:
            scrapers = SCRAPERS if args.source == "all" else {args.source: SCRAPERS[args.source]}
            articles = []
            for name, scraper in scrapers.items():
                if stop is not None and stop.is_set():
                    return 0
                try:
                    articles.extend(scraper.fetch())
                except (ValueError, RuntimeError, OSError, ParseError) as error:
                    failed_sources += 1
                    LOG.error("Scraper %s falló; se continúa con los demás: %s", name, error)
            if failed_sources == len(scrapers) and args.command != "serve":
                LOG.error("Ningún scraper respondió correctamente; no se modifican archivos")
                return 1
            if failed_sources:
                LOG.warning("Resultado parcial: %d de %d scrapers fallaron", failed_sources, len(scrapers))
        articles = [article for article in articles if window.contains(article.date)]
        articles.sort(key=lambda article: article.date, reverse=True)
        if args.command == "index":
            path = args.output if args.output is not None else Path(".news-index.json")
            selected = articles[:limit]
            write_index(path, selected)
            LOG.info("Índice actualizado: %s (%d noticias)", path, len(selected))
            return 1 if failed_sources else 0
        if args.command == "scrape":
            print(json.dumps([article.to_dict() for article in articles[:limit]], ensure_ascii=False, indent=2))
            return 1 if failed_sources else 0
        if args.command == "stage":
            totals = stage(articles, config, limit=limit)
            LOG.info("Resultado: %s", json.dumps(totals, ensure_ascii=False))
            return 1 if totals["failed"] or failed_sources else 0
        editor = OllamaEditor(config.ollama_url, config.model, config.timeout)
        if args.command == "serve":
            imported = stage(articles, config, limit=limit, stop=stop)
            # Reintentar también pendientes antiguos, aunque hayan salido de la ventana RSS.
            totals = process(editor, config, limit=limit, stop=stop)
            images = search_recent(config.content_dir, config.state_dir,
                                   os.getenv("PEXELS_API_KEY"), limit=args.image_limit,
                                   timeout=config.timeout)
            LOG.info("Importación: %s; redacción: %s; imágenes: %s", imported, totals, images)
            return int(bool(imported["failed"] or totals["failed"] or images["failed"] or failed_sources))
        totals = run(articles, editor, config, limit=limit, dry_run=args.dry_run)
        LOG.info("Resultado: %s", json.dumps(totals, ensure_ascii=False))
        return 1 if totals["failed"] or failed_sources else 0
    except (ValueError, RuntimeError, OSError, ParseError) as error:
        LOG.error("Importación detenida: %s", error)
        return 1


COMMANDS = ("ingest", "stage", "process", "images", "scrape", "index", "serve")


def parse_limit(value):
    if str(value).lower() == "all":
        return None
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("el límite debe ser cero (sin límite) o positivo")
    return number or None


def parse_image_limit(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("el límite de imágenes debe ser mayor que cero")
    return number


def serve(args, config):
    """Ciclos secuenciales; SIGTERM/SIGINT interrumpen la espera, sin solapamientos."""
    stop = Event()
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, lambda *_: stop.set())
    try:
        cycle = (
            lambda: run_remote(args, config,
                               lambda current_args, current_config: execute(current_args, current_config, stop=stop))
        ) if (args.ssh_key or args.github_token) else None
        while not stop.is_set():
            result = cycle() if cycle else execute(args, config, stop=stop)
            if result:
                LOG.warning("Ciclo con errores; se reintentará en el próximo ciclo")
            if stop.is_set():
                break
            LOG.info("Próximo ciclo en %s segundos", args.interval)
            stop.wait(args.interval)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        load_env()
    except (OSError, ValueError) as error:
        LOG.error("Configuración inválida: %s", error)
        return 1
    parser = argparse.ArgumentParser(description="RSS → borradores Hugo → redacción con Ollama")
    parser.add_argument("command", nargs="?", choices=COMMANDS, default=os.getenv("NEWS_COMMAND", "ingest"))
    parser.add_argument("--limit", type=parse_limit, default=os.getenv("NEWS_LIMIT", "0"),
                        help="Máximo de noticias/consultas; 0 o all = sin límite (predeterminado)")
    parser.add_argument("--from", dest="date_from", default=os.getenv("NEWS_FROM", "yesterday"),
                        help="Fecha inicial inclusiva; por defecto yesterday en Costa Rica; all = sin inicio")
    parser.add_argument("--until", default=os.getenv("NEWS_UNTIL", "now"),
                        help="Fecha final inclusiva (día completo o instante ISO); por defecto now; all = sin fin")
    parser.add_argument("--interval", type=int, default=os.getenv("NEWS_INTERVAL", "28800"),
                        help="Segundos de espera entre ciclos de serve (28800 = 8 horas)")
    parser.add_argument("--image-limit", type=parse_image_limit,
                        default=os.getenv("NEWS_IMAGE_LIMIT", "2"),
                        help="Máximo de noticias recientes sin imagen a buscar (predeterminado: 2)")
    parser.add_argument("--output", type=Path, help="Destino de index; NEWS_INDEX_PATH o .news-index.json")
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None,
                        help="Consulta Ollama sin escribir; --no-dry-run anula NEWS_DRY_RUN")
    parser.add_argument("--ssh-key", type=Path, default=os.getenv("NEWS_SSH_KEY") or None,
                        help="Ruta o contenido OpenSSH de llave privada; también NEWS_SSH_KEY")
    parser.add_argument("--branch", help="Rama de revisión; ai-editor-<modelo> por defecto, nunca main")
    parser.add_argument("--repo", help="Repositorio a clonar; origin o repositorio oficial por defecto")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--feed-file", type=Path, help="RSS local; NEWS_FEED_FILE")
    inputs.add_argument("--input", type=Path, help="JSON local para stage/ingest; NEWS_INPUT")
    parser.add_argument("--source", choices=("all", *SCRAPERS), default=os.getenv("NEWS_SOURCE", "all"))
    args = parser.parse_args(argv)
    args.github_token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if args.ssh_key and args.github_token:
        parser.error("elige autenticación con --ssh-key/NEWS_SSH_KEY o con GITHUB_TOKEN")
    if args.command == "images" and not os.getenv("PEXELS_API_KEY", "").strip():
        parser.error("images requiere configurar PEXELS_API_KEY")
    if (args.branch or args.repo) and not (args.ssh_key or args.github_token):
        parser.error("--branch y --repo requieren --ssh-key/NEWS_SSH_KEY o GITHUB_TOKEN")
    if (args.ssh_key or args.github_token) and args.command not in {"ingest", "stage", "process", "serve"}:
        parser.error("la autenticación Git corresponde a ingest, stage, process o serve")
    if args.command not in COMMANDS or args.source not in ("all", *SCRAPERS):
        parser.error("NEWS_COMMAND o NEWS_SOURCE no válido")
    if args.input is None and args.feed_file is None:
        args.input = Path(os.environ["NEWS_INPUT"]) if os.getenv("NEWS_INPUT") else None
        args.feed_file = Path(os.environ["NEWS_FEED_FILE"]) if os.getenv("NEWS_FEED_FILE") else None
    if args.input and args.feed_file:
        parser.error("NEWS_INPUT y NEWS_FEED_FILE son excluyentes")
    if args.dry_run is None:
        value = os.getenv("NEWS_DRY_RUN", "false").lower()
        if value not in {"true", "false"}:
            parser.error("NEWS_DRY_RUN debe ser true o false")
        args.dry_run = value == "true"
    if (args.ssh_key or args.github_token) and args.dry_run:
        parser.error("la autenticación Git no se combina con --dry-run")
    if args.interval < 1:
        parser.error("--interval / NEWS_INTERVAL debe ser mayor que cero")
    if args.command not in {"ingest", "process"} and args.dry_run:
        parser.error("--dry-run corresponde a ingest o process")
    if args.output is not None and args.command != "index":
        parser.error("--output corresponde a index")
    if args.command == "index":
        args.output = args.output or Path(os.getenv("NEWS_INDEX_PATH", ".news-index.json"))
    if args.input and args.command not in {"ingest", "stage"}:
        parser.error("--input corresponde a ingest o stage")
    if args.command == "process" and (args.feed_file or args.source != "all"):
        parser.error("process lee borradores pendientes, no consulta RSS")
    if args.input and args.source != "all":
        parser.error("--input no se combina con --source; filtra el archivo JSON")
    if args.feed_file and (args.source == "all" or args.command == "serve"):
        parser.error("--feed-file requiere un medio específico y no se admite en serve")
    try:
        DateWindow.parse(args.date_from, args.until)  # Fallar antes de consultar RSS o IA.
        config = (Config.from_env(require_model=False) if args.command in {"stage", "images"} else
                  Config.from_env() if args.command in {"ingest", "process", "serve"} else None)
    except (ValueError, OSError) as error:
        LOG.error("Configuración inválida: %s", error)
        return 1
    if args.command == "serve":
        return serve(args, config)
    if args.ssh_key or args.github_token:
        return run_remote(args, config, execute)
    return execute(args, config)


if __name__ == "__main__":
    sys.exit(main())
