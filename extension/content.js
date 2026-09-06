(() => {
  const WS_URL = "ws://127.0.0.1:8766";
  const RECONNECT_BASE_MS = 1000;
  const RECONNECT_MAX_MS = 30000;
  const POLL_INTERVAL_MS = 500;
  const SETTLE_MS = 400;

  let socket = null;
  let reconnectDelay = RECONNECT_BASE_MS;

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
            tabTitle: document.title
          }
        })
      );
      console.log("[CapoeiraHost Bridge] Conectado ao CapoeiraHost:", adapter.name);
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
      }
    };

    socket.onerror = () => {};

    socket.onclose = () => {
      console.warn("[CapoeiraHost Bridge] Conexão perdida. Reconectando em", reconnectDelay, "ms");
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

    try {
      if (payload.newChat && adapter.supportsNewChat) {
        await adapter.startNewChat();
      }
      const fullPrompt = `${payload.systemPrompt || ""}\n\n${payload.prompt || ""}`.trim();
      await adapter.injectText(fullPrompt);
      await waitForCompletion(adapter, msg.id, startedAt);
    } catch (err) {
      sendMessage(msg.id, "ERROR", null, String((err && err.message) || err));
    }
  }

  async function waitForCompletion(adapter, requestId, startedAt) {
    const sel = adapter.getSelectors();
    let lastText = "";

    const interval = setInterval(async () => {
      try {
        const isGenerating = !!document.querySelector(sel.stopGeneratingIndicator);
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