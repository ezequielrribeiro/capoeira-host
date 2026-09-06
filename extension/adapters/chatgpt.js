window.registerAdapter({
  name: "chatgpt",
  match: () => window.location.hostname.includes("chatgpt.com"),
  supportsStreaming: false,
  supportsNewChat: true,

  getSelectors: () => ({
    inputArea: "textarea#prompt-textarea, div[contenteditable='true']",
    sendButton:
      'button[data-testid="send-button"], button[aria-label*="Send"], button[aria-label*="Enviar"]',
    stopGeneratingIndicator:
      'button[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="Parar"]',
    responseBlock: 'div[data-message-author-role="assistant"], .markdown',
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
    if (!input) throw new Error("Campo de input do ChatGPT não encontrado.");

    input.focus();
    if (input.tagName === "TEXTAREA") {
      input.value = text;
    } else {
      document.execCommand("insertText", false, text);
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 400));

    const send = document.querySelector(sel.sendButton);
    if (!send) throw new Error("Botão de envio do ChatGPT não encontrado.");
    send.click();
  },

  async extractLastResponse() {
    const nodes = document.querySelectorAll(this.getSelectors().responseBlock);
    return nodes.length ? nodes[nodes.length - 1].innerText : "";
  }
});