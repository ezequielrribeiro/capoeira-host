# CapoeiraHost

Gateway local **compatível com a API do Ollama** que roteia inferências para
LLMs Web (Gemini, Claude, ChatGPT, Microsoft 365 Copilot) através de uma
**extensão de navegador (Chrome MV3)** — sem baixar modelos, sem chave de API,
reaproveitando a sessão autenticada da sua conta no navegador.

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

## Como funciona

```
Apps (Open WebUI, curl, SDKs Ollama)  ──HTTP 127.0.0.1:8765──▶  CapoeiraHost
CapoeiraHost                          ──WS 127.0.0.1:8766────▶  Extensão MV3
Extensão                              ──DOM─────────────────▶  LLM Web (aba autenticada)
```

- **8765** — API REST compatível com o Ollama (`/api/generate`, `/api/chat`, `/api/tags`, ...).
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

# Chat com streaming (NDJSON) — imprime o texto à medida que chega
python smoke_test.py --endpoint chat --prompt "Quem foi Besouro Mangangá?" --stream

# Outro modelo / endpoint personalizado
python smoke_test.py --model claude-sonnet --stream

# Reutilizar o mesmo chat na aba Web (não cria um chat novo)
python smoke_test.py --endpoint chat --prompt "Continua neste chat?" --new-chat false
```

O script imprime a **solicitação** enviada e o **retorno** do servidor. Se o
provedor estiver offline (extensão não conectada / aba fechada), ele exibe o
motivo (`503 no bridge available`) e a dica de correção. Para usar outra hora,
porta ou arquivo de config, consulte a tabela em [Configuração](#configuração-env),
ou passe `--base-url http://127.0.0.1:9000` por exemplo.

O flag `--new-chat {true,false}` envia `new_chat` no payload, sobrescrevendo
`CAPOEIRA_NEW_CHAT` **por requisição** (ausente = usa o default do servidor). Com
`--new-chat false` a extensão injeta o prompt no chat já aberto, em vez de criar
nova conversa.

Alternativa em bash (Linux/macOS):

```bash
curl http://127.0.0.1:8765/api/chat -d '{"model":"gemini-pro","stream":true,"messages":[{"role":"user","content":"Quem foi Besouro Mangangá?"}]}'
curl http://127.0.0.1:8765/api/chat -d '{"model":"gemini-pro","new_chat":false,"stream":true,"messages":[{"role":"user","content":"Continua neste chat?"}]}'
```

## Melhorias com Python

```bash
pip install requests
```

```python
import requests

r = requests.post(
    "http://127.0.0.1:8765/api/chat",
    json={
        "model": "claude-sonnet",
        "stream": False,
        "messages": [
            {"role": "user", "content": "Me conte uma lenda da capoeira."}
        ],
    },
)
print(r.json()["message"]["content"])
```

## Configuração (env)

| Variável | Default | Descrição |
|---|---|---|
| `CAPOEIRA_HOST` | `127.0.0.1` | Interface de bind (manter loopback por segurança) |
| `CAPOEIRA_HTTP_PORT` | `8765` | Porta da API REST (Ollama-compatível) |
| `CAPOEIRA_WS_PORT` | `8766` | Porta do bridge WebSocket da extensão |
| `CAPOEIRA_MODELS_FILE` | `./models.json` | Registry de perfis de provedor |
| `CAPOEIRA_TIMEOUT` | `180` | Timeout (s) por requisição antes de `504` |
| `CAPOEIRA_QUEUE` | `10` | Máximo de requisições enfileiradas por provedor |
| `CAPOEIRA_NEW_CHAT` | `true` | Iniciar um chat novo na aba Web a cada requisição. Pode ser sobrescrito por requisição via `new_chat` no body de `/api/generate` e `/api/chat` |

Exemplo:

```bash
# Windows (PowerShell):
$env:CAPOEIRA_HTTP_PORT = "9000"; $env:CAPOEIRA_WS_PORT = "9001"
python -m server.main
```

## Modelos (perfis em `models.json`)

Um "modelo" é um perfil que aponta para um provedor Web + instruções de sistema
+ opções padrão. Não há pesos de modelo.

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

## API (compatível com Ollama)

Base URL: `http://127.0.0.1:8765`

### Implementados

| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/generate` | Geração a partir de `prompt` (stream ou não) |
| POST | `/api/chat` | Conversa a partir de `messages` (stream ou não) |
| GET | `/api/tags` | Lista os perfis registrados |
| GET | `/api/ps` | Estado atual dos provedores (`idle`/`generating`) |
| POST | `/api/show` | Detalhes de um perfil (modelfile, parâmetros, template) |
| POST | `/api/create` | Registra um novo perfil (não baixa pesos) |
| POST | `/api/copy` | Copia um perfil existente |
| DELETE | `/api/delete` | Remove um perfil |
| GET | `/` `/api/version` | `{"version":"1.0.0"}` |

> **Reutilizar o chat Web** — por padrão, toda requisição inicia um **novo chat** na aba
> do provedor (`newChat: true`). Para continuar a mesma conversa (menos "pisca" e
> contexto real na Web), defina `CAPOEIRA_NEW_CHAT=false` (global) ou envie
> `"new_chat": false` no corpo de `/api/generate` ou `/api/chat` (por requisição; o
> valor por requisição tem precedência). Campo exclusivo do CapoeiraHost — clientes
> Ollama ignoram campos extras.

Exemplos de payload por requisição (`new_chat: false` reutiliza o chat aberto;
`new_chat: true` restaura o comportamento de novo chat, mesmo com o env em `false`):

```json
{ "model": "gemini-pro", "new_chat": false, "messages": [{ "role": "user", "content": "Continua neste chat?" }] }
```

```json
{ "model": "gemini-pro", "prompt": "Oi", "new_chat": false }
```

### Tool calling simulado

A UI Web dos provedores **não executa function calling nativo**. Para que agentes
possam chamar ferramentas (automações locais, etc.), o CapoeiraHost ativa um **tool
calling simulado**: quando `/api/chat` recebe a lista `tools`, as definições são
listadas no envelope de system prompt como linhas únicas (`[TOOLS]` + uma linha
`[TOOL] {"name": ..., "parameters": ...}` por ferramenta) e o modelo é orientado a
responder com **uma linha de texto puro** no contrato `[TOOL_CALL] nome {"args": ...}`.
O gateway então converte cada linha em `message.tool_calls` no formato
Ollama-compatível.

- **Ativação global, otimista:** ocorre somente quando a requisição traz `tools`.
- **Contrato de saída:** o modelo emite uma linha por chamada:
  `[TOOL_CALL] shopping {"item": "leite", "quantidade": 2}` — pode haver texto antes/depois,
  e múltiplas linhas viram chamadas paralelas.
- **Fallback:** se a resposta não for uma tool call válida, o host devolve o texto
  (com as linhas `[TOOL_CALL]` removidas) como `message.content` (nunca quebra a conversa).
- **Saída `format: "json"`** (sem `tools`): o modelo devolve o JSON entre `[JSON_START]`
  e `[JSON_END]`; o gateway extrai o bloco tolerando prosa ao redor.

```python
import requests

r = requests.post(
    "http://127.0.0.1:8765/api/chat",
    json={
        "model": "gemini-pro",
        "stream": False,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "shopping",
                    "description": "Consulta uma compra.",
                    "parameters": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]},
                },
            }
        ],
        "messages": [{"role": "user", "content": "Preciso comprar leite."}],
    },
)
resp = r.json()["message"]
print(resp.get("tool_calls", resp.get("content")))
```

Depois que o agente executa a ferramenta, ele continua a conversa enviando de volta o
assistant com `tool_calls` e um `role: "tool"` com o resultado (`tool_call_id` +
`content`). Se um `role: "tool"` chegar **sem** a lista `tools`, o host retorna `400`.

### Não aplicáveis (modelo não é hospedado)

| Endpoint | Status | Motivo |
|---|---|---|
| `/api/pull`, `/api/push`, `/api/blobs/*` | `501` | Modelos vêm da Web, não são baixados |
| `/api/embed`, `/api/embeddings` | `501` | Embeddings fora de escopo |
| `images` no payload | `400` | Não suportados via interface Web |

### Erros (formato Ollama: `{"error": "<mensagem>"}`)

| Código | Situação típica |
|---|---|
| `400` | Corpo inválido, imagens, ou tool calling sem a lista `tools` no request |
| `404` | Modelo não registrado |
| `502` | Falha reportada pela extensão durante a geração |
| `503` | Provider sem bridge conectado / fila cheia |
| `504` | Timeout de geração na Web |

## Usar com clientes do ecossistema Ollama

Qualquer aplicação que fale com `ollama serve` funciona apontando para esta API:

> ⚠️ Client **somente em modo interativo/simples**. Não use agentes, autocomplete
> contínuo ou ferramentas de desenvolvimento avançadas (OpenCode, Continue em
> modo agente, etc.) — o volume de requisições pode levar ao bloqueio dos serviços.

```bash
# Open WebUI, Continue, LibreChat...
# configure "Ollama Base URL" = http://127.0.0.1:8765

# SDK ollama (Python)
from ollama import Client
client = Client(host="http://127.0.0.1:8765")
print(client.chat(model="gemini-pro", messages=[{"role": "user", "content": "Oi"}]))

# LangChain
from langchain_community.llms import OllamaLLM
llm = OllamaLLM(model="gemini-pro", base_url="http://127.0.0.1:8765")
print(llm.invoke("Resuma a lei áurea em 1 frase."))
```

## Solução de problemas

| Sintoma | Causa provável | Correção |
|---|---|---|
| `503 no bridge available` | Extensão não carregada / aba do provedor fechada, ou servidor rodando código antigo | Recarregar `extension/`, abrir aba logada do Gemini, Claude ou Copilot 365 e **reiniciar o servidor** para aplicar mudanças no bridge |
| `422` / "Input should be a valid dictionary" | JSON não chegou como JSON (falta `Content-Type: application/json` ou quotes comidos pelo PowerShell) | Usar `smoke_test.py`, `Invoke-RestMethod -ContentType "application/json"` ou `curl --data "@body.json"` |
| `502` com msg de seletor | A interface do provedor mudou | Revisar os seletores em `extension/adapters/*.js` |
| `504 bridge timeout` | Provedor demorou > `CAPOEIRA_TIMEOUT` | Aumentar `CAPOEIRA_TIMEOUT` |
| Sem resposta e aba "piscando" | Nova conversa criada a cada requisição | Comportamento esperado com `newChat: true`. Para reutilizar o mesmo chat (sem "piscar"), defina `CAPOEIRA_NEW_CHAT=false` (ou envie `"new_chat": false` na requisição) |
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
a resposta simulada volta ao cliente) e o streaming NDJSON via `STREAM_UPDATE` +
`RESPONSE`.

## Estrutura

```
capoeira-host/
├── server/               # API + gateway + bridge (Python/FastAPI)
│   ├── main.py           # entrypoint
│   ├── config.py         # env vars, binds, portas
│   ├── registry.py       # CRUD de perfis (models.json)
│   ├── ollama_dto.py     # DTOs no formato Ollama
│   ├── http_api.py       # rotas REST
│   ├── bridge.py         # WS server 8766
│   ├── gateway.py        # Ollama → SEND_PROMPT → RESPONSE
│   ├── queue.py          # fila FIFO por provider
│   ├── prompt_builder.py # envelope de sistema/transcript/template
│   └── errors.py         # erros {"error": ...}
├── extension/            # extensão Chrome MV3
│   ├── manifest.json
│   ├── content.js        # WS client + orquestrador
│   └── adapters/         # base, gemini, claude, chatgpt, copilot365
├── models.json           # registry de perfis
├── smoke_test.py         # smoke test da API (solicitação → retorno)
├── requirements.txt
└── CAPOEIRA_HOST_SPEC.md # especificação completa
```

## Documentação

Consulte `CAPOEIRA_HOST_SPEC.md` para a especificação técnica completa
(protocolo do bridge, formatos de mensagem e detalhes da API).