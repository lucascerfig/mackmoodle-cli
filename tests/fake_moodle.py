"""Moodle falso para os testes: imita as respostas do 4.2 da graduação.

Os formatos seguem o que foi observado em 16/09/2026 (mod_assign_list_participants
só funciona com onlyids=true; core_enrol_get_enrolled_users precisa de userfields).
"""

from __future__ import annotations

import copy
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "0123456789abcdef0123456789abcdef"
PROFESSOR = 44497
AGORA = 1789593300  # 16/09/2026 18:15 (-03)
DIA = 86400


def parse_php(pares):
    raiz: dict = {}
    for chave, valor in pares:
        nomes = [re.match(r"^[^\[]+", chave).group(0)] + re.findall(r"\[([^\]]*)\]", chave)
        no = raiz
        for n in nomes[:-1]:
            no = no.setdefault(n, {})
        no[nomes[-1]] = valor
    return _listas(raiz)


def _listas(no):
    if isinstance(no, dict):
        no = {k: _listas(v) for k, v in no.items()}
        if no and all(k.isdigit() for k in no):
            return [no[k] for k in sorted(no, key=int)]
    return no


def excecao(codigo, msg):
    return {"exception": "moodle_exception", "errorcode": codigo, "message": msg}


def aluno(uid, nome, ra):
    return {"id": uid, "fullname": nome, "email": f"{ra}@mackenzista.com.br"}


def estado_inicial():
    alunos_j12 = [aluno(100 + i, n, f"1043{i:04d}") for i, n in enumerate([
        "Ana Beatriz Souza", "Bruno Lima", "Carla Exemplo Dias", "Débora Nunes", "Eduardo Paes",
        "Fábio Rocha", "Gabriela Torres", "Mauro Teste Lopes", "Otávio Reis"])]
    alunos_j11 = [aluno(200 + i, f"Aluno J11 {i:02d}", f"1044{i:04d}") for i in range(19)]
    alunos_04n = [aluno(300 + i, f"Aluno 04N {i:02d}", f"1045{i:04d}") for i in range(5)]
    alunos_04d = [aluno(400 + i, f"Aluno 04D {i:02d}", f"1046{i:04d}") for i in range(5)]

    def arquivo(uid, nome):
        return {"filename": nome, "filepath": "/", "filesize": 11,
                "fileurl": f"/webservice/pluginfile.php/1/assignsubmission_file/submission_files/{uid}/{nome}"}

    def cfg(*plugins):
        return [{"plugin": p.split(":")[1], "subtype": p.split(":")[0], "name": "enabled", "value": "1"} for p in plugins]

    tarefas = {
        138056: {"id": 138056, "cmid": 1042655, "course": 34923, "name": "Lista 1 - Processos", "grade": 100,
                 "duedate": AGORA - 6 * DIA, "cutoffdate": 0, "teamsubmission": 0, "markingworkflow": 0,
                 "submissiondrafts": 0, "blindmarking": 0,
                 "configs": cfg("assignsubmission:file", "assignsubmission:comments", "assignfeedback:comments")},
        138091: {"id": 138091, "cmid": 1042886, "course": 34922, "name": "Lista 1 - Processos", "grade": 100,
                 "duedate": AGORA + 6 * 3600, "cutoffdate": 0, "teamsubmission": 0, "markingworkflow": 0,
                 "submissiondrafts": 0, "blindmarking": 0,
                 "configs": cfg("assignsubmission:file", "assignfeedback:comments")},
        5001: {"id": 5001, "cmid": 9001, "course": 35270, "name": "Lab 4 - Threads", "grade": 10,
               "duedate": AGORA - 7 * DIA, "cutoffdate": 0, "teamsubmission": 0, "markingworkflow": 0,
               "submissiondrafts": 1, "blindmarking": 0,
               "configs": cfg("assignsubmission:onlinetext")},
        5002: {"id": 5002, "cmid": 9002, "course": 41247, "name": "Laboratório 4 - Threads e Sincronização",
               "grade": 10, "duedate": AGORA - 6 * DIA, "cutoffdate": AGORA - 5 * DIA, "teamsubmission": 0,
               "markingworkflow": 0, "submissiondrafts": 0, "blindmarking": 0,
               "configs": cfg("assignsubmission:onlinetext", "assignfeedback:comments")},
        5003: {"id": 5003, "cmid": 9003, "course": 41247, "name": "Projeto 1 - Simulador", "grade": 100,
               "duedate": AGORA - DIA, "cutoffdate": 0, "teamsubmission": 1, "markingworkflow": 0,
               "submissiondrafts": 0, "blindmarking": 0, "configs": cfg("assignsubmission:file")},
        5004: {"id": 5004, "cmid": 9004, "course": 41247, "name": "Lista antiga 2025", "grade": -3,
               "duedate": AGORA - 300 * DIA, "cutoffdate": 0, "teamsubmission": 0, "markingworkflow": 0,
               "submissiondrafts": 0, "blindmarking": 0, "configs": cfg("assignsubmission:file")},
    }
    entregas = {
        138056: {u["id"]: {"status": "submitted", "timemodified": AGORA - 8 * DIA,
                           "files": [arquivo(u["id"], "lista1.pdf")]}
                 for u in alunos_j12 if u["fullname"] not in ("Carla Exemplo Dias", "Mauro Teste Lopes")},
        138091: {u["id"]: {"status": "submitted", "timemodified": AGORA - DIA, "files": [arquivo(u["id"], "l1.pdf")]}
                 for u in alunos_j11[:8]},
        5001: {300: {"status": "submitted", "timemodified": AGORA - 8 * DIA, "text": "<p>https://github.com/fci-sisop/lab4-ana</p>"},
               301: {"status": "draft", "timemodified": AGORA - 8 * DIA, "text": "<p>rascunho</p>"},
               302: {"status": "submitted", "timemodified": AGORA - 6 * DIA, "text": "https://github.com/fci-sisop/lab4-x"}},
        5002: {400: {"status": "submitted", "timemodified": AGORA - 7 * DIA, "text": "repo 400"}},
        5003: {}, 5004: {},
    }
    # J11: status "new" para 3 e sem registro para os demais, como no Moodle real.
    for u in alunos_j11[8:11]:
        entregas[138091][u["id"]] = {"status": "new", "timemodified": AGORA - 10 * DIA, "files": []}
    for u in alunos_j12:
        if u["fullname"] in ("Carla Exemplo Dias", "Mauro Teste Lopes"):
            entregas[138056][u["id"]] = {"status": "new", "timemodified": AGORA - 10 * DIA, "files": []}
    return {
        "cursos": {
            34922: {"id": 34922, "shortname": "1|201815116.000.02J11|ENEC50545",
                    "fullname": "INTRODUCAO A SISTEMAS OPERACIONAIS [Turma 201815116.000.02J11] - 2026/2",
                    "startdate": AGORA - 40 * DIA, "alunos": alunos_j11},
            34923: {"id": 34923, "shortname": "1|201815116.000.02J12|ENEC50545",
                    "fullname": "INTRODUCAO A SISTEMAS OPERACIONAIS [Turma 201815116.000.02J12] - 2026/2",
                    "startdate": AGORA - 40 * DIA, "alunos": alunos_j12},
            35270: {"id": 35270, "shortname": "1|201815117.000.04N|ENEX51032",
                    "fullname": "SISTEMAS OPERACIONAIS [Turma 201815117.000.04N] - 2026/2",
                    "startdate": AGORA - 40 * DIA, "alunos": alunos_04n},
            41247: {"id": 41247, "shortname": "1|201815114.000.04D|ENEX51032",
                    "fullname": "SISTEMAS OPERACIONAIS [Turma 201815114.000.04D] - 2026/2",
                    "startdate": AGORA - 40 * DIA, "alunos": alunos_04d},
            1000: {"id": 1000, "shortname": "velho", "fullname": "SISTEMAS OPERACIONAIS [Turma X] - 2026/1",
                   "startdate": AGORA - 250 * DIA, "alunos": []},
        },
        "tarefas": tarefas,
        "entregas": entregas,
        "extensoes": {5001: {302: AGORA + DIA}},
        "notas": {},
        "travas": set(),
        "foruns": {c: {"id": 7000 + i, "course": c, "type": "news", "name": "Avisos"}
                   for i, c in enumerate([34922, 34923, 35270])},
        "discussoes": [],
        "conteudo": {
            35270: [
                {"name": "Laboratórios", "modules": [
                    {"id": 9001, "name": "Lab 4 - Threads", "modname": "assign", "visible": 1, "dates": []},
                    {"id": 9101, "name": "Laboratório 5 - Sincronização", "modname": "assign", "visible": 1,
                     "dates": [{"label": "Aberto:", "timestamp": AGORA - DIA, "dataid": "allowsubmissionsfromdate"},
                               {"label": "Vencimento:", "timestamp": AGORA + DIA, "dataid": "duedate"}]},
                    {"id": 9102, "name": "Lista 1 (2025)", "modname": "assign", "visible": 0,
                     "dates": [{"label": "Vencimento:", "timestamp": AGORA - 300 * DIA}]},
                    {"id": 9103, "name": "Roteiro do lab", "modname": "page", "visible": 1, "dates": []},
                ]}],
            41247: [
                {"name": "Laboratórios", "modules": [
                    {"id": 9002, "name": "Laboratório 4 - Threads e Sincronização", "modname": "assign",
                     "visible": 1, "dates": []},
                    {"id": 9201, "name": "Laboratorio 5 - Sincronizacao", "modname": "assign", "visible": 1,
                     "dates": [{"label": "Vencimento:", "timestamp": AGORA + DIA + 3600}]},
                    {"id": 9003, "name": "Projeto 1 - Simulador", "modname": "assign", "visible": 1, "dates": []},
                    {"id": 9004, "name": "Lista antiga 2025", "modname": "assign", "visible": 1, "dates": []},
                ]}],
        },
        "chamadas": [],
    }


class Fake:
    def __init__(self):
        self.estado = estado_inicial()
        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.servidor.server_address[1]}"
        self.thread = threading.Thread(target=self.servidor.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.servidor.shutdown()
        self.servidor.server_close()

    def chamadas(self, nome=None):
        return [c for c in self.estado["chamadas"] if nome is None or c[0] == nome]

    # ------------------------------------------------------------------ HTTP
    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                corpo = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                pares = urllib.parse.parse_qsl(corpo, keep_blank_values=True)
                params = parse_php(pares)
                resp = fake.despachar(params)
                dados = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(dados)

            def do_GET(self):
                partes = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(partes.query))
                if partes.path.startswith("/webservice/pluginfile.php") and q.get("token") == TOKEN:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"conteudo123")
                else:
                    self.send_response(403)
                    self.end_headers()

        return H

    # --------------------------------------------------------------- funções
    def despachar(self, p):
        fn = p.pop("wsfunction", None)
        token = p.pop("wstoken", None)
        p.pop("moodlewsrestformat", None)
        self.estado["chamadas"].append((fn, copy.deepcopy(p)))
        if token != TOKEN:
            return excecao("invalidtoken", "Token inválido - token não encontrado")
        metodo = getattr(self, "f_" + (fn or ""), None)
        if not metodo:
            return excecao("accessexception", f"função {fn} não liberada")
        try:
            return metodo(p)
        except KeyError as e:
            return excecao("invalidparameter", f"parâmetro ausente {e}")

    def _url(self, caminho):
        return self.url + caminho

    def f_core_webservice_get_site_info(self, p):
        return {"siteurl": self.url, "userid": PROFESSOR, "fullname": "PROFESSOR TESTE",
                "username": "1000000001", "release": "4.2.3+ (Build: 20231027)",
                "functions": [{"name": "x"}] * 409}

    def f_core_enrol_get_users_courses(self, p):
        assert int(p["userid"]) == PROFESSOR
        return [{k: v for k, v in c.items() if k != "alunos"} | {"visible": 1, "enddate": 0}
                for c in self.estado["cursos"].values()]

    def f_core_course_get_courses_by_field(self, p):
        ids = [int(x) for x in p["value"].split(",")]
        return {"courses": [{k: v for k, v in self.estado["cursos"][i].items() if k != "alunos"}
                            for i in ids if i in self.estado["cursos"]], "warnings": []}

    def _tarefa_por_cmid(self, cmid):
        for t in self.estado["tarefas"].values():
            if t["cmid"] == cmid:
                return t
        return None

    def f_core_course_get_course_module(self, p):
        cmid = int(p["cmid"])
        t = self._tarefa_por_cmid(cmid)
        if t:
            return {"cm": {"id": cmid, "course": t["course"], "modname": "assign", "instance": t["id"]}}
        return {"cm": {"id": cmid, "course": 35270, "modname": "page", "instance": 1}}

    def f_core_course_get_course_module_by_instance(self, p):
        t = self.estado["tarefas"].get(int(p["instance"]))
        if not t:
            return excecao("invalidrecord", "Registro não encontrado")
        return {"cm": {"id": t["cmid"], "course": t["course"], "modname": "assign", "instance": t["id"]}}

    def f_mod_assign_get_assignments(self, p):
        ids = [int(x) for x in p.get("courseids", [])]
        cursos = []
        for cid in ids:
            c = self.estado["cursos"][cid]
            cursos.append({"id": cid, "fullname": c["fullname"], "shortname": c["shortname"],
                           "assignments": [copy.deepcopy(t) for t in self.estado["tarefas"].values()
                                           if t["course"] == cid]})
        return {"courses": cursos, "warnings": [{"item": "module", "itemid": 1, "message": "No access rights in module context", "warningcode": "1"}]}

    def _alunos_da_tarefa(self, aid):
        t = self.estado["tarefas"][aid]
        return self.estado["cursos"][t["course"]]["alunos"]

    def f_mod_assign_list_participants(self, p):
        if p.get("onlyids") != "1":
            return excecao("invalidresponse", "Valor inválido de resposta detectado")
        aid = int(p["assignid"])
        saida = []
        for u in self._alunos_da_tarefa(aid):
            e = self.estado["entregas"][aid].get(u["id"])
            nota = self.estado["notas"].get((aid, u["id"]))
            enviado = bool(e and e["status"] == "submitted")
            saida.append({"id": u["id"], "fullname": u["fullname"], "recordid": u["id"],
                          "submitted": enviado,
                          "requiregrading": enviado and (nota is None or nota["tempo"] < e["timemodified"]),
                          "grantedextension": u["id"] in self.estado["extensoes"].get(aid, {}),
                          "submissionstatus": e["status"] if e else "new"})
        return saida

    def f_core_enrol_get_enrolled_users(self, p):
        opcoes = {o["name"]: o["value"] for o in p.get("options", [])}
        if "userfields" not in opcoes:
            return excecao("invalidresponse", "Valor inválido de resposta detectado")
        c = self.estado["cursos"][int(p["courseid"])]
        return [dict(u, roles=[{"shortname": "student"}]) for u in c["alunos"]] + \
               [{"id": PROFESSOR, "fullname": "LUCAS", "email": "lucas.figueiredo@mackenzie.br",
                 "roles": [{"shortname": "editingteacher"}]}]

    def f_mod_assign_get_submissions(self, p):
        blocos = []
        for aid in [int(x) for x in p["assignmentids"]]:
            subs = []
            for uid, e in self.estado["entregas"][aid].items():
                plugins = []
                if e.get("files") is not None:
                    plugins.append({"type": "file", "name": "Envio de arquivos", "fileareas": [
                        {"area": "submission_files", "files": [dict(f, fileurl=self._url(f["fileurl"])) for f in e["files"]]}]})
                if e.get("text"):
                    plugins.append({"type": "onlinetext", "editorfields": [{"name": "onlinetext", "text": e["text"], "format": 1}]})
                subs.append({"id": uid * 10, "userid": uid, "attemptnumber": 0, "status": e["status"],
                             "timemodified": e["timemodified"], "groupid": 0, "latest": 1, "plugins": plugins})
            blocos.append({"assignmentid": aid, "submissions": subs})
        return {"assignments": blocos, "warnings": []}

    def f_mod_assign_get_grades(self, p):
        blocos = []
        for aid in [int(x) for x in p["assignmentids"]]:
            gs = [{"id": 1, "userid": uid, "attemptnumber": 0, "grade": f"{n['nota']:.5f}", "timemodified": n["tempo"]}
                  for (a, uid), n in self.estado["notas"].items() if a == aid]
            if gs:
                blocos.append({"assignmentid": aid, "grades": gs})
        return {"assignments": blocos, "warnings": []}

    def f_mod_assign_get_submission_status(self, p):
        aid, uid = int(p["assignid"]), int(p["userid"])
        ext = self.estado["extensoes"].get(aid, {}).get(uid, 0)
        return {"lastattempt": {"extensionduedate": ext, "locked": (aid, uid) in self.estado["travas"]}}

    def f_mod_assign_save_grades(self, p):
        aid = int(p["assignmentid"])
        assert p["applytoall"] in ("0", "1")
        for g in p["grades"]:
            for campo in ("userid", "grade", "attemptnumber", "addattempt", "workflowstate"):
                if campo not in g:
                    return excecao("invalidparameter", f"falta {campo}")
            nota = {"nota": float(g["grade"]), "tempo": AGORA + 60}
            pd = g.get("plugindata") or {}
            if pd:
                ed = pd["assignfeedbackcomments_editor"]
                nota["comentario"] = ed["text"]
                assert ed["format"] == "1"
            self.estado["notas"][(aid, int(g["userid"]))] = nota
        return None

    def f_mod_assign_lock_submissions(self, p):
        aid = int(p["assignmentid"])
        for u in p["userids"]:
            self.estado["travas"].add((aid, int(u)))
        return []

    def f_mod_assign_unlock_submissions(self, p):
        aid = int(p["assignmentid"])
        for u in p["userids"]:
            self.estado["travas"].discard((aid, int(u)))
        return []

    def f_mod_forum_get_forums_by_courses(self, p):
        ids = [int(x) for x in p["courseids"]]
        return [f for c, f in self.estado["foruns"].items() if c in ids]

    def f_mod_forum_add_discussion(self, p):
        self.estado["discussoes"].append(p)
        return {"discussionid": 800 + len(self.estado["discussoes"]), "warnings": []}

    def f_core_course_get_contents(self, p):
        cid = int(p["courseid"])
        secoes = copy.deepcopy(self.estado["conteudo"].get(cid, []))
        for i, s in enumerate(secoes):
            s["id"] = i
            s["section"] = i
        return secoes
