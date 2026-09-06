window.registerAdapter({
  name: "copilot365",
  match: () => window.location.hostname.includes("copilot.microsoft.com"),
  supportsStreaming: false,
  supportsNewChat: true,

  getSelectors: () => ({
    inputArea: "textarea, div[contenteditable='true']",
    sendButton: "",
    stopGeneratingIndicator: "",
    responseBlock: "",
    newChatButton: ""
  }),

  async startNewChat() {},
  async injectText() {
    throw new Error("Adaptador copilot365 ainda não implementado.");
  },
  async extractLastResponse() {
    return "";
  }
});