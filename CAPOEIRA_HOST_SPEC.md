# CapoeiraHost — Gateway Local ⇄ LLM Web via Browser Bridge

**Projeto:** CapoeiraHost
**Versão:** `2.2.0`
**Status:** `Approved`
**Data:** 22 de Setembro de 2026
**Revisão:** 2.2 — CapoeiraHost passa a operar **somente em modo push**: as
respostas do LLM em `/api/generate` e `/api/chat` são entregues à API da
aplicação (registrada ou porta padrão) via `POST JSON` (§5.7–5.8). Remove o
long-poll `/api/chat/watch` e o buffer de eventos do watcher.

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
local → gateway → LLM Web usa **texto puro**: o CapoeiraHost é **pass-through**
verbatim e **não encapsula** o que recebe com tags próprias (`[SYSTEM]`,
`[OPTIONS]`, `[TOOLS]`, contrato `[TOOL_CALL]`, etc.). A formatação e o
encapsulamento do texto são responsabilidade **exclusiva** da ferramenta que
consome o host. O transcript de `/api/chat` mantém apenas os rótulos
`[USER]`/`[ASSISTANT]` para distinguir turnos.

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
5. **Entrega por push**: o chamador recebe `accepted` imediato; a resposta completa
   é entregue à API da aplicação registrada (ou à porta padrão) via `POST JSON`,
   nunca NDJSON, e `stream` é ignorado.
6. **Best-effort**: falha no push (porta fechada, erro, timeout) é logada e
   ignorada — nunca afeta o chamador.

---

## 2. Conceitos

| Conceito | Mapeamento no CapoeiraHost |
|---|---|
| Modelo | **Perfil de provedor** no registry (`models.json`) — p.ex. `gemini-pro`, `claude-sonnet` |
| Modelfile (TEMPLATE, SYSTEM, PARAMETER) | Campos do perfil: `provider`, `system_prompt`, `template`, `options` |
| Inferência | *Remota* na interface Web do provedor |
| `/api/generate` | Prompt único enviado à Web; retorna **ack** e entrega a resposta à aplicação via push |
| `/api/chat` | Conversa serializada como transcript textual e enviada como 1 prompt (novo chat por requisição; opcionalmente reutiliza via `new_chat=false`); retorna **ack** e entrega a resposta à aplicação via push |
| `stream=true` | Aceito mas **ignorado** — o host entrega o texto final completo no push (sem streaming ao chamador) |
| `options` | Aceitos no form, mas **ignorados** no prompt (o host não injeta mais `[OPTIONS]`; parâmetros de amostragem não são aplicáveis à Web) |
| Tool calling | Fora de escopo do host: **pass-through verbatim** — a ferramenta formata a instrução e interpreta a resposta por conta própria |
| `/api/chat/read` | Lê o transcript atual do chat ativo na aba Web (turnos `[USER]`/`[ASSISTANT]`) — síncrono |
| **Push de resposta** | Após o LLM retornar, o host faz `POST JSON` ao endpoint da aplicação registrada (ou à porta padrão) — ver §5.8 |

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
| `CAPOEIRA_APP_HOST` | `127.0.0.1` | Host da API da aplicação usada quando **nenhuma** aplicação está registrada |
| `CAPOEIRA_APP_PORT` | `8767` | Porta padrão da API da aplicação (fallback sem registro) |
| `CAPOEIRA_APP_PATH` | `/api/capoeira/response` | Path do endpoint de resposta que a aplicação deve implementar |
| `CAPOEIRA_APP_TIMEOUT` | `5` | Timeout (s) do push de resposta à aplicação |

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

### 5.4. `POST /api/generate` e `POST /api/chat` — modo push

> A partir da revisão 2.2, o CapoeiraHost opera **somente em modo push** para as
> rotas de geração. O chamador não recebe mais o texto da resposta no corpo HTTP
> — recebe um **ack** imediato, e o texto é entregue à API da aplicação (§5.8).

#### `/api/generate`

Campos de form: `model`*, `prompt`*, `system`, `template`, `stream`
(`true`/`false`), `new_chat` (`true`/`false`), `option.<chave>`.

```bash
curl -X POST http://127.0.0.1:8765/api/generate \
  -d model=gemini-pro \
  --data-urlencode "prompt=Explique o que é capoeira em 1 parágrafo."
```

Resposta imediata (`text/plain`):

```text
accepted: 8a2c1c55-7d1f-4a90-b3c2-0e1e8f2c4a1d
```

A geração roda em background; quando o LLM retorna, o texto é entregue **à
aplicação** via push (§5.8). `stream` é aceito mas **ignorado** — a resposta
completa vai no push.

#### `/api/chat`

Campos de form: `model`*, pares repetidos `role`/`content`, `stream`, `new_chat`,
`option.<chave>`.

```bash
curl -X POST http://127.0.0.1:8765/api/chat \
  -d model=gemini-pro \
  --data-urlencode "role=user" \
  --data-urlencode "content=Quem é Besouro Mangangá?" \
  -d new_chat=false
```

Resposta imediata: `accepted: <request_id>`, mesmo fluxo.

> **Pass-through verbatim:** o CapoeiraHost não adiciona tags próprias ao que
> recebe. O system prompt do perfil segue **verbatim** (sem `[SYSTEM]`,
> `[OPTIONS]`, `[TOOLS]`). As mensagens são serializadas no transcript apenas com
> os rótulos `[USER]`/`[ASSISTANT]` (role `system` entra como conteúdo puro), e a
> resposta da Web é **entregue à aplicação verbatim**, sem qualquer parse.
> Qualquer formatação/contrato é responsabilidade exclusiva da ferramenta que
> consome o host. `role` fora de `user|assistant|system` → `400`.

### 5.6. `POST /api/chat/read` — ler o chat ativo

Campo: `model`*. Delega um `READ_CHAT` para a extensão e devolve o transcript
atual da aba Web autenticada, um turno por linha:
`[USER]`/`[ASSISTANT]`. Provider offline → `503`; sem `supportsTranscript` → `501`.

```bash
curl -X POST http://127.0.0.1:8765/api/chat/read -d model=gemini-pro
```

```text
[USER] Quem foi Besouro Mangangá?
[ASSISTANT] Viveu no fim do século XIX no recôncavo baiano.
```

Resposta inclui header `X-Capoeira-Revision` com a última revision conhecida do watcher.

### 5.7. Registro da aplicação (`/api/app/*`)

A aplicação que consome o CapoeiraHost pode **se registrar** informando a porta
da **sua própria API REST** — o endpoint dessa API será **consumido pelo
CapoeiraHost quando o LLM retornar**. É **slot único**: enquanto houver uma
aplicação registrada, o host entrega a resposta **somente a ela**, até que o
`unregister` a descadastre.

- `GET /api/app` → `name=... | host=... | port=...` ou `no app registered`.
- `POST /api/app/register` — form `port`* (int 1–65535), `host` (default
  `127.0.0.1`), `name` (opcional). Retorna `ok host=... port=...`.
- `POST /api/app/unregister` — limpa o slot; o destino volta à porta padrão.

```bash
curl -X POST http://127.0.0.1:8765/api/app/register \
  -d port=8123 --data-urlencode "name=minha-app"
```

**Caso nenhuma aplicação se registre**, o host **tenta** consumir a API da
aplicação destino na **porta padrão** (`CAPOEIRA_APP_HOST`/`CAPOEIRA_APP_PORT`,
default `127.0.0.1:8767`) — sempre **best-effort**: sem retry, falha só loga.

### 5.8. Push de resposta (contrato da aplicação)

A aplicação deve implementar **`POST <path>`** (`CAPOEIRA_APP_PATH`, default
`/api/capoeira/response`), aceitando `application/json`:

```json
{
  "request_id": "8a2c1c55-7d1f-4a90-b3c2-0e1e8f2c4a1d",
  "model": "gemini-pro",
  "provider": "gemini",
  "endpoint": "chat",
  "stream": false,
  "text": "A capoeira é uma arte marcial afro-brasileira...",
  "timestamp": "2026-09-22T12:00:00+00:00"
}
```

- `text` é a resposta do LLM **verbatim** (§8). Em falha de geração (timeout,
  error da extensão, fila cheia), `text` vem vazio e um campo `"error"` traz a
  mensagem.
- O push é **um disparo** (1 tentativa) com timeout `CAPOEIRA_APP_TIMEOUT`;
  resposta `2xx` = ack. Falha de envio (porta fechada, erro HTTP, timeout) é
  registrada em log e **ignorada** — nunca afeta quem chamou `generate`/`chat`.
- A aplicação envia comandos ao LLM consumindo a API padrão (`/api/generate`,
  `/api/chat`) e recebe os resultados de volta neste endpoint.

### 5.9. `POST /api/show`

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

### 5.10. `POST /api/create` — registrar perfil

Campos: `model`*, `from` (provedor ou perfil base), `system`, `template`,
`parameter.<chave>` (ex.: `parameter.temperature=0.3`). Retorna `ok`.

### 5.11. `POST /api/copy` e `DELETE /api/delete`

- `/api/copy`: campos `source`, `destination`.
- `/api/delete`: campo `model`.

Ambos retornam `ok`.

### 5.12. Não aplicáveis (modelo não é hospedado)

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
    "supportsTranscript": true,
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

**`CHAT_UPDATE`** (watcher de DOM — mudança detectada no chat ativo):

```json
{
  "version": "1.0",
  "action": "CHAT_UPDATE",
  "id": "watch-uuid-1",
  "payload": {
    "provider": "gemini",
    "revision": 7,
    "transcript": [
      { "role": "user", "content": "Quem foi Besouro Mangangá?" },
      { "role": "assistant", "content": "Viveu no fim do século XIX..." }
    ],
    "messages": [{ "role": "user", "content": "Quem foi Besouro Mangangá?" }]
  }
}
```

O `content.js` roda um watcher (`setInterval`) que compara o digest do
`extractTranscript()` do adaptador; quando muda e **não** há `SEND_PROMPT` do
próprio agente em andamento, envia `CHAT_UPDATE` com `revision` incremental e o
**delta** (`messages`). Enquanto o agente gera (`SEND_PROMPT` em flight), o
watcher apenas ressincroniza o baseline — sem eco. `revision` nunca reseta (é
monotônico, mesmo em chat novo).

> Na revisão 2.2, o `CHAT_UPDATE`/watcher alimenta **apenas** o header
> `X-Capoeira-Revision` de `POST /api/chat/read` — não há mais long-poll
> (`/api/chat/watch` foi removido).

### 6.2. Servidor → Extensão

**`READ_CHAT`** — pedido on-demand do transcript atual (resposta via `RESPONSE`
correlacionado por `id`, payload `{ "transcript": [...] }`):

```json
{
  "version": "1.0",
  "action": "READ_CHAT",
  "id": "read-uuid-1",
  "payload": { "provider": "gemini" }
}
```

**`SEND_PROMPT`** — payload simplificado (o texto é **verbatim**: o `systemPrompt`
é o system do perfil sem tags e `prompt` é o transcript montado pelo gateway;
`tools`/`options`/`conversation`/`expectFormat` foram removidos):

```json
{
  "version": "1.0",
  "action": "SEND_PROMPT",
  "id": "req-uuid-1234",
  "payload": {
    "provider": "gemini",
    "model": "gemini-pro",
    "newChat": true,
    "systemPrompt": "Você é um assistente útil. Responda de forma concisa e direta.",
    "prompt": "[USER] O que é capoeira?"

  }
}
```

### 6.3. Ciclo de vida da conexão

1. Extensão abre aba do provedor → `content.js` conecta `ws://127.0.0.1:8766` → `HELLO`.
2. Servidor marca `provider` como **disponível**. `/api/tags` lista todos; `/api/ps`
   somente os online. Provider offline → `/api/generate` e `/api/chat` retornam `503`
   (síncrono, antes do ack).
3. Requisições HTTP por provider entram em fila FIFO (`CAPOEIRA_QUEUE`), uma geração por vez.
4. Ao concluir, servidor recebe `RESPONSE`/`STREAM_UPDATE` correlacionado por `id`, monta o
   texto completo e **entrega à aplicação via push** (§5.8). O chamador da requisição já recebeu
   `accepted: <request_id>` no passo 2.
5. Desconexão: servidor marca provider offline; gerações em background falham e são reportadas
   à aplicação (`error` no push). Extensão tenta reconexão com **exponential backoff**
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

Todos os adaptadores carregados declaram `supportsTranscript: true` e
implementam `extractTranscript()` (via `transcriptSelectors` no base). A
extração é **best-effort** — os seletores de mensagens de **usuário** são novos
e mais sujeitos a mudanças no DOM dos provedores (especialmente Gemini e Copilot
365); utilize-os para validar manualmente em cada página real.

---

## 8. Montagem do Prompt (Gateway)

O gateway transforma a requisição (form) no prompt da Web de forma **pass-through
verbatim** — não adiciona tags nem contratos próprios:

1. **System prompt** = system do perfil **verbatim**, sem `[SYSTEM]`/`[OPTIONS]`/
   `[TOOLS]`/`[MODE TOOL_CALLING]`. A formatação é responsabilidade exclusiva da
   ferramenta que consome o host.
2. **Transcript de conversa** (para `/api/chat`): mensagens serializadas em turnos
   apenas com os rótulos `[USER]`/`[ASSISTANT]` (role `system` entra como conteúdo
   puro) dentro do `prompt`, precedidas do system. Por padrão, cada requisição
   inicia **novo chat na Web** (`new_chat=true`); quando `new_chat=false`, a
   extensão injeta o prompt no chat aberto e emite o system apenas na **primeira
   interação da sessão** — nas iterações seguintes envia só o transcript até que um
   `new_chat=true` reinicie a sessão.
3. **Templates**: se o perfil define `template`, aplicado sobre `system + prompt`.

4. **Resposta**: o texto da Web é **entregue à aplicação verbatim** (via push,
   §5.8), sem parse de `[TOOL_CALL]` ou qualquer transformação.

> **Sem `format:"json"`:** não há mais `[MODE_JSON]`/`[JSON_START]`/`[JSON_END]` —
> essa saída estruturada foi removida nesta revisão.

---

## 9. Tratamento de Erros (text/plain)

Todos os erros **síncronos** de validação retornam corpo **`text/plain`** com a
mensagem:

| Caso | HTTP | Corpo (exemplo) |
|---|---|---|
| Campo obrigatório ausente / role inválido | `400` | `campo 'model' é obrigatório` |
| Modelo não registrado | `404` | `model 'x' not found` |
| Provider sem bridge conectado | `503` | `no bridge available for provider 'gemini'` |
| `pull`/`push`/`blobs`/`embed` | `501` | ver §5.12 |
| Provider sem `supportsTranscript` | `501` | `no transcript support for provider '...'` |

Erros de **geração em background** (fila cheia, timeout, error da extensão) **não**
aparecem para o chamador (que já recebeu o `accepted`): são **entregues à
aplicação** no payload do push com `"text": ""` e o campo `"error"` (§5.8).

---

## 10. Requisitos Não-Funcionais (RNF)

- **RNF-01 (Segurança Local):** bind padrão `127.0.0.1`; WebSocket valida `Origin`
  por allowlist e responde headers de Private Network Access; CORS restrito a localhost.
- **RNF-02 (Resiliência):** reconexão da extensão com exponential backoff; estado
  offline propagado como `503`.
- **RNF-03 (Atomicidade):** cada requisição HTTP mapeia 1:1 para um ciclo
  `SEND_PROMPT → RESPONSE` com `id` correlacionado; por padrão novo chat por
  requisição (`new_chat=true`).
- **RNF-04 (Streaming):** `stream=true` é aceito mas **ignorado** no modo push —
  a resposta completa é entregue à aplicação no payload do push; nunca NDJSON.
- **RNF-05 (Latência):** `executionTimeMs` da resposta vira meta-dado interno; o
  chamador recebe apenas o ack e o texto via push.
- **RNF-06 (Texto puro):** **nenhum JSON** entre agente local, gateway e UI Web;
  o texto trafega **verbatim** (pass-through), sem tags nem contrato adicionados
  pelo host. JSON permanece apenas no WebSocket bridge (host⇄extensão) e no
  **contrato de push host⇄aplicação** (§5.8).
- **RNF-07 (Push best-effort):** a entrega à aplicação é um disparo com timeout;
  falha (porta fechada, erro HTTP, timeout) é logada e **ignorada** — nunca
  afeta o chamador e nunca faz retry. Registro da aplicação é slot único e
  exclusivo até `unregister`.

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
│   ├── http_api.py           # rotas textuais (form-urlencoded → text/plain + /api/app)
│   ├── bridge.py             # WS server 8766, allowlist de Origin, headers PNA, correlação id
│   ├── gateway.py            # form → SEND_PROMPT → texto → RESPONSE/STREAM_UPDATE
│   ├── watcher.py            # ChatWatcher: ingest de CHAT_UPDATE, revision por provider
│   ├── app_client.py         # AppRegistrar (slot único) + AppClient (push JSON §5.8)
│   ├── queue.py              # FIFO por provider + locks
│   ├── prompt_builder.py     # system verbatim, transcript, template
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
│   ├── test_integration.py   # suíte de integração (push e2e + WS fake)
│   └── test_app_registry.py  # registro da aplicação + contrato de push
├── conftest.py
├── smoke_test.py             # smoke test da API (form → ack)
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

# 3. (opcional) Registrar a aplicação que receberá as respostas
curl -X POST http://127.0.0.1:8765/api/app/register -d port=8123

# 4. Testar — script cross-platform (README §Testar)
python smoke_test.py --endpoint generate --prompt "Explique o que é capoeira em 1 parágrafo."
python smoke_test.py --endpoint chat
curl http://127.0.0.1:8765/api/version
```

> O `generate`/`chat` respondem `accepted: <request_id>`; a resposta do LLM chega
> via `POST JSON` na API da aplicação (`/api/capoeira/response`), ou na porta
> padrão `CAPOEIRA_APP_PORT` se nenhuma aplicação estiver registrada.

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
- Embeddings, upload de imagens, pull/push de modelos. *(Tool calling nativo não é
  suportado pelo host; o texto é pass-through verbatim — ver §5.4.)*
