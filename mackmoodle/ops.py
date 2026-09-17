"""Operações sobre o Moodle. Tudo aqui devolve dicts serializáveis em JSON.

Leitura: cursos, tarefas, envios, comparar.
Escrita (sempre via plano): notas por entrega, notas por CSV, trava, aviso.
"""

from __future__ import annotations

import csv
import html
import io
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import planos
from .client import Moodle, MoodleErro
from .planos import FUSO, agora, fmt_data

DOMINIO_ALUNO = "mackenzista.com.br"
LOTE_NOTAS = 40


# ============================================================ utilidades
def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def ra_do_email(email: str | None) -> str | None:
    if email and email.lower().endswith("@" + DOMINIO_ALUNO):
        return email.split("@")[0]
    return None


def texto_para_html(texto: str) -> str:
    blocos = re.split(r"\n\s*\n", (texto or "").strip())
    return "".join("<p>" + html.escape(b).replace("\n", "<br>") + "</p>" for b in blocos if b.strip())


def html_para_texto(conteudo: str) -> str:
    t = re.sub(r"<br\s*/?>", "\n", conteudo or "", flags=re.I)
    t = re.sub(r"</p\s*>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def semestre_atual() -> str:
    d = datetime.fromtimestamp(agora(), FUSO)
    return f"{d.year}/{1 if d.month <= 6 else 2}"


def _float(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _plugins_ativos(tarefa: dict) -> set[str]:
    ativos = set()
    for c in tarefa.get("configs") or []:
        if c.get("name") == "enabled" and str(c.get("value")) == "1":
            ativos.add(f"{c.get('subtype')}:{c.get('plugin')}")
    return ativos


# ============================================================ leitura
def info(m: Moodle) -> dict:
    si = m.chamar("core_webservice_get_site_info")
    return {
        "site": si.get("siteurl"),
        "usuario": si.get("fullname"),
        "userid": si.get("userid"),
        "versao_moodle": si.get("release"),
        "n_funcoes": len(si.get("functions") or []),
    }


def cursos(m: Moodle, userid: int, semestre: str | None = None, todos: bool = False) -> dict:
    lista = m.chamar("core_enrol_get_users_courses", userid=userid) or []
    alvo = None if todos else (semestre or semestre_atual())
    linhas = []
    for c in lista:
        nome = c.get("fullname") or ""
        if alvo and alvo not in nome:
            continue
        linhas.append({
            "id": c.get("id"),
            "nome": nome,
            "curto": c.get("shortname"),
            "visivel": bool(c.get("visible", 1)),
            "inicio": fmt_data(c.get("startdate")),
            "fim": fmt_data(c.get("enddate")),
        })
    linhas.sort(key=lambda x: x["nome"])
    return {"semestre": alvo or "todos", "cursos": linhas}


def resolver_tarefa(m: Moodle, ref) -> dict:
    """Aceita id da tarefa, 'cm:<cmid>' ou a URL mod/assign/view.php?id=<cmid>."""
    texto = str(ref).strip()
    cmid = None
    mt = re.search(r"view\.php\?(?:.*&)?id=(\d+)", texto)
    if mt:
        cmid = int(mt.group(1))
    elif texto.lower().startswith("cm:"):
        cmid = int(texto[3:])
    if cmid is not None:
        cm = m.chamar("core_course_get_course_module", cmid=cmid)["cm"]
        if cm.get("modname") != "assign":
            raise MoodleErro(f"O módulo {cmid} é '{cm.get('modname')}', não uma tarefa.")
        instancia, curso = int(cm["instance"]), int(cm["course"])
    else:
        instancia = int(texto)
        cm = m.chamar("core_course_get_course_module_by_instance", module="assign", instance=instancia)["cm"]
        curso = int(cm["course"])
    dados = m.chamar("mod_assign_get_assignments", courseids=[curso])
    for c in dados.get("courses") or []:
        for a in c.get("assignments") or []:
            if int(a["id"]) == instancia:
                a = dict(a)
                a["_curso"] = {"id": c.get("id"), "nome": c.get("fullname"), "curto": c.get("shortname")}
                return a
    raise MoodleErro(f"Tarefa {instancia} não encontrada ou sem acesso.")


def _meta_tarefa(a: dict) -> dict:
    plugins = _plugins_ativos(a)
    return {
        "id": a["id"],
        "cmid": a.get("cmid"),
        "nome": a.get("name"),
        "curso": a.get("_curso", {}),
        "nota_maxima": _float(a.get("grade")),
        "prazo": a.get("duedate") or 0,
        "prazo_fmt": fmt_data(a.get("duedate")),
        "data_limite": a.get("cutoffdate") or 0,
        "data_limite_fmt": fmt_data(a.get("cutoffdate")),
        "em_grupo": bool(a.get("teamsubmission")),
        "fluxo_avaliacao": bool(a.get("markingworkflow")),
        "rascunhos": bool(a.get("submissiondrafts")),
        "anonima": bool(a.get("blindmarking")),
        "comentarios_feedback": "assignfeedback:comments" in plugins,
        "envio": sorted(p.split(":")[1] for p in plugins if p.startswith("assignsubmission:") and not p.endswith(":comments")),
    }


def tarefas(m: Moodle, courseids: list[int], contar: bool = False) -> dict:
    dados = m.chamar("mod_assign_get_assignments", courseids=courseids)
    linhas = []
    for c in dados.get("courses") or []:
        for a in c.get("assignments") or []:
            a = dict(a)
            a["_curso"] = {"id": c.get("id"), "nome": c.get("fullname"), "curto": c.get("shortname")}
            meta = _meta_tarefa(a)
            linha = {
                "curso": c.get("id"),
                "tarefa": meta["id"],
                "cmid": meta["cmid"],
                "nome": meta["nome"],
                "prazo": meta["prazo_fmt"],
                "data_limite": meta["data_limite_fmt"],
                "nota_max": meta["nota_maxima"],
                "prazo_ts": meta["prazo"],
            }
            if contar:
                try:
                    parts = _participantes(m, meta["id"])
                    linha["alunos"] = len(parts)
                    linha["entregas"] = sum(1 for p in parts if p.get("submitted"))
                    linha["a_avaliar"] = sum(1 for p in parts if p.get("requiregrading"))
                except MoodleErro as e:
                    linha["erro"] = str(e)
            linhas.append(linha)
    linhas.sort(key=lambda x: (x["curso"], x["prazo_ts"] or 0, x["nome"] or ""))
    return {"tarefas": linhas, "avisos": [w.get("message") for w in dados.get("warnings") or []]}


def _participantes(m: Moodle, assignid: int) -> list[dict]:
    # onlyids=True evita o erro "Valor inválido de resposta" visto no 4.2 da graduação.
    return m.chamar("mod_assign_list_participants", assignid=assignid, groupid=0, filter="",
                    skip=0, limit=0, onlyids=True, includeenrolments=False, tablesort=False) or []


def _emails(m: Moodle, courseid: int) -> dict[int, str]:
    try:
        usuarios = m.chamar("core_enrol_get_enrolled_users", courseid=courseid,
                            options=[{"name": "userfields", "value": "id,fullname,email"}]) or []
    except MoodleErro:
        return {}
    return {int(u["id"]): u.get("email") or "" for u in usuarios}


def envios(m: Moodle, ref, incluir_texto: bool = True) -> dict:
    a = resolver_tarefa(m, ref)
    meta = _meta_tarefa(a)
    parts = _participantes(m, meta["id"])
    emails = _emails(m, meta["curso"]["id"])

    subs: dict[int, dict] = {}
    for bloco in (m.chamar("mod_assign_get_submissions", assignmentids=[meta["id"]]) or {}).get("assignments") or []:
        for s in bloco.get("submissions") or []:
            uid = int(s.get("userid") or 0)
            if uid and (uid not in subs or int(s.get("attemptnumber", 0)) >= int(subs[uid].get("attemptnumber", 0))):
                subs[uid] = s
    notas: dict[int, dict] = {}
    for bloco in (m.chamar("mod_assign_get_grades", assignmentids=[meta["id"]]) or {}).get("assignments") or []:
        for g in bloco.get("grades") or []:
            uid = int(g["userid"])
            if uid not in notas or int(g.get("attemptnumber", 0)) >= int(notas[uid].get("attemptnumber", 0)):
                notas[uid] = g

    agora_ts = agora()
    alunos = []
    for p in parts:
        uid = int(p["id"])
        s = subs.get(uid)
        status = (s or {}).get("status") or p.get("submissionstatus") or "sem envio"
        arquivos, texto = [], ""
        for pl in (s or {}).get("plugins") or []:
            if pl.get("type") == "file":
                for area in pl.get("fileareas") or []:
                    for f in area.get("files") or []:
                        arquivos.append({"nome": f.get("filename"), "caminho": f.get("filepath", "/"),
                                         "url": f.get("fileurl"), "tamanho": f.get("filesize")})
            if pl.get("type") == "onlinetext" and incluir_texto:
                for ef in pl.get("editorfields") or []:
                    texto = html_para_texto(ef.get("text", ""))
        g = notas.get(uid)
        nota = _float(g.get("grade")) if g else None
        if nota is not None and nota < 0:
            nota = None
        enviado_em = int((s or {}).get("timemodified") or 0) if status == "submitted" else 0
        extensao_ate = 0
        if p.get("grantedextension"):
            try:
                st = m.chamar("mod_assign_get_submission_status", assignid=meta["id"], userid=uid)
                extensao_ate = int((st.get("lastattempt") or {}).get("extensionduedate") or 0)
            except MoodleErro:
                extensao_ate = -1
        prazo_aluno = extensao_ate if extensao_ate > 0 else meta["prazo"]
        email = emails.get(uid, "")
        alunos.append({
            "userid": uid,
            "nome": p.get("fullname"),
            "email": email,
            "ra": ra_do_email(email),
            "status": status,
            "entregou": status == "submitted",
            "enviado_em": fmt_data(enviado_em) if enviado_em else "",
            "atrasado": bool(enviado_em and prazo_aluno and enviado_em > prazo_aluno),
            "extensao_ate": fmt_data(extensao_ate) if extensao_ate > 0 else ("?" if extensao_ate < 0 else ""),
            "prazo_aberto": bool(prazo_aluno and agora_ts < prazo_aluno),
            "arquivos": arquivos,
            "texto": texto,
            "nota": nota,
        })
    alunos.sort(key=lambda x: normalizar(x["nome"] or ""))
    resumo = {
        "alunos": len(alunos),
        "entregaram": sum(a["entregou"] for a in alunos),
        "rascunhos": sum(a["status"] == "draft" for a in alunos),
        "com_nota": sum(a["nota"] is not None for a in alunos),
        "com_extensao": sum(bool(a["extensao_ate"]) for a in alunos),
    }
    meta["prazo_aberto"] = bool(meta["prazo"] and agora_ts < meta["prazo"])
    return {"tarefa": meta, "resumo": resumo, "alunos": alunos}


def _estado(dados: dict) -> list:
    return sorted((a["userid"], a["status"], a["nota"]) for a in dados["alunos"])


# ============================================================ planos de notas
def _checagens_gerais(meta: dict, bloqueios: list, avisos: list, estado_fluxo: str | None):
    if meta["nota_maxima"] is None or meta["nota_maxima"] <= 0:
        bloqueios.append("A tarefa usa escala ou não tem nota numérica; a v1 só lança nota numérica.")
    if meta["em_grupo"]:
        bloqueios.append("Tarefa em grupo: a v1 não lança nota por grupo. Use a tela do Moodle.")
    if meta["anonima"]:
        avisos.append("A tarefa usa avaliação anônima; confira a identidade antes de revelar.")
    if meta["fluxo_avaliacao"] and not estado_fluxo:
        bloqueios.append("A tarefa usa fluxo de avaliação. Informe o estado (por exemplo, 'released' "
                         "para liberar a nota ou 'inmarking' para deixar em avaliação).")
    avisos.append("O Moodle notifica cada aluno que receber nota por este caminho (não há como desligar pela API).")


def _novo_plano(tipo, m, userid, meta, dados, itens, ignorados, avisos, bloqueios, opcoes, resumo):
    plano = {
        "id": planos.novo_id(tipo),
        "tipo": tipo,
        "criado_em": agora(),
        "site": m.site,
        "userid": userid,
        "tarefa": meta,
        "itens": itens,
        "ignorados": ignorados,
        "avisos": avisos,
        "bloqueios": bloqueios,
        "opcoes": opcoes,
        "impressao": planos.impressao(_estado(dados)) if dados else None,
        "resumo": resumo,
        "aplicado_em": None,
    }
    planos.salvar(plano)
    return plano


def planejar_notas_por_entrega(m: Moodle, userid: int, ref, nota_entregou: float | None = None,
                               nota_nao_entregou: float = 0.0, sobrescrever: bool = False,
                               forcar_prazo: bool = False, rascunho: str = "ignorar",
                               estado_fluxo: str | None = None) -> dict:
    if rascunho not in ("ignorar", "zero", "entregou"):
        raise ValueError("rascunho deve ser 'ignorar', 'zero' ou 'entregou'")
    dados = envios(m, ref, incluir_texto=False)
    meta = dados["tarefa"]
    bloqueios, avisos, itens, ignorados = [], [], [], []
    _checagens_gerais(meta, bloqueios, avisos, estado_fluxo)
    maxima = meta["nota_maxima"] or 0
    nota_entregou = maxima if nota_entregou is None else float(nota_entregou)
    nota_nao_entregou = float(nota_nao_entregou)
    for n in (nota_entregou, nota_nao_entregou):
        if maxima > 0 and not (0 <= n <= maxima):
            bloqueios.append(f"Nota {n:g} fora do intervalo 0 a {maxima:g}.")
    if meta["prazo_aberto"] and not forcar_prazo:
        bloqueios.append(f"O prazo ainda não acabou ({meta['prazo_fmt']}). Quem ainda não entregou "
                         "receberia a nota de não entrega. Espere o prazo ou use forcar_prazo.")
    if not meta["data_limite"]:
        avisos.append("A tarefa não tem data limite: quem receber a nota de não entrega ainda pode enviar "
                      "atrasado. Considere travar os envios desses alunos.")

    for a in dados["alunos"]:
        base = {"userid": a["userid"], "nome": a["nome"], "email": a["email"], "status": a["status"]}
        if a["prazo_aberto"] and a["extensao_ate"] and not forcar_prazo:
            ignorados.append({**base, "motivo": f"extensão até {a['extensao_ate']}"})
            continue
        if a["status"] == "draft" and rascunho == "ignorar":
            ignorados.append({**base, "motivo": "rascunho não enviado"})
            continue
        entregou = a["entregou"] or (a["status"] == "draft" and rascunho == "entregou")
        nota = nota_entregou if entregou else nota_nao_entregou
        if a["nota"] is not None and not sobrescrever:
            ignorados.append({**base, "motivo": f"já tem nota {a['nota']:g}"})
            continue
        if a["nota"] is not None and abs(a["nota"] - nota) < 1e-9:
            ignorados.append({**base, "motivo": "nota já é essa"})
            continue
        itens.append({**base, "nota": nota, "nota_atual": a["nota"], "entregou": entregou,
                      "comentario": ""})
    rascunhos = [a["nome"] for a in dados["alunos"] if a["status"] == "draft"]
    if rascunhos:
        avisos.append(f"{len(rascunhos)} aluno(s) com rascunho não enviado (tratamento: {rascunho}).")
    n_ok = sum(1 for i in itens if i["entregou"])
    resumo = (f"{meta['curso'].get('curto') or meta['curso'].get('id')} · {meta['nome']}: "
              f"{n_ok} com {nota_entregou:g}, {len(itens) - n_ok} com {nota_nao_entregou:g}, "
              f"{len(ignorados)} ignorado(s)")
    opcoes = {"modo": "entrega", "nota_entregou": nota_entregou, "nota_nao_entregou": nota_nao_entregou,
              "sobrescrever": sobrescrever, "forcar_prazo": forcar_prazo, "rascunho": rascunho,
              "estado_fluxo": estado_fluxo or ""}
    return _novo_plano("notas", m, userid, meta, dados, itens, ignorados, avisos, bloqueios, opcoes, resumo)


COLUNAS_ID = ("userid", "email", "ra", "nome")
ALIASES = {
    "id": "userid", "id_usuario": "userid", "user_id": "userid",
    "e_mail": "email", "endereco_de_email": "email",
    "matricula": "ra", "tia": "ra",
    "nome_completo": "nome", "aluno": "nome", "estudante": "nome",
    "nota_final": "nota", "grade": "nota",
    "comentarios": "comentario", "feedback": "comentario", "comentario_de_feedback": "comentario",
}


def _chave_coluna(k: str) -> str:
    c = normalizar(k).replace(" ", "_")
    return ALIASES.get(c, c)


def ler_csv(caminho: str | Path) -> list[dict]:
    bruto = Path(caminho).read_text(encoding="utf-8-sig")
    try:
        dialeto = csv.Sniffer().sniff(bruto.splitlines()[0] if bruto else ",", delimiters=",;\t")
    except csv.Error:
        dialeto = csv.excel
    leitor = csv.DictReader(io.StringIO(bruto), dialect=dialeto)
    linhas = []
    for linha in leitor:
        linhas.append({_chave_coluna(k): (v or "").strip() for k, v in linha.items() if k})
    return linhas


def planejar_notas_csv(m: Moodle, userid: int, ref, linhas: list[dict], sobrescrever: bool = False,
                       forcar_prazo: bool = False, ignorar_nao_encontrados: bool = False,
                       estado_fluxo: str | None = None) -> dict:
    """Linhas com uma coluna de identificação (userid, email, ra ou nome), 'nota' e,
    opcionalmente, 'comentario'. Nota em branco significa não mexer."""
    dados = envios(m, ref, incluir_texto=False)
    meta = dados["tarefa"]
    bloqueios, avisos, itens, ignorados = [], [], [], []
    _checagens_gerais(meta, bloqueios, avisos, estado_fluxo)
    maxima = meta["nota_maxima"] or 0
    if meta["prazo_aberto"]:
        msg = f"O prazo ainda não acabou ({meta['prazo_fmt']}); alunos podem alterar o envio depois da nota."
        (avisos if forcar_prazo else bloqueios).append(msg + ("" if forcar_prazo else " Use forcar_prazo para seguir."))

    por_id = {a["userid"]: a for a in dados["alunos"]}
    por_email = {a["email"].lower(): a for a in dados["alunos"] if a["email"]}
    por_ra = {a["ra"]: a for a in dados["alunos"] if a["ra"]}
    por_nome: dict[str, list] = {}
    for a in dados["alunos"]:
        por_nome.setdefault(normalizar(a["nome"]), []).append(a)

    linhas = [{_chave_coluna(k): (str(v) if v is not None else "").strip() for k, v in l.items()}
              for l in linhas]
    if linhas and "nota" not in linhas[0]:
        bloqueios.append("O CSV precisa de uma coluna 'nota'.")
        linhas = []
    if linhas and not any(c in linhas[0] for c in COLUNAS_ID):
        bloqueios.append("O CSV precisa de uma coluna de identificação: userid, email, ra ou nome.")
        linhas = []

    usados: dict[int, int] = {}
    nao_encontrados, fora, com_comentario = [], [], 0
    for n, l in enumerate(linhas, start=2):
        aluno = None
        if l.get("userid", "").isdigit():
            aluno = por_id.get(int(l["userid"]))
        if not aluno and l.get("email"):
            aluno = por_email.get(l["email"].lower())
        if not aluno and l.get("ra"):
            aluno = por_ra.get(l["ra"])
        if not aluno and l.get("nome"):
            cands = por_nome.get(normalizar(l["nome"]), [])
            if len(cands) == 1:
                aluno = cands[0]
            elif len(cands) > 1:
                nao_encontrados.append(f"linha {n}: nome '{l['nome']}' é ambíguo")
                continue
        ident = l.get("userid") or l.get("email") or l.get("ra") or l.get("nome") or "?"
        if not aluno:
            nao_encontrados.append(f"linha {n}: '{ident}' não está na tarefa")
            continue
        if aluno["userid"] in usados:
            bloqueios.append(f"{aluno['nome']} aparece nas linhas {usados[aluno['userid']]} e {n}.")
            continue
        usados[aluno["userid"]] = n
        if l.get("nota", "") == "":
            ignorados.append({"userid": aluno["userid"], "nome": aluno["nome"], "email": aluno["email"],
                              "status": aluno["status"], "motivo": "nota em branco no CSV"})
            continue
        nota = _float(l["nota"])
        if nota is None or (maxima > 0 and not (0 <= nota <= maxima)):
            fora.append(f"linha {n} ({aluno['nome']}): nota '{l['nota']}'")
            continue
        comentario = l.get("comentario") or ""
        base = {"userid": aluno["userid"], "nome": aluno["nome"], "email": aluno["email"], "status": aluno["status"]}
        if aluno["nota"] is not None and not sobrescrever:
            ignorados.append({**base, "motivo": f"já tem nota {aluno['nota']:g}"})
            continue
        if aluno["nota"] is not None and abs(aluno["nota"] - nota) < 1e-9 and not comentario:
            ignorados.append({**base, "motivo": "nota já é essa"})
            continue
        if comentario:
            com_comentario += 1
        itens.append({**base, "nota": nota, "nota_atual": aluno["nota"], "entregou": aluno["entregou"],
                      "comentario": comentario})
    if fora:
        bloqueios.append(f"Notas inválidas ou fora de 0 a {maxima:g}: " + "; ".join(fora))
    if nao_encontrados:
        msg = "Linhas sem aluno correspondente: " + "; ".join(nao_encontrados)
        (avisos if ignorar_nao_encontrados else bloqueios).append(msg)
    if com_comentario and not meta["comentarios_feedback"]:
        bloqueios.append("O CSV traz comentários, mas a tarefa não tem 'Comentários de feedback' ativado.")
    sem_linha = [a["nome"] for a in dados["alunos"] if a["userid"] not in usados]
    if sem_linha:
        avisos.append(f"{len(sem_linha)} aluno(s) da tarefa não aparecem no CSV e ficam como estão.")
    sem_envio = [i["nome"] for i in itens if not i["entregou"]]
    if sem_envio:
        avisos.append(f"{len(sem_envio)} aluno(s) recebem nota sem ter envio no Moodle.")
    resumo = (f"{meta['curso'].get('curto') or meta['curso'].get('id')} · {meta['nome']}: "
              f"{len(itens)} nota(s), {com_comentario} com comentário, {len(ignorados)} ignorada(s)")
    opcoes = {"modo": "csv", "sobrescrever": sobrescrever, "forcar_prazo": forcar_prazo,
              "estado_fluxo": estado_fluxo or ""}
    return _novo_plano("notas", m, userid, meta, dados, itens, ignorados, avisos, bloqueios, opcoes, resumo)


def planejar_trava(m: Moodle, userid: int, ref, userids: list[int], destravar: bool = False) -> dict:
    dados = envios(m, ref, incluir_texto=False)
    meta = dados["tarefa"]
    por_id = {a["userid"]: a for a in dados["alunos"]}
    itens, bloqueios = [], []
    for uid in userids:
        a = por_id.get(int(uid))
        if not a:
            bloqueios.append(f"Usuário {uid} não está na tarefa.")
            continue
        itens.append({"userid": a["userid"], "nome": a["nome"], "email": a["email"], "status": a["status"]})
    acao = "destravar" if destravar else "travar"
    resumo = f"{meta['curso'].get('curto')} · {meta['nome']}: {acao} envio de {len(itens)} aluno(s)"
    return _novo_plano("trava", m, userid, meta, None, itens, [], [], bloqueios,
                       {"acao": acao}, resumo)


def planejar_aviso(m: Moodle, userid: int, courseids: list[int], assunto: str, mensagem: str,
                   html_pronto: bool = False) -> dict:
    foruns = m.chamar("mod_forum_get_forums_by_courses", courseids=courseids) or []
    por_curso: dict[int, dict] = {}
    for f in foruns:
        if f.get("type") == "news" and int(f["course"]) not in por_curso:
            por_curso[int(f["course"])] = f
    nomes = {c["id"]: c for c in (m.chamar("core_course_get_courses_by_field", field="ids",
                                           value=",".join(str(c) for c in courseids)) or {}).get("courses", [])}
    itens, bloqueios = [], []
    for cid in courseids:
        f = por_curso.get(int(cid))
        curso = nomes.get(int(cid), {})
        if not f:
            bloqueios.append(f"O curso {cid} não tem fórum de avisos acessível.")
            continue
        itens.append({"curso": int(cid), "curso_nome": curso.get("fullname", ""),
                      "forum": int(f["id"]), "forum_nome": f.get("name")})
    if not assunto.strip():
        bloqueios.append("Assunto vazio.")
    if not mensagem.strip():
        bloqueios.append("Mensagem vazia.")
    corpo = mensagem if html_pronto else texto_para_html(mensagem)
    avisos = ["Os avisos são enviados por e-mail aos alunos depois do tempo de edição do fórum "
              "(normalmente 30 minutos); nesse intervalo dá para editar ou apagar no Moodle."]
    meta = {"assunto": assunto, "mensagem_html": corpo}
    resumo = f"Aviso '{assunto}' em {len(itens)} curso(s)"
    return _novo_plano("aviso", m, userid, meta, None, itens, [], avisos, bloqueios, {}, resumo)


# ============================================================ aplicação
def aplicar(m: Moodle, userid: int, plano_id: str, travar_sem_entrega: bool = False) -> dict:
    plano = planos.carregar(plano_id)
    planos.pronto_para_aplicar(plano)
    if plano.get("site") != m.site or int(plano.get("userid") or 0) != int(userid):
        raise ValueError("O plano foi criado para outro site ou outro usuário.")
    tipo = plano["tipo"]
    if tipo == "notas":
        relatorio = _aplicar_notas(m, plano, travar_sem_entrega)
    elif tipo == "trava":
        relatorio = _aplicar_trava(m, plano)
    elif tipo == "aviso":
        relatorio = _aplicar_aviso(m, plano)
    else:
        raise ValueError(f"Tipo de plano desconhecido: {tipo}")
    plano["aplicado_em"] = agora()
    plano["relatorio"] = relatorio
    planos.salvar(plano)
    return {"plano": plano_id, "tipo": tipo, **relatorio}


def _aplicar_notas(m: Moodle, plano: dict, travar_sem_entrega: bool) -> dict:
    meta = plano["tarefa"]
    atual = envios(m, meta["id"], incluir_texto=False)
    if planos.impressao(_estado(atual)) != plano["impressao"]:
        raise ValueError("A tarefa mudou desde o plano (novo envio, nota ou aluno). Gere o plano de novo.")
    estado_fluxo = plano["opcoes"].get("estado_fluxo") or ""
    itens = plano["itens"]
    for i in range(0, len(itens), LOTE_NOTAS):
        lote = []
        for it in itens[i:i + LOTE_NOTAS]:
            g = {"userid": it["userid"], "grade": it["nota"], "attemptnumber": -1,
                 "addattempt": False, "workflowstate": estado_fluxo}
            if it.get("comentario"):
                g["plugindata"] = {"assignfeedbackcomments_editor": {
                    "text": texto_para_html(it["comentario"]), "format": 1}}
            lote.append(g)
        if lote:
            m.chamar("mod_assign_save_grades", assignmentid=meta["id"], applytoall=False, grades=lote)
    travados = []
    if travar_sem_entrega:
        sem_entrega = [it["userid"] for it in itens if not it["entregou"]]
        if sem_entrega:
            m.chamar("mod_assign_lock_submissions", assignmentid=meta["id"], userids=sem_entrega)
            travados = sem_entrega
    depois = envios(m, meta["id"], incluir_texto=False)
    notas = {a["userid"]: a["nota"] for a in depois["alunos"]}
    divergencias = [{"userid": it["userid"], "nome": it["nome"], "esperado": it["nota"],
                     "encontrado": notas.get(it["userid"])}
                    for it in itens
                    if notas.get(it["userid"]) is None or abs(notas[it["userid"]] - it["nota"]) > 1e-6]
    return {"gravadas": len(itens), "travados": len(travados), "divergencias": divergencias,
            "resumo_depois": depois["resumo"]}


def _aplicar_trava(m: Moodle, plano: dict) -> dict:
    ids = [it["userid"] for it in plano["itens"]]
    fn = "mod_assign_unlock_submissions" if plano["opcoes"]["acao"] == "destravar" else "mod_assign_lock_submissions"
    if ids:
        m.chamar(fn, assignmentid=plano["tarefa"]["id"], userids=ids)
    return {"acao": plano["opcoes"]["acao"], "alunos": len(ids)}


def _aplicar_aviso(m: Moodle, plano: dict) -> dict:
    postados = []
    for it in plano["itens"]:
        r = m.chamar("mod_forum_add_discussion", forumid=it["forum"], subject=plano["tarefa"]["assunto"],
                     message=plano["tarefa"]["mensagem_html"])
        postados.append({"curso": it["curso"], "discussao": (r or {}).get("discussionid")})
    return {"postados": postados}


# ============================================================ arquivos
PROIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def nome_arquivo(nome: str) -> str:
    """Nome de arquivo aceito no Linux, no macOS e no Windows."""
    limpo = PROIBIDOS.sub("_", nome or "").strip().rstrip(". ")
    return limpo or "arquivo"


def _fatia(texto: str, limite: int = 60) -> str:
    t = normalizar(texto).replace(" ", "-")
    return t[:limite] or "sem-nome"


def baixar(m: Moodle, ref, destino: str | Path, incluir_rascunhos: bool = False,
           sobrescrever: bool = False) -> dict:
    dados = envios(m, ref, incluir_texto=True)
    meta = dados["tarefa"]
    raiz = Path(destino).expanduser() / _fatia(f"{meta['curso'].get('curto') or meta['curso'].get('id')}-{meta['nome']}", 90)
    raiz.mkdir(parents=True, exist_ok=True)
    baixados, pulados, indice = 0, 0, []
    for a in dados["alunos"]:
        if not (a["entregou"] or (incluir_rascunhos and a["status"] == "draft")):
            continue
        pasta = raiz / _fatia(f"{a['ra'] or a['userid']}-{a['nome']}")
        pasta.mkdir(parents=True, exist_ok=True)
        nomes = []
        for f in a["arquivos"]:
            partes = [nome_arquivo(x) for x in (f.get("caminho") or "/").strip("/").split("/") if x]
            alvo = pasta.joinpath(*partes, nome_arquivo(f["nome"]))
            if alvo.exists() and not sobrescrever and (f.get("tamanho") in (None, alvo.stat().st_size)):
                pulados += 1
            else:
                m.baixar(f["url"], alvo)
                baixados += 1
            nomes.append(str(alvo.relative_to(pasta)))
        if a["texto"]:
            (pasta / "texto_online.txt").write_text(a["texto"] + "\n", encoding="utf-8")
            nomes.append("texto_online.txt")
        indice.append({"userid": a["userid"], "nome": a["nome"], "email": a["email"], "ra": a["ra"] or "",
                       "status": a["status"], "enviado_em": a["enviado_em"], "atrasado": a["atrasado"],
                       "pasta": pasta.name, "arquivos": " | ".join(nomes), "nota_atual": a["nota"]})
    arq_indice = raiz / "indice.csv"
    with open(arq_indice, "w", newline="", encoding="utf-8") as f:
        campos = ["userid", "nome", "email", "ra", "status", "enviado_em", "atrasado", "pasta", "arquivos", "nota_atual"]
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(indice)
    return {"pasta": str(raiz), "alunos": len(indice), "baixados": baixados, "ja_existiam": pulados,
            "indice": str(arq_indice)}


def modelo_csv_notas(m: Moodle, ref, destino: str | Path) -> dict:
    """Gera um CSV com os alunos da tarefa, pronto para preencher nota e comentário."""
    dados = envios(m, ref, incluir_texto=True)
    arq = Path(destino).expanduser()
    arq.parent.mkdir(parents=True, exist_ok=True)
    with open(arq, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["userid", "ra", "nome", "email", "status", "texto_enviado", "nota_atual", "nota", "comentario"])
        for a in dados["alunos"]:
            w.writerow([a["userid"], a["ra"] or "", a["nome"], a["email"], a["status"],
                        a["texto"][:300], "" if a["nota"] is None else f"{a['nota']:g}", "", ""])
    return {"arquivo": str(arq), "alunos": len(dados["alunos"])}


# ============================================================ comparação
DATAS_PRINCIPAIS = ("duedate", "timeclose", "timedue", "cutoffdate", "deadline")
PADRAO_SOLTO = re.compile(r"^(lab|lista|projeto|questionario|quiz|aula|prova|atividade|exercicio)s?\s*(?:de\s+)?(\d+)\b")


def _chaves(modname: str, nome: str) -> tuple[str, str | None]:
    n = normalizar(nome).replace("laboratorio", "lab")
    exata = f"{modname}:{n}"
    ms = PADRAO_SOLTO.match(n)
    solta = f"{modname}:{ms.group(1)} {ms.group(2)}" if ms else None
    return exata, solta


def comparar(m: Moodle, courseids: list[int], incluir_antigas: bool = False) -> dict:
    info_cursos = {c["id"]: c for c in (m.chamar("core_course_get_courses_by_field", field="ids",
                                                 value=",".join(str(c) for c in courseids)) or {}).get("courses", [])}
    prazos: dict[int, int] = {}
    for c in (m.chamar("mod_assign_get_assignments", courseids=courseids) or {}).get("courses") or []:
        for a in c.get("assignments") or []:
            prazos[int(a["cmid"])] = int(a.get("duedate") or 0)

    linhas: dict[str, dict] = {}
    soltas: dict[str, str] = {}
    antigas_puladas = 0
    for cid in courseids:
        inicio = int(info_cursos.get(cid, {}).get("startdate") or 0)
        secoes = m.chamar("core_course_get_contents", courseid=cid,
                          options=[{"name": "excludecontents", "value": 1}]) or []
        for sec in secoes:
            for mod in sec.get("modules") or []:
                if mod.get("modname") in ("label", "qbank"):
                    continue
                cmid = int(mod["id"])
                datas = [int(d.get("timestamp") or 0) for d in mod.get("dates") or [] if d.get("timestamp")]
                fechamento = [int(d["timestamp"]) for d in mod.get("dates") or []
                              if d.get("timestamp") and d.get("dataid") in DATAS_PRINCIPAIS]
                principal = prazos.get(cmid) or (max(fechamento) if fechamento else (max(datas) if datas else 0))
                todas = datas + ([prazos[cmid]] if prazos.get(cmid) else [])
                if not incluir_antigas and todas and inicio and max(todas) < inicio:
                    antigas_puladas += 1
                    continue
                exata, solta = _chaves(mod["modname"], mod.get("name", ""))
                chave = exata
                if exata not in linhas and solta and solta in soltas:
                    chave = soltas[solta]
                if chave not in linhas:
                    linhas[chave] = {"chave": chave, "tipo": mod["modname"], "turmas": {}}
                    if solta:
                        soltas.setdefault(solta, chave)
                linhas[chave]["turmas"].setdefault(cid, []).append({
                    "cmid": cmid,
                    "nome": mod.get("name"),
                    "secao": sec.get("name"),
                    "visivel": bool(mod.get("visible", 1)),
                    "data": principal,
                    "data_fmt": fmt_data(principal),
                })

    resultado = []
    for ln in linhas.values():
        dif = []
        ausentes = [c for c in courseids if c not in ln["turmas"]]
        if ausentes:
            dif.append("ausente em " + ", ".join(_rotulo(info_cursos, c) for c in ausentes))
        repetidas = [c for c, v in ln["turmas"].items() if len(v) > 1]
        if repetidas:
            dif.append("repetida em " + ", ".join(_rotulo(info_cursos, c) for c in repetidas))
        presentes = [v[0] for v in ln["turmas"].values()]
        if len({p["nome"] for p in presentes}) > 1:
            dif.append("nome difere")
        if len({p["visivel"] for p in presentes}) > 1:
            dif.append("visibilidade difere")
        datas = {p["data"] for p in presentes}
        if len(datas) > 1:
            dias = {fmt_data(d)[:10] if d else "-" for d in datas}
            dif.append("data difere" if len(dias) > 1 else "horário difere")
        if len({p["secao"] for p in presentes}) > 1:
            dif.append("seção difere")
        resultado.append({
            "chave": ln["chave"],
            "tipo": ln["tipo"],
            "diferencas": dif,
            "turmas": {_rotulo(info_cursos, c): v for c, v in ln["turmas"].items()},
        })
    resultado.sort(key=lambda r: (not r["diferencas"], r["chave"]))
    return {
        "cursos": [{"id": c, "rotulo": _rotulo(info_cursos, c), "nome": info_cursos.get(c, {}).get("fullname")}
                   for c in courseids],
        "atividades": resultado,
        "com_diferenca": sum(1 for r in resultado if r["diferencas"]),
        "antigas_ignoradas": antigas_puladas,
    }


def _rotulo(info_cursos: dict, cid: int) -> str:
    nome = info_cursos.get(cid, {}).get("fullname") or ""
    mt = re.search(r"Turma\s+[\d.]+?(\w+)\]", nome)
    return f"{mt.group(1)} ({cid})" if mt else str(cid)
