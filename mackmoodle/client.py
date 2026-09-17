"""Cliente mínimo da API REST do Moodle (serviço do app, moodle_mobile_app)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__

USER_AGENT = f"mackmoodle/{__version__} (+https://github.com/lucascerfig/mackmoodle-cli)"


class MoodleErro(Exception):
    """Erro devolvido pelo Moodle ou falha de rede."""

    def __init__(self, mensagem: str, codigo: str | None = None, funcao: str | None = None):
        super().__init__(mensagem)
        self.codigo = codigo
        self.funcao = funcao

    def __str__(self) -> str:
        base = super().__str__()
        partes = [p for p in (self.funcao, self.codigo) if p]
        return f"{base} [{' / '.join(partes)}]" if partes else base


class SemToken(MoodleErro):
    pass


def achatar(params: dict) -> list[tuple[str, str]]:
    """Converte dicts e listas no formato de formulário do PHP (a[0][b]=c)."""
    saida: list[tuple[str, str]] = []

    def add(prefixo: str, valor):
        if valor is None:
            return
        if isinstance(valor, dict):
            for k, v in valor.items():
                add(f"{prefixo}[{k}]", v)
        elif isinstance(valor, (list, tuple)):
            for i, v in enumerate(valor):
                add(f"{prefixo}[{i}]", v)
        elif isinstance(valor, bool):
            saida.append((prefixo, "1" if valor else "0"))
        else:
            saida.append((prefixo, str(valor)))

    for chave, valor in params.items():
        add(chave, valor)
    return saida


class Moodle:
    def __init__(self, site: str, token: str | None, timeout: float = 60.0):
        self.site = site.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ------------------------------------------------------------------ REST
    def chamar(self, funcao: str, **params):
        if not self.token:
            raise SemToken("Sem token do Moodle. Rode `mackmoodle login`.", funcao=funcao)
        corpo = [("wstoken", self.token), ("wsfunction", funcao), ("moodlewsrestformat", "json")]
        corpo += achatar(params)
        dados = urllib.parse.urlencode(corpo).encode()
        req = urllib.request.Request(
            f"{self.site}/webservice/rest/server.php",
            data=dados,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                bruto = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise MoodleErro(f"HTTP {e.code} ao chamar o Moodle", funcao=funcao) from e
        except urllib.error.URLError as e:
            raise MoodleErro(
                f"Não foi possível alcançar {self.site} ({e.reason}). "
                "Verifique a conexão ou se a rede bloqueia o endereço.",
                codigo="rede", funcao=funcao) from e
        try:
            resultado = json.loads(bruto) if bruto.strip() else None
        except json.JSONDecodeError as e:
            raise MoodleErro(f"Resposta inesperada do Moodle: {bruto[:200]}", funcao=funcao) from e
        if isinstance(resultado, dict) and "exception" in resultado:
            codigo = resultado.get("errorcode")
            msg = resultado.get("message") or codigo or "erro"
            if codigo in ("invalidtoken", "accessexception"):
                msg += " (token inválido ou expirado: rode `mackmoodle login`)"
            raise MoodleErro(msg, codigo=codigo, funcao=funcao)
        return resultado

    # --------------------------------------------------------------- arquivos
    def baixar(self, url: str, destino: Path) -> int:
        """Baixa um arquivo de webservice/pluginfile.php usando o token."""
        partes = urllib.parse.urlsplit(url)
        consulta = urllib.parse.parse_qsl(partes.query)
        consulta = [(k, v) for k, v in consulta if k != "token"] + [("token", self.token or "")]
        url_final = urllib.parse.urlunsplit(partes._replace(query=urllib.parse.urlencode(consulta)))
        req = urllib.request.Request(url_final, headers={"User-Agent": USER_AGENT})
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_name(destino.name + ".part")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp, open(tmp, "wb") as f:
                total = 0
                while True:
                    bloco = resp.read(1 << 16)
                    if not bloco:
                        break
                    f.write(bloco)
                    total += len(bloco)
        except urllib.error.URLError as e:
            if tmp.exists():
                tmp.unlink()
            raise MoodleErro(f"Falha ao baixar {destino.name}: {e}") from e
        os.replace(tmp, destino)
        return total
