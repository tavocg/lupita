import argparse
from contextlib import nullcontext
import json
import logging
from pathlib import Path
import sys
from xml.etree.ElementTree import ParseError

from .config import Config, load_env
from .editor import CATEGORIES, OllamaEditor
from .http import RequestError
from .models import canonical_url
from .scrapers import nacion
from .storage import destination, known_urls, pipeline_lock, render, write_article


LOG = logging.getLogger("lupita")


def run(articles, editor, config, *, limit: int, dry_run: bool = False) -> dict:
    totals = {"written": 0, "previewed": 0, "duplicates": 0, "excluded": 0, "failed": 0}
    with nullcontext() if dry_run else pipeline_lock(config.state_dir):
        seen = known_urls(config.content_dir)
        attempted = 0
        for article in articles:
            url = canonical_url(article.source_url)
            if url in seen:
                totals["duplicates"] += 1
                continue
            if attempted >= limit:
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
                if dry_run:
                    print(json.dumps({"path": str(path), "markdown": markdown}, ensure_ascii=False))
                    totals["previewed"] += 1
                else:
                    write_article(path, markdown)
                    totals["written"] += 1
                    LOG.info("Creada: %s", path)
                seen.add(url)
            except (RequestError, ValueError, OSError) as error:
                totals["failed"] += 1
                LOG.error("Noticia omitida (%s): %s", url, error)
    return totals


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RSS → Ollama → noticias Hugo")
    parser.add_argument("command", nargs="?", choices=("ingest", "scrape"), default="ingest")
    parser.add_argument("--limit", type=int, default=10, help="Máximo de noticias nuevas a procesar (10)")
    parser.add_argument("--dry-run", action="store_true", help="Consulta Ollama y muestra el resultado sin escribir")
    parser.add_argument("--feed-file", type=Path, help="Lee un RSS local en lugar de descargarlo")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit debe ser mayor que cero")
    if args.command == "scrape" and args.dry_run:
        parser.error("scrape ya es de solo lectura; --dry-run corresponde a ingest")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        load_env()
        config = Config.from_env() if args.command == "ingest" else None
        articles = nacion.parse_feed(args.feed_file.read_bytes()) if args.feed_file else nacion.fetch()
        if args.command == "scrape":
            print(json.dumps([article.to_dict() for article in articles[:args.limit]], ensure_ascii=False, indent=2))
            return 0
        editor = OllamaEditor(config.ollama_url, config.model, config.timeout)
        totals = run(articles, editor, config, limit=args.limit, dry_run=args.dry_run)
        LOG.info("Resultado: %s", json.dumps(totals, ensure_ascii=False))
        return 1 if totals["failed"] else 0
    except (ValueError, RuntimeError, OSError, ParseError) as error:
        LOG.error("Importación detenida: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
