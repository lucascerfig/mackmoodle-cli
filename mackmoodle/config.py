"""Configuração local: site, token e dados do usuário.

O token fica em um arquivo com permissão 600 dentro de um diretório 700.
Variáveis de ambiente têm precedência:
  MACKMOODLE_SITE, MACKMOODLE_TOKEN, MACKMOODLE_CONFIG_DIR, MACKMOODLE_DATA_DIR
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

from . import SITE_PADRAO

# Validade padrão do token no Moodle (tokenduration): 12 semanas.
VALIDADE_PADRAO_S = 12 * 7 * 24 * 3600


def _base_config() -> Path:
    if os.environ.get("MACKMOODLE_CONFIG_DIR"):
        return Path(os.environ["MACKMOODLE_CONFIG_DIR"])
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home())) / "mackmoodle"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "mackmoodle"


def _base_dados() -> Path:
    if os.environ.get("MACKMOODLE_DATA_DIR"):
        return Path(os.environ["MACKMOODLE_DATA_DIR"])
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "mackmoodle"
    xdg = os.environ.get("XDG_STATE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "state") / "mackmoodle"


def dir_config() -> Path:
    return _base_config()


def arquivo_config() -> Path:
    return dir_config() / "config.json"


def dir_planos() -> Path:
    d = _base_dados() / "planos"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, stat.S_IRWXU)
    except OSError:
        pass
    return d


def carregar() -> dict:
    """Lê a configuração, aplicando as variáveis de ambiente por cima."""
    dados: dict = {}
    arq = arquivo_config()
    if arq.exists():
        try:
            dados = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            dados = {}
    if os.environ.get("MACKMOODLE_SITE"):
        dados["site"] = os.environ["MACKMOODLE_SITE"]
    if os.environ.get("MACKMOODLE_TOKEN"):
        dados["token"] = os.environ["MACKMOODLE_TOKEN"]
        dados["origem_token"] = "ambiente"
    dados.setdefault("site", SITE_PADRAO)
    dados["site"] = dados["site"].rstrip("/")
    return dados


def salvar(dados: dict) -> Path:
    d = dir_config()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, stat.S_IRWXU)
    except OSError:
        pass
    arq = arquivo_config()
    tmp = arq.with_suffix(".tmp")
    limpo = {k: v for k, v in dados.items() if k != "origem_token"}
    # Cria já com permissão restrita antes de escrever o token.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(limpo, f, ensure_ascii=False, indent=2)
    os.replace(tmp, arq)
    try:
        os.chmod(arq, 0o600)
    except OSError:
        pass
    return arq


def apagar() -> bool:
    arq = arquivo_config()
    if arq.exists():
        arq.unlink()
        return True
    return False


def idade_token_dias(dados: dict) -> float | None:
    t = dados.get("obtido_em")
    if not t:
        return None
    return (time.time() - float(t)) / 86400


def aviso_validade(dados: dict) -> str | None:
    dias = idade_token_dias(dados)
    if dias is None:
        return None
    restante = VALIDADE_PADRAO_S / 86400 - dias
    if restante <= 0:
        return (f"O token foi obtido há {dias:.0f} dias e, pelo padrão do Moodle (12 semanas), "
                "já deve ter expirado. Rode `mackmoodle login`.")
    if restante <= 7:
        return (f"O token foi obtido há {dias:.0f} dias; pelo padrão do Moodle ele expira em "
                f"cerca de {restante:.0f} dias.")
    return None
