import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class RequestError(RuntimeError):
    pass


def request(url: str, *, payload: dict | None = None, timeout: int = 30) -> bytes:
    headers = {"User-Agent": "Lupita/0.1 (RSS news reader)"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    for attempt in range(3):
        try:
            with urlopen(Request(url, data=data, headers=headers), timeout=timeout) as response:
                result = response.read(10 * 1024 * 1024 + 1)
                if len(result) > 10 * 1024 * 1024:
                    raise RequestError("Respuesta demasiado grande (máximo 10 MiB)")
                return result
        except HTTPError as error:
            if attempt == 2 or error.code not in {429, 502, 503, 504}:
                raise RequestError(f"HTTP {error.code}; revisa la URL, el modelo y el servicio") from error
        except (URLError, TimeoutError, OSError) as error:
            # Un POST lento podría seguir generando: no lo repetimos automáticamente.
            if payload is not None or attempt == 2:
                raise RequestError(f"No se pudo completar la conexión ({type(error).__name__})") from error
        time.sleep(attempt + 1)
    raise RequestError("No se pudo completar la petición")
