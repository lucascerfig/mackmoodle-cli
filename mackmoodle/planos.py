"""Planos de gravação: toda escrita no Moodle nasce de um plano salvo em disco.

O fluxo é sempre  planejar -> mostrar ao professor -> aplicar(plano_id).
Na aplicação, o estado da tarefa é conferido de novo pela impressão digital
gravada no plano; se mudou, a aplicação é recusada.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import dir_planos

FUSO = timezone(timedelta(hours=-3))  # São Paulo, sem horário de verão desde 2019
VALIDADE_PLANO_S = 24 * 3600


def agora() -> float:
    """Hora atual; MACKMOODLE_AGORA permite fixar o relógio nos testes."""
    fixo = os.environ.get("MACKMOODLE_AGORA")
    return float(fixo) if fixo else time.time()


def fmt_data(ts) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(float(ts), FUSO).strftime("%d/%m/%Y %H:%M")


def impressao(obj) -> str:
    bruto = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(bruto.encode()).hexdigest()[:16]


def novo_id(tipo: str) -> str:
    carimbo = datetime.fromtimestamp(agora(), FUSO).strftime("%Y%m%d-%H%M%S")
    return f"{tipo}-{carimbo}-{secrets.token_hex(2)}"


def caminho(plano_id: str) -> Path:
    if not plano_id or "/" in plano_id or "\\" in plano_id or plano_id.startswith("."):
        raise ValueError("id de plano inválido")
    return dir_planos() / f"{plano_id}.json"


def salvar(plano: dict) -> Path:
    arq = caminho(plano["id"])
    fd = os.open(arq, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(plano, f, ensure_ascii=False, indent=2)
    return arq


def carregar(plano_id: str) -> dict:
    arq = caminho(plano_id)
    if not arq.exists():
        raise FileNotFoundError(f"Plano {plano_id} não encontrado em {arq.parent}")
    return json.loads(arq.read_text(encoding="utf-8"))


def listar(limite: int = 20) -> list[dict]:
    arqs = sorted(dir_planos().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    saida = []
    for a in arqs[:limite]:
        try:
            p = json.loads(a.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        saida.append({
            "id": p.get("id"),
            "tipo": p.get("tipo"),
            "criado_em": fmt_data(p.get("criado_em")),
            "aplicado_em": fmt_data(p.get("aplicado_em")) if p.get("aplicado_em") else "",
            "resumo": p.get("resumo", ""),
            "bloqueios": len(p.get("bloqueios") or []),
        })
    return saida


def pronto_para_aplicar(plano: dict) -> None:
    if plano.get("aplicado_em"):
        raise ValueError(f"O plano {plano['id']} já foi aplicado em {fmt_data(plano['aplicado_em'])}.")
    if plano.get("bloqueios"):
        raise ValueError("O plano tem bloqueios e não pode ser aplicado:\n- " + "\n- ".join(plano["bloqueios"]))
    if agora() - float(plano.get("criado_em", 0)) > VALIDADE_PLANO_S:
        raise ValueError("O plano tem mais de 24 horas. Gere um novo plano antes de aplicar.")
