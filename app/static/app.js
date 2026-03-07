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

  // Headings (###, ##, #)
  html = html.replace(/^#{3}\s+(.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^#{2}\s+(.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^#{1}\s+(.+)$/gm, "<h1>$1</h1>");

  // Unordered lists (- item)
  html = html.replace(/(^|\n)(- .+(?:\n- .+)*)/g, function (_, before, block) {
    const items = block
      .split("\n")
      .map((l) => "<li>" + l.replace(/^- /, "") + "</li>")
      .join("");
    return before + "<ul>" + items + "</ul>";
  });

  // Bold and italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

  // Paragraphs from double newlines
  html = html.replace(/\n{2,}/g, "</p><p>");
  html = "<p>" + html + "</p>";
  html = html.replace(/<p>\s*<table>/g, "<table>");
  html = html.replace(/<\/table>\s*<\/p>/g, "</table>");
  html = html.replace(/<p>\s*<(h[1-3]|ul)>/g, "<$1>");
  html = html.replace(/<\/(h[1-3]|ul)>\s*<\/p>/g, "</$1>");

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

      const primaryUrl = citation.url || citation.local_url || citation.official_url || "";
      const title = document.createElement(primaryUrl ? "a" : "div");
      title.className = "citation-heading";
      title.textContent = citation.heading;
      if (primaryUrl) {
        title.href = primaryUrl;
        title.target = "_blank";
        title.rel = "noopener noreferrer";
      }

      const quote = document.createElement("span");
      quote.className = "quote";
      quote.textContent = `\u201c${citation.quote}\u201d`;

      const links = document.createElement("div");
      links.className = "citation-links";

      if (citation.official_url && citation.official_url !== primaryUrl) {
        const official = document.createElement("a");
        official.href = citation.official_url;
        official.target = "_blank";
        official.rel = "noopener noreferrer";
        official.textContent = "Official source";
        links.appendChild(official);
      }

      wrap.append(label, title, quote);
      if (links.childNodes.length) {
        wrap.appendChild(links);
      }
      citationsEl.appendChild(wrap);
    }
  }

  messagesEl.appendChild(node);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function loadHealth() {
  if (!healthEl) {
    return;
  }

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

  const thinking = document.createElement("article");
  thinking.className = "message assistant thinking";
  thinking.innerHTML =
    '<header><span class="role">Assistant</span></header>' +
    '<div class="content"><span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span> Thinking</div>';
  messagesEl.appendChild(thinking);
  messagesEl.scrollTop = messagesEl.scrollHeight;

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
    thinking.remove();
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
    "Ask me anything about Somerville ordinances. I'll answer based on cited sections.",
  meta: "ready",
});

void loadHealth();
