# mackmoodle-cli

Linha de comando para professores da graduação do Mackenzie trabalharem com o Moodle
(`https://graduacao.mackenzie.br`) sem passar pela interface web. Com ela é possível listar cursos e
tarefas, ver a situação de cada aluno numa tarefa, lançar notas em lote com prévia e conferência,
travar envios, publicar o mesmo aviso em várias turmas, baixar os envios e comparar a estrutura de
turmas diferentes.

Versão 0.1.1, em fase de testes.

## Sumário

1. [Requisitos](#requisitos)
2. [Instalação](#instalação)
3. [Autenticação](#autenticação)
4. [Como o Moodle é acessado](#como-o-moodle-é-acessado)
5. [Comandos](#comandos)
6. [Gravações: plano, prévia e conferência](#gravações-plano-prévia-e-conferência)
7. [Planilha de notas (CSV)](#planilha-de-notas-csv)
8. [Arquivos e variáveis de ambiente](#arquivos-e-variáveis-de-ambiente)
9. [Limitações](#limitações)
10. [Desenvolvimento](#desenvolvimento)

## Requisitos

- Python 3.9 ou mais recente, em Linux, macOS ou Windows. Nenhuma biblioteca externa é usada.
- Conta de professor no Moodle da graduação, com acesso pelo Office 365 (DRT@mackenzie.br).
- Navegador para o login.

## Instalação

O repositório é privado. A instalação usa o acesso do professor ao GitHub, por SSH ou pelo `gh`.

Com uv:

```bash
uv tool install git+ssh://git@github.com/lucascerfig/mackmoodle-cli.git
```

Ou com pipx:

```bash
pipx install git+ssh://git@github.com/lucascerfig/mackmoodle-cli.git
```

Os dois instalam o comando `mackmoodle`. Para atualizar: `uv tool upgrade mackmoodle-cli` ou
`pipx upgrade mackmoodle-cli`.

No Windows, os mesmos comandos funcionam no PowerShell. Sem chave SSH, é possível clonar com
`gh repo clone lucascerfig/mackmoodle-cli` e instalar a partir da pasta: `uv tool install .`

Sem instalar: clonar o repositório e executar `python bin/mackmoodle` na pasta dele.

Conferir a instalação:

```bash
mackmoodle --version
```

## Autenticação

### Por que um token

O Moodle da graduação autentica pelo Office 365. O professor não tem senha própria no Moodle,
então o login por usuário e senha da API (`login/token.php`) não serve. Para o aplicativo oficial
do Moodle, o site oferece o serviço de web services `moodle_mobile_app`: o aplicativo abre uma
página do Moodle no navegador, o professor faz o login do Office 365 e o Moodle devolve uma chave
(token) desse serviço. O `mackmoodle` usa exatamente esse caminho.

### Passo a passo do `mackmoodle login`

1. O programa gera um identificador aleatório e abre no navegador:

   ```
   https://graduacao.mackenzie.br/admin/tool/mobile/launch.php
       ?service=moodle_mobile_app&passport=<identificador>&urlscheme=moodlemobile&confirmed=1
   ```

2. Se o navegador ainda não estiver logado, o Moodle pede o login do Office 365.
3. A página de confirmação tenta abrir o aplicativo do Moodle. Se o navegador perguntar, basta
   cancelar. A página continua aberta, com o link "Clique aqui..." apontando para
   `moodlemobile://token=...`.
4. O professor clica com o botão direito nesse link, copia o endereço e cola no terminal. O texto
   colado não aparece na tela.
5. O programa decodifica o trecho em base64, que tem a forma `verificador:::token[:::chave]`, e:
   - confere se o verificador é `md5(endereço do site + identificador)`, o que garante que o link
     veio deste pedido de login e deste site;
   - descarta a chave privada, que não é usada;
   - testa o token com `core_webservice_get_site_info` e obtém o nome e o id do professor;
   - grava site, token, id, nome e data em um arquivo local.

Depois do login, `mackmoodle status` mostra o usuário e testa a conexão.

### O que é guardado e onde

| Sistema | Arquivo |
|---------|---------|
| Linux e macOS | `~/.config/mackmoodle/config.json` (ou `$XDG_CONFIG_HOME/mackmoodle/`) |
| Windows | `%APPDATA%\mackmoodle\config.json` |

No Linux e no macOS, o arquivo é criado com permissão 600 e a pasta com 700.

### Validade, alcance e revogação

- No padrão do Moodle, o token vale 12 semanas. A administração do site pode ter configurado outro
  prazo. O programa avisa quando o token tem mais de 11 semanas e informa quando deve ter vencido.
- O Moodle reaproveita o token válido mais recente do usuário para esse serviço. Por isso, quem já
  usa o aplicativo do Moodle no celular recebe o mesmo token.
- O token tem as mesmas permissões que o professor tem na interface web, limitadas às funções do
  serviço do aplicativo.
- `mackmoodle logout` apaga só a cópia local. O token continua válido no Moodle até vencer. Para
  professores, a página "Chaves de segurança" do Moodle não lista esse token; a revogação antes do
  prazo é feita pela administração do Moodle.
- O token equivale a uma senha: não deve ser enviado por e-mail, mensagem ou colado em outros
  lugares. Se isso acontecer, avise a administração do Moodle.

## Como o Moodle é acessado

### Chamadas

Todas as operações usam a API REST de web services do Moodle:

```
POST https://graduacao.mackenzie.br/webservice/rest/server.php
     wstoken=<token>&wsfunction=<função>&moodlewsrestformat=json&<parâmetros>
```

Listas e estruturas seguem a notação de formulário do PHP (`grades[0][userid]=123`). As respostas
são JSON. Erros do Moodle (`exception`) são mostrados com a mensagem e o código. O cabeçalho
`User-Agent` identifica o programa como `mackmoodle/<versão>`.

Arquivos enviados pelos alunos são baixados das URLs `webservice/pluginfile.php/...` que a própria
API devolve, com o token como parâmetro.

O programa não lê páginas do Moodle e não usa a sessão do navegador. Fora do login, que acontece
no navegador, tudo passa pela API. As chamadas ficam registradas nos logs do Moodle como uso de
web service pelo professor.

### Funções usadas

| Função do Moodle | Onde é usada |
|------------------|--------------|
| `core_webservice_get_site_info` | `login`, `status` |
| `core_enrol_get_users_courses` | `cursos` |
| `core_course_get_courses_by_field` | `aviso`, `comparar` |
| `core_course_get_course_module`, `core_course_get_course_module_by_instance` | localizar a tarefa |
| `mod_assign_get_assignments` | `tarefas`, `envios`, `comparar` |
| `mod_assign_list_participants` | `tarefas --contar`, `envios` |
| `core_enrol_get_enrolled_users` | e-mail dos alunos em `envios` |
| `mod_assign_get_submissions` | situação, data, arquivos e texto dos envios |
| `mod_assign_get_grades` | nota atual |
| `mod_assign_get_submission_status` | data da extensão, quando o aluno tem uma |
| `mod_assign_save_grades` | `notas` (nota e comentário) |
| `mod_assign_lock_submissions`, `mod_assign_unlock_submissions` | `travar`, `--travar-sem-entrega` |
| `mod_forum_get_forums_by_courses`, `mod_forum_add_discussion` | `aviso` |
| `core_course_get_contents` | `comparar` |

### Particularidades observadas no Moodle da graduação (versão 4.2)

- `mod_assign_list_participants` só responde com `onlyids=true`; com `false`, devolve "Valor
  inválido de resposta detectado". O programa sempre usa `onlyids=true`.
- `core_enrol_get_enrolled_users` precisa da opção `userfields`; o programa pede
  `id,fullname,email`.
- Nota gravada pela API sempre gera notificação para o aluno. O serviço não oferece opção para
  desativar. Para lançar sem notificar, use a avaliação rápida na página da tarefa, com
  "Notificar estudantes" desmarcado.
- Avisos publicados pela API seguem o tempo de edição do fórum antes do envio por e-mail.
- O serviço do aplicativo não permite criar ou editar atividades, importar conteúdo entre cursos
  nem alterar datas. Essas mudanças continuam sendo feitas na interface web.

## Comandos

Em todos os comandos, `<tarefa>` aceita o id da tarefa, `cm:<cmid>` ou a URL copiada do navegador
(`https://graduacao.mackenzie.br/mod/assign/view.php?id=<cmid>`). A opção global `--json`, antes do
nome do comando, troca a saída por JSON.

### Conta

| Comando | O que faz |
|---------|-----------|
| `mackmoodle login [--site URL] [--sem-navegador]` | Faz o login descrito acima e guarda o token. `--sem-navegador` só mostra o endereço. |
| `mackmoodle logout` | Apaga o token deste computador. |
| `mackmoodle status [--offline]` | Mostra site, usuário, idade do token e testa a conexão. |

### Consulta

`mackmoodle cursos [--semestre 2026/2] [--todos]`
Lista os cursos do professor com o id de cada um. Por padrão, mostra só os cursos cujo nome
contém o semestre atual (por exemplo, "2026/2").

`mackmoodle tarefas <curso>... [--contar]`
Lista as tarefas dos cursos com id, cmid, prazo, data limite e nota máxima. Com `--contar`, inclui
o número de alunos, de entregas e de envios que aguardam avaliação.

`mackmoodle envios <tarefa> [--csv arquivo] [--texto]`
Mostra, para cada aluno: RA, situação do envio (`submitted`, `draft`, `new`, `sem envio`), data,
se foi entregue com atraso, extensão concedida, número de arquivos e nota atual. `--texto` mostra
o texto online enviado. `--csv` salva tudo em planilha.

`mackmoodle comparar <curso> <curso>... [--so-diferencas] [--incluir-antigas]`
Compara as atividades de duas ou mais turmas e aponta: atividade ausente numa turma, nome
diferente, prazo diferente (dia ou só horário), visibilidade diferente e seção diferente.
Atividades do mesmo tipo com o mesmo prefixo e número (lab, lista, projeto, prova, questionário;
por exemplo "Lab 4 - Threads" e "Laboratório 4 - Threads e Sincronização") são tratadas como a
mesma. Atividades com todas as datas anteriores ao início do curso são consideradas de
semestres anteriores e ficam de fora, salvo `--incluir-antigas`.

### Notas

`mackmoodle notas entrega <tarefa> [opções]`
Uma nota para quem entregou e outra para quem não entregou.

| Opção | Efeito |
|-------|--------|
| `--nota-entregou N` | nota de quem entregou (padrão: nota máxima da tarefa) |
| `--nota-nao-entregou N` | nota de quem não entregou (padrão: 0) |
| `--rascunho ignorar\|zero\|entregou` | o que fazer com rascunho não enviado (padrão: ignorar) |
| `--sobrescrever` | alterar também quem já tem nota |
| `--forcar-prazo` | permitir antes do fim do prazo ou de extensões |
| `--estado-fluxo ESTADO` | estado a gravar quando a tarefa usa fluxo de avaliação (ex.: `released`) |
| `--travar-sem-entrega` | travar o envio de quem não entregou |
| `--aplicar` / `--sim` | gravar logo após a prévia, com confirmação / sem perguntar |

`mackmoodle notas csv <tarefa> <arquivo> [opções]`
Nota e, se quiser, comentário de feedback por aluno, a partir de uma planilha (formato abaixo).
Aceita `--sobrescrever`, `--forcar-prazo`, `--estado-fluxo`, `--travar-sem-entrega`, `--aplicar`,
`--sim` e `--ignorar-nao-encontrados` (segue mesmo com linhas que não correspondem a nenhum aluno).

`mackmoodle modelo-csv <tarefa> <arquivo>`
Gera a planilha da tarefa já com userid, RA, nome, e-mail, situação, texto enviado e nota atual,
com as colunas `nota` e `comentario` em branco para preencher.

### Envios e avisos

`mackmoodle travar <tarefa> <userid>... [--destravar]`
Trava (ou libera, com `--destravar`) o envio dos alunos indicados. Os userids aparecem em
`mackmoodle --json envios <tarefa>` e na planilha do `modelo-csv`.

`mackmoodle aviso <curso>... --assunto "..." (--mensagem "..." | --arquivo msg.txt) [--html]`
Publica o mesmo aviso no fórum de avisos de cada curso. Texto simples vira parágrafos (uma linha em
branco separa parágrafos). `--html` indica que a mensagem já está em HTML.

`mackmoodle baixar <tarefa> [--destino pasta] [--rascunhos] [--sobrescrever]`
Cria uma pasta por aluno (RA e nome), baixa os arquivos enviados, salva o texto online em
`texto_online.txt` e escreve `indice.csv` com a situação de cada um. Arquivos que já existem com o
mesmo tamanho não são baixados de novo. Nomes de arquivo são ajustados para valer em qualquer
sistema operacional.

### Planos

| Comando | O que faz |
|---------|-----------|
| `mackmoodle aplicar <plano> [--travar-sem-entrega] [--sim]` | Grava no Moodle um plano gerado antes. |
| `mackmoodle planos [-n 20]` | Lista os planos recentes, com situação e resumo. |
| `mackmoodle plano <plano>` | Mostra um plano completo. |

## Gravações: plano, prévia e conferência

Os comandos `notas`, `travar` e `aviso` não gravam nada de imediato. Eles:

1. leem a situação atual da tarefa ou dos cursos;
2. montam um **plano** com a lista do que será gravado, os alunos ignorados e o motivo, os avisos e
   os bloqueios;
3. mostram a prévia e salvam o plano com um id (`notas-20260916-181500-ab12`).

A gravação acontece com `mackmoodle aplicar <id>`, ou com `--aplicar` no próprio comando, que pede
confirmação (`--sim` dispensa a pergunta).

**Bloqueios** (o plano não pode ser aplicado):

- prazo da tarefa ainda aberto, na regra de entrega, salvo `--forcar-prazo`;
- nota fora do intervalo entre 0 e a nota máxima;
- tarefa em grupo ou com nota por escala;
- fluxo de avaliação ativo sem `--estado-fluxo`;
- linhas do CSV sem aluno correspondente, com nome ambíguo ou repetidas;
- comentário numa tarefa sem "Comentários de feedback" ativado.

**Avisos** (mostrados, mas não impedem):

- os alunos serão notificados;
- a tarefa não tem data limite, e quem recebe nota de não entrega ainda pode enviar;
- há rascunhos não enviados, alunos fora do CSV ou nota para aluno sem envio.

**Na aplicação**, o programa:

- recusa planos já aplicados, com mais de 24 horas ou criados por outro usuário;
- lê a tarefa de novo e recusa o plano se algo mudou desde a prévia (novo envio, nova nota, novo
  aluno);
- grava em lotes de 40 alunos;
- lê as notas outra vez e informa qualquer diferença em relação ao plano.

Códigos de saída: `0` concluído, `1` cancelado, `2` erro, `3` plano com bloqueios,
`4` diferença encontrada na conferência.

Exemplo:

```bash
mackmoodle notas entrega https://graduacao.mackenzie.br/mod/assign/view.php?id=1042655
# confere a prévia
mackmoodle aplicar notas-20260916-181500-ab12 --travar-sem-entrega
```

## Planilha de notas (CSV)

- Separador vírgula ou ponto e vírgula; codificação UTF-8 (com ou sem BOM).
- Uma coluna de identificação, usada nesta ordem de preferência: `userid`, `email`, `ra`, `nome`.
  O nome é comparado sem acentos e sem diferença entre maiúsculas e minúsculas.
- Coluna `nota`, obrigatória. Aceita vírgula decimal. Nota em branco deixa o aluno como está.
- Coluna `comentario`, opcional. Linhas em branco separam parágrafos.
- Nomes de coluna aceitos como equivalentes: `E-mail`, `Matrícula`/`TIA` (RA), `Nome completo`,
  `Aluno`, `Nota final`, `Comentário`, `Feedback`.

Exemplo:

```csv
ra;nota;comentario
10430001;9,5;Faltou tratar o caso de fila vazia.
10430002;10;
```

## Arquivos e variáveis de ambiente

| Item | Linux e macOS | Windows |
|------|---------------|---------|
| Configuração e token | `~/.config/mackmoodle/config.json` | `%APPDATA%\mackmoodle\config.json` |
| Planos | `~/.local/state/mackmoodle/planos/` | `%LOCALAPPDATA%\mackmoodle\planos\` |

| Variável | Uso |
|----------|-----|
| `MACKMOODLE_SITE` | outro endereço de Moodle |
| `MACKMOODLE_TOKEN` | token vindo do ambiente, sem arquivo de configuração |
| `MACKMOODLE_CONFIG_DIR` | outra pasta de configuração |
| `MACKMOODLE_DATA_DIR` | outra pasta para os planos |

O pacote também instala `mackmoodle-mcp`, que expõe as mesmas operações pelo protocolo MCP
(entrada e saída padrão), para integração com outros programas.

## Limitações

- Só tarefas (assign) com nota numérica e envio individual.
- Sem extensão de prazo individual e sem feedback em arquivo.
- Sem criação ou edição de atividades, importação entre cursos ou mudança de datas, que o serviço
  do aplicativo não permite.
- O filtro de semestre de `cursos` depende do padrão de nome dos cursos da graduação
  ("... - 2026/2").

## Desenvolvimento

```bash
python -m unittest discover -s tests
```

Os testes sobem um servidor local que reproduz as respostas do Moodle da graduação, inclusive as
particularidades descritas acima, e não acessam o Moodle real.

Estrutura:

| Arquivo | Conteúdo |
|---------|----------|
| `mackmoodle/client.py` | chamadas REST e download de arquivos |
| `mackmoodle/auth.py` | login pelo link do aplicativo |
| `mackmoodle/config.py` | configuração e token |
| `mackmoodle/ops.py` | consultas, planos, aplicação, download e comparação |
| `mackmoodle/planos.py` | armazenamento e validade dos planos |
| `mackmoodle/cli.py` | comandos |
| `mackmoodle/mcp_server.py` | servidor MCP |
| `tests/` | testes e servidor de teste |
