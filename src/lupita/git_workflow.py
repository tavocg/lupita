"""Ejecución aislada desde main y entrega en una rama para revisión humana."""

from dataclasses import replace
import hashlib
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
import base64

from .storage import pipeline_lock

LOG = logging.getLogger("lupita")
DEFAULT_REPOSITORY = "git@github.com:tavocg/lupita"


def git(*args, cwd=None, env=None):
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(f"Git {args[0]} falló: {result.stderr.strip()}")
    return result.stdout.strip()


def review_branch(model):
    label = re.sub(r"[^a-zA-Z0-9-]+", "-", model).strip("-").lower()
    return "ai-editor-" + (label or "stage")


def validate_branch(branch):
    if branch.casefold() == "main" or branch.startswith(("-", "refs/")):
        raise ValueError("La rama de revisión no puede ser main ni una referencia completa")
    # No aceptar la expansión de @{-1} que admite check-ref-format --branch.
    git("check-ref-format", "refs/heads/" + branch)


def default_repository():
    try:
        return git("remote", "get-url", "origin")
    except RuntimeError:
        return DEFAULT_REPOSITORY


def content_relative(path):
    if path.is_absolute():
        try:
            path = path.relative_to(Path.cwd())
        except ValueError as error:
            raise ValueError("CONTENT_DIR debe estar dentro del repositorio para el modo remoto") from error
    if path == Path(".") or ".." in path.parts or path.parts[0] in {".git", ".pipeline"}:
        raise ValueError("CONTENT_DIR debe ser un subdirectorio de contenido del repositorio")
    return path


def github_https_repository(value):
    """Normaliza remotos GitHub a HTTPS, sin credenciales embebidas."""
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parts = urlsplit(value)
        if parts.scheme == "ssh" and parts.hostname == "github.com":
            path = parts.path.lstrip("/")
        elif parts.scheme == "https" and parts.hostname == "github.com":
            if parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError("--repo HTTPS no debe contener credenciales, query ni fragmento")
            path = parts.path.lstrip("/")
        else:
            raise ValueError("El token GitHub requiere un remoto git@github.com: o https://github.com/")
    path = path.removesuffix("/")
    if not path or len(path.split("/")) != 2 or path in {".", ".."}:
        raise ValueError("--repo debe identificar OWNER/REPO en github.com")
    if not path.endswith(".git"):
        path += ".git"
    return "https://github.com/" + path


def run_remote(args, config, execute):
    """Siempre crea un clon nuevo; nunca cambia la rama ni los archivos locales."""
    clone = None
    temporary_key = None
    try:
        branch = args.branch or review_branch(config.model)
        validate_branch(branch)
        token = (args.github_token or "").strip()
        if token:
            key = None
            key_content = None
        else:
            key_value = str(args.ssh_key)
            if "-----BEGIN " in key_value and "PRIVATE KEY-----" in key_value:
                key_content = key_value.replace("\\n", "\n")
                if not key_content.endswith("\n"):
                    key_content += "\n"
                key = None
            else:
                key = Path(key_value).expanduser().resolve(strict=True)
                if not key.is_file():
                    raise ValueError("--ssh-key debe ser una ruta o el contenido de una llave privada")
                key_content = None
        relative = content_relative(config.content_dir)
        repository = args.repo or default_repository()
        if token:
            repository = github_https_repository(repository)
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        # Evita que una traza curl de Git imprima encabezados de autenticación.
        for name in list(env):
            if name.startswith("GIT_TRACE") or name == "GIT_CURL_VERBOSE":
                env.pop(name, None)
        if token:
            encoded = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
            env.update({
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "http.extraheader",
                "GIT_CONFIG_VALUE_0": "",
                "GIT_CONFIG_KEY_1": "http.extraheader",
                "GIT_CONFIG_VALUE_1": "AUTHORIZATION: basic " + encoded,
            })
            env.pop("GIT_SSH_COMMAND", None)
        else:
            env.pop("GIT_CONFIG_COUNT", None)
            env["GIT_SSH_COMMAND"] = ""
        # Separar estado y bloqueo por repositorio/rama; conservar referencias para process.
        scope = hashlib.sha256((repository + "\0" + branch).encode()).hexdigest()[:20]
        state = config.state_dir.resolve() / "git" / scope
        with pipeline_lock(state):
            if key_content is not None:
                handle = tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=state, prefix=".ssh-key-", delete=False,
                )
                temporary_key = Path(handle.name)
                try:
                    os.chmod(temporary_key, 0o600)
                    handle.write(key_content)
                finally:
                    handle.close()
                key = temporary_key
            if not token:
                # La identidad no pasa por una shell sin escapar. Se mantiene known_hosts.
                env["GIT_SSH_COMMAND"] = (
                    "ssh -i " + shlex.quote(str(key)) +
                    " -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes"
                )
            clone = Path(tempfile.mkdtemp(prefix="checkout-", dir=state))
            git("clone", "--no-hardlinks", "--single-branch", "--branch", "main",
                "--", repository, str(clone), env=env)
            target = "refs/heads/" + branch
            advertised = git("ls-remote", "--heads", "origin", target, cwd=clone, env=env)
            previous = advertised.split()[0] if advertised else ""
            git("checkout", "-b", branch, "refs/remotes/origin/main", cwd=clone, env=env)
            content = clone / relative
            if any((clone / Path(*relative.parts[:i])).is_symlink()
                   for i in range(1, len(relative.parts) + 1)):
                raise ValueError("CONTENT_DIR no puede atravesar enlaces simbólicos")
            if content.exists() and any(p.is_symlink() for p in content.rglob("*")):
                raise ValueError("El contenido del clon no puede contener enlaces simbólicos")
            references = state / "references"
            run_state = clone / ".git" / "lupita"
            if references.exists():
                shutil.copytree(references, run_state / "references")
            remote_config = replace(config, content_dir=content, state_dir=run_state)
            result = execute(args, remote_config)
            if (run_state / "references").exists():
                shutil.copytree(run_state / "references", references, dirs_exist_ok=True)
            # No borrar referencias privadas al procesar: el siguiente run puede
            # partir de main con el mismo pendiente si la revisión aún no se integró.
            # Las referencias RSS permanecen privadas, incluso para borradores pendientes.
            if content.exists():
                git("add", "-A", "--", relative.as_posix(), cwd=clone, env=env)
            changed = git("diff", "--cached", "--name-only", cwd=clone, env=env)
            if result and not changed:
                raise RuntimeError("El pipeline falló sin cambios; no se actualiza la rama remota")
            if changed:
                git("-c", "user.name=Lupita AI Editor", "-c", "user.email=ai-editor@lupita.invalid",
                    "commit", "-m", f"Redacción {args.command}: {config.model or 'sin IA'}",
                    cwd=clone, env=env)
            # Lease explícito: falla si alguien actualizó la rama después de nuestra lectura.
            # Incluso sin novedades, reemplaza una rama obsoleta por el main actual.
            git("push", f"--force-with-lease={target}:{previous}", "origin",
                f"HEAD:{target}", cwd=clone, env=env)
            LOG.info("Rama de revisión actualizada: %s (%s)", branch,
                     "con commit" if changed else "sin cambios de contenido")
            shutil.rmtree(clone)
            clone = None
            return result
    except (ValueError, RuntimeError, OSError) as error:
        LOG.error("Ejecución Git detenida: %s", error)
        if clone is not None:
            LOG.error("Clon conservado para recuperar los cambios: %s", clone)
        return 1
    finally:
        if temporary_key is not None:
            temporary_key.unlink(missing_ok=True)
