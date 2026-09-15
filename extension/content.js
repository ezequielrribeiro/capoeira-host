(() => {
  const WS_URL = "ws://127.0.0.1:8766";
  const RECONNECT_BASE_MS = 1000;
  const RECONNECT_MAX_MS = 30000;
  const POLL_INTERVAL_MS = 500;
  const WATCH_INTERVAL_MS = 800;
  const SETTLE_MS = 400;

  let socket = null;
  let reconnectDelay = RECONNECT_BASE_MS;
  let systemHeadersSeeded = false;
  let inFlightRequest = false;
  let baselineDigest = null;
  let revision = 0;

  function getActiveAdapter() {
    const adapters = window.CapoeiraHostAdapters || [];
    return adapters.find((a) => a && typeof a.match === "function" && a.match()) || null;
  }

  function connect() {
    socket = new WebSocket(WS_URL);

    socket.onopen = () => {
      reconnectDelay = RECONNECT_BASE_MS;
      const adapter = getActiveAdapter();
      if (!adapter) {
        console.warn("[CapoeiraHost Bridge] Nenhum adaptador compatível nesta página.");
        return;
      }
      socket.send(
        JSON.stringify({
          version: "1.0",
          action: "HELLO",
          id: crypto.randomUUID(),
          payload: {
            provider: adapter.name,
            adapters: [adapter.name],
            supportsStreaming: !!adapter.supportsStreaming,
            supportsNewChat: !!adapter.supportsNewChat,
            supportsTranscript: !!adapter.supportsTranscript,
            tabTitle: document.title
          }
        })
      );
      console.log("[CapoeiraHost Bridge] Conectado ao CapoeiraHost:", adapter.name);
      startWatcher();
    };

    socket.onmessage = async (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      if (data.action === "SEND_PROMPT") {
        await handleSendPrompt(data);
      } else if (data.action === "READ_CHAT") {
        await handleReadChat(data);
      }
    };

    socket.onerror = () => {};

    socket.onclose = () => {
      console.warn("[CapoeiraHost Bridge] Conexão perdida. Reconectando em", reconnectDelay, "ms");
      stopWatcher();
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    };
  }

  async function handleSendPrompt(msg) {
    const adapter = getActiveAdapter();
    if (!adapter) {
      sendMessage(msg.id, "ERROR", null, "Nenhum adaptador compatível para esta página.");
      return;
    }

    const payload = msg.payload || {};
    const startedAt = Date.now();
    inFlightRequest = true;

    try {
      if (payload.newChat && adapter.supportsNewChat) {
        await adapter.startNewChat();
        systemHeadersSeeded = false;
        syncBaseline();
      }
      const system = systemHeadersSeeded ? "" : (payload.systemPrompt || "");
      const fullPrompt = `${system}\n\n${payload.prompt || ""}`.trim();
      await adapter.injectText(fullPrompt);
      systemHeadersSeeded = true;
      await waitForCompletion(adapter, msg.id, startedAt);
    } catch (err) {
      sendMessage(msg.id, "ERROR", null, String((err && err.message) || err));
    } finally {
      inFlightRequest = false;
      syncBaseline();
    }
  }

  async function handleReadChat(msg) {
    const adapter = getActiveAdapter();
    if (!adapter || !adapter.supportsTranscript) {
      sendMessage(msg.id, "ERROR", null, "Nenhum suporte a transcript neste adaptador.");
      return;
    }
    try {
      const transcript = await adapter.extractTranscript();
      sendMessage(msg.id, "SUCCESS", { transcript }, null);
    } catch (err) {
      sendMessage(msg.id, "ERROR", null, String((err && err.message) || err));
    }
  }

  async function waitForCompletion(adapter, requestId, startedAt) {
    const sel = adapter.getSelectors();
    let lastText = "";

    const interval = setInterval(async () => {
      try {
        const isGenerating =
          typeof adapter.isGenerating === "function"
            ? adapter.isGenerating()
            : !!document.querySelector(sel.stopGeneratingIndicator);
        const current = await adapter.extractLastResponse();

        if (adapter.supportsStreaming && current && current.length > lastText.length) {
          const partial = current.slice(lastText.length);
          lastText = current;
          socket.send(
            JSON.stringify({
              version: "1.0",
              action: "STREAM_UPDATE",
              status: "STREAMING",
              id: requestId,
              payload: { partial }
            })
          );
        }

        if (!isGenerating) {
          clearInterval(interval);
          await new Promise((r) => setTimeout(r, SETTLE_MS));
          const finalText = await adapter.extractLastResponse();
          sendMessage(
            requestId,
            "SUCCESS",
            { rawResponse: finalText, executionTimeMs: Date.now() - startedAt },
            null
          );
        }
      } catch (err) {
        clearInterval(interval);
        sendMessage(requestId, "ERROR", null, String((err && err.message) || err));
      }
    }, POLL_INTERVAL_MS);
  }

  // ------------------------------------------------------------------ watcher

  let watcherInterval = null;

  function startWatcher() {
    const adapter = getActiveAdapter();
    if (!adapter || !adapter.supportsTranscript || watcherInterval) return;
    console.log("[CapoeiraHost Bridge] Watcher de chat ativo:", adapter.name);
    baselineDigest = null;
    revision = 0;
    watcherInterval = setInterval(watchTick, WATCH_INTERVAL_MS);
  }

  function stopWatcher() {
    if (watcherInterval) {
      clearInterval(watcherInterval);
      watcherInterval = null;
    }
    baselineDigest = null;
  }

  function syncBaseline() {
    const adapter = getActiveAdapter();
    if (!adapter || !adapter.supportsTranscript) return;
    adapter.extractTranscript().then((transcript) => {
      baselineDigest = digestOf(transcript);
    }).catch(() => {});
  }

  function digestOf(transcript) {
    return JSON.stringify(transcript || []);
  }

  async function watchTick() {
    const adapter = getActiveAdapter();
    if (!adapter || !adapter.supportsTranscript || !socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    if (inFlightRequest) {
      syncBaseline();
      return;
    }

    let transcript;
    try {
      transcript = await adapter.extractTranscript();
    } catch (err) {
      return;
    }

    const currentDigest = digestOf(transcript);
    if (baselineDigest === null) {
      baselineDigest = currentDigest;
      return;
    }
    if (currentDigest === baselineDigest) {
      return;
    }

    const current = transcript || [];
    const baseline = JSON.parse(baselineDigest);
    const firstDiff = firstDiffIndex(baseline, current);
    const delta = firstDiff >= 0 ? current.slice(firstDiff) : [];

    baselineDigest = currentDigest;
    if (!delta.length) return;

    revision += 1;
    socket.send(
      JSON.stringify({
        version: "1.0",
        action: "CHAT_UPDATE",
        id: crypto.randomUUID(),
        payload: {
          provider: adapter.name,
          revision,
          transcript: current,
          messages: delta
        }
      })
    );
  }

  function firstDiffIndex(baseline, current) {
    const max = Math.max(baseline.length, current.length);
    for (let i = 0; i < max; i++) {
      if (JSON.stringify(baseline[i]) !== JSON.stringify(current[i])) return i;
    }
    return -1;
  }

  function sendMessage(requestId, status, responsePayload, error) {
    socket.send(
      JSON.stringify({
        version: "1.0",
        action: status === "SUCCESS" ? "RESPONSE" : "ERROR",
        status,
        id: requestId,
        payload: responsePayload,
        error
      })
    );
  }

  connect();
})();