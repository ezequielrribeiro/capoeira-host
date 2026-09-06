window.registerAdapter({
  name: "claude",
  match: () => window.location.hostname.includes("claude.ai"),
  supportsStreaming: true,
  supportsNewChat: true,

  getSelectors: () => ({
    inputArea: '[contenteditable="true"].ProseMirror, textarea',
    sendButton: 'button[aria-label*="Send"], button[aria-label*="Enviar"]',
    stopGeneratingIndicator:
      'button[aria-label*="Stop"], button.stop-button, button[aria-label*="Parar"]',
    responseBlock: "div.font-claude-message, .font-claude-message",
    newChatButton: "a[href='/']"
  }),

  async startNewChat() {
    const button = document.querySelector(this.getSelectors().newChatButton);
    if (button) button.click();
    await new Promise((r) => setTimeout(r, 600));
  },

  async injectText(text) {
    const sel = this.getSelectors();
    const input = document.querySelector(sel.inputArea);
    if (!input) throw new Error("Campo de input do Claude não encontrado.");

    input.focus();
    document.execCommand("insertText", false, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 400));

    const send = document.querySelector(sel.sendButton);
    if (!send) throw new Error("Botão de envio do Claude não encontrado.");
    send.click();
  },

  async extractLastResponse() {
    const nodes = document.querySelectorAll(this.getSelectors().responseBlock);
    return nodes.length ? nodes[nodes.length - 1].innerText : "";
  }
});