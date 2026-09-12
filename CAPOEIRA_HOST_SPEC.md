# CapoeiraHost — Gateway Local ⇄ LLM Web via Browser Bridge

**Projeto:** CapoeiraHost
**Versão:** `2.0.0`
**Status:** `Approved`
**Data:** 12 de Setembro de 2026
**Revisão:** 2.0 — comunicação **textual** (sem JSON) entre agente/ferramenta local, gateway e LLM via interface Web.

---

## 1. Visão Geral e Objetivos

### 1.1. Propósíto

O **CapoeiraHost** é um serviço local (`127.0.0.1:8765`) que roteia chamadas de
inferência para **LLMs Web** (Gemini, Claude, Microsoft 365 Copilot, ChatGPT) por
meio de um **Browser Extension Bridge** (WebSocket local na `8766` + injeção no DOM),
reaproveitando a sessão autenticada do navegador.

### 1.2. Decisão-chave: comunicação textual (sem JSON)

**Origem do desenho:** o CapoeiraHost foi, originalmente, inspirado no **padrão de
comunicação do Ollama**. Como a inferência acontece via interface Web, e **cada
modelo pode se comportar de forma diferente sob a interface Web que for fornecida**,
exigir fidelidade ao JSON se mostrou frágil (code fences, escaping, quebras de
linha). Em vez disso, optou-se por um **formato alternativo** de texto puro —
robusto diante dessa característica — mantendo JSON apenas onde o fio é nosso
(`models.json` e o bridge WebSocket host⇄extensão).

A interface Web dos LLMs **nem sempre reproduz JSON corretamente** (adiciona code
fences, escapa aspas, quebra linha, etc.). Por isso **todo o fio** agente/ferramenta
local → gateway → LLM Web usa um **contrato de texto puro** (`chave=valor` com
separador `|` e tags de linha única como `[TOOL_CALL]`, `[TOOL_RESULT]`):

- A **API HTTP** não é mais compatível com o Ollama: os POSTs usam
  `application/x-www-form-urlencoded` e as respostas são `text/plain`.
- O `format:"json"` / `[MODE_JSON]` foi **removido** — não há mais saída estruturada
  via Web; o que a Web produz é texto.
- JSON permanece **apenas** em: `models.json` (config do registry, não é
  requisição/retorno) e no **WebSocket bridge** host⇄extensão (código nosso, que
  não sofre a ação da UI Web).

```
┌──────────────────────────────────────────────────────────────────────┐
│               AGENTE / FERRAMENTA LOCAL (curl, script, UI)          │
│                    POST form-urlencoded → text/plain                │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ 127.0.0.1:8765
┌────────────────────────────────▼─────────────────────────────────────┐
│                        CAPOEIRAHOST (SERVER)                         │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │ HTTP API     │──▶│ Gateway /    │──▶│ Model Registry (perfis)  │  │
│  │ (text/plain) │   │ Scheduler    │   └──────────────────────────┘  │
│  └──────────────┘   └──────┬───────┘                                  │
│  ┌─────────────────────────▼──────────────────────────────────────┐  │
│  │ Bridge WS Server (WS) — ws://127.0.0.1:8766  (JSON, nosso)     │  │
│  └────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────┬─────────────────────────────────────┘
                                  │ WebSocket (JSON)
┌─────────────────────────────────▼─────────────────────────────────────┐
│                  BROWSER EXTENSION BRIDGE (Chrome MV3)                │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │ WS Client    │──▶│ Orchestrator │──▶│ Provider Adapters        │  │
│  └──────────────┘   └──────────────┘   │ (Gemini/Claude/365/Chat)│  │
│                                         └────────────┬─────────────┘  │
└───────────────────────────────────────────────────────┼───────────────┘
                                                        │ DOM (texto puro)
┌───────────────────────────────────────────────────────▼───────────────┐
│                       WEB LLM INTERFACE (navegador)                   │
│                              (texto puro, sem JSON)                   │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.3. Problema de Negócio

Muitos usuários possuem acesso a LLMs **apenas pela interface Web** (login/sessão
no navegador), sem API paga disponível. O CapoeiraHost oferece uma API textual
local simples, reaproveitando a sessão autenticada do navegador e **evitando
JSON** na comunicação — que as UIs Web costumam corromper.

### 1.4. Escopo — O QUE FOI DISPENSADO

- ❌ Compatibilidade com a API **Ollama** (removida nesta revisão).
- ❌ Saída estruturada (`format:"json"`) — removida.
- ❌ CLI, processamento de código (AST/Tree-Sitter), diff applier, grafos.
- ❌ Embeddings, upload de imagens, pull/push de modelos.

### 1.5. Objetivos de Design

1. **Agnóstico a LLMs Web**: provedores plugáveis via adaptadores na extensão.
2. **Texto puro em todo o fio**: nenhum JSON entre agente local, gateway e UI Web.
3. **Atomicidade de requisição**: 1 requisição HTTP = 1 ciclo de geração na Web
   (novo chat por requisição por default), sem estado compartilhado indevido.
4. **Segurança local**: loopback por padrão, validação de `Origin` no WebSocket,
   CORS restrito a localhost.
5. **Streaming honesto**: texto puro transmitido em chunks (`STREAM_UPDATE` real ou
   replay final), nunca NDJSON/JSON.

---

## 2. Conceitos

| Conceito | Mapeamento no CapoeiraHost |
|---|---|
| Modelo | **Perfil de provedor** no registry (`models.json`) — p.ex. `gemini-pro`, `claude-sonnet` |
| Modelfile (TEMPLATE, SYSTEM, PARAMETER) | Campos do perfil: `provider`, `system_prompt`, `template`, `options` |
| Inferência | *Remota* na interface Web do provedor |
| `/api/generate` | Prompt único enviado à Web (transcript simples) |
| `/api/chat` | Conversa serializada como transcript textual e enviada como 1 prompt (novo chat por requisição; opcionalmente reutiliza via `new_chat=false`) |
| `stream=true` | Streaming de texto puro (replay final em chunks OU `STREAM_UPDATE` incremental se o adaptador suportar) |
| `options` | Traduzidos em instruções injetadas no envelope de system prompt (`[OPTIONS]`) |
| Tool calling | **Simulado** via contrato textual `[TOOL_CALL] nome | chave=valor` (sem JSON) |

---

## 3. Portas, Bind e Configuração

| Variável | Default | Descrição |
|---|---|---|
| `CAPOEIRA_HOST` | `127.0.0.1` | Interface de bind do serviço (manter loopback por segurança) |
| `CAPOEIRA_HTTP_PORT` | `8765` | **API textual** (form-urlencoded → text/plain) |
| `CAPOEIRA_WS_PORT` | `8766` | Bridge WebSocket para a extensão |
| `CAPOEIRA_MODELS_FILE` | `./models.json` | Arquivo de registry de perfis (JSON de config) |
| `CAPOEIRA_TIMEOUT` | `180` | Timeout (s) por requisição antes de `504` |
| `CAPOEIRA_QUEUE` | `10` | Máximo de requisições enfileiradas por provedor |
| `CAPOEIRA_NEW_CHAT` | `true` | Inicia um chat novo na aba Web a cada requisição (padrão). Pode ser sobrescrito via `new_chat` no form de `/api/generate` e `/api/chat` |

---

## 4. Model Registry (perfis de provedor)

`models.json` continua **JSON** — é configuração, não é requisição/retorno.

```json
{
  "version": "1",
  "default_provider": "gemini",
  "models": [
    {
      "name": "gemini-pro",
      "display_name": "Gemini Pro (Web)",
      "provider": "gemini",
      "system_prompt": "Você é um assistente útil. Responda de forma concisa e direta.",
      "template": "[INST] {{ .System }} [/INST]\n\n{{ .Prompt }}",
      "streaming": false,
      "options": { "temperature": 0.7, "num_predict": 2048, "num_ctx": 32768 },
      "created_at": "2026-09-03T00:00:00Z"
    }
  ]
}
```

---

## 5. API HTTP (textual — sem JSON)

Base URL: `http://127.0.0.1:8765`

- Requests POST usam `application/x-www-form-urlencoded` (campos `chave=valor`
  repetíveis para `role`/`content`).
- Respostas são `text/plain`; streaming é texto puro em chunks (sem NDJSON).
- Erros retornam `text/plain` com a mensagem + códigos HTTP.

### 5.1. `GET /` e `GET /api/version`

```text
1.0.0
```

### 5.2. `GET /api/tags` — listar perfis

Uma linha por perfil (uma por linha do registry):

```text
gemini-pro | provider=gemini | streaming=false | modified_at=2026-09-03T00:00:00Z
claude-sonnet | provider=claude | streaming=true | modified_at=2026-09-03T00:00:00Z
```

### 5.3. `GET /api/ps` — estado atual

Somente providers com bridge conectado:

```text
gemini-pro | provider=gemini | status=idle
```

### 5.4. `POST /api/generate`

Campos de form: `model`*, `prompt`*, `system`, `template`, `stream`
(`true`/`false`), `new_chat` (`true`/`false`), `option.<chave>`.

```bash
curl -X POST http://127.0.0.1:8765/api/generate \
  -d model=gemini-pro \
  --data-urlencode "prompt=Explique o que é capoeira em 1 parágrafo." \
  --data-urlencode "option.temperature=0.5"
```

Resposta (não-stream):

```text
A capoeira é uma arte marcial afro-brasileira que combina dança, luta e música, reconhecida como patrimônio da humanidade.
```

Resposta (stream): o mesmo texto chega em **chunks** (`text/plain; charset=utf-8`),
sem NDJSON e sem meta-dados.

### 5.5. `POST /api/chat`

Campos de form: `model`*, pares repetidos `role`/`content`, `tool_call_id` (para
`role=tool`), `tools` (contrato textual de tool calling), `stream`, `new_chat`,
`option.<chave>`.

```bash
curl -X POST http://127.0.0.1:8765/api/chat \
  -d model=gemini-pro \
  --data-urlencode "role=user" \
  --data-urlencode "content=Quem é Besouro Mangangá?" \
  -d new_chat=false
```

Resposta (não-stream):

```text
Viveu no fim do século XIX no recôncavo baiano, tornando-se uma das maiores lendas da capoeira.
```

> **Tool calling simulado (contrato textual):** quando o request traz `tools`, o
> gateway lista as definições no envelope de system prompt como **linhas únicas**
> (`[TOOL] chave=valor`, uma por ferramenta) e instrui o modelo Web a emitir **uma
> linha de texto puro por chamada** no formato `[TOOL_CALL] nome | chave=valor` —
> sem blocos de código e sem JSON. O gateway faz **scan por regex** em todo o texto
> (tolerante a prosa ao redor e a code fences), valida as linhas e devolve-as como
> `text/plain` (prosa removida). Múltiplas linhas viram chamadas paralelas. Se não
> houver linha válida, faz **fallback** devolvendo o texto (com as linhas
> `[TOOL_CALL]` removidas).
>
> **Sem `tools` no request**: `role=tool` → `400 Bad Request`.

Campos do request (`tools`, definições de ferramenta — **sem JSON**, um tool por linha):

```text
tools=name=shopping | desc=Consulta/prepara uma compra. | item:string | quantidade:int
```

Exemplo de resposta do modelo Web com tool call (1 linha por chamada; prosa opcional antes/depois):

```text
[TOOL_CALL] shopping | item=leite | quantidade=2
```

O agente executa a ferramenta e continua o ciclo enviando `role=tool` (+
`tool_call_id`) com o resultado:

```text
role=tool · content=Leite comprado · tool_call_id=call-1
```

### 5.6. `POST /api/show`

Campo: `model`. Retorna bloco `text/plain`:

```text
model: gemini-pro
display: Gemini Pro (Web)
provider: gemini
streaming: false
template: [INST] {{ .System }} [/INST]

{{ .Prompt }}
options: temperature=0.7; num_predict=2048
```

### 5.7. `POST /api/create` — registrar perfil

Campos: `model`*, `from` (provedor ou perfil base), `system`, `template`,
`parameter.<chave>` (ex.: `parameter.temperature=0.3`). Retorna `ok`.

### 5.8. `POST /api/copy` e `DELETE /api/delete`

- `/api/copy`: campos `source`, `destination`.
- `/api/delete`: campo `model`.

Ambos retornam `ok`.

### 5.9. Não aplicáveis (modelo não é hospedado)

| Endpoint | Status | Resposta (`text/plain`) |
|---|---|---|
| `POST /api/pull` | `501` | `pull não é suportado: modelos são acessados via Browser Bridge` |
| `POST /api/push` | `501` | `push não é suportado` |
| `POST /api/blobs/*` | `501` | `blobs não são usados` |
| `POST /api/embed` / `/api/embeddings` | `501` | `embeddings estão fora de escopo no CapoeiraHost` |

---

## 6. Bridge WebSocket (Servidor ↔ Extensão)

- O **CapoeiraHost escuta** em `ws://127.0.0.1:8766`; a **extensão conecta** como client.
- Mensagens JSON (protocolo nosso — mantido). Correlação via campo `id` (UUID).
- Validação de `Origin` (CSWSH): ausência de `Origin`, `chrome-extension://<id>`,
  páginas dos provedores e origens locais; demais → `403`.
- **Private Network Access (Chrome):** handshake envia
  `Access-Control-Allow-Private-Network: true` e `Access-Control-Allow-Origin` refletindo a origem de cada conexão.

### 6.1. Extensão → Servidor

**`HELLO`** (registro de capacidades):

```json
{
  "version": "1.0",
  "action": "HELLO",
  "id": "conn-uuid-1",
  "payload": {
    "provider": "gemini",
    "adapters": ["gemini"],
    "supportsStreaming": false,
    "supportsNewChat": true,
    "tabTitle": "Aba Gemini — Capoeira"
  }
}
```

**`RESPONSE`** (resposta final):

```json
{
  "version": "1.0",
  "action": "RESPONSE",
  "status": "SUCCESS",
  "id": "req-uuid-1234",
  "payload": {
    "rawResponse": "A capoeira é uma arte marcial afro-brasileira...",
    "executionTimeMs": 4250
  },
  "error": null
}
```

**`ERROR`** (falha de injeção / captura):

```json
{
  "version": "1.0",
  "action": "ERROR",
  "status": "ERROR",
  "id": "req-uuid-1234",
  "payload": null,
  "error": "Campo de input do Gemini não encontrado."
}
```

**`STREAM_UPDATE`** (incremental real — somente se `supportsStreaming: true`):

```json
{
  "version": "1.0",
  "action": "STREAM_UPDATE",
  "status": "STREAMING",
  "id": "req-uuid-1234",
  "payload": { "partial": "A capoeira é uma arte " }
}
```

O `STREAM_UPDATE` é capturado pelo `content.js` via **polling** (`setInterval` de
500ms); o `RESPONSE` final com o texto completo é a fonte da verdade.

### 6.2. Servidor → Extensão

**`SEND_PROMPT`** — payload simplificado (o contrato textual vive dentro de
`systemPrompt`/`prompt`; `conversation`, `options` e `expectFormat` foram removidos):

```json
{
  "version": "1.0",
  "action": "SEND_PROMPT",
  "id": "req-uuid-1234",
  "payload": {
    "provider": "gemini",
    "model": "gemini-pro",
    "newChat": true,
    "systemPrompt": "[SYSTEM] Você é um assistente útil. Responda de forma concisa\n[OPTIONS] temperature=0.7\n[TOOLS] ferramentas disponíveis (uma por linha, formato chave=valor, sem JSON):\n[TOOL] name=shopping | desc=... | item:string\n[MODE TOOL_CALLING] Se for necessário chamar uma ferramenta, emita EXATAMENTE uma linha por chamada neste formato: ...",
    "prompt": "[FullPrompt/transcript reduzido e montado pelo gateway]"
  }
}
```

### 6.3. Ciclo de vida da conexão

1. Extensão abre aba do provedor → `content.js` conecta `ws://127.0.0.1:8766` → `HELLO`.
2. Servidor marca `provider` como **disponível**. `/api/tags` lista todos; `/api/ps`
   somente os online. Provider offline → `/api/generate` e `/api/chat` retornam `503`.
3. Requisições HTTP por provider entram em fila FIFO (`CAPOEIRA_QUEUE`), uma geração por vez.
4. Ao concluir, servidor envia `RESPONSE`/`STREAM_UPDATE` correlacionado por `id` e
   finaliza a resposta HTTP (texto puro).
5. Desconexão: servidor marca provider offline; requisições em andamento → `504`;
   fila pendente → `503`. Extensão tenta reconexão com **exponential backoff**
   (base 1s → teto 30s).

---

## 7. Provider Adapters (Extensão MV3)

Mesmo padrão anterior; o adapter só tramita texto. Os adaptadores são registrados
via `window.registerAdapter(...)` e selecionados por `match()`.

| Adapter | Host | Observações |
|---|---|---|
| `gemini.js` | `gemini.google.com` | Default — implementado e **carregado** |
| `claude.js` | `claude.ai` | Implementado e **carregado**; `supportsStreaming: true` |
| `chatgpt.js` | `chatgpt.com` | Implementado, mas **não carregado** no `manifest.json` |
| `copilot365.js` | `m365.cloud.microsoft/chat` | Implementado e **carregado**; usa hook `isGenerating()` |

> ⚠️ `host_permissions` para `ws://127.0.0.1:8766/*` é **obrigatória** no MV3.
> Adaptadores implementados mas fora da lista do `manifest.json` não são injetados.

---

## 8. Montagem do Prompt (Gateway)

O gateway transforma a requisição (form) no prompt da Web:

1. **Envelope de sistema** = system prompt do perfil + instruções derivadas de
   `options` + contrato de tool calling (se houver `tools`):

   ```
   [SYSTEM]
   <system_prompt do perfil>
   [OPTIONS] temperature=0.7; num_predict=512; stop=STOP
   [TOOLS] ferramentas disponíveis (uma por linha, formato chave=valor, sem JSON):
   [TOOL] name=shopping | desc=... | item:string | quantidade:int
   [MODE TOOL_CALLING] Se for necessário chamar uma ferramenta, emita EXATAMENTE
   uma linha por chamada neste formato (sem blocos de código, sem JSON e sem
   marcação markdown):

   [TOOL_CALL] nome_da_ferramenta | chave1=valor1 | chave2=valor2
   ```

2. **Transcript de conversa** (para `/api/chat`): mensagens serializadas em turnos
   com tags de linha única (`[SYSTEM]`, `[USER]`, `[ASSISTANT]`, `[TOOL_CALL] nome |
   chave=valor`, `[TOOL_RESULT] (id) conteúdo`) dentro do `prompt`, precedidas do
   system. Por padrão, cada requisição inicia **novo chat na Web** (`new_chat=true`);
   quando `new_chat=false`, a extensão injeta no chat aberto.

3. **Templates**: se o perfil define `template`, aplicado sobre `system + prompt`.

> **Sem `format:"json"`:** não há mais `[MODE_JSON]`/`[JSON_START]`/`[JSON_END]` —
> essa saída estruturada foi removida nesta revisão.

---

## 9. Tratamento de Erros (text/plain)

Todos os erros HTTP retornam corpo **`text/plain`** com a mensagem:

| Caso | HTTP | Corpo (exemplo) |
|---|---|---|
| Campo obrigatório ausente / role inválido | `400` | `campo 'model' é obrigatório` |
| Modelo não registrado | `404` | `model 'x' not found` |
| Provider sem bridge conectado | `503` | `no bridge available for provider 'gemini'` |
| Fila cheia para o provider | `503` | `bridge queue full (10)` |
| Timeout de geração na Web | `504` | `bridge timeout after the configured window` |
| Extensão reportou `ERROR` | `502` | mensagem vinda da extensão |
| `role=tool` sem `tools` | `400` | `role 'tool' exige o campo 'tools' no request (tool calling simulado)` |
| `pull`/`push`/`blobs`/`embed` | `501` | ver §5.9 |

---

## 10. Requisitos Não-Funcionais (RNF)

- **RNF-01 (Segurança Local):** bind padrão `127.0.0.1`; WebSocket valida `Origin`
  por allowlist e responde headers de Private Network Access; CORS restrito a localhost.
- **RNF-02 (Resiliência):** reconexão da extensão com exponential backoff; estado
  offline propagado como `503`.
- **RNF-03 (Atomicidade):** cada requisição HTTP mapeia 1:1 para um ciclo
  `SEND_PROMPT → RESPONSE` com `id` correlacionado; por padrão novo chat por
  requisição (`new_chat=true`).
- **RNF-04 (Streaming):** `stream=true` responde **texto puro** em chunks (replay
  final ou `STREAM_UPDATE` incremental); nunca NDJSON.
- **RNF-05 (Latência):** `executionTimeMs` da resposta vira meta-dado interno; o corpo
  entregue ao cliente é somente texto.
- **RNF-06 (Texto puro):** **nenhum JSON** entre agente local, gateway e UI Web; a
  conversão de estrutura (quando existir) ocorre no gestor de tool calling, que
  trabalha com linhas `[TOOL_CALL] nome | chave=valor`.

---

## 11. Estrutura de Diretórios

```
capoeira-host/
├── server/
│   ├── __init__.py
│   ├── main.py               # entrypoint: inicia servidor HTTP + WS
│   ├── config.py             # env vars, binds, portas
│   ├── registry.py           # Model Registry (models.json, JSON de config)
│   ├── ollama_dto.py         # dataclasses mínimas de request (sem models JSON)
│   ├── http_api.py           # rotas textuais (form-urlencoded → text/plain)
│   ├── bridge.py             # WS server 8766, allowlist de Origin, headers PNA, correlação id
│   ├── gateway.py            # form → SEND_PROMPT → texto → RESPONSE/STREAM_UPDATE
│   ├── queue.py              # FIFO por provider + locks
│   ├── prompt_builder.py     # envelope de sistema, transcript, contrato textual
│   └── errors.py             # erros text/plain padronizados
├── extension/
│   ├── manifest.json         # Chrome MV3 (ws://127.0.0.1:8766/* + adaptadores ativos)
│   ├── content.js            # WS client + orquestrador + polling de streaming
│   └── adapters/
│       ├── base.js
│       ├── gemini.js
│       ├── claude.js
│       ├── chatgpt.js
│       └── copilot365.js
├── tests/
│   └── test_integration.py   # suíte de integração (form → text/plain + WS fake)
├── conftest.py
├── smoke_test.py             # smoke test da API (form → texto)
├── models.json               # registry de perfis (JSON de config)
├── requirements.txt          # fastapi · uvicorn · websockets · pydantic · python-multipart
├── requirements-dev.txt      # pytest · httpx
├── README.md
└── CAPOEIRA_HOST_SPEC.md     # este documento
```

---

## 12. Exemplos de Uso

```bash
# 1. Iniciar o gateway
export CAPOEIRA_HTTP_PORT=8765
export CAPOEIRA_WS_PORT=8766
python -m server.main

# 2. Carregar a extensão no Chrome e abrir uma aba autenticada do Gemini.

# 3. Testar — script cross-platform (README §Testar)
python smoke_test.py --endpoint generate --prompt "Explique o que é capoeira em 1 parágrafo."
python smoke_test.py --endpoint chat --stream
curl http://127.0.0.1:8765/api/version
```

> **Após alterar o código:** reinicie o servidor e, ao (re)carregar a extensão,
> **recarregue a aba do provedor** — o WebSocket do content script usa a origem da
> página, então a aba precisa estar ativa e logada para o bridge registrar o
> provider como online.

---

## 13. Roadmap

- **Fase 1 (Core):** HTTP+WS server, registry, `/api/generate` e `/api/chat`
  (não-stream) com adapter Gemini.
- **Fase 2 (Streaming):** streaming de texto puro + `STREAM_UPDATE` incremental no
  adapter Claude; timeouts e filas.
- **Fase 3 (Registry CRUD):** `/api/create`/`/api/copy`/`/api/delete`/`/api/show`;
  persistência `models.json`.
- **Fase 4 (Provedores):** adapters ChatGPT e Copilot 365; fallback `provider: auto`.

---

## 14. Fora de Escopo (explícito)

- Compatibilidade com a **API Ollama** (removida — API agora é textual).
- Saída estruturada `format:"json"`.
- CLI do usuário (agente de terminal).
- Redução de contexto (Tree-Sitter/AST, skeleton, grafos de dependência).
- Aplicador de diffs / escrita em arquivos locais.
- Embeddings, upload de imagens, pull/push de modelos. *(Tool calling é suportado
  de forma **simulada** via contrato textual — ver §5.5.)*
