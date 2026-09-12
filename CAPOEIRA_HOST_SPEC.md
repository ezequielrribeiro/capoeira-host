# CapoeiraHost — Gateway Ollama-compatível via Browser Bridge

**Projeto:** CapoeiraHost
**Versão:** `1.0.0`
**Status:** `Approved`
**Data:** 03 de Setembro de 2026
**Revisão:** 1.1.0 — 05/09/2026 — alinhamento com README e código (bridge: allowlist de `Origin` do content script + headers de Private Network Access; status real dos adaptadores; `/api/tags`; estrutura/scripts auxiliares)
**Base:** [doc-base.pdf](doc-base.pdf) (Spec CapoeiraCode — Gemini)

---

## 1. Visão Geral e Objetivos

### 1.1. Propósito

O **CapoeiraHost** é um serviço local que emula o **servidor Ollama** (`ollama serve`), expondo uma **API REST compatível com o Ollama** na porta **8765**, para que qualquer aplicação que já fale com o Ollama (Open WebUI, Continue, LibreChat, scripts, etc.) possa utilizá-la sem alterações.

A diferença fundamental: em vez de **hospedar e executar modelos localmente**, o CapoeiraHost **roteia as inferências para LLMs Web** (Gemini, Claude, Microsoft 365 Copilot, ChatGPT) por meio de um **Browser Extension Bridge** (WebSocket local + injeção no DOM).

```
┌──────────────────────────────────────────────────────────────────────┐
│                       OUTRAS APLICAÇÕES                               │
│   Open WebUI · Continue · LibreChat · Scripts curl · SDKs Ollama     │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ HTTP REST (Ollama-compatível)
                                 │ 127.0.0.1:8765
┌────────────────────────────────▼─────────────────────────────────────┐
│                        CAPOEIRAHOST (SERVER)                          │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │ HTTP API     │──▶│ Gateway /    │──▶│ Model Registry (perfis)  │  │
│  │ (Ollama API) │   │ Scheduler    │   └──────────────────────────┘  │
│  └──────────────┘   └──────┬───────┘                                  │
│  ┌─────────────────────────▼──────────────────────────────────────┐  │
│  │ Bridge WS Server (WS) — ws://127.0.0.1:8766                    │  │
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
                                                        │ DOM (injeção)
┌───────────────────────────────────────────────────────▼───────────────┐
│                       WEB LLM INTERFACE (navegador)                   │
└───────────────────────────────────────────────────────────────────────┘
```

### 1.2. Problema de Negócio

Muitos usuários possuem acesso a LLMs **apenas pela interface Web** (login/sessão no navegador), sem API paga disponível. Ferramentas nativas do ecossistema Ollama (servidores de UI, agentes, scripts) dependem de uma API local. O CapoeiraHost oferece essa API, reaproveitando a sessão autenticada do navegador.

### 1.3. Escopo — O QUE FOI DISPENSADO

Por decisão de design, o CapoeiraHost **NÃO inclui** as seguintes partes do CapoeiraCode original:

- ❌ **CLI** (`cli/main.py`, Click/Typer, comandos `refactor`, etc.)
- ❌ **Processamento e pré-processamento de código**: Context Engine, AST/Tree-Sitter, Structural Reducer, LanguageAdapters (`extract_skeleton`, `extract_dependencies`)
- ❌ **Diff Applier** (aplicação atômica de alterações em arquivos locais)
- ❌ Extração de dependências, grafos, treesitter queries para PHP/JS/HTML/CSS

O CapoeiraHost é **apenas um gateway de inferência**: recebe comandos Ollama e responde com texto.

### 1.4. Objetivos de Design

1. **Compatibilidade Ollama first**: endpoints, payloads, streaming NDJSON e erros no mesmo formato do `ollama serve`.
2. **Agnóstico a LLMs Web**: provedores plugáveis via adaptadores na extensão.
3. **Atomicidade de requisição**: 1 requisição HTTP = 1 ciclo de geração na Web (novo chat por requisição por default), sem estado compartilhado indevido.
4. **Segurança local**: loopback por padrão, validação de `Origin` no WebSocket, CORS restrito a localhost.
5. **Streaming honesto**: emissão via NDJSON com dois níveis (replay em chunks ou streaming incremental real via DOM).

---

## 2. Conceitos: Ollama ↔ CapoeiraHost

| Conceito Ollama | Mapeamento no CapoeiraHost |
|---|---|
| Modelo hospedado (`ollama pull llama3`) | **Perfil de provedor** registrado no registry (`models.json`) — p.ex. `gemini-pro`, `claude-sonnet` |
| Modelfile (TEMPLATE, SYSTEM, PARAMETER) | Campos do perfil: `provider`, `system_prompt`, `template`, `options` (defaults) |
| Inferência local (GGUF, GPU) | Inferência *remota* na interface Web do provedor |
| `/api/pull`, `/api/push`, blobs | **Não aplicável** → `501 Not Implemented` (modelos não são baixados) |
| `/api/generate` | Prompt único enviado à Web (transcript simples) |
| `/api/chat` | Conversa serializada como transcript e enviada como 1 prompt (novo chat na Web por requisição; opcionalmente reutiliza o chat aberto via `new_chat:false`) |
| `stream: true` (NDJSON) | Streaming emitido em NDJSON (replay de chunks do passo final OU `STREAM_UPDATE` incremental se o adaptador suportar) |
| `options` (temperature, num_predict, stop…) | Traduzidos em instruções injetadas no envelope de system prompt (a Web não expõe esses controles) |
| `/api/embed` | **Fora de escopo** → `501 Not Implemented` (sem embeddings via Web) |

---

## 3. Portas, Bind e Configuração

| Variável | Default | Descrição |
|---|---|---|
| `CAPOEIRA_HOST` | `127.0.0.1` | Interface de bind do serviço (manter loopback por segurança) |
| `CAPOEIRA_HTTP_PORT` | `8765` | **API REST compatível com Ollama** (como `OLLAMA_HOST`) |
| `CAPOEIRA_WS_PORT` | `8766` | Bridge WebSocket para a extensão (movido da 8765 da spec-base, liberada à API HTTP) |
| `CAPOEIRA_MODELS_FILE` | `./models.json` | Arquivo de registry de perfis |
| `CAPOEIRA_TIMEOUT` | `180` | Timeout (s) por requisição antes de `504` |
| `CAPOEIRA_QUEUE` | `10` | Máximo de requisições enfileiradas por provedor |
| `CAPOEIRA_NEW_CHAT` | `true` | Inicia um chat novo na aba Web a cada requisição (padrão). `false` reutiliza o chat aberto; pode ser sobrescrito por requisição via `new_chat` no body de `/api/generate` e `/api/chat` |

> **Mudança vs. doc-base**: na spec original o WebSocket ocupava a 8765. Aqui a **8765 passa a ser da API HTTP** (acesso das demais aplicações) e o **bridge WebSocket vai para a 8766** (só a extensão). Ambos restritos a `127.0.0.1`.

---

## 4. Model Registry (Perfis de Provedor)

Um "modelo" no CapoeiraHost é um **perfil** que aponta para um provedor Web + instruções de sistema + defaults. Não há pesos de modelo.

### 4.1. `models.json`

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
    },
    {
      "name": "claude-sonnet",
      "display_name": "Claude Sonnet (Web)",
      "provider": "claude",
      "system_prompt": "Você é um assistente de IA. Responda com clareza.",
      "template": "{{ .System }}\n\n{{ .Prompt }}",
      "streaming": true,
      "options": { "temperature": 1.0, "num_predict": 4096 },
      "created_at": "2026-09-03T00:00:00Z"
    },
    {
      "name": "copilot-365",
      "display_name": "Microsoft 365 Copilot (Web)",
      "provider": "copilot365",
      "system_prompt": "Assistente profissional da Microsoft 365.",
      "streaming": false,
      "options": {},
      "created_at": "2026-09-03T00:00:00Z"
    },
    {
      "name": "chatgpt",
      "display_name": "ChatGPT (Web)",
      "provider": "chatgpt",
      "system_prompt": "Você é ChatGPT, um assistente útil.",
      "streaming": false,
      "options": {},
      "created_at": "2026-09-03T00:00:00Z"
    }
  ]
}
```

### 4.2. Esquema de perfil

```jsonc
{
  "name": "string",                 // obrigatório, único
  "provider": "string",             // obrigatório: gemini | claude | copilot365 | chatgpt | auto
  "display_name": "string",
  "system_prompt": "string",
  "template": "string",             // molda prompt (Go template estilo Ollama)
  "streaming": "boolean",           // se o adaptador suporta incremento real
  "options": { "temperature": 0.7, "num_predict": 2048, "num_ctx": 32768, "top_p": 1, "top_k": 40, "stop": ["</s>"], "seed": null },
  "created_at": "RFC3339"
}
```

---

## 5. API HTTP (compatível com Ollama)

Base URL: `http://127.0.0.1:8765`

### 5.1. `GET /` e `GET /api/version`

```json
{ "version": "1.0.0" }
```

### 5.2. `GET /api/tags` — listar perfis

Lista **todos** os perfis do registry (com ou sem bridge conectado). Para ver apenas
os providers online, use `GET /api/ps`.

```json
{
  "models": [
    {
      "name": "gemini-pro",
      "model": "gemini-pro",
      "modified_at": "2026-09-03T00:00:00Z",
      "size": 1,
      "digest": "sha256:0000000000000000000000000000000000000000000000000000",
      "details": {
        "parent_model": "",
        "format": "web",
        "family": "gemini",
        "families": ["gemini"],
        "parameter_size": "web",
        "quantization_level": "web"
      }
    }
  ]
}
```

### 5.3. `GET /api/ps` — estado atual

```json
{
  "models": [
    {
      "name": "gemini-pro",
      "model": "gemini-pro",
      "size": 0,
      "digest": "sha256:0000",
      "details": { "family": "gemini", "format": "web", "parameter_size": "web", "quantization_level": "web" },
      "expires_at": "0001-01-01T00:00:00Z",
      "size_vram": 0,
      "status": "idle" | "generating"
    }
  ]
}
```

### 5.4. `POST /api/generate`

Request (compatível com Ollama):

```jsonc
{
  "model": "gemini-pro",
  "prompt": "Explique o que é capoeira em 1 parágrafo.",
  "system": "Responda em português.",           // opcional (overrides do perfil)
  "template": null,                              // opcional
  "stream": true,                                // opcional
  "format": "json",                              // opcional ("json" ativa o contrato [JSON_START]...[JSON_END] + extração)
  "raw": false,                                  // opcional (ignorado)
  "new_chat": false,                             // opcional (override de CAPOEIRA_NEW_CHAT para esta requisição)
  "images": null,                                // NÃO suportado → 400
  "keep_alive": 0,                               // opcional (aceito, ignorado)
  "options": { "temperature": 0.5, "num_predict": 512 }
}
```

Resposta **não-stream**:

```json
{
  "model": "gemini-pro",
  "created_at": "2026-09-03T12:00:00.000000Z",
  "response": "A capoeira é uma arte marcial afro-brasileira...",
  "done": true,
  "done_reason": "stop",
  "context": [],
  "total_duration": 4250000000,
  "load_duration": 0,
  "prompt_eval_count": 42,
  "prompt_eval_duration": 0,
  "eval_count": 178,
  "eval_duration": 4250000000
}
```

Resposta **stream** (NDJSON, uma linha por objeto):

```json
{"model":"gemini-pro","created_at":"...","response":"A capoeira ","done":false}
{"model":"gemini-pro","created_at":"...","response":"é uma arte mo","done":false}
{"model":"gemini-pro","created_at":"...","response":"","done":true,"done_reason":"stop","total_duration":4250000000,"eval_count":178}
```

### 5.5. `POST /api/chat`

Request:

```jsonc
{
  "model": "gemini-pro",
  "messages": [
    { "role": "system", "content": "Seja curto." },
    { "role": "user", "content": "Quem é Besouro Mangangá?" },
    { "role": "assistant", "content": "Besouro Mangangá foi um lendário mestre... " },
    { "role": "user", "content": "E em que ano ele viveu?" }
  ],
  "stream": true,
  "format": null,
  "new_chat": false,                             // opcional (override de CAPOEIRA_NEW_CHAT para esta requisição)
  "options": { "temperature": 0.7 }
}
```

Resposta **não-stream**:

```json
{
  "model": "gemini-pro",
  "created_at": "2026-09-03T12:00:00.000000Z",
  "message": { "role": "assistant", "content": "Viveu no fim do século XIX..." },
  "done": true,
  "done_reason": "stop",
  "total_duration": 5800000000,
  "eval_count": 96,
  "eval_duration": 5800000000
}
```

Resposta **stream** (NDJSON):

```json
{"model":"gemini-pro","created_at":"...","message":{"role":"assistant","content":"Viveu "},"done":false}
{"model":"gemini-pro","created_at":"...","message":{"role":"assistant","content":"no fim do século XIX..."},"done":false}
{"model":"gemini-pro","created_at":"...","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop","total_duration":5800000000,"eval_count":96}
```

> **Tool calling simulado**: quando o request traz `tools`, o CapoeiraHost lista as
> definições no envelope de system prompt como **linhas únicas** (`[TOOLS]` header + uma
> linha `[TOOL] {"name":..., "parameters":...}` por ferramenta — sem blob JSON) e instrui
> o modelo Web a emitir **uma linha de texto puro por chamada** no formato
> `[TOOL_CALL] nome {"args": ...}` — sem blocos de código/markdown. O gateway faz **scan
> por regex** em todo o texto (tolerante a prosa ao redor), converte cada linha em
> `message.tool_calls` no formato Ollama (`[{"function": {"name", "arguments"}}]`), e
> múltiplas linhas viram chamadas **paralelas**. Se não houver linha válida, faz
> **fallback** devolvendo o texto (com as linhas `[TOOL_CALL]` removidas) em
> `message.content`. Nesse modo também são aceitos assistant com `tool_calls` e mensagens
> `role: "tool"` (`tool_call_id` + `content`) para continuar o ciclo.
>
> **Sem `tools` no request**: `role: "tool"` ou `tool_calls` → `400 Bad Request`
> (`{"error":"tool calls / role 'tool' exigem a lista 'tools' no request (tool calling simulado)"}`).

Exemplo de resposta do modelo Web com tool call (1 linha por chamada; prosa opcional antes/depois):

```text
[TOOL_CALL] shopping {"item": "leite", "quantidade": 2}
```

> **Saída `format: "json"`**: em `/api/generate` e `/api/chat` com `format: "json"`
> (sem `tools`), o envelope injeta `[MODE_JSON]` e o modelo é orientado a devolver o JSON
> entre `[JSON_START]` e `[JSON_END]`. O gateway **extrai** o bloco com parsing tolerante a
> prosa/code fences (validado por `json.loads`); sem marcadores válidos, faz fallback
> devolvendo o texto cru (com as linhas de marcador removidas).

### 5.6. `POST /api/show`

```json
{
  "model": "gemini-pro",
  "license": "Termos de uso do provedor Web",
  "modelfile": "# CapoeiraHost profile\ndisplay: Gemini Pro (Web)\nprovider: gemini\nSYSTEM: \"Você é um assistente útil...\"",
  "parameters": { "temperature": "0.7", "num_predict": "2048" },
  "template": "[INST] {{ .System }} [/INST]\n\n{{ .Prompt }}",
  "details": { "format": "web", "family": "gemini", "parameter_size": "web", "quantization_level": "web" },
  "messages": []
}
```

### 5.7. `POST /api/create` — registrar perfil

Adaptação: cria/atualiza um perfil no registry (não baixa pesos).

```jsonc
{
  "model": "gemini-pro",
  "from": "gemini",                     // provedor base (ou nome de perfil existente)
  "system": "Novo system prompt.",
  "template": "custom template",
  "parameters": { "temperature": "0.3", "stop": ["STOP"] },
  "stream": false
}
```

### 5.8. `POST /api/copy` e `DELETE /api/delete`

```jsonc
{ "source": "gemini-pro", "destination": "gemini-pro-v2" }
```
```jsonc
{ "model": "gemini-pro" }
```

### 5.9. Não aplicáveis (modelo não é hospedado)

| Endpoint | Status | Resposta |
|---|---|---|
| `POST /api/pull` | `501` | `{"error":"pull não é suportado: modelos são acessados via Browser Bridge"}` |
| `POST /api/push` | `501` | `{"error":"push não é suportado"}` |
| `POST /api/blobs/*` | `501` | `{"error":"blobs não são usados"}` |
| `POST /api/embed` / `POST /api/embeddings` | `501` | `{"error":"embeddings estão fora de escopo no CapoeiraHost"}` |

---

## 6. Bridge WebSocket (Servidor ↔ Extensão)

- O **CapoeiraHost escuta** em `ws://127.0.0.1:8766`; a **extensão conecta** como client.
- Mensagens JSON. Correlação de requisições via campo `id` (UUID).
- Validação de `Origin` (CSWSH): o content script abre o WebSocket com a **origem da página**
  (ex.: `https://gemini.google.com`), não com `chrome-extension://`. A allowlist aceita:
  ausência de `Origin` (testes locais/SPA), `chrome-extension://<id>`, as páginas dos provedores
  (`https://gemini.google.com`, `https://claude.ai`, `https://chatgpt.com`,
  `https://m365.cloud.microsoft`) e origens locais (`http(s)://localhost[:porta]` /
  `127.0.0.1[:porta]`). Origens desconhecidas → handshake rejeitado (`403`).
- **Private Network Access (Chrome):** uma página pública conectando à rede privada
  (`ws://127.0.0.1:8766`) exige no handshake `Access-Control-Allow-Private-Network: true` —
  o bridge envia esse header (via `process_response`), além de `Access-Control-Allow-Origin`
  refletindo a origem de cada conexão. Sem isso o Chrome bloqueia o WebSocket antes do HELLO.

### 6.1. Extensão → Servidor

**`HELLO`** (registro de capacidades ao conectar):

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

**`RESPONSE`** (resposta final — equivalente ao da spec-base):

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

**`STREAM_UPDATE`** (incremental real — somente se `supportsStreaming: true` no HELLO):

```json
{
  "version": "1.0",
  "action": "STREAM_UPDATE",
  "status": "STREAMING",
  "id": "req-uuid-1234",
  "payload": { "partial": "A capoeira é uma arte " }
}
```

O `STREAM_UPDATE` é capturado pelo `content.js` via **polling** (`setInterval` de 500ms):
enquanto o indicador de geração ("Parar") estiver presente, ele difere
`extractLastResponse()` para produzir o delta de texto. Após a conclusão (indicador
sumir + settle de 400ms), sempre chega um `RESPONSE` final com o texto completo
(fonte da verdade).

### 6.2. Servidor → Extensão

**`SEND_PROMPT`** (evolução direta da spec-base):

```json
{
  "version": "1.0",
  "action": "SEND_PROMPT",
  "id": "req-uuid-1234",
  "payload": {
    "provider": "gemini",
    "model": "gemini-pro",
    "newChat": true,
    "systemPrompt": "Você é um assistente útil. Responda de forma concisa\n[Options] temperature=0.7; num_predict=2048; stop=[STOP]\n[JSON MODE] Responda APENAS com JSON válido.",
    "prompt": "[FullPrompt reduzido/montado pelo gateway]",
    "conversation": [ { "role": "user", "content": "..." } ],
    "options": { "temperature": 0.7, "num_predict": 512, "format": "json" }
  }
}
```

### 6.3. Ciclo de vida da conexão

1. Extensão abre aba do provedor → `content.js` conecta `ws://127.0.0.1:8766` → envia `HELLO`.
2. Servidor marca `provider` como **disponível**. `GET /api/tags` lista **todos** os perfis do
   registry (online ou não); `GET /api/ps` lista somente os de providers com bridge conectado.
   Com provider offline, `/api/generate` e `/api/chat` retornam `503`.
3. Requisições HTTP por provider entram em fila FIFO (`CAPOEIRA_QUEUE`), uma geração por vez (a Web processa 1 turno por vez).
4. Ao concluir, servidor envia `RESPONSE`/`STREAM_UPDATE` correlacionado por `id` e finaliza a resposta HTTP. O `id` é reutilizado do payload HTTP interno (novo UUID por requisição).
5. Desconexão: servidor marca provider offline; requisições em andamento → `504`; fila pendente → `503`. Extensão tenta reconexão com **exponential backoff** (base 1s → teto 30s), sem travar a UI.

---

## 7. Provider Adapters (Extensão MV3)

Mesmo padrão da spec-base, agora orientado a **conversa** e **streaming opcional**.

### 7.1. Interface

Adaptadores são registrados via `window.registerAdapter(...)` (base.js) e a seleção é feita
pelo `match()` (o primeiro adaptador compatível). Streaming incremental é entregue pelo
`content.js` (polling — ver 6.1); o adaptador só informa `supportsStreaming`.

```js
window.registerAdapter({
  name: "gemini",                      // 'gemini' | 'claude' | 'copilot365' | 'chatgpt'
  match: () => window.location.hostname.includes("gemini.google.com"),
  supportsStreaming: false,
  supportsNewChat: true,

  getSelectors: () => ({
    inputArea: '',                     // campo de texto
    sendButton: '',                    // botão de envio
    stopGeneratingIndicator: '',       // botão "Parar" (indica geração em curso)
    responseBlock: '',                 // nó das respostas do modelo
    newChatButton: ''                  // botão "Nova conversa" (quando newChat:true)
  }),

  async startNewChat() {},             // clica em "Nova conversa" (limpa histórico)
  async injectText(text) {},           // preenche e envia
  async extractLastResponse() {}       // lê a última resposta completa
});
```

> **Hook opcional `isGenerating()`**: quando o provedor não expõe um seletor CSS
> estável para o indicador de geração (ex.: um único botão que alterna entre os
> ícones de enviar/parar, como no Copilot 365), o adapter pode fornecer
> `async isGenerating() -> boolean`. O `content.js` prefere esse hook ao seletor
> `stopGeneratingIndicator` quando presente (retrocompatível com os demais adapters).

### 7.2. Provedores suportados (inicial)

| Adapter | Host | Observações |
|---|---|---|
| `gemini.js` | `gemini.google.com` | Default do projeto — implementado e **carregado** no `manifest.json` |
| `claude.js` | `claude.ai` | Implementado e **carregado**; `supportsStreaming: true` (delta incremental via polling) |
| `chatgpt.js` | `chatgpt.com` | Implementado, mas **não carregado** no `manifest.json` (provider fica offline → `503`) |
| `copilot365.js` | `m365.cloud.microsoft/chat` | Implementado e **carregado**; usa o hook `isGenerating()` (botão único enviar/parar) |

### 7.3. `manifest.json` (essencial)

```json
{
  "manifest_version": 3,
  "name": "CapoeiraHost Bridge",
  "version": "1.0.0",
  "description": "Ponte WebSocket entre o CapoeiraHost (API Ollama local) e UIs Web de LLMs.",
  "permissions": ["activeTab"],
  "host_permissions": [
    "ws://127.0.0.1:8766/*",
    "*://chatgpt.com/*",
    "*://claude.ai/*",
    "*://gemini.google.com/*",
    "*://m365.cloud.microsoft/*"
  ],
  "content_scripts": [
    {
      "matches": [
        "*://chatgpt.com/*",
        "*://claude.ai/*",
        "*://gemini.google.com/*",
        "*://m365.cloud.microsoft/*"
      ],
      "js": ["adapters/base.js", "adapters/gemini.js", "adapters/claude.js", "adapters/copilot365.js", "content.js"]
    }
  ]
}
```

> ⚠️ `host_permissions` para `ws://127.0.0.1:8766/*` é **obrigatória** no MV3 para que o content script possa abrir o WebSocket local.
> Adaptadores implementados mas fora dessa lista (ex.: `chatgpt.js`) **não são injetados** — para habilitá-los, adicione-os ao array `js` e recarregue a extensão.

---

## 8. Montagem do Prompt (Gateway)

O gateway transforma a requisição Ollama no prompt da Web:

1. **Envelope de sistema** = system prompt do perfil + instruções derivadas de `options` + modo `format: "json"` (herdado do *Tool-Calling Simulado* da spec-base):
   ```
   [SYSTEM]
   <system_prompt do perfil>
   [OPTIONS] temperature=0.7; num_predict=512; stop=STOP
   [TOOLS] ferramentas disponíveis (uma por linha, respeitando o schema de 'parameters'):
   [TOOL] {"name": "shopping", "description": "...", "parameters": {...}}
   [MODE_JSON] Responda com o JSON EXATAMENTE entre as linhas de marcador abaixo, sem markdown e sem texto fora delas:
   [JSON_START]
   {"chave": "valor"}
   [JSON_END]
   ```
   > Todos os marcadores de comunicação seguem o contrato de **linha única** `[TAG] valor`.
   > `[TOOLS]`/`[TOOL]` listam as ferramentas; `[MODE_JSON]`+`[JSON_START]`/`[JSON_END]`
   > regem a saída `format:"json"` (o gateway extrai o JSON com parsing tolerante a prosa);
   > `[MODE TOOL_CALLING]`+`[TOOL_CALL]` regem o tool calling simulado. `[MODE_JSON]` só é
   > injetado quando não há `tools`.
2. **Transcript de conversa** (para `/api/chat`): as mensagens são serializadas em turnos
   com tags de linha única (`[SYSTEM]`, `[USER]`, `[ASSISTANT]`, `[TOOL_CALL] nome {args}`,
   `[TOOL_RESULT] (id) conteúdo`) dentro do `prompt`, precedidas do system. Por padrão, cada
   requisição inicia **novo chat na Web** (`newChat: true`) e reproduz todo o histórico —
   atomicidade e idempotência por requisição. Quando `newChat: false` (via `CAPOEIRA_NEW_CHAT` ou
   `new_chat` no request), a extensão **não** clica em "Nova conversa": o prompt é injetado no
   chat aberto e a resposta é a última do bloco.
3. **Templates**: se o perfil define `template`, ele é aplicado sobre `system + prompt` (estilo Modelfile/Ollama).

---

## 9. Tratamento de Erros (formato Ollama)

Todos os erros HTTP retornam corpo `{"error": "<mensagem>"}`.

| Caso | HTTP | Mensagem sugerida |
|---|---|---|
| JSON inválido / campos inválidos | `400` | `{"error":"invalid request body"}` |
| Modelo não registrado | `404` | `{"error":"model 'x' not found"}` |
| Provider do modelo sem bridge conectado | `503` | `{"error":"no bridge available for provider 'gemini'"}` |
| Fila cheia para o provider | `503` | `{"error":"bridge queue full (10)"}` |
| Timeout de geração na Web | `504` | `{"error":"bridge timeout after 180s"}` |
| Extensão reportou `ERROR` | `502` | `{"error":"<message vindo da extensão>"}` |
| `images` / `tool`/`tool_calls` sem a lista `tools` / embeddings | `400`/`501` | ver seções 5.4/5.5/5.9 |

---

## 10. Requisitos Não-Funcionais (RNF)

- **RNF-01 (Segurança Local):** bind padrão `127.0.0.1`; WebSocket valida `Origin` por allowlist (sem origem, `chrome-extension://`, páginas dos provedores, origens locais — demais → `403`) e responde headers de Private Network Access; CORS restrito a `http://localhost:*` e `http://127.0.0.1:*` na API HTTP.
- **RNF-02 (Resiliência):** reconexão da extensão com exponential backoff sem travar a UI; estado de "offline" propagado como `503`.
- **RNF-03 (Atomicidade):** cada requisição HTTP mapeia 1:1 para um ciclo `SEND_PROMPT → RESPONSE` com `id` correlacionado; falha de requisição nunca afeta requisições subsequentes (fila FIFO + cancelamento por timeout). Por padrão cada requisição inicia um **novo chat na Web** (`newChat: true`); ao optar por reutilizar o chat (`CAPOEIRA_NEW_CHAT=false` ou `new_chat:false` por requisição), a conversa Web acumula contexto real, mas falhas deixam resíduo nessa conversa.
- **RNF-04 (Streaming):** `stream:true` SEMPRE responde em NDJSON. Se o adaptador não suportar incremento, o gateway transmite a `rawResponse` em chunks (por frases) após a geração concluir — nunca sai da spec NDJSON do Ollama.
- **RNF-05 (Latência):** `executionTimeMs` da resposta vira `total_duration`/`eval_duration`; `eval_count` = caracteres da resposta (estimativa honesta para clientes que exibem métricas).
- **RNF-06 (Compatibilidade):** embeddings fora de escopo (erro explícito `501`); **tool calling simulado** em `/api/chat` (ativado quando o request traz `tools`) com fallback para texto quando o modelo Web não produz um tool call válido.

---

## 11. Estrutura de Diretórios

```
capoeira-host/
├── server/
│   ├── __init__.py
│   ├── main.py               # entrypoint: inicia servidor HTTP + WS
│   ├── config.py             # env vars, binds, portas
│   ├── registry.py           # Model Registry (models.json) — CRUD de perfis
│   ├── ollama_dto.py         # Pydantic: /api/* request/response (formato Ollama)
│   ├── http_api.py           # rotas REST compatíveis com Ollama
│   ├── bridge.py             # WS server 8766, allowlist de Origin, headers PNA, correlação id
│   ├── gateway.py            # Ollama req → SEND_PROMPT → aguarda RESPONSE/STREAM_UPDATE
│   ├── queue.py              # FIFO por provider + locks
│   ├── prompt_builder.py     # envelope de sistema, transcript, template, JSON mode
│   └── errors.py             # respostas {"error": ...} padronizadas
├── extension/
│   ├── manifest.json         # Chrome MV3 (ws://127.0.0.1:8766/* + adaptadores ativos)
│   ├── content.js            # WS client + orquestrador + polling de streaming
│   └── adapters/
│       ├── base.js           # base + registerAdapter
│       ├── gemini.js         # implementado e carregado
│       ├── claude.js         # implementado e carregado
│       ├── chatgpt.js        # implementado, não carregado no manifest
│       └── copilot365.js     # implementado e carregado (m365.cloud.microsoft/chat)
├── tests/
│   └── test_integration.py   # suíte de integração (TestClient + WebSocket fake)
├── conftest.py               # raiz p/ import de `server.*` no pytest
├── smoke_test.py             # smoke test da API (solicitação → retorno)
├── models.json               # registry de perfis de exemplo
├── requirements.txt          # fastapi · uvicorn · websockets · pydantic
├── requirements-dev.txt      # pytest · httpx (testes)
├── README.md                 # guia de uso (iniciar/testar/troubleshooting)
└── CAPOEIRA_HOST_SPEC.md     # este documento
```

---

## 12. Exemplos de Uso

```bash
# 1. Iniciar o gateway
export CAPOEIRA_HTTP_PORT=8765
export CAPOEIRA_WS_PORT=8766
python -m server.main

# 2. Carregar a extensão no Chrome (chrome://extensions → modo dev → load unpacked) e
#    abrir uma aba autenticada do Gemini.

# 3. Testar — script cross-platform (README §Testar)
python smoke_test.py --endpoint generate --prompt "Explique o que é capoeira em 1 parágrafo."
python smoke_test.py --endpoint chat --stream
curl http://127.0.0.1:8765/api/version
```

> **Após alterar o código:** reinicie o servidor e, ao (re)carregar a extensão, **recarregue
> a aba do provedor** — o WebSocket do content script usa a origem da página, então a aba
> precisa estar ativa e logada para o bridge registrar o provider como online.

Clientes Ollama existentes (Open WebUI, `ollama` SDKs, langchain `OllamaLLM`) funcionam apontando `base_url=http://127.0.0.1:8765`.

---

## 13. Roadmap

- **Fase 1 (Core):** HTTP+WS server, registry, `/api/generate` e `/api/chat` (não-stream) com adapter Gemini.
- **Fase 2 (Streaming):** NDJSON replay + `STREAM_UPDATE` incremental no adapter Claude; timeouts e filas.
- **Fase 3 (Registry CRUD):** `/api/create`/`/api/copy`/`/api/delete`/`/api/show`; persistência `models.json`.
- **Fase 4 (Provedores):** adapters ChatGPT (implementado, fora do manifest) e Copilot 365 (✅ entregue em `m365.cloud.microsoft/chat`); fallback `provider: auto` (primeiro bridge online).

---

## 14. Fora de Escopo (explícito)

- CLI do usuário (agente de terminal) — **dispensado**.
- Redução de contexto (Tree-Sitter/AST, skeleton, grafos de dependência) — **dispensado**.
- Aplicador de diffs / escrita em arquivos locais — **dispensado**.
- Embeddings, upload de imagens, pull/push de modelos. *(Tools/function-calling são suportados de forma **simulada** via prompt — ver §5.5.)*