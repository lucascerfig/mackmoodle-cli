"""Login pelo mesmo fluxo que o app oficial do Moodle usa com SSO.

1. O navegador abre admin/tool/mobile/launch.php (com confirmed=1).
2. Depois do login do Office 365, a página mostra um link moodlemobile://token=BASE64.
3. O professor copia o endereço desse link e cola no terminal.
4. BASE64 decodifica para  md5(site + passport) ::: token [::: privatetoken].

O token nunca é impresso. O privatetoken é descartado.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
import time
import urllib.parse

from .client import Moodle, MoodleErro


def novo_passport() -> str:
    return secrets.token_hex(8)


def url_login(site: str, passport: str) -> str:
    q = urllib.parse.urlencode({
        "service": "moodle_mobile_app",
        "passport": passport,
        "urlscheme": "moodlemobile",
        "confirmed": 1,
    })
    return f"{site.rstrip('/')}/admin/tool/mobile/launch.php?{q}"


def _b64(texto: str) -> bytes:
    texto = texto.strip()
    texto = urllib.parse.unquote(texto)
    texto += "=" * (-len(texto) % 4)
    try:
        return base64.b64decode(texto, validate=False)
    except (binascii.Error, ValueError):
        return base64.urlsafe_b64decode(texto)


def extrair_token(colado: str) -> tuple[str, str]:
    """Recebe o link copiado (ou só o trecho base64) e devolve (siteid, token)."""
    colado = colado.strip().strip('"').strip("'")
    m = re.search(r"token=([A-Za-z0-9+/=_%-]+)", colado)
    trecho = m.group(1) if m else colado
    try:
        partes = _b64(trecho).decode("utf-8", errors="strict").split(":::")
    except (UnicodeDecodeError, binascii.Error, ValueError) as e:
        raise MoodleErro("Não reconheci o link colado. Copie o endereço do link "
                         "'Clique aqui...' da página de confirmação.") from e
    if len(partes) < 2 or not re.fullmatch(r"[a-f0-9]{32}", partes[1] or ""):
        raise MoodleErro("O link colado não contém um token válido.")
    return partes[0], partes[1]


def conferir_siteid(site: str, passport: str, siteid: str) -> bool:
    return hashlib.md5((site.rstrip("/") + passport).encode()).hexdigest() == siteid


def validar(site: str, token: str) -> dict:
    """Confere o token com core_webservice_get_site_info e devolve os dados úteis."""
    m = Moodle(site, token)
    info = m.chamar("core_webservice_get_site_info")
    return {
        "site": (info.get("siteurl") or site).rstrip("/"),
        "token": token,
        "userid": info.get("userid"),
        "nome": info.get("fullname"),
        "usuario": info.get("username"),
        "versao_moodle": info.get("release"),
        "obtido_em": int(time.time()),
        "n_funcoes": len(info.get("functions") or []),
    }
