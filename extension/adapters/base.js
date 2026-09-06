window.registerAdapter = function (adapter) {
  window.CapoeiraHostAdapters = window.CapoeiraHostAdapters || [];
  window.CapoeiraHostAdapters.push(adapter);
};

window.CapoeiraAdapterBase = {
  name: "base",
  match: () => false,
  supportsStreaming: false,
  supportsNewChat: true,

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
  }
};