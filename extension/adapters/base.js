window.registerAdapter = function (adapter) {
  window.CapoeiraHostAdapters = window.CapoeiraHostAdapters || [];
  window.CapoeiraHostAdapters.push(adapter);
};

function collectTranscript(selectors) {
  const nodes = [];
  for (const role of ["user", "assistant"]) {
    const sel = selectors[role];
    if (!sel) continue;
    document.querySelectorAll(sel).forEach((el) => {
      const content = (el.innerText || "").trim();
      if (!content) return;
      nodes.push({ role, content, el });
    });
  }
  nodes.sort((a, b) => {
    const pos = a.el.compareDocumentPosition(b.el);
    if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });
  return nodes.map(({ role, content }) => ({ role, content }));
}

window.CapoeiraAdapterBase = {
  name: "base",
  match: () => false,
  supportsStreaming: false,
  supportsNewChat: true,
  supportsTranscript: false,

  transcriptSelectors: { user: "", assistant: "" },

  getSelectors: () => ({
    inputArea: "",
    sendButton: "",
    stopGeneratingIndicator: "",
    responseBlock: "",
    newChatButton: ""
  }),

  async startNewChat() {},

  async injectText(text) {
    throw new Error("injectText não implementado.");
  },

  async extractLastResponse() {
    return "";
  },

  async extractTranscript() {
    return collectTranscript(this.transcriptSelectors);
  }
};