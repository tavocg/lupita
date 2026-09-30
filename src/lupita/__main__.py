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
from .news_index import read_index, write_index
from .scrapers import (
    delfino, diarioextra, elfinanciero, elmundo, nacion, ncrnoticias,
    observador, repretel, semanario, teletica,
)
from .storage import destination, known_urls, pipeline_lock, render, write_article


LOG = logging.getLogger("lupita")
SCRAPERS = {
    "nacion": nacion, "delfino": delfino, "semanario": semanario, "teletica": teletica,
    "elfinanciero": elfinanciero, "observador": observador, "diarioextra": diarioextra,
    "ncrnoticias": ncrnoticias, "elmundo": elmundo, "repretel": repretel,
}


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
    parser.add_argument("command", nargs="?", choices=("ingest", "scrape", "index"), default="ingest")
    parser.add_argument("--limit", type=int, help="Máximo de noticias (10 para ingest/scrape; todas para index)")
    parser.add_argument("--output", type=Path, help="Destino de index (por defecto .news-index.json)")
    parser.add_argument("--dry-run", action="store_true", help="Consulta Ollama y muestra el resultado sin escribir")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--feed-file", type=Path, help="Lee un RSS local en lugar de descargarlo")
    inputs.add_argument("--input", type=Path, help="Lee un índice JSON para ingest, sin consultar scrapers")
    parser.add_argument("--source", choices=("all", *SCRAPERS), default="all",
                        help="Medio a consultar (por defecto todos)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit debe ser mayor que cero")
    if args.command != "ingest" and args.dry_run:
        parser.error("--dry-run corresponde a ingest")
    if args.output is not None and args.command != "index":
        parser.error("--output corresponde a index")
    if args.input and args.command != "ingest":
        parser.error("--input corresponde a ingest")
    if args.input and args.source != "all":
        parser.error("--input no se combina con --source; filtra el archivo JSON")
    if args.feed_file and args.source == "all":
        parser.error("--feed-file requiere --source con un medio específico: " + ", ".join(SCRAPERS))
    limit = args.limit if args.limit is not None or args.command == "index" else 10
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        load_env()
        config = Config.from_env() if args.command == "ingest" else None
        failed_sources = 0
        if args.input:
            articles = read_index(args.input)
        elif args.feed_file:
            articles = SCRAPERS[args.source].parse_feed(args.feed_file.read_bytes())
        else:
            scrapers = SCRAPERS if args.source == "all" else {args.source: SCRAPERS[args.source]}
            articles = []
            for name, scraper in scrapers.items():
                try:
                    articles.extend(scraper.fetch())
                except (ValueError, RuntimeError, OSError, ParseError) as error:
                    failed_sources += 1
                    LOG.error("Scraper %s falló; se continúa con los demás: %s", name, error)
            if failed_sources == len(scrapers):
                LOG.error("Ningún scraper respondió correctamente; no se modifican archivos")
                return 1
            if failed_sources:
                LOG.warning("Resultado parcial: %d de %d scrapers fallaron", failed_sources, len(scrapers))
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
        editor = OllamaEditor(config.ollama_url, config.model, config.timeout)
        totals = run(articles, editor, config, limit=limit, dry_run=args.dry_run)
        LOG.info("Resultado: %s", json.dumps(totals, ensure_ascii=False))
        return 1 if totals["failed"] or failed_sources else 0
    except (ValueError, RuntimeError, OSError, ParseError) as error:
        LOG.error("Importación detenida: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
