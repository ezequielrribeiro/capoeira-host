# CapoeiraHost

Gateway local que roteia inferências para **LLMs Web** (Gemini, Claude, ChatGPT,
Microsoft 365 Copilot) através de uma **extensão de navegador (Chrome MV3)** — sem
baixar modelos, sem chave de API, reaproveitando a sessão autenticada da sua
conta no navegador.

> **⚠️ Disclaimer**
>
> Esta ferramenta é voltada, principalmente, para **pequenas requisições**: testes
> pontuais, scripts simples e uso interativo. Ela depende da interface Web dos
> LLMs (Gemini, Claude, Copilot…), que é feita para uso humano interativo, **um
> turno por vez**.
>
> **Evite integrá-la a ferramentas de desenvolvimento avançadas** (como Opencode
> e afins, agentes de terminal, autocomplete contínuo…). Essas soluções
> demandam uma conexão mais robusta e constante com o modelo e realizam
> **diversas requisições**, ficando passíveis de **bloqueio dos serviços**
> (rate-limit, CAPTCHA, suspensão de conta) e de lentidão/instabilidade por causa
> da fila FIFO de 1 requisição por vez.
>
> Use a ferramenta para chamadas pontuais e de baixo volume.

## A comunicação é textual, sem JSON

**Histórico:** o protocolo foi, originalmente, inspirado no **padrão de comunicação
do Ollama** (API JSON/NDJSON). Contudo, a inferência acontece via interface Web, e
**cada modelo pode se comportar de forma diferente sob a interface Web que for
fornecida** — reproduzir, recompor ou formatar JSON de maneiras distintas.
Isso tornava o JSON pouco confiável no fio, então foi adotado um **formato
alternativo de texto puro**, buscando robustez diante dessa variabilidade.

> **⚠️ Mudança de protocolo:** a API **não é mais compatível com a API Ollama**.
> Requisições usam `application/x-www-form-urlencoded` (`chave=valor`) e respostas
> são `text/plain`. Isso foi decidido porque a interface Web nem sempre reproduz
> JSON corretamente — então todo o fio do host até o LLM Web usa um **contrato de
> texto puro** (tags `[TOOL_CALL]`, `[TOOL_RESULT]`, etc.), sem JSON embutido.

O JSON permanece apenas em `models.json` (configuração do registry) e no WebSocket
bridge (`host ⇄ extensão`, que são código nosso e não sofrem a ação da UI Web).

## Como funciona

```
Apps (curl, formulários, scripts, SDKs) ──HTTP 127.0.0.1:8765──▶  CapoeiraHost
CapoeiraHost                              ──WS 127.0.0.1:8766────▶  Extensão MV3
Extensão                                  ──DOM─────────────────▶  LLM Web (aba autenticada)
```

- **8765** — API textual (`/api/generate`, `/api/chat`, `/api/tags`, ...).
- **8766** — Bridge WebSocket onde a extensão do navegador se conecta.
- Os "modelos" são **perfis de provedor** definidos em `models.json` (nenhum peso é baixado).

## Pré-requisitos

- Python 3.10+
- pip
- Chrome ou Edge (para carregar a extensão MV3)
- Uma conta logada em pelo menos um provedor Web (Gemini, Claude ou Copilot 365, hoje)

## Instalação

```bash
# (opcional, recomendado) criar um ambiente virtual
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

## Iniciar (passo a passo)

### 1. Subir o servidor

```bash
python -m server.main
```

O server escuta em `http://127.0.0.1:8765` (API) e `ws://127.0.0.1:8766` (bridge).

### 2. Carregar a extensão no navegador

1. Abra `chrome://extensions` (ou `edge://extensions`).
2. Ative o **Modo do desenvolvedor** (canto superior direito).
3. Clique em **Carregar sem compactação** / **Load unpacked**.
4. Selecione a pasta `extension/` deste projeto.

### 3. Abrir uma aba autenticada do provedor

Mantenha uma aba aberta e **logada** no serviço Web do modelo que você quer usar:

| Provedor | Endereço | Status do adaptador |
|---|---|---|
| Gemini | `gemini.google.com` | ✅ Implementado |
| Claude | `claude.ai` | ✅ Implementado (streaming incremental) |
| ChatGPT | `chatgpt.com` | ⚠️ Implementado, mas **não habilitado** no `manifest.json` |
| Copilot 365 | `m365.cloud.microsoft/chat` | ✅ Implementado |

> A extensão conecta ao WebSocket local automaticamente ao carregar a aba.
> Provedores sem aba aberta aparecem como **offline** → requisições retornam `503`.

### 4. Testar

Use o script `smoke_test.py` (stdlib, sem problemas de quoting de shell no
Windows/PowerShell):

```powershell
# Versão
curl http://127.0.0.1:8765/api/version

# Geração simples (não-stream)
python smoke_test.py --endpoint generate --prompt "O que é capoeira?"

# Chat com streaming (texto puro) — imprime o texto à medida que chega
python smoke_test.py --endpoint chat --prompt "Quem foi Besouro Mangangá?" --stream

# Outro modelo
python smoke_test.py --model claude-sonnet --stream

# Reutilizar o mesmo chat na aba Web (não cria um chat novo)
python smoke_test.py --endpoint chat --prompt "Continua neste chat?" --new-chat false
```

O script imprime a **solicitação** (`chave=valor`) enviada e o **retorno** do
servidor. Se o provedor estiver offline (extensão não conectada / aba fechada),
ele exibe o motivo (`503 no bridge available`) e a dica de correção.

O flag `--new-chat {true,false}` envia `new_chat` no payload, sobrescrevendo
`CAPOEIRA_NEW_CHAT` **por requisição** (ausente = usa o default do servidor). Com
`--new-chat false` a extensão injeta o prompt no chat já aberto, em vez de criar
nova conversa.

Exemplos diretos com `curl`:

```bash
# Geração simples (form-urlencoded)
curl -d model=gemini-pro --data-urlencode "prompt=O que é capoeira?" http://127.0.0.1:8765/api/generate

# Chat com streaming
curl -d model=gemini-pro --data-urlencode "role=user" --data-urlencode "content=Quem foi Besouro Mangangá?" -d stream=true http://127.0.0.1:8765/api/chat
```

```powershell
# PowerShell
curl.exe -d model=gemini-pro --data-urlencode "prompt=Oi" http://127.0.0.1:8765/api/generate
```

## Melhorias com Python

```bash
pip install requests
```

```python
import requests

r = requests.post(
    "http://127.0.0.1:8765/api/chat",
    data={"model": "claude-sonnet", "role": "user", "content": "Me conte uma lenda da capoeira."},
)
print(r.text)
```

## Configuração (env)

| Variável | Default | Descrição |
|---|---|---|
| `CAPOEIRA_HOST` | `127.0.0.1` | Interface de bind (manter loopback por segurança) |
| `CAPOEIRA_HTTP_PORT` | `8765` | Porta da API textual |
| `CAPOEIRA_WS_PORT` | `8766` | Porta do bridge WebSocket da extensão |
| `CAPOEIRA_MODELS_FILE` | `./models.json` | Registry de perfis de provedor |
| `CAPOEIRA_TIMEOUT` | `180` | Timeout (s) por requisição antes de `504` |
| `CAPOEIRA_QUEUE` | `10` | Máximo de requisições enfileiradas por provedor |
| `CAPOEIRA_NEW_CHAT` | `true` | Iniciar um chat novo na aba Web a cada requisição. Pode ser sobrescrito por requisição via `new_chat` no form de `/api/generate` e `/api/chat` |

Exemplo:

```bash
# Windows (PowerShell):
$env:CAPOEIRA_HTTP_PORT = "9000"; $env:CAPOEIRA_WS_PORT = "9001"
python -m server.main
```

## Modelos (perfis em `models.json`)

Um "modelo" é um perfil que aponta para um provedor Web + instruções de sistema
+ opções padrão. `models.json` continua em formato JSON (é config, não é
requisição/retorno).

```json
{
  "name": "gemini-pro",
  "provider": "gemini",
  "display_name": "Gemini Pro (Web)",
  "system_prompt": "Você é um assistente útil. Responda de forma concisa e direta.",
  "template": "[INST] {{ .System }} [/INST]\n\n{{ .Prompt }}",
  "streaming": false,
  "options": { "temperature": 0.7, "num_predict": 2048, "num_ctx": 32768 }
}
```

O registro padrão já traz 4 perfis: `gemini-pro`, `claude-sonnet`, `copilot-365` e `chatgpt`.
Você pode editar o arquivo, criar novos perfis via `/api/create` (ver abaixo) ou apagar via `/api/delete`.

## API (textual — sem JSON)

Base URL: `http://127.0.0.1:8765`

Todas as respostas são `text/plain`. Requests POST usam
`application/x-www-form-urlencoded` (campos `chave=valor`, repetíveis).

### Implementados

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/version` | `1.0.0` |
| GET | `/api/tags` | Lista perfis, um por linha: `gemini-pro | provider=gemini | streaming=false | modified_at=...` |
| GET | `/api/ps` | Estado dos providers online: `gemini-pro | provider=gemini | status=idle` |
| POST | `/api/generate` | Geração a partir de `prompt` (stream ou não) |
| POST | `/api/chat` | Conversa a partir de `role`/`content` repetidos (stream ou não) |
| POST | `/api/show` | Detalhes de um perfil (bloco de texto) |
| POST | `/api/create` | Registra um novo perfil (campos `model`, `from`, `system`, `template`, `parameter.<chave>`) |
| POST | `/api/copy` | Copia um perfil (`source`, `destination`) |
| DELETE | `/api/delete` | Remove um perfil (`model`) |

#### `POST /api/generate`

Campos: `model`*, `prompt`*, `system`, `template`, `stream` (`true`/`false`),
`new_chat` (`true`/`false`), `option.<chave>` (ex.: `option.temperature=0.7`).

Resposta: `text/plain` com o texto gerado. Com `stream=true`, o texto chega em
chunks (sem NDJSON).

```bash
curl -d model=gemini-pro --data-urlencode "prompt=Explique o que é capoeira em 1 parágrafo." http://127.0.0.1:8765/api/generate
```

#### `POST /api/chat`

Campos: `model`*, e pares repetidos `role`/`content` (na ordem da conversa),
`tool_call_id` (para `role=tool`), `tools` (contrato textual, ver abaixo),
`stream`, `new_chat`, `option.<chave>`.

```bash
curl -d model=gemini-pro --data-urlencode "role=user" --data-urlencode "content=Quem é Besouro Mangangá?" -d new_chat=false http://127.0.0.1:8765/api/chat
```

Resposta: `text/plain` com a resposta do assistente.

> **Reutilizar o chat Web** — por padrão, toda requisição inicia um **novo chat** na aba
> do provedor (`newChat: true`). Para continuar a mesma conversa (menos "pisca" e
> contexto real na Web), defina `CAPOEIRA_NEW_CHAT=false` (global) ou envie
> `new_chat=false` no form (por requisição; o valor por requisição tem precedência).

### Tool calling simulado (contrato textual)

A UI Web dos provedores **não executa function calling nativo**. O CapoeiraHost
ativa um **tool calling simulado** quando `/api/chat` recebe o campo `tools` com
definições no formato textual (`chave=valor`, um tool por linha):

```
name=shopping | desc=Consulta/prepara uma compra. | item:string | quantidade:int
```

Esse texto é injetado no envelope de system prompt como `[TOOL] ...` e o modelo é
orientado a responder com **uma linha de texto puro** por chamada:

```
[TOOL_CALL] shopping | item=leite | quantidade=2
```

Valores: números/booleans vão direto (`2`, `true`), strings com espaço usam aspas
simples (`'valor com espaço'`); várias linhas `[TOOL_CALL]` viram chamadas
paralelas.

- O host devolve as linhas `[TOOL_CALL]` (prosa removida) como `text/plain`.
- **Fallback:** se a Web responder texto (sem linha válida), o host devolve a prosa
  (com as linhas `[TOOL_CALL]` removidas) — nunca quebra a conversa.
- O agente executa a ferramenta e continua o ciclo mandando `role=tool` (+
  `tool_call_id`) com o resultado; sem o campo `tools`, o `role=tool` retorna `400`.

```python
import requests

r = requests.post(
    "http://127.0.0.1:8765/api/chat",
    data={
        "model": "gemini-pro",
        "tools": "name=shopping | desc=Consulta/prepara uma compra. | item:string | quantidade:int",
        "role": "user",
        "content": "Preciso comprar leite.",
    },
)
print(r.text)  # [TOOL_CALL] shopping | item=leite | quantidade=2
```

### Não aplicáveis (modelo não é hospedado)

| Endpoint | Status | Motivo |
|---|---|---|
| `/api/pull`, `/api/push`, `/api/blobs/*` | `501` | Modelos vêm da Web, não são baixados |
| `/api/embed`, `/api/embeddings` | `501` | Embeddings fora de escopo |

### Erros

Erros retornam **`text/plain`** com a mensagem e os códigos HTTP:

| Código | Situação típica |
|---|---|
| `400` | Campo obrigatório ausente, `role` inválido, ou `role=tool` sem `tools` |
| `404` | Modelo não registrado |
| `502` | Falha reportada pela extensão durante a geração |
| `503` | Provider sem bridge conectado / fila cheia |
| `504` | Timeout de geração na Web |

## Solução de problemas

| Sintoma | Causa provável | Correção |
|---|---|---|
| `503 no bridge available` | Extensão não carregada / aba do provedor fechada, ou servidor rodando código antigo | Recarregar `extension/`, abrir aba logada do Gemini, Claude ou Copilot 365 e **reiniciar o servidor** para aplicar mudanças no bridge |
| `400 campo 'model' é obrigatório` | Body não enviado como form-urlencoded (falta `Content-Type` ou quotes comidos pelo PowerShell) | Usar `smoke_test.py`, `Invoke-RestMethod -Method Post -Form`, ou `curl -d model=...` |
| `502` com msg de seletor | A interface do provedor mudou | Revisar os seletores em `extension/adapters/*.js` |
| `504 bridge timeout` | Provedor demorou > `CAPOEIRA_TIMEOUT` | Aumentar `CAPOEIRA_TIMEOUT` |
| Sem resposta e aba "piscando" | Nova conversa criada a cada requisição | Comportamento esperado com `new_chat=true`. Para reutilizar o mesmo chat (sem "piscar"), defina `CAPOEIRA_NEW_CHAT=false` (ou envie `new_chat=false` na requisição) |
| Provedor não lista em `/api/ps` | Provider sem conexão WS | Abrir/recarregar a aba do provedor |

## Testes

A suíte automatizada valida o servidor e a comunicação com o *agente* na web
(extensão) via `TestClient` + um cliente WebSocket que **simula a extensão** —
sem precisar abrir um navegador real.

```bash
pip install -r requirements-dev.txt

python -m pytest tests/ -v
```

Os testes cobrem: `GET /api/version`, `GET /api/tags`, erro `503` com provider
offline, um fluxo ponta a ponta de `/api/chat` (o bridge recebe `SEND_PROMPT` e
a resposta simulada volta ao cliente), o streaming de texto puro via
`STREAM_UPDATE` + `RESPONSE`, e o tool calling simulado no contrato textual.

## Estrutura

```
capoeira-host/
├── server/               # API + gateway + bridge (Python/FastAPI)
│   ├── main.py           # entrypoint
│   ├── config.py         # env vars, binds, portas
│   ├── registry.py       # CRUD de perfis (models.json)
│   ├── ollama_dto.py     # dataclasses mínimas de request
│   ├── http_api.py       # rotas (form-urlencoded + text/plain)
│   ├── bridge.py         # WS server 8766
│   ├── gateway.py        # request → SEND_PROMPT → texto
│   ├── queue.py          # fila FIFO por provider
│   ├── prompt_builder.py # envelope de sistema/transcript/contrato textual
│   └── errors.py         # erros text/plain
├── extension/            # extensão Chrome MV3
│   ├── manifest.json
│   ├── content.js        # WS client + orquestrador
│   └── adapters/         # base, gemini, claude, chatgpt, copilot365
├── models.json           # registry de perfis (JSON de config)
├── smoke_test.py         # smoke test da API (form → texto)
├── requirements.txt
└── CAPOEIRA_HOST_SPEC.md # especificação completa
```

## Documentação

Consulte `CAPOEIRA_HOST_SPEC.md` para a especificação técnica completa
(protocolo do bridge, formato textual de comunicação e detalhes da API).