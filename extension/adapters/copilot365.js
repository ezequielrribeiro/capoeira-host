window.registerAdapter({
  name: "copilot365",
  match: () => window.location.hostname.endsWith("m365.cloud.microsoft"),
  supportsStreaming: false,
  supportsNewChat: true,

  SEND_LABELS: ["send", "enviar"],
  STOP_LABELS: ["stop", "parar", "cancel", "cancelar"],

  getSelectors: () => ({
    inputArea:
      '.fai-BebopLiteChatInput__inputWrapper [contenteditable="true"], #m365-chat-input-shared-container [contenteditable="true"], #m365-chat-input-shared-container textarea',
    sendButton: "button.fai-BebopLiteChatInput__send",
    stopGeneratingIndicator: "",
    responseBlock: '[id^="response-id_"]',
    newChatButton: "div.fai-CopilotNavDrawerBody a:first-child"
  }),

  _readLabel() {
    const btn = document.querySelector(this.getSelectors().sendButton);
    if (!btn) return "";
    return (
      btn.getAttribute("aria-label") ||
      btn.getAttribute("title") ||
      btn.innerText ||
      ""
    ).toLowerCase();
  },

  isGenerating() {
    const btn = document.querySelector(this.getSelectors().sendButton);
    if (!btn) return false;

    const label = this._readLabel();
    if (this.STOP_LABELS.some((t) => label.includes(t))) return true;
    if (this.SEND_LABELS.some((t) => label.includes(t))) return false;

    return !btn.querySelector(":scope > span");
  },

  async startNewChat() {
    const link = document.querySelector(this.getSelectors().newChatButton);
    if (!link) throw new Error("Botão de nova conversa do Copilot 365 não encontrado.");
    link.click();
    await new Promise((r) => setTimeout(r, 800));
  },

  async injectText(text) {
    const sel = this.getSelectors();
    const input = document.querySelector(sel.inputArea);
    if (!input) throw new Error("Campo de input do Copilot 365 não encontrado.");

    input.focus();
    if (input.tagName === "TEXTAREA") {
      input.value = text;
    } else {
      document.execCommand("insertText", false, text);
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 300));

    const send = document.querySelector(sel.sendButton);
    if (send && !send.disabled) {
      send.click();
    } else {
      input.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Enter",
          code: "Enter",
          keyCode: 13,
          which: 13,
          bubbles: true
        })
      );
    }

    await this._waitForGenerationStart();
  },

  async _waitForGenerationStart() {
    const deadline = Date.now() + 2000;
    while (Date.now() < deadline) {
      const started =
        this.isGenerating() ||
        document.querySelectorAll(this.getSelectors().responseBlock).length > 0;
      if (started) return;
      await new Promise((r) => setTimeout(r, 150));
    }
  },

  async extractLastResponse() {
    const nodes = document.querySelectorAll(this.getSelectors().responseBlock);
    return nodes.length ? nodes[nodes.length - 1].innerText : "";
  }
});