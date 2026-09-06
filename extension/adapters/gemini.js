window.registerAdapter({
  name: "gemini",
  match: () => window.location.hostname.includes("gemini.google.com"),
  supportsStreaming: false,
  supportsNewChat: true,

  getSelectors: () => ({
    inputArea: '.input-area div[contenteditable="true"], textarea',
    sendButton:
      'button[aria-label*="Enviar"], button.send-button, button[aria-label*="Send"]',
    stopGeneratingIndicator:
      'button[aria-label*="Parar"], .stop-generating-icon, button[aria-label*="Stop"]',
    responseBlock: ".model-response-text, message-content",
    newChatButton: 'a[aria-label*="Nova conversa"], a[aria-label*="New chat"]'
  }),

  async startNewChat() {
    const button = document.querySelector(this.getSelectors().newChatButton);
    if (button) button.click();
    await new Promise((r) => setTimeout(r, 600));
  },

  async injectText(text) {
    const sel = this.getSelectors();
    const input = document.querySelector(sel.inputArea);
    if (!input) throw new Error("Campo de input do Gemini não encontrado.");

    input.focus();
    document.execCommand("insertText", false, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 400));

    const send = document.querySelector(sel.sendButton);
    if (!send) throw new Error("Botão de envio do Gemini não encontrado.");
    send.click();
  },

  async extractLastResponse() {
    const nodes = document.querySelectorAll(this.getSelectors().responseBlock);
    return nodes.length ? nodes[nodes.length - 1].innerText : "";
  }
});