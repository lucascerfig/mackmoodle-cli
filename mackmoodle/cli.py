"""CLI `mackmoodle`.

Toda gravação segue dois passos: o comando gera e salva um plano (prévia);
`mackmoodle aplicar <plano>` grava. O atalho `--aplicar` faz os dois com
confirmação interativa (ou `--sim`, para quando a confirmação já foi dada).
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import sys
import webbrowser

from . import SITE_PADRAO, __version__, auth, config, ops, planos
from .client import Moodle, MoodleErro


# ============================================================ saída
def tabela(linhas: list[dict], colunas: list[tuple[str, str]], limite_col: int = 48) -> str:
    if not linhas:
        return "(nenhum)"
    cab = [t for _, t in colunas]
    corpo = []
    for l in linhas:
        linha = []
        for k, _ in colunas:
            v = l.get(k)
            if v is None:
                v = ""
            elif isinstance(v, bool):
                v = "sim" if v else ""
            elif isinstance(v, float):
                v = f"{v:g}"
            v = str(v).replace("\n", " ")
            if len(v) > limite_col:
                v = v[: limite_col - 1] + "…"
            linha.append(v)
        corpo.append(linha)
    larg = [max(len(cab[i]), *(len(r[i]) for r in corpo)) for i in range(len(cab))]
    fmt = "  ".join("{:<%d}" % w for w in larg)
    saida = [fmt.format(*cab), fmt.format(*("-" * w for w in larg))]
    saida += [fmt.format(*r) for r in corpo]
    return "\n".join(saida)


def imprimir_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def mostrar_plano(p: dict) -> None:
    print(f"\nPlano {p['id']} ({p['tipo']})")
    print(p.get("resumo", ""))
    t = p.get("tarefa") or {}
    if p["tipo"] in ("notas", "trava"):
        curso = t.get("curso") or {}
        print(f"Curso: {curso.get('nome') or curso.get('id')}")
        print(f"Tarefa: {t.get('nome')} (id {t.get('id')}, cmid {t.get('cmid')})")
        print(f"Prazo: {t.get('prazo_fmt')}  Data limite: {t.get('data_limite_fmt')}  "
              f"Nota máxima: {t.get('nota_maxima') if t.get('nota_maxima') is not None else '-'}")
    if p["tipo"] == "aviso":
        print(f"Assunto: {t.get('assunto')}")
        print("Mensagem (HTML):")
        print("  " + (t.get("mensagem_html") or "")[:600])
    if p.get("bloqueios"):
        print("\nBLOQUEIOS (o plano não pode ser aplicado):")
        for b in p["bloqueios"]:
            print(f"  - {b}")
    if p.get("avisos"):
        print("\nAvisos:")
        for a in p["avisos"]:
            print(f"  - {a}")
    print()
    if p["tipo"] == "notas":
        print(tabela(p["itens"], [("nome", "Aluno"), ("status", "Envio"), ("nota_atual", "Nota atual"),
                                  ("nota", "Nova nota"), ("comentario", "Comentário")]))
    elif p["tipo"] == "trava":
        print(f"Ação: {p['opcoes']['acao']}")
        print(tabela(p["itens"], [("nome", "Aluno"), ("status", "Envio"), ("userid", "userid")]))
    elif p["tipo"] == "aviso":
        print(tabela(p["itens"], [("curso_nome", "Curso"), ("forum_nome", "Fórum")], limite_col=70))
    if p.get("ignorados"):
        print("\nIgnorados:")
        print(tabela(p["ignorados"], [("nome", "Aluno"), ("status", "Envio"), ("motivo", "Motivo")]))
    if p.get("aplicado_em"):
        print(f"\nAplicado em {planos.fmt_data(p['aplicado_em'])}.")


# ============================================================ contexto
def _cliente(exigir_login: bool = True) -> tuple[Moodle, dict]:
    dados = config.carregar()
    if exigir_login and not dados.get("token"):
        raise MoodleErro("Sem token do Moodle. Rode `mackmoodle login`.")
    aviso = config.aviso_validade(dados)
    if aviso:
        print(f"Atenção: {aviso}", file=sys.stderr)
    return Moodle(dados["site"], dados.get("token")), dados


def _confirmar(args, pergunta: str) -> bool:
    if getattr(args, "sim", False):
        return True
    if not sys.stdin.isatty():
        print("Sem terminal interativo: use --sim para confirmar.", file=sys.stderr)
        return False
    resp = input(f"{pergunta} [s/N] ").strip().lower()
    return resp in ("s", "sim", "y", "yes")


def _depois_de_planejar(args, m: Moodle, dados: dict, plano: dict) -> int:
    if args.json and not args.aplicar:
        imprimir_json(plano)
        return 0 if not plano["bloqueios"] else 3
    if not args.json:
        mostrar_plano(plano)
    if plano["bloqueios"]:
        if args.json:
            imprimir_json(plano)
        return 3
    if not args.aplicar:
        print(f"\nPlano salvo. Para gravar: mackmoodle aplicar {plano['id']}"
              + (" --travar-sem-entrega" if getattr(args, "travar_sem_entrega", False) else ""))
        return 0
    if not _confirmar(args, "Gravar no Moodle?"):
        print(f"Nada foi gravado. O plano continua salvo: {plano['id']}")
        return 1
    rel = ops.aplicar(m, dados["userid"], plano["id"],
                      travar_sem_entrega=getattr(args, "travar_sem_entrega", False))
    _mostrar_relatorio(args, rel)
    return 0 if not rel.get("divergencias") else 4


def _mostrar_relatorio(args, rel: dict) -> None:
    if args.json:
        imprimir_json(rel)
        return
    if rel["tipo"] == "notas":
        print(f"\nGravadas {rel['gravadas']} nota(s); {rel['travados']} envio(s) travado(s).")
        r = rel.get("resumo_depois") or {}
        print(f"Agora: {r.get('com_nota')} de {r.get('alunos')} alunos com nota.")
        if rel["divergencias"]:
            print("DIVERGÊNCIAS na conferência:")
            print(tabela(rel["divergencias"], [("nome", "Aluno"), ("esperado", "Esperado"), ("encontrado", "No Moodle")]))
        else:
            print("Conferência: todas as notas batem com o plano.")
    elif rel["tipo"] == "trava":
        print(f"\n{rel['acao'].capitalize()}: {rel['alunos']} aluno(s).")
    elif rel["tipo"] == "aviso":
        print(f"\nAviso postado em {len(rel['postados'])} curso(s).")
        for p in rel["postados"]:
            print(f"  curso {p['curso']}: discussão {p['discussao']}")


# ============================================================ comandos
def cmd_login(args) -> int:
    dados = config.carregar()
    site = (args.site or dados.get("site") or SITE_PADRAO).rstrip("/")
    passport = auth.novo_passport()
    url = auth.url_login(site, passport)
    print("1. Abra este endereço no navegador e faça o login do Office 365, se ele pedir:\n")
    print(f"   {url}\n")
    print("2. A página de confirmação tenta abrir o app do Moodle. Se o navegador perguntar, cancele.")
    print("3. Clique com o botão direito no link 'Clique aqui...' da página e copie o endereço do link.")
    print("4. Cole abaixo (começa com moodlemobile://token=). O token não é exibido.\n")
    if not args.sem_navegador:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            pass
    try:
        # getpass não ecoa o link colado, para o token não ficar no histórico do terminal.
        colado = getpass.getpass("Link (não aparece ao colar): ") if sys.stdin.isatty() else sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado.")
        return 1
    siteid, token = auth.extrair_token(colado)
    if not auth.conferir_siteid(site, passport, siteid):
        print("Atenção: o link não corresponde a este pedido de login (site ou sessão diferente). "
              "Vou validar o token mesmo assim.", file=sys.stderr)
    novo = auth.validar(site, token)
    novo.pop("n_funcoes", None)
    arq = config.salvar(novo)
    print(f"\nLogin feito como {novo['nome']} (userid {novo['userid']}) em {novo['site']}.")
    print(f"Token salvo em {arq} (permissão 600). Pelo padrão do Moodle, ele vale 12 semanas "
          "e é o mesmo usado pelo app no celular.")
    return 0


def cmd_logout(args) -> int:
    if config.apagar():
        print("Token removido deste computador. Ele continua válido no Moodle até expirar.")
    else:
        print("Não havia token salvo.")
    return 0


def cmd_status(args) -> int:
    dados = config.carregar()
    saida = {
        "site": dados.get("site"),
        "logado": bool(dados.get("token")),
        "origem_token": dados.get("origem_token", "arquivo" if dados.get("token") else None),
        "usuario": dados.get("nome"),
        "userid": dados.get("userid"),
        "token_obtido_ha_dias": round(config.idade_token_dias(dados) or 0, 1) if dados.get("obtido_em") else None,
        "arquivo_config": str(config.arquivo_config()),
        "versao": __version__,
    }
    if dados.get("token") and not args.offline:
        try:
            saida["conexao"] = ops.info(Moodle(dados["site"], dados["token"]))
        except MoodleErro as e:
            saida["conexao"] = {"erro": str(e)}
    if args.json:
        imprimir_json(saida)
    else:
        for k, v in saida.items():
            print(f"{k}: {v}")
        aviso = config.aviso_validade(dados)
        if aviso:
            print(f"Atenção: {aviso}")
    return 0


def cmd_cursos(args) -> int:
    m, dados = _cliente()
    r = ops.cursos(m, dados["userid"], semestre=args.semestre, todos=args.todos)
    if args.json:
        imprimir_json(r)
    else:
        print(f"Semestre: {r['semestre']}")
        print(tabela(r["cursos"], [("id", "id"), ("nome", "Curso")], limite_col=90))
    return 0


def cmd_tarefas(args) -> int:
    m, _ = _cliente()
    r = ops.tarefas(m, args.cursos, contar=args.contar)
    if args.json:
        imprimir_json(r)
        return 0
    cols = [("curso", "Curso"), ("tarefa", "Tarefa"), ("cmid", "cmid"), ("nome", "Nome"), ("prazo", "Prazo"),
            ("data_limite", "Limite"), ("nota_max", "Máx")]
    if args.contar:
        cols += [("alunos", "Alunos"), ("entregas", "Entregas"), ("a_avaliar", "A avaliar")]
    print(tabela(r["tarefas"], cols))
    if r["avisos"]:
        print(f"\n{len(r['avisos'])} atividade(s) sem acesso foram ignoradas pelo Moodle.")
    return 0


def cmd_envios(args) -> int:
    m, _ = _cliente()
    r = ops.envios(m, args.tarefa, incluir_texto=True)
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            campos = ["userid", "nome", "email", "ra", "status", "enviado_em", "atrasado", "extensao_ate",
                      "nota", "arquivos", "texto"]
            w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
            w.writeheader()
            for a in r["alunos"]:
                w.writerow({**a, "arquivos": " | ".join(x["nome"] for x in a["arquivos"])})
    if args.json:
        imprimir_json(r)
        return 0
    t = r["tarefa"]
    print(f"{t['curso'].get('nome')}\n{t['nome']} (id {t['id']})  prazo {t['prazo_fmt']}"
          f"{' (aberto)' if t['prazo_aberto'] else ''}  limite {t['data_limite_fmt']}  máx {t['nota_maxima']}")
    linhas = [{**a, "n_arq": len(a["arquivos"])} for a in r["alunos"]]
    cols = [("nome", "Aluno"), ("ra", "RA"), ("status", "Envio"), ("enviado_em", "Enviado em"),
            ("atrasado", "Atrasado"), ("extensao_ate", "Extensão até"), ("n_arq", "Arq."), ("nota", "Nota")]
    if args.texto:
        cols.append(("texto", "Texto"))
    print(tabela(linhas, cols))
    s = r["resumo"]
    print(f"\n{s['alunos']} alunos, {s['entregaram']} entregaram, {s['rascunhos']} rascunho(s), "
          f"{s['com_nota']} com nota, {s['com_extensao']} com extensão.")
    if args.csv:
        print(f"CSV salvo em {args.csv}")
    return 0


def cmd_modelo_csv(args) -> int:
    m, _ = _cliente()
    r = ops.modelo_csv_notas(m, args.tarefa, args.arquivo)
    imprimir_json(r) if args.json else print(f"Modelo com {r['alunos']} alunos salvo em {r['arquivo']}. "
                                            "Preencha as colunas 'nota' e 'comentario'.")
    return 0


def cmd_notas_entrega(args) -> int:
    m, dados = _cliente()
    p = ops.planejar_notas_por_entrega(
        m, dados["userid"], args.tarefa, nota_entregou=args.nota_entregou,
        nota_nao_entregou=args.nota_nao_entregou, sobrescrever=args.sobrescrever,
        forcar_prazo=args.forcar_prazo, rascunho=args.rascunho, estado_fluxo=args.estado_fluxo)
    return _depois_de_planejar(args, m, dados, p)


def cmd_notas_csv(args) -> int:
    m, dados = _cliente()
    linhas = ops.ler_csv(args.arquivo)
    p = ops.planejar_notas_csv(
        m, dados["userid"], args.tarefa, linhas, sobrescrever=args.sobrescrever,
        forcar_prazo=args.forcar_prazo, ignorar_nao_encontrados=args.ignorar_nao_encontrados,
        estado_fluxo=args.estado_fluxo)
    return _depois_de_planejar(args, m, dados, p)


def cmd_travar(args) -> int:
    m, dados = _cliente()
    p = ops.planejar_trava(m, dados["userid"], args.tarefa, args.usuarios, destravar=args.destravar)
    return _depois_de_planejar(args, m, dados, p)


def cmd_aviso(args) -> int:
    m, dados = _cliente()
    if args.arquivo:
        with open(args.arquivo, encoding="utf-8") as f:
            mensagem = f.read()
    else:
        mensagem = args.mensagem or ""
    p = ops.planejar_aviso(m, dados["userid"], args.cursos, args.assunto, mensagem, html_pronto=args.html)
    return _depois_de_planejar(args, m, dados, p)


def cmd_aplicar(args) -> int:
    m, dados = _cliente()
    p = planos.carregar(args.plano)
    if not args.json:
        mostrar_plano(p)
    planos.pronto_para_aplicar(p)
    if not _confirmar(args, "Gravar no Moodle?"):
        print("Nada foi gravado.")
        return 1
    rel = ops.aplicar(m, dados["userid"], args.plano, travar_sem_entrega=args.travar_sem_entrega)
    _mostrar_relatorio(args, rel)
    return 0 if not rel.get("divergencias") else 4


def cmd_planos(args) -> int:
    lista = planos.listar(args.n)
    if args.json:
        imprimir_json(lista)
    else:
        print(tabela(lista, [("id", "Plano"), ("criado_em", "Criado"), ("aplicado_em", "Aplicado"),
                             ("bloqueios", "Bloq."), ("resumo", "Resumo")], limite_col=80))
    return 0


def cmd_plano(args) -> int:
    p = planos.carregar(args.plano)
    imprimir_json(p) if args.json else mostrar_plano(p)
    return 0


def cmd_baixar(args) -> int:
    m, _ = _cliente()
    r = ops.baixar(m, args.tarefa, args.destino, incluir_rascunhos=args.rascunhos, sobrescrever=args.sobrescrever)
    if args.json:
        imprimir_json(r)
    else:
        print(f"{r['alunos']} aluno(s); {r['baixados']} arquivo(s) baixado(s), {r['ja_existiam']} já existiam.")
        print(f"Pasta: {r['pasta']}\nÍndice: {r['indice']}")
    return 0


def cmd_comparar(args) -> int:
    m, _ = _cliente()
    r = ops.comparar(m, args.cursos, incluir_antigas=args.incluir_antigas)
    if args.json:
        imprimir_json(r)
        return 0
    rotulos = [c["rotulo"] for c in r["cursos"]]
    linhas = []
    for a in r["atividades"]:
        if args.so_diferencas and not a["diferencas"]:
            continue
        l = {"atividade": a["chave"].split(":", 1)[1], "tipo": a["tipo"], "dif": "; ".join(a["diferencas"])}
        for rot in rotulos:
            v = a["turmas"].get(rot)
            l[rot] = (v[0]["data_fmt"] if v[0]["data"] else "ok") + ("" if v[0]["visivel"] else " (oculta)") if v else "-"
        linhas.append(l)
    cols = [("atividade", "Atividade"), ("tipo", "Tipo")] + [(r_, r_) for r_ in rotulos] + [("dif", "Diferenças")]
    print(tabela(linhas, cols, limite_col=40))
    print(f"\n{r['com_diferenca']} atividade(s) com diferença; {r['antigas_ignoradas']} atividade(s) de "
          "semestres anteriores ignoradas (use --incluir-antigas para ver).")
    return 0


# ============================================================ parser
TAREFA_AJUDA = "id da tarefa, cm:<cmid> ou a URL da tarefa (mod/assign/view.php?id=...)"
EPILOGO = ("Comandos que alteram o Moodle (notas, travar, aviso) geram primeiro um plano com a prévia. "
           "Nada é gravado até `mackmoodle aplicar <plano>` ou até a opção --aplicar ser confirmada.")


def _flags_escrita(p):
    p.add_argument("--aplicar", action="store_true", help="gravar logo após a prévia (pede confirmação)")
    p.add_argument("--sim", action="store_true",
                   help="confirmar sem perguntar (usar só depois de revisar a prévia)")


def construir_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mackmoodle",
        description="Linha de comando para o Moodle da graduação do Mackenzie.",
        epilog=EPILOGO)
    ap.add_argument("--version", action="version", version=f"mackmoodle {__version__}")
    ap.add_argument("--json", action="store_true", help="saída em JSON, para uso em scripts")
    sub = ap.add_subparsers(dest="comando", required=True, metavar="comando")

    p = sub.add_parser("login", help="entrar com a conta do Office 365 e guardar o token")
    p.add_argument("--site", help=f"endereço do Moodle (padrão: {SITE_PADRAO})")
    p.add_argument("--sem-navegador", action="store_true", help="só mostrar o endereço, sem abrir o navegador")
    p.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="apagar o token guardado neste computador").set_defaults(func=cmd_logout)

    p = sub.add_parser("status", help="mostrar a configuração e testar a conexão")
    p.add_argument("--offline", action="store_true", help="não testar a conexão")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("cursos", help="listar os seus cursos")
    p.add_argument("--semestre", help="texto procurado no nome do curso, ex.: 2026/2 (padrão: semestre atual)")
    p.add_argument("--todos", action="store_true", help="listar cursos de todos os semestres")
    p.set_defaults(func=cmd_cursos)

    p = sub.add_parser("tarefas", help="listar as tarefas de um ou mais cursos")
    p.add_argument("cursos", nargs="+", type=int, metavar="curso", help="id do curso (ver `mackmoodle cursos`)")
    p.add_argument("--contar", action="store_true", help="incluir alunos, entregas e pendências (mais lento)")
    p.set_defaults(func=cmd_tarefas)

    p = sub.add_parser("envios", help="situação de cada aluno numa tarefa")
    p.add_argument("tarefa", help=TAREFA_AJUDA)
    p.add_argument("--csv", metavar="ARQUIVO", help="salvar a lista em CSV")
    p.add_argument("--texto", action="store_true", help="mostrar o texto online enviado")
    p.set_defaults(func=cmd_envios)

    p = sub.add_parser("modelo-csv", help="gerar planilha da tarefa para preencher nota e comentário")
    p.add_argument("tarefa", help=TAREFA_AJUDA)
    p.add_argument("arquivo", help="caminho do CSV a criar")
    p.set_defaults(func=cmd_modelo_csv)

    pn = sub.add_parser("notas", help="lançar notas (por regra de entrega ou por CSV)")
    subn = pn.add_subparsers(dest="modo", required=True, metavar="modo")
    p = subn.add_parser("entrega", help="uma nota para quem entregou e outra para quem não entregou",
                        epilog=EPILOGO)
    p.add_argument("tarefa", help=TAREFA_AJUDA)
    p.add_argument("--nota-entregou", type=float, metavar="N", help="nota de quem entregou (padrão: nota máxima)")
    p.add_argument("--nota-nao-entregou", type=float, default=0.0, metavar="N",
                   help="nota de quem não entregou (padrão: 0)")
    p.add_argument("--rascunho", choices=["ignorar", "zero", "entregou"], default="ignorar",
                   help="tratamento de rascunho não enviado (padrão: ignorar)")
    p.add_argument("--sobrescrever", action="store_true", help="alterar também quem já tem nota")
    p.add_argument("--forcar-prazo", action="store_true", help="permitir com prazo ou extensão ainda em aberto")
    p.add_argument("--estado-fluxo", metavar="ESTADO",
                   help="estado do fluxo de avaliação, quando a tarefa usa (ex.: released)")
    p.add_argument("--travar-sem-entrega", action="store_true", help="travar o envio de quem não entregou")
    _flags_escrita(p)
    p.set_defaults(func=cmd_notas_entrega)

    p = subn.add_parser("csv", help="nota e comentário por aluno a partir de um CSV", epilog=EPILOGO)
    p.add_argument("tarefa", help=TAREFA_AJUDA)
    p.add_argument("arquivo", help="CSV com uma coluna de identificação (userid, email, ra ou nome), "
                                   "a coluna nota e, se quiser, a coluna comentario")
    p.add_argument("--sobrescrever", action="store_true", help="alterar também quem já tem nota")
    p.add_argument("--forcar-prazo", action="store_true", help="permitir com prazo ainda em aberto")
    p.add_argument("--ignorar-nao-encontrados", action="store_true",
                   help="seguir mesmo com linhas que não correspondem a nenhum aluno")
    p.add_argument("--estado-fluxo", metavar="ESTADO",
                   help="estado do fluxo de avaliação, quando a tarefa usa (ex.: released)")
    p.add_argument("--travar-sem-entrega", action="store_true",
                   help="travar o envio dos alunos do CSV que não entregaram")
    _flags_escrita(p)
    p.set_defaults(func=cmd_notas_csv)

    p = sub.add_parser("travar", help="travar ou destravar o envio de alunos", epilog=EPILOGO)
    p.add_argument("tarefa", help=TAREFA_AJUDA)
    p.add_argument("usuarios", nargs="+", type=int, metavar="userid",
                   help="id do aluno no Moodle (ver `mackmoodle envios`)")
    p.add_argument("--destravar", action="store_true", help="liberar o envio em vez de travar")
    _flags_escrita(p)
    p.set_defaults(func=cmd_travar)

    p = sub.add_parser("aviso", help="publicar o mesmo aviso no fórum de avisos de vários cursos",
                       epilog=EPILOGO)
    p.add_argument("cursos", nargs="+", type=int, metavar="curso", help="id do curso")
    p.add_argument("--assunto", required=True, help="assunto do aviso")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--mensagem", help="texto do aviso")
    g.add_argument("--arquivo", help="arquivo de texto com a mensagem")
    p.add_argument("--html", action="store_true", help="a mensagem já está em HTML")
    _flags_escrita(p)
    p.set_defaults(func=cmd_aviso)

    p = sub.add_parser("aplicar", help="gravar no Moodle um plano gerado antes")
    p.add_argument("plano", help="id do plano (ex.: notas-20260916-181500-ab12)")
    p.add_argument("--travar-sem-entrega", action="store_true",
                   help="em planos de notas, travar o envio de quem não entregou")
    p.add_argument("--sim", action="store_true", help="confirmar sem perguntar")
    p.set_defaults(func=cmd_aplicar)

    p = sub.add_parser("planos", help="listar os planos recentes")
    p.add_argument("-n", type=int, default=20, help="quantidade (padrão: 20)")
    p.set_defaults(func=cmd_planos)

    p = sub.add_parser("plano", help="mostrar um plano")
    p.add_argument("plano", help="id do plano")
    p.set_defaults(func=cmd_plano)

    p = sub.add_parser("baixar", help="baixar os arquivos e textos enviados numa tarefa")
    p.add_argument("tarefa", help=TAREFA_AJUDA)
    p.add_argument("--destino", default="./envios", help="pasta de destino (padrão: ./envios)")
    p.add_argument("--rascunhos", action="store_true", help="incluir rascunhos não enviados")
    p.add_argument("--sobrescrever", action="store_true", help="baixar de novo arquivos já existentes")
    p.set_defaults(func=cmd_baixar)

    p = sub.add_parser("comparar", help="comparar atividades, nomes e datas entre cursos")
    p.add_argument("cursos", nargs="+", type=int, metavar="curso", help="id do curso (dois ou mais)")
    p.add_argument("--incluir-antigas", action="store_true", help="incluir atividades de semestres anteriores")
    p.add_argument("--so-diferencas", action="store_true", help="mostrar só as atividades com diferença")
    p.set_defaults(func=cmd_comparar)

    return ap


def _saida_tolerante() -> None:
    # No Windows, a saída redirecionada usa a codificação local; caracteres fora dela
    # são substituídos em vez de interromper o programa.
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    _saida_tolerante()
    ap = construir_parser()
    args = ap.parse_args(argv)
    for nome in ("aplicar", "sim", "travar_sem_entrega"):
        if not hasattr(args, nome):
            setattr(args, nome, False)
    try:
        return args.func(args)
    except (MoodleErro, ValueError, FileNotFoundError) as e:
        if args.json:
            imprimir_json({"erro": str(e)})
        else:
            print(f"Erro: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
