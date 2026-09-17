"""Servidor MCP (stdio, JSON-RPC 2.0 por linha) sobre o mesmo núcleo do CLI.

Sem dependências externas. Toda gravação exige um plano gerado antes e
`confirmado_pelo_professor: true` na aplicação.
"""

from __future__ import annotations

import json
import sys
import traceback

from . import __version__, config, ops, planos
from .client import Moodle, MoodleErro

VERSOES = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCOES = (
    "Ferramentas para o Moodle da graduação do Mackenzie, com as permissões do professor logado. "
    "Leitura é livre. Para gravar (notas, trava de envio, aviso): chame uma ferramenta moodle_planejar_*, "
    "mostre ao professor o resumo, os bloqueios, os avisos e a lista de alunos, e só chame "
    "moodle_aplicar_plano depois que ele aprovar explicitamente na conversa. Nunca aplique um plano com "
    "bloqueios. Lançar nota pela API sempre notifica o aluno. Se não houver token, peça ao professor para "
    "rodar `mackmoodle login` no terminal; nunca peça o token ou o link de login na conversa."
)

TAREFA = {"type": "string",
          "description": "id da tarefa (ex.: \"138056\"), 'cm:<cmid>' ou a URL mod/assign/view.php?id=<cmid>"}
CURSOS = {"type": "array", "items": {"type": "integer"}, "minItems": 1, "description": "ids dos cursos"}


def _ferramenta(nome, descricao, props=None, obrig=(), leitura=True, destrutiva=False):
    return {
        "name": nome,
        "description": descricao,
        "inputSchema": {"type": "object", "properties": props or {}, "required": list(obrig),
                        "additionalProperties": False},
        "annotations": {"readOnlyHint": leitura, "destructiveHint": destrutiva,
                        "idempotentHint": leitura, "openWorldHint": True},
    }


FERRAMENTAS = [
    _ferramenta("moodle_status", "Mostra se há login, com qual usuário e site, e testa a conexão com o Moodle."),
    _ferramenta("moodle_cursos", "Lista os cursos do professor (padrão: semestre atual, pelo nome do curso).",
                {"semestre": {"type": "string", "description": "ex.: 2026/2"},
                 "todos": {"type": "boolean", "default": False}}),
    _ferramenta("moodle_tarefas", "Lista as tarefas (assign) de um ou mais cursos, com prazo, data limite e nota "
                "máxima. Com contar=true, inclui alunos, entregas e pendências de avaliação.",
                {"cursos": CURSOS, "contar": {"type": "boolean", "default": False}}, ["cursos"]),
    _ferramenta("moodle_envios", "Mostra, para uma tarefa, cada aluno com status do envio, data, atraso, "
                "extensão, arquivos, texto online e nota atual.",
                {"tarefa": TAREFA, "incluir_texto": {"type": "boolean", "default": True}}, ["tarefa"]),
    _ferramenta("moodle_modelo_csv", "Gera um CSV local com os alunos da tarefa para preencher nota e comentário.",
                {"tarefa": TAREFA, "arquivo": {"type": "string", "description": "caminho do CSV a criar"}},
                ["tarefa", "arquivo"], leitura=False),
    _ferramenta("moodle_planejar_notas_entrega",
                "Planeja (não grava) notas por regra de entrega: nota_entregou para quem enviou e "
                "nota_nao_entregou para os demais. Devolve plano_id, bloqueios, avisos e a lista de alunos.",
                {"tarefa": TAREFA,
                 "nota_entregou": {"type": "number", "description": "padrão: nota máxima"},
                 "nota_nao_entregou": {"type": "number", "default": 0},
                 "rascunho": {"type": "string", "enum": ["ignorar", "zero", "entregou"], "default": "ignorar"},
                 "sobrescrever": {"type": "boolean", "default": False},
                 "forcar_prazo": {"type": "boolean", "default": False},
                 "estado_fluxo": {"type": "string"}}, ["tarefa"]),
    _ferramenta("moodle_planejar_notas_csv",
                "Planeja (não grava) notas e comentários a partir de um CSV local ou de linhas. Cada linha "
                "identifica o aluno por userid, email, ra ou nome e traz 'nota' e, opcionalmente, 'comentario'.",
                {"tarefa": TAREFA,
                 "arquivo": {"type": "string", "description": "caminho de um CSV local"},
                 "linhas": {"type": "array", "items": {"type": "object"}},
                 "sobrescrever": {"type": "boolean", "default": False},
                 "forcar_prazo": {"type": "boolean", "default": False},
                 "ignorar_nao_encontrados": {"type": "boolean", "default": False},
                 "estado_fluxo": {"type": "string"}}, ["tarefa"]),
    _ferramenta("moodle_planejar_trava", "Planeja (não grava) travar ou destravar o envio de alunos numa tarefa.",
                {"tarefa": TAREFA, "usuarios": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
                 "destravar": {"type": "boolean", "default": False}}, ["tarefa", "usuarios"]),
    _ferramenta("moodle_planejar_aviso", "Planeja (não grava) um aviso no fórum de avisos de cada curso indicado.",
                {"cursos": CURSOS, "assunto": {"type": "string"},
                 "mensagem": {"type": "string", "description": "texto simples; parágrafos separados por linha em branco"},
                 "html": {"type": "boolean", "default": False}}, ["cursos", "assunto", "mensagem"]),
    _ferramenta("moodle_ver_plano", "Mostra um plano salvo.", {"plano_id": {"type": "string"}}, ["plano_id"]),
    _ferramenta("moodle_listar_planos", "Lista os planos recentes.", {"n": {"type": "integer", "default": 20}}),
    _ferramenta("moodle_aplicar_plano",
                "GRAVA no Moodle um plano gerado antes. Só chamar depois que o professor aprovou a prévia "
                "na conversa. Recusa planos com bloqueios, aplicados, com mais de 24 h ou se a tarefa mudou.",
                {"plano_id": {"type": "string"},
                 "confirmado_pelo_professor": {"type": "boolean",
                                               "description": "true somente após aprovação explícita"},
                 "travar_sem_entrega": {"type": "boolean", "default": False,
                                        "description": "em planos de notas, trava o envio de quem não entregou"}},
                ["plano_id", "confirmado_pelo_professor"], leitura=False, destrutiva=True),
    _ferramenta("moodle_baixar_envios", "Baixa arquivos e textos enviados para uma pasta local, com indice.csv.",
                {"tarefa": TAREFA, "destino": {"type": "string"},
                 "incluir_rascunhos": {"type": "boolean", "default": False},
                 "sobrescrever": {"type": "boolean", "default": False}}, ["tarefa", "destino"], leitura=False),
    _ferramenta("moodle_comparar_turmas",
                "Compara atividades entre cursos: ausências, nomes, datas, visibilidade e seção. Ignora "
                "atividades de semestres anteriores, salvo incluir_antigas.",
                {"cursos": CURSOS, "incluir_antigas": {"type": "boolean", "default": False},
                 "so_diferencas": {"type": "boolean", "default": True}}, ["cursos"]),
]


def _cliente():
    dados = config.carregar()
    if not dados.get("token"):
        raise MoodleErro("Sem login. É preciso rodar `mackmoodle login` no terminal do professor. "
                         "O token não deve ser copiado para outro lugar.")
    return Moodle(dados["site"], dados["token"]), dados


def _plano_compacto(p: dict) -> dict:
    return {
        "plano_id": p["id"],
        "tipo": p["tipo"],
        "resumo": p["resumo"],
        "bloqueios": p["bloqueios"],
        "avisos": p["avisos"],
        "alvo": p["tarefa"],
        "itens": p["itens"],
        "ignorados": p["ignorados"],
        "opcoes": p.get("opcoes"),
        "aplicado_em": planos.fmt_data(p["aplicado_em"]) if p.get("aplicado_em") else None,
        "relatorio": p.get("relatorio"),
        "proximo_passo": ("Plano bloqueado: corrija e gere de novo." if p["bloqueios"] else
                          "Mostre esta prévia ao professor e só aplique com aprovação explícita."),
    }


def executar(nome: str, a: dict):
    if nome == "moodle_status":
        dados = config.carregar()
        saida = {"site": dados.get("site"), "logado": bool(dados.get("token")), "usuario": dados.get("nome"),
                 "userid": dados.get("userid"), "aviso_validade": config.aviso_validade(dados),
                 "versao": __version__}
        if dados.get("token"):
            try:
                saida["conexao"] = ops.info(Moodle(dados["site"], dados["token"]))
            except MoodleErro as e:
                saida["conexao"] = {"erro": str(e)}
        else:
            saida["como_logar"] = "Rodar `mackmoodle login` no terminal do computador do professor."
        return saida
    if nome == "moodle_ver_plano":
        return _plano_compacto(planos.carregar(a["plano_id"]))
    if nome == "moodle_listar_planos":
        return {"planos": planos.listar(int(a.get("n", 20)))}

    m, dados = _cliente()
    uid = dados["userid"]
    if nome == "moodle_cursos":
        return ops.cursos(m, uid, semestre=a.get("semestre"), todos=bool(a.get("todos")))
    if nome == "moodle_tarefas":
        return ops.tarefas(m, a["cursos"], contar=bool(a.get("contar")))
    if nome == "moodle_envios":
        return ops.envios(m, a["tarefa"], incluir_texto=a.get("incluir_texto", True))
    if nome == "moodle_modelo_csv":
        return ops.modelo_csv_notas(m, a["tarefa"], a["arquivo"])
    if nome == "moodle_planejar_notas_entrega":
        p = ops.planejar_notas_por_entrega(
            m, uid, a["tarefa"], nota_entregou=a.get("nota_entregou"),
            nota_nao_entregou=a.get("nota_nao_entregou", 0), sobrescrever=bool(a.get("sobrescrever")),
            forcar_prazo=bool(a.get("forcar_prazo")), rascunho=a.get("rascunho", "ignorar"),
            estado_fluxo=a.get("estado_fluxo"))
        return _plano_compacto(p)
    if nome == "moodle_planejar_notas_csv":
        if a.get("arquivo"):
            linhas = ops.ler_csv(a["arquivo"])
        elif a.get("linhas") is not None:
            linhas = a["linhas"]
        else:
            raise ValueError("Informe 'arquivo' ou 'linhas'.")
        p = ops.planejar_notas_csv(
            m, uid, a["tarefa"], linhas, sobrescrever=bool(a.get("sobrescrever")),
            forcar_prazo=bool(a.get("forcar_prazo")),
            ignorar_nao_encontrados=bool(a.get("ignorar_nao_encontrados")), estado_fluxo=a.get("estado_fluxo"))
        return _plano_compacto(p)
    if nome == "moodle_planejar_trava":
        return _plano_compacto(ops.planejar_trava(m, uid, a["tarefa"], a["usuarios"],
                                                  destravar=bool(a.get("destravar"))))
    if nome == "moodle_planejar_aviso":
        return _plano_compacto(ops.planejar_aviso(m, uid, a["cursos"], a["assunto"], a["mensagem"],
                                                  html_pronto=bool(a.get("html"))))
    if nome == "moodle_aplicar_plano":
        if a.get("confirmado_pelo_professor") is not True:
            raise ValueError("Aplicação recusada: falta a aprovação explícita do professor "
                             "(confirmado_pelo_professor=true).")
        return ops.aplicar(m, uid, a["plano_id"], travar_sem_entrega=bool(a.get("travar_sem_entrega")))
    if nome == "moodle_baixar_envios":
        return ops.baixar(m, a["tarefa"], a["destino"], incluir_rascunhos=bool(a.get("incluir_rascunhos")),
                          sobrescrever=bool(a.get("sobrescrever")))
    if nome == "moodle_comparar_turmas":
        r = ops.comparar(m, a["cursos"], incluir_antigas=bool(a.get("incluir_antigas")))
        if a.get("so_diferencas", True):
            r["atividades"] = [x for x in r["atividades"] if x["diferencas"]]
        return r
    raise ValueError(f"Ferramenta desconhecida: {nome}")


def _resposta(id_, resultado=None, erro=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if erro is not None:
        msg["error"] = erro
    else:
        msg["result"] = resultado
    return msg


def tratar(msg: dict):
    metodo = msg.get("method")
    id_ = msg.get("id")
    params = msg.get("params") or {}
    if id_ is None:  # notificação
        return None
    if metodo == "initialize":
        pedida = params.get("protocolVersion")
        return _resposta(id_, {
            "protocolVersion": pedida if pedida in VERSOES else VERSOES[1],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "mackmoodle", "version": __version__},
            "instructions": INSTRUCOES,
        })
    if metodo == "ping":
        return _resposta(id_, {})
    if metodo == "tools/list":
        return _resposta(id_, {"tools": FERRAMENTAS})
    if metodo == "tools/call":
        nome = params.get("name")
        args = params.get("arguments") or {}
        if nome not in {f["name"] for f in FERRAMENTAS}:
            return _resposta(id_, erro={"code": -32602, "message": f"Ferramenta desconhecida: {nome}"})
        try:
            dado = executar(nome, args)
            texto = json.dumps(dado, ensure_ascii=False, default=str)
            res = {"content": [{"type": "text", "text": texto}], "isError": False}
            if isinstance(dado, dict):
                res["structuredContent"] = json.loads(texto)
            return _resposta(id_, res)
        except (MoodleErro, ValueError, FileNotFoundError, KeyError) as e:
            return _resposta(id_, {"content": [{"type": "text", "text": f"Erro: {e}"}], "isError": True})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return _resposta(id_, {"content": [{"type": "text", "text": f"Erro interno: {e}"}], "isError": True})
    if metodo in ("resources/list", "prompts/list"):
        chave = metodo.split("/")[0]
        return _resposta(id_, {chave: []})
    return _resposta(id_, erro={"code": -32601, "message": f"Método não suportado: {metodo}"})


def main() -> int:
    # O protocolo usa UTF-8; no Windows os fluxos padrão usam outra codificação.
    for fluxo, modo in ((sys.stdin, "strict"), (sys.stdout, "strict")):
        try:
            fluxo.reconfigure(encoding="utf-8", errors=modo)
        except (AttributeError, ValueError):
            pass
    entrada = sys.stdin
    for linha in entrada:
        linha = linha.strip()
        if not linha:
            continue
        try:
            msg = json.loads(linha)
        except json.JSONDecodeError:
            saida = _resposta(None, erro={"code": -32700, "message": "JSON inválido"})
        else:
            if isinstance(msg, list):
                respostas = [r for r in (tratar(x) for x in msg) if r is not None]
                saida = respostas or None
            else:
                saida = tratar(msg)
        if saida is not None:
            sys.stdout.write(json.dumps(saida, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
