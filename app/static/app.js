const messagesEl = document.getElementById("messages");
const formEl = document.getElementById("chat-form");
const promptEl = document.getElementById("prompt");
const sendBtn = document.getElementById("send");
const tpl = document.getElementById("message-template");
const healthEl = document.getElementById("health");

/** @type {{role: 'user'|'assistant', content: string}[]} */
const history = [];

/** Minimal markdown renderer for bold, italic, and simple tables. */
function renderMarkdown(text) {
  let html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Tables: detect lines with pipes
  const lines = html.split("\n");
  const out = [];
  let inTable = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.startsWith("|") && line.endsWith("|")) {
      if (/^\|[\s\-:|]+\|$/.test(line)) continue;
      const cells = line.slice(1, -1).split("|").map((c) => c.trim());
      if (!inTable) {
        out.push("<table>");
        inTable = true;
        out.push("<tr>" + cells.map((c) => `<th>${c}</th>`).join("") + "</tr>");
      } else {
        out.push("<tr>" + cells.map((c) => `<td>${c}</td>`).join("") + "</tr>");
      }
    } else {
      if (inTable) {
        out.push("</table>");
        inTable = false;
      }
      out.push(line);
    }
  }
  if (inTable) out.push("</table>");
  html = out.join("\n");

  // Bold and italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

  // Paragraphs from double newlines
  html = html.replace(/\n{2,}/g, "</p><p>");
  html = "<p>" + html + "</p>";
  html = html.replace(/<p>\s*<table>/g, "<table>");
  html = html.replace(/<\/table>\s*<\/p>/g, "</table>");

  return html;
}

function appendMessage({ role, content, meta = "", citations = [] }) {
  const node = tpl.content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  node.querySelector(".role").textContent = role === "assistant" ? "Assistant" : "You";
  node.querySelector(".meta").textContent = meta;
  node.querySelector(".content").innerHTML = renderMarkdown(content);

  const citationsEl = node.querySelector(".citations");
  if (citations.length) {
    for (const citation of citations) {
      const wrap = document.createElement("article");
      wrap.className = "citation";

      const label = document.createElement("div");
      label.className = "citation-label";
      label.textContent = "Citation";

      const title = document.createElement("div");
      title.className = "citation-heading";
      title.textContent = citation.heading;

      const quote = document.createElement("span");
      quote.className = "quote";
      quote.textContent = `\u201c${citation.quote}\u201d`;

      wrap.append(label, title, quote);
      citationsEl.appendChild(wrap);
    }
  }

  messagesEl.appendChild(node);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function loadHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    healthEl.textContent = `Provider: ${data.provider} · Model: ${data.model} · Sections: ${data.sections_loaded.toLocaleString()}`;
  } catch (err) {
    healthEl.textContent = "Backend health check failed. Confirm the server is running.";
  }
}

function buildRequestHistory() {
  // Include recent turns only; server also enforces its own limit.
  return history.slice(-12).map((item) => ({ role: item.role, content: item.content }));
}

async function sendMessage(message) {
  sendBtn.disabled = true;

  appendMessage({ role: "user", content: message });
  history.push({ role: "user", content: message });

  try {
    const payload = {
      message,
      history: buildRequestHistory(),
    };

    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }

    const data = await response.json();
    const meta = `Confidence: ${data.refused ? "needs clarification" : data.confidence}${data.used_long_context_verification ? " · long-context check" : ""}`;

    appendMessage({
      role: "assistant",
      content: data.answer,
      meta,
      citations: data.citations || [],
    });
    history.push({ role: "assistant", content: data.answer });
  } catch (err) {
    appendMessage({
      role: "assistant",
      content:
        "The server failed to complete this request. Check API credentials/provider settings and try again.",
      meta: "error",
    });
  } finally {
    sendBtn.disabled = false;
  }
}

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = promptEl.value.trim();
  if (!message) {
    return;
  }

  promptEl.value = "";
  await sendMessage(message);
});

promptEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    formEl.requestSubmit();
  }
});

appendMessage({
  role: "assistant",
  content:
    "Ask a legal question and I will answer only from cited Somerville ordinance sections. If I can't ground it, I'll say so.",
  meta: "ready",
});

void loadHealth();
