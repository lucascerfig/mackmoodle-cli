import base64
import contextlib
import csv
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

import fake_moodle  # noqa: E402
from fake_moodle import AGORA, PROFESSOR, TOKEN, Fake  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_antigo = dict(os.environ)
        os.environ["MACKMOODLE_CONFIG_DIR"] = str(Path(self.tmp.name) / "cfg")
        os.environ["MACKMOODLE_DATA_DIR"] = str(Path(self.tmp.name) / "dados")
        os.environ["MACKMOODLE_AGORA"] = str(AGORA)
        for k in ("MACKMOODLE_TOKEN", "MACKMOODLE_SITE"):
            os.environ.pop(k, None)
        self.fake = Fake().__enter__()
        from mackmoodle import config
        from mackmoodle.client import Moodle
        config.salvar({"site": self.fake.url, "token": TOKEN, "userid": PROFESSOR,
                       "nome": "LUCAS", "obtido_em": AGORA - 3600})
        self.m = Moodle(self.fake.url, TOKEN)

    def tearDown(self):
        self.fake.__exit__(None, None, None)
        os.environ.clear()
        os.environ.update(self.env_antigo)
        self.tmp.cleanup()

    def cli(self, *args, entrada=""):
        from mackmoodle import cli
        out, err = io.StringIO(), io.StringIO()
        antigo_stdin = sys.stdin
        sys.stdin = io.StringIO(entrada)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                codigo = cli.main(list(args))
        finally:
            sys.stdin = antigo_stdin
        return codigo, out.getvalue(), err.getvalue()


class TestUtilidades(unittest.TestCase):
    def test_achatar(self):
        from mackmoodle.client import achatar
        pares = achatar({"a": 1, "grades": [{"userid": 5, "addattempt": False,
                                             "plugindata": {"ed": {"text": "x", "format": 1}}}], "n": None})
        self.assertIn(("grades[0][userid]", "5"), pares)
        self.assertIn(("grades[0][addattempt]", "0"), pares)
        self.assertIn(("grades[0][plugindata][ed][text]", "x"), pares)
        self.assertNotIn("n", [k for k, _ in pares])

    def test_extrair_token_e_siteid(self):
        from mackmoodle import auth
        site, passport = "https://graduacao.mackenzie.br", "abc123"
        siteid = hashlib.md5((site + passport).encode()).hexdigest()
        b64 = base64.b64encode(f"{siteid}:::{TOKEN}:::privado".encode()).decode()
        for colado in (f"moodlemobile://token={b64}", b64, f"  'moodlemobile://token={b64}'  "):
            sid, tok = auth.extrair_token(colado)
            self.assertEqual(tok, TOKEN)
            self.assertTrue(auth.conferir_siteid(site, passport, sid))
        self.assertFalse(auth.conferir_siteid(site, "outro", siteid))
        with self.assertRaises(Exception):
            auth.extrair_token("moodlemobile://token=" + base64.b64encode(b"x:::naoehex").decode())

    def test_url_login(self):
        from mackmoodle import auth
        u = auth.url_login("https://graduacao.mackenzie.br/", "p1")
        self.assertTrue(u.startswith("https://graduacao.mackenzie.br/admin/tool/mobile/launch.php?"))
        self.assertIn("confirmed=1", u)
        self.assertIn("service=moodle_mobile_app", u)

    def test_normalizar_e_rotulo(self):
        from mackmoodle import ops
        self.assertEqual(ops.normalizar("Laboratório 4 - Threads!"), "laboratorio 4 threads")
        info = {1: {"fullname": "SISTEMAS OPERACIONAIS [Turma 201815114.000.04G] - 2026/2"},
                2: {"fullname": "INTRODUCAO A SO [Turma 201815116.000.02J11] - 2026/2"}}
        self.assertEqual(ops._rotulo(info, 1), "04G (1)")
        self.assertEqual(ops._rotulo(info, 2), "02J11 (2)")
        self.assertEqual(ops._rotulo(info, 3), "3")

    def test_agora_fixo(self):
        from mackmoodle.planos import fmt_data
        self.assertEqual(fmt_data(AGORA), "16/09/2026 18:15")

    def test_nome_arquivo(self):
        from mackmoodle import ops
        self.assertEqual(ops.nome_arquivo('lista:1?.pdf'), "lista_1_.pdf")
        self.assertEqual(ops.nome_arquivo("pasta/arq*.c"), "pasta_arq_.c")
        self.assertEqual(ops.nome_arquivo(" . "), "arquivo")
        self.assertEqual(ops.nome_arquivo("relatório final.pdf"), "relatório final.pdf")

    def test_texto_html(self):
        from mackmoodle import ops
        self.assertEqual(ops.texto_para_html("a <b>\nc\n\nd"), "<p>a &lt;b&gt;<br>c</p><p>d</p>")
        self.assertEqual(ops.html_para_texto("<p>x &amp; y</p><p>z</p>"), "x & y\nz")


class TestConfig(Base):
    @unittest.skipIf(os.name == "nt", "permissões POSIX não se aplicam ao Windows")
    def test_permissoes(self):
        from mackmoodle import config
        arq = config.arquivo_config()
        self.assertEqual(stat.S_IMODE(arq.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(arq.parent.stat().st_mode), 0o700)
        self.assertEqual(config.carregar()["token"], TOKEN)

    def test_ambiente_tem_precedencia(self):
        from mackmoodle import config
        os.environ["MACKMOODLE_TOKEN"] = "f" * 32
        self.assertEqual(config.carregar()["token"], "f" * 32)

    def test_aviso_validade(self):
        from mackmoodle import config
        self.assertIsNone(config.aviso_validade({"obtido_em": AGORA - 3600}))
        self.assertIn("expira", config.aviso_validade({"obtido_em": AGORA - 80 * 86400}))
        self.assertIn("expirado", config.aviso_validade({"obtido_em": AGORA - 90 * 86400}))


class TestLeitura(Base):
    def test_cursos_semestre(self):
        from mackmoodle import ops
        r = ops.cursos(self.m, PROFESSOR)
        self.assertEqual(r["semestre"], "2026/2")
        self.assertEqual(len(r["cursos"]), 4)
        self.assertEqual(len(ops.cursos(self.m, PROFESSOR, todos=True)["cursos"]), 5)

    def test_envios_j12(self):
        from mackmoodle import ops
        r = ops.envios(self.m, 138056)
        self.assertEqual(r["resumo"]["alunos"], 9)
        self.assertEqual(r["resumo"]["entregaram"], 7)
        caio = next(a for a in r["alunos"] if a["nome"].startswith("Carla"))
        self.assertFalse(caio["entregou"])
        self.assertTrue(caio["ra"].startswith("1043"))
        self.assertFalse(r["tarefa"]["prazo_aberto"])
        # só a variante segura das duas funções problemáticas foi usada
        for _, p in self.fake.chamadas("mod_assign_list_participants"):
            self.assertEqual(p["onlyids"], "1")

    def test_resolver_por_url_e_cm(self):
        from mackmoodle import ops
        a = ops.resolver_tarefa(self.m, "https://graduacao.mackenzie.br/mod/assign/view.php?id=1042655")
        self.assertEqual(a["id"], 138056)
        self.assertEqual(ops.resolver_tarefa(self.m, "cm:1042886")["id"], 138091)
        with self.assertRaises(Exception):
            ops.resolver_tarefa(self.m, "cm:9103")

    def test_rascunho_e_extensao(self):
        from mackmoodle import ops
        r = ops.envios(self.m, 5001)
        por_id = {a["userid"]: a for a in r["alunos"]}
        self.assertEqual(por_id[301]["status"], "draft")
        self.assertTrue(por_id[302]["prazo_aberto"])
        self.assertTrue(por_id[302]["extensao_ate"])
        self.assertIn("github.com/fci-sisop/lab4-ana", por_id[300]["texto"])

    def test_tarefas_contar(self):
        from mackmoodle import ops
        r = ops.tarefas(self.m, [34922, 34923], contar=True)
        j12 = next(t for t in r["tarefas"] if t["tarefa"] == 138056)
        self.assertEqual((j12["alunos"], j12["entregas"], j12["a_avaliar"]), (9, 7, 7))

    def test_token_invalido(self):
        from mackmoodle.client import Moodle, MoodleErro
        with self.assertRaises(MoodleErro) as ctx:
            Moodle(self.fake.url, "0" * 32).chamar("core_webservice_get_site_info")
        self.assertIn("mackmoodle login", str(ctx.exception))

    def test_rede_inacessivel(self):
        from mackmoodle.client import Moodle, MoodleErro
        with self.assertRaises(MoodleErro) as ctx:
            Moodle("http://127.0.0.1:9", TOKEN, timeout=2).chamar("core_webservice_get_site_info")
        self.assertEqual(ctx.exception.codigo, "rede")


class TestNotasEntrega(Base):
    def test_fluxo_completo_j12(self):
        from mackmoodle import ops
        p = ops.planejar_notas_por_entrega(self.m, PROFESSOR, 138056)
        self.assertEqual(p["bloqueios"], [])
        notas = sorted(i["nota"] for i in p["itens"])
        self.assertEqual(notas, [0.0, 0.0] + [100.0] * 7)
        self.assertTrue(any("notifica" in a for a in p["avisos"]))
        self.assertTrue(any("data limite" in a for a in p["avisos"]))
        self.assertEqual(self.fake.chamadas("mod_assign_save_grades"), [])  # planejar não grava

        rel = ops.aplicar(self.m, PROFESSOR, p["id"], travar_sem_entrega=True)
        self.assertEqual(rel["gravadas"], 9)
        self.assertEqual(rel["travados"], 2)
        self.assertEqual(rel["divergencias"], [])
        self.assertEqual(len(self.fake.chamadas("mod_assign_save_grades")), 1)
        travados = {u for (a, u) in self.fake.estado["travas"] if a == 138056}
        self.assertEqual(len(travados), 2)
        # sem comentário, não manda plugindata
        enviado = self.fake.chamadas("mod_assign_save_grades")[0][1]
        self.assertNotIn("plugindata", enviado["grades"][0])

        with self.assertRaises(ValueError):
            ops.aplicar(self.m, PROFESSOR, p["id"])  # já aplicado

        p2 = ops.planejar_notas_por_entrega(self.m, PROFESSOR, 138056)
        self.assertEqual(p2["itens"], [])
        self.assertEqual(len(p2["ignorados"]), 9)

    def test_prazo_aberto_bloqueia(self):
        from mackmoodle import ops
        p = ops.planejar_notas_por_entrega(self.m, PROFESSOR, 138091)
        self.assertTrue(any("prazo" in b for b in p["bloqueios"]))
        with self.assertRaises(ValueError):
            ops.aplicar(self.m, PROFESSOR, p["id"])
        p2 = ops.planejar_notas_por_entrega(self.m, PROFESSOR, 138091, forcar_prazo=True)
        self.assertEqual(p2["bloqueios"], [])
        self.assertEqual(sum(1 for i in p2["itens"] if i["nota"] == 100), 8)
        self.assertEqual(sum(1 for i in p2["itens"] if i["nota"] == 0), 11)

    def test_estado_mudou(self):
        from mackmoodle import ops
        p = ops.planejar_notas_por_entrega(self.m, PROFESSOR, 138056)
        caio = next(u for u in self.fake.estado["cursos"][34923]["alunos"] if u["fullname"].startswith("Carla"))
        self.fake.estado["entregas"][138056][caio["id"]] = {"status": "submitted", "timemodified": AGORA, "files": []}
        with self.assertRaises(ValueError) as ctx:
            ops.aplicar(self.m, PROFESSOR, p["id"])
        self.assertIn("mudou", str(ctx.exception))
        self.assertEqual(self.fake.chamadas("mod_assign_save_grades"), [])

    def test_rascunho_e_extensao_no_plano(self):
        from mackmoodle import ops
        p = ops.planejar_notas_por_entrega(self.m, PROFESSOR, 5001)
        motivos = {i["userid"]: i["motivo"] for i in p["ignorados"]}
        self.assertIn("rascunho", motivos[301])
        self.assertIn("extensão", motivos[302])
        p2 = ops.planejar_notas_por_entrega(self.m, PROFESSOR, 5001, rascunho="zero")
        self.assertEqual(next(i["nota"] for i in p2["itens"] if i["userid"] == 301), 0)

    def test_grupo_e_escala_bloqueiam(self):
        from mackmoodle import ops
        self.assertTrue(any("grupo" in b for b in ops.planejar_notas_por_entrega(self.m, PROFESSOR, 5003)["bloqueios"]))
        self.assertTrue(any("escala" in b for b in ops.planejar_notas_por_entrega(self.m, PROFESSOR, 5004)["bloqueios"]))

    def test_nota_fora_do_intervalo(self):
        from mackmoodle import ops
        p = ops.planejar_notas_por_entrega(self.m, PROFESSOR, 138056, nota_entregou=120)
        self.assertTrue(any("fora do intervalo" in b for b in p["bloqueios"]))

    def test_plano_de_outro_usuario(self):
        from mackmoodle import ops
        p = ops.planejar_notas_por_entrega(self.m, PROFESSOR, 138056)
        with self.assertRaises(ValueError):
            ops.aplicar(self.m, 999, p["id"])


class TestNotasCSV(Base):
    def escrever(self, texto, nome="notas.csv"):
        arq = Path(self.tmp.name) / nome
        arq.write_text(texto, encoding="utf-8")
        return arq

    def test_csv_ponto_e_virgula_com_comentarios(self):
        from mackmoodle import ops
        alunos = self.fake.estado["cursos"][34923]["alunos"]
        linhas = ["E-mail;Nota;Comentário"]
        linhas.append(f"{alunos[0]['email']};9,5;Bom trabalho.\nFaltou o item 3.".replace("\n", " "))
        linhas.append(f"{alunos[1]['email'].upper()};80;")
        arq = self.escrever("\n".join(linhas))
        p = ops.planejar_notas_csv(self.m, PROFESSOR, 138056, ops.ler_csv(arq))
        self.assertEqual(p["bloqueios"], [])
        self.assertEqual({i["nota"] for i in p["itens"]}, {9.5, 80.0})
        self.assertTrue(any("não aparecem no CSV" in a for a in p["avisos"]))
        ops.aplicar(self.m, PROFESSOR, p["id"])
        n = self.fake.estado["notas"][(138056, alunos[0]["id"])]
        self.assertIn("Bom trabalho", n["comentario"])
        self.assertTrue(n["comentario"].startswith("<p>"))

    def test_identificacao_por_ra_nome_userid(self):
        from mackmoodle import ops
        alunos = self.fake.estado["cursos"][34923]["alunos"]
        linhas = [
            {"ra": alunos[2]["email"].split("@")[0], "nota": "10"},
            {"nome": "debora nunes", "nota": "20"},
            {"userid": str(alunos[4]["id"]), "nota": "30"},
        ]
        p = ops.planejar_notas_csv(self.m, PROFESSOR, 138056, linhas)
        self.assertEqual(p["bloqueios"], [])
        self.assertEqual(len(p["itens"]), 3)
        self.assertTrue(any("sem ter envio" in a for a in p["avisos"]))

    def test_erros_bloqueiam(self):
        from mackmoodle import ops
        linhas = [{"nome": "Fulano Inexistente", "nota": "10"},
                  {"nome": "Bruno Lima", "nota": "abc"},
                  {"nome": "Otávio Reis", "nota": "150"}]
        p = ops.planejar_notas_csv(self.m, PROFESSOR, 138056, linhas)
        texto = " ".join(p["bloqueios"])
        self.assertIn("não está na tarefa", texto)
        self.assertIn("'abc'", texto)
        self.assertIn("'150'", texto)
        p2 = ops.planejar_notas_csv(self.m, PROFESSOR, 138056, linhas[:1], ignorar_nao_encontrados=True)
        self.assertEqual(p2["bloqueios"], [])

    def test_duplicado_e_sem_coluna(self):
        from mackmoodle import ops
        p = ops.planejar_notas_csv(self.m, PROFESSOR, 138056,
                                   [{"nome": "Bruno Lima", "nota": "1"}, {"nome": "BRUNO LIMA", "nota": "2"}])
        self.assertTrue(any("aparece nas linhas" in b for b in p["bloqueios"]))
        p2 = ops.planejar_notas_csv(self.m, PROFESSOR, 138056, [{"x": "1", "nota": "2"}])
        self.assertTrue(any("identificação" in b for b in p2["bloqueios"]))
        p3 = ops.planejar_notas_csv(self.m, PROFESSOR, 138056, [{"nome": "Bruno Lima"}])
        self.assertTrue(any("'nota'" in b for b in p3["bloqueios"]))

    def test_comentario_sem_plugin(self):
        from mackmoodle import ops
        p = ops.planejar_notas_csv(self.m, PROFESSOR, 5001, [{"userid": "300", "nota": "10", "comentario": "ok"}])
        self.assertTrue(any("Comentários de feedback" in b for b in p["bloqueios"]))

    def test_nota_em_branco_ignora(self):
        from mackmoodle import ops
        p = ops.planejar_notas_csv(self.m, PROFESSOR, 138056, [{"nome": "Bruno Lima", "nota": ""}])
        self.assertEqual(p["itens"], [])
        self.assertEqual(p["ignorados"][0]["motivo"], "nota em branco no CSV")

    def test_modelo_csv(self):
        from mackmoodle import ops
        arq = Path(self.tmp.name) / "modelo.csv"
        r = ops.modelo_csv_notas(self.m, 138056, arq)
        self.assertEqual(r["alunos"], 9)
        with arq.open(encoding="utf-8") as f:
            linhas = list(csv.DictReader(f))
        self.assertEqual(set(linhas[0]), {"userid", "ra", "nome", "email", "status", "texto_enviado",
                                          "nota_atual", "nota", "comentario"})
        # o modelo preenchido volta pelo mesmo caminho
        for l in linhas:
            l["nota"] = "50"
        p = ops.planejar_notas_csv(self.m, PROFESSOR, 138056, linhas)
        self.assertEqual(p["bloqueios"], [])
        self.assertEqual(len(p["itens"]), 9)


class TestTravaAvisoBaixarComparar(Base):
    def test_trava_e_destrava(self):
        from mackmoodle import ops
        p = ops.planejar_trava(self.m, PROFESSOR, 138056, [100, 101])
        ops.aplicar(self.m, PROFESSOR, p["id"])
        self.assertEqual({u for a, u in self.fake.estado["travas"]}, {100, 101})
        p2 = ops.planejar_trava(self.m, PROFESSOR, 138056, [100], destravar=True)
        ops.aplicar(self.m, PROFESSOR, p2["id"])
        self.assertEqual({u for a, u in self.fake.estado["travas"]}, {101})
        p3 = ops.planejar_trava(self.m, PROFESSOR, 138056, [99999])
        self.assertTrue(p3["bloqueios"])

    def test_aviso(self):
        from mackmoodle import ops
        p = ops.planejar_aviso(self.m, PROFESSOR, [34922, 34923], "Prova", "Linha 1\n\nLinha <2>")
        self.assertEqual(p["bloqueios"], [])
        self.assertEqual(len(p["itens"]), 2)
        rel = ops.aplicar(self.m, PROFESSOR, p["id"])
        self.assertEqual(len(rel["postados"]), 2)
        self.assertEqual(self.fake.estado["discussoes"][0]["message"], "<p>Linha 1</p><p>Linha &lt;2&gt;</p>")
        p2 = ops.planejar_aviso(self.m, PROFESSOR, [41247], "x", "y")
        self.assertTrue(any("fórum de avisos" in b for b in p2["bloqueios"]))

    def test_baixar(self):
        from mackmoodle import ops
        # arquivo com nome inválido no Windows
        uid = self.fake.estado["cursos"][34923]["alunos"][0]["id"]
        self.fake.estado["entregas"][138056][uid]["files"].append(
            {"filename": 'resp:1?.txt', "filepath": "/sub:dir/", "filesize": 11,
             "fileurl": "/webservice/pluginfile.php/1/x/resp.txt"})
        destino = Path(self.tmp.name) / "envios"
        r = ops.baixar(self.m, 138056, destino)
        self.assertEqual(r["alunos"], 7)
        self.assertEqual(r["baixados"], 8)
        self.assertEqual(len(list(Path(r["pasta"]).rglob("resp_1_.txt"))), 1)
        self.assertEqual(len(list(Path(r["pasta"]).rglob("sub_dir"))), 1)
        pdfs = list(Path(r["pasta"]).rglob("lista1.pdf"))
        self.assertEqual(len(pdfs), 7)
        self.assertEqual(pdfs[0].read_bytes(), b"conteudo123")
        r2 = ops.baixar(self.m, 138056, destino)
        self.assertEqual(r2["baixados"], 0)
        self.assertEqual(r2["ja_existiam"], 8)
        r3 = ops.baixar(self.m, 5001, destino)
        pasta = Path(r3["pasta"])
        self.assertTrue(any(p.name == "texto_online.txt" for p in pasta.rglob("*")))

    def test_comparar(self):
        from mackmoodle import ops
        r = ops.comparar(self.m, [35270, 41247])
        por_chave = {a["chave"]: a for a in r["atividades"]}
        lab4 = por_chave["assign:lab 4 threads"]
        self.assertIn("nome difere", lab4["diferencas"])
        self.assertIn("data difere", lab4["diferencas"])
        self.assertEqual(len(lab4["turmas"]), 2)
        lab5 = por_chave["assign:lab 5 sincronizacao"]
        self.assertIn("horário difere", lab5["diferencas"])
        self.assertTrue(any(d.startswith("ausente em 04D") for d in por_chave["page:roteiro do lab"]["diferencas"]))
        self.assertTrue(any(d.startswith("ausente em 04N") for d in por_chave["assign:projeto 1 simulador"]["diferencas"]))
        self.assertEqual(r["antigas_ignoradas"], 2)
        self.assertEqual(len(ops.comparar(self.m, [35270, 41247], incluir_antigas=True)["atividades"]),
                         len(r["atividades"]) + 2)


class TestCLI(Base):
    def test_preview_e_aplicar(self):
        codigo, out, _ = self.cli("notas", "entrega", "138056")
        self.assertEqual(codigo, 0)
        self.assertIn("Carla Exemplo Dias", out)
        self.assertIn("mackmoodle aplicar notas-", out)
        self.assertEqual(self.fake.chamadas("mod_assign_save_grades"), [])
        plano_id = out.split("mackmoodle aplicar ")[1].split()[0]

        codigo, out, err = self.cli("aplicar", plano_id)  # sem tty e sem --sim: recusa
        self.assertEqual(codigo, 1)
        self.assertEqual(self.fake.chamadas("mod_assign_save_grades"), [])

        codigo, out, _ = self.cli("aplicar", plano_id, "--travar-sem-entrega", "--sim")
        self.assertEqual(codigo, 0, out)
        self.assertIn("Gravadas 9", out)
        self.assertIn("todas as notas batem", out)

    def test_bloqueio_retorna_3(self):
        codigo, out, _ = self.cli("notas", "entrega", "138091", "--aplicar", "--sim")
        self.assertEqual(codigo, 3)
        self.assertIn("BLOQUEIOS", out)
        self.assertEqual(self.fake.chamadas("mod_assign_save_grades"), [])

    def test_json(self):
        codigo, out, _ = self.cli("--json", "envios", "138056")
        self.assertEqual(codigo, 0)
        self.assertEqual(json.loads(out)["resumo"]["entregaram"], 7)
        codigo, out, _ = self.cli("--json", "notas", "entrega", "138056", "--aplicar", "--sim")
        self.assertEqual(json.loads(out)["gravadas"], 9)

    def test_leituras(self):
        for args in (["cursos"], ["tarefas", "34922", "34923", "--contar"], ["envios", "138056", "--texto"],
                     ["comparar", "35270", "41247", "--so-diferencas"], ["planos"], ["status"]):
            codigo, out, err = self.cli(*args)
            self.assertEqual(codigo, 0, (args, out, err))
        self.assertIn("04N (35270)", self.cli("comparar", "35270", "41247")[1])

    def test_saida_em_codificacao_limitada(self):
        from mackmoodle import cli
        self.fake.estado["cursos"][34923]["alunos"][1]["fullname"] = "Bruno 李 Lima"
        bruto = io.BytesIO()
        fluxo = io.TextIOWrapper(bruto, encoding="cp1252")
        antigo = sys.stdout
        sys.stdout = fluxo
        try:
            codigo = cli.main(["envios", "138056"])
            fluxo.flush()
        finally:
            sys.stdout = antigo
        self.assertEqual(codigo, 0)
        self.assertIn(b"Bruno ? Lima", bruto.getvalue())

    def test_sem_login(self):
        from mackmoodle import config
        config.apagar()
        codigo, _, err = self.cli("cursos")
        self.assertEqual(codigo, 2)
        self.assertIn("mackmoodle login", err)

    def test_login_colando_link(self):
        from mackmoodle import auth, config
        config.apagar()
        passports = []
        original = auth.novo_passport
        auth.novo_passport = lambda: passports.append("pp") or "pp"
        try:
            siteid = hashlib.md5((self.fake.url + "pp").encode()).hexdigest()
            link = "moodlemobile://token=" + base64.b64encode(f"{siteid}:::{TOKEN}".encode()).decode()
            codigo, out, err = self.cli("login", "--site", self.fake.url, "--sem-navegador", entrada=link + "\n")
        finally:
            auth.novo_passport = original
        self.assertEqual(codigo, 0, err)
        self.assertNotIn(TOKEN, out + err)
        dados = config.carregar()
        self.assertEqual(dados["token"], TOKEN)
        self.assertEqual(dados["userid"], PROFESSOR)
        self.assertNotIn("Atenção", err)

    def test_aviso_por_arquivo(self):
        arq = Path(self.tmp.name) / "msg.txt"
        arq.write_text("Olá turma.", encoding="utf-8")
        codigo, out, _ = self.cli("aviso", "34922", "34923", "--assunto", "P1", "--arquivo", str(arq), "--aplicar", "--sim")
        self.assertEqual(codigo, 0, out)
        self.assertEqual(len(self.fake.estado["discussoes"]), 2)


class TestMCP(Base):
    def conversar(self, mensagens):
        entrada = "\n".join(json.dumps(m) for m in mensagens) + "\n"
        proc = subprocess.run([sys.executable, str(RAIZ / "bin" / "mackmoodle-mcp")], input=entrada,
                              capture_output=True, text=True, encoding="utf-8", env=dict(os.environ), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]

    def test_handshake_e_lista(self):
        r = self.conversar([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": "inexistente"},
        ])
        self.assertEqual([x["id"] for x in r], [1, 2, 3, 4])
        self.assertEqual(r[0]["result"]["protocolVersion"], "2025-06-18")
        nomes = {t["name"] for t in r[1]["result"]["tools"]}
        self.assertIn("moodle_aplicar_plano", nomes)
        aplicar = next(t for t in r[1]["result"]["tools"] if t["name"] == "moodle_aplicar_plano")
        self.assertTrue(aplicar["annotations"]["destructiveHint"])
        self.assertEqual(r[3]["error"]["code"], -32601)

    def test_planejar_e_aplicar(self):
        chamada = lambda i, nome, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                         "params": {"name": nome, "arguments": args}}
        r = self.conversar([chamada(1, "moodle_planejar_notas_entrega", {"tarefa": "138056"})])
        plano = r[0]["result"]["structuredContent"]
        self.assertEqual(plano["bloqueios"], [])
        pid = plano["plano_id"]
        r = self.conversar([
            chamada(2, "moodle_aplicar_plano", {"plano_id": pid, "confirmado_pelo_professor": False}),
            chamada(3, "moodle_aplicar_plano", {"plano_id": pid, "confirmado_pelo_professor": True,
                                                "travar_sem_entrega": True}),
            chamada(4, "moodle_status", {}),
            chamada(5, "moodle_comparar_turmas", {"cursos": [35270, 41247]}),
        ])
        self.assertTrue(r[0]["result"]["isError"])
        self.assertFalse(r[1]["result"]["isError"], r[1])
        self.assertEqual(r[1]["result"]["structuredContent"]["gravadas"], 9)
        self.assertTrue(r[2]["result"]["structuredContent"]["logado"])
        self.assertNotIn(TOKEN, json.dumps(r))
        self.assertTrue(all(a["diferencas"] for a in r[3]["result"]["structuredContent"]["atividades"]))

    def test_sem_login(self):
        from mackmoodle import config
        config.apagar()
        r = self.conversar([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "moodle_cursos", "arguments": {}}}])
        self.assertTrue(r[0]["result"]["isError"])
        self.assertIn("mackmoodle login", r[0]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
