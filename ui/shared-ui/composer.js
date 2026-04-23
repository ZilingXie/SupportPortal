(() => {
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function sanitizeUrl(value) {
    const raw = String(value || "").trim();
    if (!raw) {
      return null;
    }
    const markdownMatch = raw.match(/\((https?:\/\/[^)\s]+)\)/i);
    const candidate = markdownMatch ? markdownMatch[1] : raw;
    const trimmed = candidate.replace(/[)\],.;]+$/g, "");

    const withProtocol = /^[a-z]+:\/\//i.test(trimmed)
      ? trimmed
      : /^[\w.-]+\.[a-z]{2,}(\/.*)?$/i.test(trimmed)
      ? `https://${trimmed}`
      : trimmed;

    try {
      const parsed = new URL(withProtocol);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        return parsed.toString();
      }
    } catch {
      return null;
    }
    return null;
  }

  function formatMultilineText(value) {
    return escapeHtml(value).replaceAll("\n", "<br>");
  }

  function buildMarkdownInlineTextNode(value) {
    return {
      type: "text",
      value: String(value ?? ""),
    };
  }

  function collectMarkdownInlinePlainText(nodes = []) {
    return nodes
      .map((node) => {
        if (!node) {
          return "";
        }
        if (node.type === "text" || node.type === "code") {
          return String(node.value ?? "");
        }
        return collectMarkdownInlinePlainText(node.children || []);
      })
      .join("");
  }

  function canOpenMarkdownInlineDelimiter(value, index, delimiter) {
    const previousChar = index > 0 ? String(value[index - 1] || "") : "";
    const nextChar = String(value[index + delimiter.length] || "");
    if (!nextChar || /\s/.test(nextChar)) {
      return false;
    }
    if (delimiter === "_" && /[A-Za-z0-9]/.test(previousChar)) {
      return false;
    }
    return true;
  }

  function parseMarkdownInlineCodeToken(value, startIndex) {
    let index = startIndex + 1;
    let codeText = "";
    while (index < value.length) {
      const currentChar = value[index];
      if (currentChar === "\n") {
        return null;
      }
      if (currentChar === "\\") {
        if (index + 1 < value.length) {
          codeText += value[index + 1];
          index += 2;
          continue;
        }
        return null;
      }
      if (currentChar === "`") {
        return {
          value: codeText,
          nextIndex: index + 1,
        };
      }
      codeText += currentChar;
      index += 1;
    }
    return null;
  }

  function parseMarkdownLinkUrlToken(value, startIndex) {
    if (String(value[startIndex] || "") !== "(") {
      return null;
    }
    let index = startIndex + 1;
    let url = "";
    while (index < value.length) {
      const currentChar = value[index];
      if (currentChar === "\n") {
        return null;
      }
      if (currentChar === "\\") {
        if (index + 1 < value.length) {
          url += value[index + 1];
          index += 2;
          continue;
        }
        return null;
      }
      if (currentChar === ")") {
        return {
          url,
          nextIndex: index + 1,
        };
      }
      url += currentChar;
      index += 1;
    }
    return null;
  }

  function parseMarkdownInlineSequence(value, startIndex = 0, stopToken = "") {
    const nodes = [];
    let textBuffer = "";
    let index = startIndex;

    const flushTextBuffer = () => {
      if (!textBuffer) {
        return;
      }
      nodes.push(buildMarkdownInlineTextNode(textBuffer));
      textBuffer = "";
    };

    while (index < value.length) {
      if (stopToken && value.startsWith(stopToken, index)) {
        flushTextBuffer();
        return {
          nodes,
          nextIndex: index + stopToken.length,
          closed: true,
        };
      }

      const currentChar = value[index];

      if (currentChar === "\\") {
        if (index + 1 < value.length) {
          textBuffer += value[index + 1];
          index += 2;
          continue;
        }
        textBuffer += currentChar;
        index += 1;
        continue;
      }

      if (currentChar === "`") {
        const codeToken = parseMarkdownInlineCodeToken(value, index);
        if (codeToken) {
          flushTextBuffer();
          nodes.push({
            type: "code",
            value: codeToken.value,
          });
          index = codeToken.nextIndex;
          continue;
        }
      }

      if (value.startsWith("**", index) && canOpenMarkdownInlineDelimiter(value, index, "**")) {
        const strongToken = parseMarkdownInlineSequence(value, index + 2, "**");
        if (strongToken.closed && collectMarkdownInlinePlainText(strongToken.nodes).trim()) {
          flushTextBuffer();
          nodes.push({
            type: "strong",
            children: strongToken.nodes,
          });
          index = strongToken.nextIndex;
          continue;
        }
      }

      if ((currentChar === "_" || currentChar === "*") && canOpenMarkdownInlineDelimiter(value, index, currentChar)) {
        const emphasisToken = parseMarkdownInlineSequence(value, index + 1, currentChar);
        if (emphasisToken.closed && collectMarkdownInlinePlainText(emphasisToken.nodes).trim()) {
          flushTextBuffer();
          nodes.push({
            type: "em",
            children: emphasisToken.nodes,
          });
          index = emphasisToken.nextIndex;
          continue;
        }
      }

      if (currentChar === "[") {
        const labelToken = parseMarkdownInlineSequence(value, index + 1, "]");
        if (labelToken.closed) {
          const urlToken = parseMarkdownLinkUrlToken(value, labelToken.nextIndex);
          if (urlToken) {
            const rawToken = value.slice(index, urlToken.nextIndex);
            const href = sanitizeUrl(urlToken.url);
            flushTextBuffer();
            if (href && collectMarkdownInlinePlainText(labelToken.nodes).trim()) {
              nodes.push({
                type: "link",
                href,
                children: labelToken.nodes,
              });
            } else {
              nodes.push(buildMarkdownInlineTextNode(rawToken));
            }
            index = urlToken.nextIndex;
            continue;
          }
        }
      }

      textBuffer += currentChar;
      index += 1;
    }

    flushTextBuffer();
    return {
      nodes,
      nextIndex: index,
      closed: false,
    };
  }

  function renderMarkdownInlineNodes(nodes = []) {
    return nodes
      .map((node) => {
        if (!node) {
          return "";
        }
        switch (node.type) {
          case "text":
            return escapeHtml(node.value);
          case "strong":
            return `<strong>${renderMarkdownInlineNodes(node.children || [])}</strong>`;
          case "em":
            return `<em>${renderMarkdownInlineNodes(node.children || [])}</em>`;
          case "link":
            return `<a href="${escapeHtml(node.href || "")}" target="_blank" rel="noopener noreferrer">${renderMarkdownInlineNodes(
              node.children || []
            )}</a>`;
          case "code":
            return `<code>${escapeHtml(node.value)}</code>`;
          default:
            return "";
        }
      })
      .join("");
  }

  function renderInlineMarkdown(value) {
    const text = String(value ?? "");
    if (!text) {
      return "";
    }
    return renderMarkdownInlineNodes(parseMarkdownInlineSequence(text).nodes);
  }

  function isOrderedListLine(line) {
    return /^\s*\d+\.\s+/.test(String(line || ""));
  }

  function isUnorderedListLine(line) {
    return /^\s*[-*]\s+/.test(String(line || ""));
  }

  function renderMarkdownMessage(value) {
    const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
    const html = [];
    let index = 0;

    while (index < lines.length) {
      const currentLine = lines[index];
      const trimmedLine = currentLine.trim();
      if (!trimmedLine) {
        index += 1;
        continue;
      }

      const fenceMatch = trimmedLine.match(/^```([A-Za-z0-9_+-]*)\s*$/);
      if (fenceMatch) {
        const language = String(fenceMatch[1] || "").trim().toLowerCase();
        const codeLines = [];
        index += 1;
        while (index < lines.length && !lines[index].trim().match(/^```/)) {
          codeLines.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) {
          index += 1;
        }
        const languageAttr = language ? ` class="language-${escapeHtml(language)}"` : "";
        html.push(`<pre><code${languageAttr}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        continue;
      }

      if (isOrderedListLine(trimmedLine)) {
        const items = [];
        while (index < lines.length && isOrderedListLine(lines[index])) {
          items.push(lines[index].replace(/^\s*\d+\.\s+/, ""));
          index += 1;
        }
        html.push(
          `<ol>${items.map((item) => `<li>${renderInlineMarkdown(item.trim())}</li>`).join("")}</ol>`
        );
        continue;
      }

      if (isUnorderedListLine(trimmedLine)) {
        const items = [];
        while (index < lines.length && isUnorderedListLine(lines[index])) {
          items.push(lines[index].replace(/^\s*[-*]\s+/, ""));
          index += 1;
        }
        html.push(
          `<ul>${items.map((item) => `<li>${renderInlineMarkdown(item.trim())}</li>`).join("")}</ul>`
        );
        continue;
      }

      const paragraphLines = [];
      while (index < lines.length) {
        const line = lines[index];
        const trimmed = line.trim();
        if (!trimmed || trimmed.match(/^```/) || isOrderedListLine(trimmed) || isUnorderedListLine(trimmed)) {
          break;
        }
        paragraphLines.push(trimmed);
        index += 1;
      }
      html.push(`<p>${paragraphLines.map((line) => renderInlineMarkdown(line)).join("<br>")}</p>`);
    }

    return html.join("");
  }

  function buildDefaultComposerToolbarState() {
    return {
      bold: false,
      italic: false,
      list: false,
      codeBlock: false,
    };
  }

  function normalizeComposerToolbarActionStateKey(action) {
    return String(action || "").trim() === "code-block" ? "codeBlock" : String(action || "").trim();
  }

  function renderComposerFormattingToolbarButtons({
    toolbarState = buildDefaultComposerToolbarState(),
    canCompose = true,
  } = {}) {
    const buttons = [
      { action: "bold", icon: "format_bold", label: "Bold" },
      { action: "italic", icon: "format_italic", label: "Italic" },
      { action: "list", icon: "format_list_bulleted", label: "List" },
      { action: "code-block", icon: "code_blocks", label: "Code block" },
      { action: "attach", icon: "attach_file", label: "Attach" },
    ];
    return buttons
      .map((item) => {
        const stateKey = normalizeComposerToolbarActionStateKey(item.action);
        const isActive = Boolean(toolbarState[stateKey]);
        const activeClass = isActive ? " is-active" : "";
        return `
        <button
          class="new-ticket-toolbar-button${activeClass}"
          type="button"
          data-composer-markdown-action="${escapeHtml(item.action)}"
          aria-label="${escapeHtml(item.label)}"
          title="${escapeHtml(item.label)}"
          aria-pressed="${isActive ? "true" : "false"}"
          ${canCompose ? "" : "disabled"}
        >
          <span class="material-symbols-outlined" aria-hidden="true">${item.icon}</span>
        </button>
      `;
      })
      .join("");
  }

  function stripComposerZeroWidthSpaces(value) {
    return String(value || "").replace(/\u200B/g, "");
  }

  function decodeRichComposerHtmlEntities(value) {
    return String(value || "")
      .replace(/&nbsp;/gi, " ")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&amp;/g, "&");
  }

  function parseRichComposerHtmlAttributes(value) {
    const attrs = {};
    String(value || "").replace(/([A-Za-z0-9:_-]+)(?:\s*=\s*"([^"]*)")?/g, (_match, name, rawValue) => {
      attrs[String(name || "").toLowerCase()] = rawValue ?? "";
      return "";
    });
    return attrs;
  }

  function parseRichComposerHtmlFragment(value) {
    const root = { type: "root", children: [] };
    const stack = [root];
    const tokens = String(value || "").match(/<\/?[^>]+>|[^<]+/g) || [];

    tokens.forEach((token) => {
      const current = stack[stack.length - 1];
      if (!token) {
        return;
      }
      if (token.startsWith("</")) {
        const closingTag = token.slice(2, -1).trim().toLowerCase();
        while (stack.length > 1) {
          const candidate = stack.pop();
          if (candidate?.tag === closingTag) {
            break;
          }
        }
        return;
      }
      if (token.startsWith("<")) {
        const raw = token.slice(1, -1).trim();
        const selfClosing = raw.endsWith("/");
        const normalizedRaw = selfClosing ? raw.slice(0, -1).trim() : raw;
        const nameMatch = normalizedRaw.match(/^([A-Za-z0-9:_-]+)/);
        if (!nameMatch) {
          return;
        }
        const tag = nameMatch[1].toLowerCase();
        const node = {
          type: "element",
          tag,
          attrs: parseRichComposerHtmlAttributes(normalizedRaw.slice(nameMatch[1].length)),
          children: [],
        };
        current.children.push(node);
        if (!selfClosing && tag !== "br") {
          stack.push(node);
        }
        return;
      }
      current.children.push({
        type: "text",
        value: decodeRichComposerHtmlEntities(token),
      });
    });

    return root;
  }

  function isRichComposerBlockTag(tagName) {
    return ["p", "div", "ul", "ol", "pre"].includes(String(tagName || "").toLowerCase());
  }

  function isRichComposerWhitespaceTextNode(node) {
    return (
      node?.type === "text" && stripComposerZeroWidthSpaces(String(node.value || "")).trim().length === 0
    );
  }

  function isRichComposerBreakNode(node) {
    return node?.type === "element" && String(node.tag || "").toLowerCase() === "br";
  }

  function isRichComposerEmptyBlockWrapperNode(node) {
    if (
      node?.type !== "element" ||
      !["p", "div"].includes(String(node.tag || "").toLowerCase())
    ) {
      return false;
    }
    return (node.children || []).every(
      (child) => isRichComposerWhitespaceTextNode(child) || isRichComposerBreakNode(child)
    );
  }

  function isRichComposerEmptyListNode(node) {
    if (
      node?.type !== "element" ||
      !["ul", "ol"].includes(String(node.tag || "").toLowerCase())
    ) {
      return false;
    }
    const items = (node.children || []).filter(
      (child) => child?.type === "element" && String(child.tag || "").toLowerCase() === "li"
    );
    if (items.length === 0) {
      return true;
    }
    return items.every((item) =>
      (item.children || []).every(
        (child) => isRichComposerWhitespaceTextNode(child) || isRichComposerBreakNode(child)
      )
    );
  }

  function normalizeRichComposerParsedNodes(nodes = []) {
    const normalized = [];

    nodes.forEach((node) => {
      if (!node) {
        return;
      }
      if (node.type === "text") {
        if (!String(node.value || "")) {
          return;
        }
        normalized.push({
          ...node,
          value: String(node.value || ""),
        });
        return;
      }
      if (node.type !== "element") {
        return;
      }

      const normalizedChildren = normalizeRichComposerParsedNodes(node.children || []);
      const normalizedNode = {
        ...node,
        children: normalizedChildren,
      };
      const normalizedTag = String(normalizedNode.tag || "").toLowerCase();

      if (isRichComposerEmptyBlockWrapperNode(normalizedNode)) {
        return;
      }

      if (["ul", "ol"].includes(normalizedTag)) {
        const items = normalizedChildren.filter(
          (child) => child?.type === "element" && String(child.tag || "").toLowerCase() === "li"
        );
        const normalizedList = {
          ...normalizedNode,
          children: items,
        };
        if (isRichComposerEmptyListNode(normalizedList)) {
          return;
        }
        normalized.push(normalizedList);
        return;
      }

      if (["p", "div"].includes(normalizedTag)) {
        const hasOnlyBlocksOrWhitespace =
          normalizedChildren.length > 0 &&
          normalizedChildren.every(
            (child) =>
              isRichComposerWhitespaceTextNode(child) ||
              (child?.type === "element" && isRichComposerBlockTag(child.tag))
          );
        if (hasOnlyBlocksOrWhitespace) {
          normalized.push(
            ...normalizedChildren.filter(
              (child) => child?.type === "element" && isRichComposerBlockTag(child.tag)
            )
          );
          return;
        }
      }

      normalized.push(normalizedNode);
    });

    return normalized;
  }

  function renderRichComposerHtmlAttributes(attrs = {}) {
    return Object.entries(attrs)
      .filter(([name]) => String(name || "").trim())
      .map(([name, rawValue]) => {
        const normalizedName = String(name || "").trim().toLowerCase();
        const value = rawValue ?? "";
        return ` ${normalizedName}="${escapeHtml(String(value))}"`;
      })
      .join("");
  }

  function renderRichComposerHtmlNode(node) {
    if (!node) {
      return "";
    }
    if (node.type === "text") {
      return escapeHtml(String(node.value || ""));
    }
    if (node.type !== "element") {
      return "";
    }
    const tag = String(node.tag || "").toLowerCase();
    if (tag === "br") {
      return "<br>";
    }
    return `<${tag}${renderRichComposerHtmlAttributes(node.attrs || {})}>${renderRichComposerHtmlNodes(
      node.children || []
    )}</${tag}>`;
  }

  function renderRichComposerHtmlNodes(nodes = []) {
    return nodes.map((node) => renderRichComposerHtmlNode(node)).join("");
  }

  function isRichComposerCaretMarkerNode(node) {
    return (
      node?.type === "element" &&
      String(node.tag || "").toLowerCase() === "span" &&
      String(node.attrs?.["data-composer-caret-marker"] || "").toLowerCase() === "true"
    );
  }

  function isRichComposerEmptyLineMarkerNode(node) {
    return (
      node?.type === "element" &&
      String(node.tag || "").toLowerCase() === "span" &&
      String(node.attrs?.["data-composer-empty-line"] || "").toLowerCase() === "true"
    );
  }

  function buildRichComposerCaretMarkerNode() {
    return {
      type: "element",
      tag: "span",
      attrs: { "data-composer-caret-marker": "true" },
      children: [],
    };
  }

  function cloneRichComposerParsedNode(node) {
    if (!node) {
      return null;
    }
    if (node.type === "text") {
      return {
        type: "text",
        value: String(node.value || ""),
      };
    }
    if (node.type !== "element") {
      return null;
    }
    return {
      type: "element",
      tag: String(node.tag || "").toLowerCase(),
      attrs: { ...(node.attrs || {}) },
      children: cloneRichComposerParsedNodes(node.children || []),
    };
  }

  function cloneRichComposerParsedNodes(nodes = []) {
    return (Array.isArray(nodes) ? nodes : []).map((node) => cloneRichComposerParsedNode(node)).filter(Boolean);
  }

  function cloneRichComposerElementNodeWithChildren(node, children = []) {
    return {
      type: "element",
      tag: String(node?.tag || "").toLowerCase(),
      attrs: { ...(node?.attrs || {}) },
      children: cloneRichComposerParsedNodes(children),
    };
  }

  function hasRichComposerCaretMarkerInParsedNodes(nodes = []) {
    return (Array.isArray(nodes) ? nodes : []).some((node) => {
      if (!node) {
        return false;
      }
      if (isRichComposerCaretMarkerNode(node)) {
        return true;
      }
      return node.type === "element" && hasRichComposerCaretMarkerInParsedNodes(node.children || []);
    });
  }

  function isRichComposerParsedNodeStructurallyEmpty(node, { ignoreCaretMarker = true } = {}) {
    if (!node) {
      return true;
    }
    if (node.type === "text") {
      return stripComposerZeroWidthSpaces(String(node.value || "")).trim().length === 0;
    }
    if (node.type !== "element") {
      return true;
    }
    if (isRichComposerBreakNode(node) || isRichComposerEmptyLineMarkerNode(node)) {
      return true;
    }
    if (ignoreCaretMarker && isRichComposerCaretMarkerNode(node)) {
      return true;
    }
    return (node.children || []).every((child) =>
      isRichComposerParsedNodeStructurallyEmpty(child, { ignoreCaretMarker })
    );
  }

  function areRichComposerParsedNodesStructurallyEmpty(nodes = [], options = {}) {
    return (Array.isArray(nodes) ? nodes : []).every((node) =>
      isRichComposerParsedNodeStructurallyEmpty(node, options)
    );
  }

  function unwrapRichComposerSingleBlockChildren(nodes = []) {
    const normalizedNodes = cloneRichComposerParsedNodes(nodes);
    const meaningfulNodes = normalizedNodes.filter((node) => {
      if (!node) {
        return false;
      }
      if (node.type === "text") {
        return String(node.value || "").length > 0;
      }
      return true;
    });
    if (meaningfulNodes.length !== 1) {
      return normalizedNodes;
    }
    const candidate = meaningfulNodes[0];
    if (
      candidate?.type !== "element" ||
      !["p", "div"].includes(String(candidate.tag || "").toLowerCase())
    ) {
      return normalizedNodes;
    }
    const hasNestedBlocks = (candidate.children || []).some(
      (child) => child?.type === "element" && isRichComposerBlockTag(child.tag)
    );
    if (hasNestedBlocks) {
      return normalizedNodes;
    }
    return cloneRichComposerParsedNodes(candidate.children || []);
  }

  function buildRichComposerListItemChildrenWithFallback(children = [], { includeCaretMarker = false } = {}) {
    const normalizedChildren = cloneRichComposerParsedNodes(children);
    if (!areRichComposerParsedNodesStructurallyEmpty(normalizedChildren, { ignoreCaretMarker: true })) {
      return normalizedChildren;
    }
    const fallbackChildren = [];
    if (includeCaretMarker) {
      fallbackChildren.push(buildRichComposerCaretMarkerNode());
    }
    fallbackChildren.push({
      type: "element",
      tag: "br",
      attrs: {},
      children: [],
    });
    return fallbackChildren;
  }

  function buildRichComposerEmptyLineBlockNode() {
    return {
      type: "element",
      tag: "div",
      attrs: {},
      children: [
        {
          type: "element",
          tag: "span",
          attrs: {
            "data-composer-empty-line": "true",
          },
          children: [
            {
              type: "text",
              value: "\u200B",
            },
          ],
        },
      ],
    };
  }

  function isRichComposerEmptyLineInlineMarkerNode(node) {
    return (
      node?.type === "element" &&
      String(node.tag || "").toLowerCase() === "span" &&
      String(node.attrs?.["data-composer-empty-line"] || "").toLowerCase() === "true"
    );
  }

  function isRichComposerEmptyLineBlockNode(node) {
    if (
      node?.type !== "element" ||
      !["p", "div"].includes(String(node.tag || "").toLowerCase())
    ) {
      return false;
    }
    const children = node.children || [];
    return (
      children.length > 0 &&
      children.every(
        (child) =>
          isRichComposerWhitespaceTextNode(child) || isRichComposerEmptyLineInlineMarkerNode(child)
      )
    );
  }

  function buildRichComposerPlainTextCarrierNode(children = [], { includeCaretMarker = false } = {}) {
    const normalizedChildren = cloneRichComposerParsedNodes(children);
    if (!areRichComposerParsedNodesStructurallyEmpty(normalizedChildren, { ignoreCaretMarker: true })) {
      return {
        type: "element",
        tag: "div",
        attrs: {},
        children: normalizedChildren,
      };
    }
    const emptyLineChildren = cloneRichComposerParsedNodes(buildRichComposerEmptyLineBlockNode().children || []);
    return {
      type: "element",
      tag: "div",
      attrs: {},
      children: includeCaretMarker
        ? [buildRichComposerCaretMarkerNode(), ...emptyLineChildren]
        : emptyLineChildren,
    };
  }

  function splitRichComposerParsedNodesAtCaretMarker(nodes = []) {
    const beforeNodes = [];
    const afterNodes = [];
    let foundMarker = false;

    (Array.isArray(nodes) ? nodes : []).forEach((node) => {
      if (!node) {
        return;
      }
      if (foundMarker) {
        afterNodes.push(cloneRichComposerParsedNode(node));
        return;
      }
      if (isRichComposerCaretMarkerNode(node)) {
        foundMarker = true;
        afterNodes.push(buildRichComposerCaretMarkerNode());
        return;
      }
      if (node.type === "element" && !isRichComposerBreakNode(node)) {
        const splitChildren = splitRichComposerParsedNodesAtCaretMarker(node.children || []);
        if (splitChildren.foundMarker) {
          foundMarker = true;
          if (!areRichComposerParsedNodesStructurallyEmpty(splitChildren.beforeNodes, { ignoreCaretMarker: false })) {
            beforeNodes.push(cloneRichComposerElementNodeWithChildren(node, splitChildren.beforeNodes));
          }
          if (splitChildren.afterNodes.length > 0 && isRichComposerCaretMarkerNode(splitChildren.afterNodes[0])) {
            afterNodes.push(buildRichComposerCaretMarkerNode());
            const trailingChildren = splitChildren.afterNodes.slice(1);
            if (!areRichComposerParsedNodesStructurallyEmpty(trailingChildren, { ignoreCaretMarker: false })) {
              afterNodes.push(cloneRichComposerElementNodeWithChildren(node, trailingChildren));
            }
          } else if (
            !areRichComposerParsedNodesStructurallyEmpty(splitChildren.afterNodes, { ignoreCaretMarker: false })
          ) {
            afterNodes.push(cloneRichComposerElementNodeWithChildren(node, splitChildren.afterNodes));
          }
          return;
        }
      }
      beforeNodes.push(cloneRichComposerParsedNode(node));
    });

    return {
      beforeNodes,
      afterNodes,
      foundMarker,
    };
  }

  function wrapRichComposerBlockHtmlInList(blockHtml) {
    const parsed = parseRichComposerHtmlFragment(String(blockHtml || ""));
    const blockChildren = unwrapRichComposerSingleBlockChildren(parsed.children || []);
    const listItemChildren = buildRichComposerListItemChildrenWithFallback(blockChildren, {
      includeCaretMarker: hasRichComposerCaretMarkerInParsedNodes(blockChildren),
    });
    return renderRichComposerHtmlNodes([
      {
        type: "element",
        tag: "ul",
        attrs: {},
        children: [
          {
            type: "element",
            tag: "li",
            attrs: {},
            children: listItemChildren,
          },
        ],
      },
    ]);
  }

  function splitRichComposerListItemHtmlAtCaret(listItemHtml) {
    const parsed = parseRichComposerHtmlFragment(String(listItemHtml || ""));
    const listItemNode =
      (parsed.children || []).find(
        (node) => node?.type === "element" && String(node.tag || "").toLowerCase() === "li"
      ) || null;
    if (!listItemNode) {
      return String(listItemHtml || "");
    }
    const splitChildren = splitRichComposerParsedNodesAtCaretMarker(listItemNode.children || []);
    if (!splitChildren.foundMarker) {
      return String(listItemHtml || "");
    }
    const beforeChildren = buildRichComposerListItemChildrenWithFallback(splitChildren.beforeNodes);
    const afterChildren = buildRichComposerListItemChildrenWithFallback(splitChildren.afterNodes, {
      includeCaretMarker: true,
    });
    return renderRichComposerHtmlNodes([
      {
        type: "element",
        tag: "li",
        attrs: { ...(listItemNode.attrs || {}) },
        children: beforeChildren,
      },
      {
        type: "element",
        tag: "li",
        attrs: { ...(listItemNode.attrs || {}) },
        children: afterChildren,
      },
    ]);
  }

  function exitRichComposerCurrentListItemHtml(listHtml) {
    const parsed = parseRichComposerHtmlFragment(String(listHtml || ""));
    const listNode =
      (parsed.children || []).find(
        (node) =>
          node?.type === "element" && ["ul", "ol"].includes(String(node.tag || "").toLowerCase())
      ) || null;
    if (!listNode) {
      return String(listHtml || "");
    }
    const listItems = (listNode.children || []).filter(
      (node) => node?.type === "element" && String(node.tag || "").toLowerCase() === "li"
    );
    const exitIndex = listItems.findIndex((item) => hasRichComposerCaretMarkerInParsedNodes(item.children || []));
    if (exitIndex < 0) {
      return String(listHtml || "");
    }
    const beforeItems = cloneRichComposerParsedNodes(listItems.slice(0, exitIndex));
    const afterItems = cloneRichComposerParsedNodes(listItems.slice(exitIndex + 1));
    const exitItem = listItems[exitIndex];
    const exitBlock = buildRichComposerPlainTextCarrierNode(exitItem.children || [], {
      includeCaretMarker: hasRichComposerCaretMarkerInParsedNodes(exitItem.children || []),
    });
    const renderedNodes = [];
    if (beforeItems.length > 0) {
      renderedNodes.push({
        type: "element",
        tag: String(listNode.tag || "").toLowerCase(),
        attrs: { ...(listNode.attrs || {}) },
        children: beforeItems,
      });
    }
    renderedNodes.push(exitBlock);
    if (afterItems.length > 0) {
      renderedNodes.push({
        type: "element",
        tag: String(listNode.tag || "").toLowerCase(),
        attrs: { ...(listNode.attrs || {}) },
        children: afterItems,
      });
    }
    return renderRichComposerHtmlNodes(renderedNodes);
  }

  function normalizeRichComposerHtmlString(value) {
    const normalized = String(value || "")
      .replace(/<(\/?)b>/gi, "<$1strong>")
      .replace(/<(\/?)i>/gi, "<$1em>")
      .replace(/<span[^>]*data-composer-caret-marker="true"[^>]*><\/span>/gi, "");
    const parsed = parseRichComposerHtmlFragment(normalized);
    return renderRichComposerHtmlNodes(normalizeRichComposerParsedNodes(parsed.children || []));
  }

  function escapeMarkdownLiteralText(value) {
    return String(value || "")
      .replace(/\\/g, "\\\\")
      .replace(/`/g, "\\`")
      .replace(/\*/g, "\\*")
      .replace(/_/g, "\\_")
      .replace(/\[/g, "\\[")
      .replace(/\]/g, "\\]");
  }

  function escapeMarkdownParagraphLineStarts(value) {
    return String(value || "")
      .split("\n")
      .map((line) =>
        line
          .replace(/^(-\s+)/, "\\$1")
          .replace(/^(\d+\.\s+)/, "\\$1")
          .replace(/^(```)/, "\\$1")
      )
      .join("\n");
  }

  function serializeRichComposerPlainTextNodes(nodes = []) {
    return nodes
      .map((node) => {
        if (!node) {
          return "";
        }
        if (node.type === "text") {
          return stripComposerZeroWidthSpaces(node.value);
        }
        if (node.type === "element" && node.tag === "br") {
          return "\n";
        }
        return serializeRichComposerPlainTextNodes(node.children || []);
      })
      .join("");
  }

  function wrapSerializedInlineMarkdown(marker, inner) {
    if (!marker || !inner) {
      return inner || "";
    }
    const leadingWhitespaceMatch = inner.match(/^\s+/);
    const trailingWhitespaceMatch = inner.match(/\s+$/);
    const leadingWhitespace = leadingWhitespaceMatch ? leadingWhitespaceMatch[0] : "";
    const trailingWhitespace = trailingWhitespaceMatch ? trailingWhitespaceMatch[0] : "";
    const core = inner.slice(leadingWhitespace.length, inner.length - trailingWhitespace.length);
    if (!core) {
      return inner;
    }
    return `${leadingWhitespace}${marker}${core}${marker}${trailingWhitespace}`;
  }

  function serializeRichComposerInlineNodes(nodes = []) {
    return nodes
      .map((node) => {
        if (!node) {
          return "";
        }
        if (node.type === "text") {
          return escapeMarkdownLiteralText(stripComposerZeroWidthSpaces(node.value));
        }
        if (node.type !== "element") {
          return "";
        }
        switch (node.tag) {
          case "br":
            return "\n";
          case "strong": {
            const inner = serializeRichComposerInlineNodes(node.children || []);
            return wrapSerializedInlineMarkdown("**", inner);
          }
          case "em": {
            const inner = serializeRichComposerInlineNodes(node.children || []);
            return wrapSerializedInlineMarkdown("*", inner);
          }
          case "a": {
            const href = sanitizeUrl(node.attrs?.href);
            const label = serializeRichComposerInlineNodes(node.children || []);
            return href && label ? `[${label}](${href})` : label;
          }
          case "code": {
            const codeText = serializeRichComposerPlainTextNodes(node.children || []);
            return codeText ? `\`${codeText}\`` : "";
          }
          default:
            return serializeRichComposerInlineNodes(node.children || []);
        }
      })
      .join("");
  }

  function serializeRichComposerBlockNode(node) {
    if (!node) {
      return "";
    }
    if (node.type === "text") {
      return escapeMarkdownParagraphLineStarts(
        escapeMarkdownLiteralText(stripComposerZeroWidthSpaces(node.value))
      ).trim();
    }
    if (node.type !== "element") {
      return "";
    }

    switch (node.tag) {
      case "ul":
        return node.children
          .filter((child) => child?.type === "element" && child.tag === "li")
          .map((child) => serializeRichComposerInlineNodes(child.children || []).trim())
          .filter(Boolean)
          .map((item) => `- ${item}`)
          .join("\n")
          .trim();
      case "ol":
        return node.children
          .filter((child) => child?.type === "element" && child.tag === "li")
          .map((child) => serializeRichComposerInlineNodes(child.children || []).trim())
          .filter(Boolean)
          .map((item, index) => `${index + 1}. ${item}`)
          .join("\n")
          .trim();
      case "pre": {
        const codeNode =
          (node.children || []).find((child) => child?.type === "element" && child.tag === "code") ||
          node;
        const className = String(codeNode.attrs?.class || "");
        const languageMatch = className.match(/language-([A-Za-z0-9_+-]+)/i);
        const language = languageMatch ? String(languageMatch[1] || "").trim().toLowerCase() : "";
        const codeText = stripComposerZeroWidthSpaces(
          serializeRichComposerPlainTextNodes(codeNode.children || [])
        ).replace(/\n$/, "");
        if (!codeText) {
          return "";
        }
        return `\`\`\`${language}\n${codeText}\n\`\`\``.trim();
      }
      case "p":
      case "div": {
        const hasNestedBlocks = (node.children || []).some(
          (child) => child?.type === "element" && isRichComposerBlockTag(child.tag)
        );
        if (hasNestedBlocks) {
          return serializeRichComposerRootNodes(node.children || []);
        }
        return escapeMarkdownParagraphLineStarts(
          serializeRichComposerInlineNodes(node.children || [])
        ).trim();
      }
      default:
        return escapeMarkdownParagraphLineStarts(
          serializeRichComposerInlineNodes(node.children || [])
        ).trim();
    }
  }

  function serializeRichComposerRootNodes(nodes = []) {
    const parts = [];
    let inlineBuffer = [];

    const flushInlineBuffer = () => {
      if (inlineBuffer.length === 0) {
        return;
      }
      const serialized = escapeMarkdownParagraphLineStarts(
        serializeRichComposerInlineNodes(inlineBuffer)
      )
        .replace(/\n{3,}/g, "\n\n")
        .trim();
      if (serialized) {
        parts.push(serialized);
      }
      inlineBuffer = [];
    };

    nodes.forEach((node) => {
      if (node?.type === "element" && isRichComposerBlockTag(node.tag)) {
        flushInlineBuffer();
        const block = serializeRichComposerBlockNode(node);
        if (block) {
          parts.push(block);
        }
        return;
      }
      inlineBuffer.push(node);
    });

    flushInlineBuffer();
    return parts.join("\n\n").replace(/\n{3,}/g, "\n\n").trim();
  }

  function serializeRichComposerHtmlToMarkdown(value) {
    const normalizedHtml = normalizeRichComposerHtmlString(value);
    if (!normalizedHtml) {
      return "";
    }
    const parsed = parseRichComposerHtmlFragment(normalizedHtml);
    return serializeRichComposerRootNodes(parsed.children || []);
  }

  function isRichComposerPlainTextCarrierNode(node) {
    if (!node) {
      return false;
    }
    if (node.type === "text") {
      return stripComposerZeroWidthSpaces(String(node.value || "")).trim().length > 0;
    }
    if (node.type !== "element") {
      return false;
    }
    if (isRichComposerEmptyLineBlockNode(node)) {
      return true;
    }
    const normalizedTag = String(node.tag || "").toLowerCase();
    if (["pre", "ul", "ol"].includes(normalizedTag)) {
      return false;
    }
    if (["p", "div"].includes(normalizedTag)) {
      return !(node.children || []).some(
        (child) => child?.type === "element" && isRichComposerBlockTag(child.tag)
      );
    }
    return stripComposerZeroWidthSpaces(serializeRichComposerPlainTextNodes([node])).trim().length > 0;
  }

  function ensureRichComposerEditableLinesAroundCodeBlocksHtml(value) {
    const normalizedHtml = normalizeRichComposerHtmlString(value);
    if (!normalizedHtml) {
      return "";
    }
    const parsed = parseRichComposerHtmlFragment(normalizedHtml);
    const normalizedNodes = normalizeRichComposerParsedNodes(parsed.children || []);
    const ensuredNodes = [];

    normalizedNodes.forEach((node, index) => {
      const normalizedTag = String(node?.tag || "").toLowerCase();
      if (node?.type === "element" && normalizedTag === "pre") {
        const previousNode = ensuredNodes[ensuredNodes.length - 1] || null;
        if (!isRichComposerPlainTextCarrierNode(previousNode)) {
          ensuredNodes.push(buildRichComposerEmptyLineBlockNode());
        }
        ensuredNodes.push(node);
        const nextNode = normalizedNodes[index + 1] || null;
        if (!isRichComposerPlainTextCarrierNode(nextNode)) {
          ensuredNodes.push(buildRichComposerEmptyLineBlockNode());
        }
        return;
      }
      ensuredNodes.push(node);
    });

    return normalizeRichComposerHtmlString(renderRichComposerHtmlNodes(ensuredNodes));
  }

  function buildRichComposerPlainTextBreakNodes(value) {
    const nodes = [];
    String(value || "")
      .split("\n")
      .forEach((line, index) => {
        if (index > 0) {
          nodes.push({
            type: "element",
            tag: "br",
            attrs: {},
            children: [],
          });
        }
        if (line) {
          nodes.push({
            type: "text",
            value: line,
          });
        }
      });
    return nodes;
  }

  function unwrapRichComposerCodeBlockHtml(value) {
    const normalizedHtml = normalizeRichComposerHtmlString(value);
    if (!normalizedHtml) {
      return "";
    }
    const parsed = parseRichComposerHtmlFragment(normalizedHtml);
    const unwrappedNodes = [];
    normalizeRichComposerParsedNodes(parsed.children || []).forEach((node) => {
      const normalizedTag = String(node?.tag || "").toLowerCase();
      if (node?.type === "element" && normalizedTag === "pre") {
        const codeNode =
          (node.children || []).find(
            (child) => child?.type === "element" && String(child.tag || "").toLowerCase() === "code"
          ) || node;
        const codeText = stripComposerZeroWidthSpaces(
          serializeRichComposerPlainTextNodes(codeNode.children || [])
        ).replace(/\n$/, "");
        unwrappedNodes.push(...buildRichComposerPlainTextBreakNodes(codeText));
        return;
      }
      unwrappedNodes.push(node);
    });
    return normalizeRichComposerHtmlString(renderRichComposerHtmlNodes(unwrappedNodes));
  }

  function buildRichComposerHtmlFromMarkdown(value) {
    const markdown = String(value || "").trim();
    if (!markdown) {
      return "";
    }
    return ensureRichComposerEditableLinesAroundCodeBlocksHtml(
      String(renderMarkdownMessage(markdown) || "")
        .replace(/ target="_blank"/g, "")
        .replace(/ rel="noopener noreferrer"/g, "")
    );
  }

  function unwrapRichComposerListHtml(value) {
    const normalizedHtml = normalizeRichComposerHtmlString(value);
    if (!normalizedHtml) {
      return "";
    }
    const parsed = parseRichComposerHtmlFragment(normalizedHtml);
    const unwrappedNodes = [];
    normalizeRichComposerParsedNodes(parsed.children || []).forEach((node) => {
      const normalizedTag = String(node?.tag || "").toLowerCase();
      if (node?.type === "element" && ["ul", "ol"].includes(normalizedTag)) {
        const items = (node.children || []).filter(
          (child) => child?.type === "element" && String(child.tag || "").toLowerCase() === "li"
        );
        items.forEach((item, index) => {
          if (index > 0) {
            unwrappedNodes.push({
              type: "element",
              tag: "br",
              attrs: {},
              children: [],
            });
          }
          unwrappedNodes.push(...(item.children || []));
        });
        return;
      }
      unwrappedNodes.push(node);
    });
    return normalizeRichComposerHtmlString(renderRichComposerHtmlNodes(unwrappedNodes));
  }

  function unwrapRichComposerInlineTagNodes(nodes = [], tagName) {
    const normalizedTagName = String(tagName || "").trim().toLowerCase();
    const unwrappedNodes = [];
    (Array.isArray(nodes) ? nodes : []).forEach((node) => {
      if (!node) {
        return;
      }
      if (node.type !== "element") {
        unwrappedNodes.push(node);
        return;
      }
      const normalizedNode = {
        ...node,
        children: unwrapRichComposerInlineTagNodes(node.children || [], normalizedTagName),
      };
      if (String(normalizedNode.tag || "").toLowerCase() === normalizedTagName) {
        unwrappedNodes.push(...(normalizedNode.children || []));
        return;
      }
      unwrappedNodes.push(normalizedNode);
    });
    return unwrappedNodes;
  }

  function unwrapRichComposerInlineTagHtml(value, tagName) {
    const normalizedHtml = normalizeRichComposerHtmlString(value);
    const normalizedTagName = String(tagName || "").trim().toLowerCase();
    if (!normalizedHtml || !normalizedTagName) {
      return normalizedHtml;
    }
    const parsed = parseRichComposerHtmlFragment(normalizedHtml);
    const normalizedNodes = normalizeRichComposerParsedNodes(parsed.children || []);
    const unwrappedNodes = unwrapRichComposerInlineTagNodes(normalizedNodes, normalizedTagName);
    return normalizeRichComposerHtmlString(renderRichComposerHtmlNodes(unwrappedNodes));
  }

  function isTextComposerElement(element) {
    return Boolean(
      element &&
        typeof element === "object" &&
        typeof element.focus === "function" &&
        typeof element.value === "string"
    );
  }

  function isRichTextComposerElement(element) {
    return Boolean(
      element &&
        typeof element === "object" &&
        typeof element.focus === "function" &&
        typeof element.innerHTML === "string" &&
        typeof element.getAttribute === "function"
    );
  }

  function isComposerElementDisabled(element) {
    if (!element) {
      return true;
    }
    if (typeof element.disabled === "boolean") {
      return element.disabled;
    }
    return String(element.getAttribute?.("contenteditable") || "").toLowerCase() === "false";
  }

  function getComposerSelectionObject() {
    if (globalThis.window?.getSelection) {
      return globalThis.window.getSelection();
    }
    if (globalThis.document?.getSelection) {
      return globalThis.document.getSelection();
    }
    return null;
  }

  function getComposerNodePath(root, node) {
    const path = [];
    let current = node;
    while (current && current !== root) {
      const parent = current.parentNode;
      if (!parent) {
        return null;
      }
      const index = Array.from(parent.childNodes || []).indexOf(current);
      if (index < 0) {
        return null;
      }
      path.unshift(index);
      current = parent;
    }
    return current === root ? path : null;
  }

  function resolveComposerNodePath(root, path = []) {
    let current = root;
    for (const segment of path) {
      if (!current?.childNodes || !current.childNodes[segment]) {
        return null;
      }
      current = current.childNodes[segment];
    }
    return current;
  }

  function clampComposerNodeOffset(node, offset) {
    const normalizedOffset = Number.isFinite(offset) ? Number(offset) : 0;
    if (!node) {
      return 0;
    }
    if (node.nodeType === 3) {
      return Math.max(0, Math.min(String(node.textContent || "").length, normalizedOffset));
    }
    return Math.max(0, Math.min(node.childNodes?.length || 0, normalizedOffset));
  }

  function captureRichComposerSelectionBookmark(element) {
    const selection = getComposerSelectionObject();
    if (!isRichTextComposerElement(element) || !selection || selection.rangeCount === 0) {
      return null;
    }
    const range = selection.getRangeAt(0);
    if (!element.contains(range.startContainer) || !element.contains(range.endContainer)) {
      return null;
    }
    return {
      startPath: getComposerNodePath(element, range.startContainer),
      startOffset: range.startOffset,
      endPath: getComposerNodePath(element, range.endContainer),
      endOffset: range.endOffset,
    };
  }

  function restoreRichComposerSelectionBookmark(element, bookmark) {
    if (!isRichTextComposerElement(element) || !bookmark || !globalThis.document?.createRange) {
      return false;
    }
    const selection = getComposerSelectionObject();
    if (!selection) {
      return false;
    }
    const startNode = resolveComposerNodePath(element, bookmark.startPath);
    const endNode = resolveComposerNodePath(element, bookmark.endPath);
    if (!startNode || !endNode) {
      return false;
    }
    const range = globalThis.document.createRange();
    range.setStart(startNode, clampComposerNodeOffset(startNode, bookmark.startOffset));
    range.setEnd(endNode, clampComposerNodeOffset(endNode, bookmark.endOffset));
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
  }

  function captureComposerPreservationState(element) {
    if (!element || isComposerElementDisabled(element)) {
      return null;
    }
    if (isTextComposerElement(element)) {
      if (globalThis.document?.activeElement !== element) {
        return null;
      }
      return {
        kind: "text",
        selectionStart:
          typeof element.selectionStart === "number" ? element.selectionStart : element.value.length,
        selectionEnd:
          typeof element.selectionEnd === "number" ? element.selectionEnd : element.value.length,
        selectionDirection:
          typeof element.selectionDirection === "string" ? element.selectionDirection : "none",
        scrollTop: typeof element.scrollTop === "number" ? element.scrollTop : 0,
      };
    }
    if (!isRichTextComposerElement(element)) {
      return null;
    }
    const activeInside =
      globalThis.document?.activeElement === element ||
      (typeof element.contains === "function" && element.contains(globalThis.document?.activeElement));
    if (!activeInside) {
      return null;
    }
    return {
      kind: "rich",
      selectionBookmark: captureRichComposerSelectionBookmark(element),
      scrollTop: typeof element.scrollTop === "number" ? element.scrollTop : 0,
    };
  }

  function restoreComposerPreservationState(element, snapshot) {
    if (!element || !snapshot || isComposerElementDisabled(element)) {
      return;
    }
    try {
      element.focus({ preventScroll: true });
    } catch {
      element.focus();
    }
    if (snapshot.kind === "text" && isTextComposerElement(element)) {
      if (typeof element.setSelectionRange === "function") {
        element.setSelectionRange(
          snapshot.selectionStart,
          snapshot.selectionEnd,
          snapshot.selectionDirection || "none"
        );
      }
    }
    if (snapshot.kind === "rich" && isRichTextComposerElement(element)) {
      restoreRichComposerSelectionBookmark(element, snapshot.selectionBookmark);
    }
    if (typeof snapshot.scrollTop === "number" && typeof element.scrollTop === "number") {
      element.scrollTop = snapshot.scrollTop;
    }
  }

  function findNearestComposerAncestor(node, tagName, root) {
    let current = node;
    const normalizedTagName = String(tagName || "").toLowerCase();
    while (current && current !== root) {
      if (current.nodeType === 1 && String(current.tagName || "").toLowerCase() === normalizedTagName) {
        return current;
      }
      current = current.parentNode;
    }
    return null;
  }

  function setComposerSelectionRange(range) {
    const selection = getComposerSelectionObject();
    if (!selection || !range) {
      return false;
    }
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
  }

  function selectComposerNodeContents(node) {
    if (!node || !globalThis.document?.createRange) {
      return false;
    }
    const range = globalThis.document.createRange();
    range.selectNodeContents(node);
    return setComposerSelectionRange(range);
  }

  function selectComposerNodes(nodes = []) {
    const normalizedNodes = (Array.isArray(nodes) ? nodes : []).filter(Boolean);
    if (normalizedNodes.length === 0 || !globalThis.document?.createRange) {
      return false;
    }
    if (normalizedNodes.length === 1) {
      return selectComposerNodeContents(normalizedNodes[0]);
    }
    const range = globalThis.document.createRange();
    range.setStartBefore(normalizedNodes[0]);
    range.setEndAfter(normalizedNodes[normalizedNodes.length - 1]);
    return setComposerSelectionRange(range);
  }

  function placeComposerCaretAfterNode(node) {
    if (!node || !globalThis.document?.createRange) {
      return false;
    }
    const range = globalThis.document.createRange();
    range.setStartAfter(node);
    range.collapse(true);
    return setComposerSelectionRange(range);
  }

  function placeComposerCaretInsideNode(node, offset = 0) {
    if (!node || !globalThis.document?.createRange) {
      return false;
    }
    const range = globalThis.document.createRange();
    range.setStart(node, clampComposerNodeOffset(node, offset));
    range.collapse(true);
    return setComposerSelectionRange(range);
  }

  function placeComposerCaretAtEnd(node) {
    if (!node) {
      return false;
    }
    let current = node;
    while (current?.nodeType === 1 && current.childNodes?.length) {
      current = current.lastChild;
    }
    if (!current) {
      return false;
    }
    if (current.nodeType === 3) {
      return placeComposerCaretInsideNode(current, String(current.textContent || "").length);
    }
    if (current.nodeType === 1 && String(current.tagName || "").toLowerCase() === "br") {
      return placeComposerCaretAfterNode(current);
    }
    return placeComposerCaretInsideNode(current, current.childNodes?.length || 0);
  }

  function placeComposerCaretAtStart(node) {
    if (!node) {
      return false;
    }
    let current = node;
    while (current?.nodeType === 1 && current.childNodes?.length) {
      current = current.firstChild;
    }
    if (!current) {
      return false;
    }
    if (current.nodeType === 3) {
      return placeComposerCaretInsideNode(current, 0);
    }
    if (current.nodeType === 1 && String(current.tagName || "").toLowerCase() === "br") {
      return placeComposerCaretAfterNode(current);
    }
    return placeComposerCaretInsideNode(current, 0);
  }

  function getComposerSelectionRange(element) {
    const selection = getComposerSelectionObject();
    if (!isRichTextComposerElement(element) || !selection || selection.rangeCount === 0) {
      return null;
    }
    const range = selection.getRangeAt(0);
    if (!element.contains(range.startContainer) || !element.contains(range.endContainer)) {
      return null;
    }
    return range;
  }

  function getComposerRangeContextNode(range, root) {
    if (!range) {
      return null;
    }
    if (!range.collapsed) {
      return range.commonAncestorContainer || range.startContainer;
    }
    const startContainer = range.startContainer || null;
    if (!startContainer || startContainer.nodeType !== 1) {
      return startContainer;
    }
    const childNodes = Array.from(startContainer.childNodes || []);
    if (childNodes.length === 0) {
      return startContainer;
    }
    const previousChild =
      range.startOffset > 0 && range.startOffset - 1 < childNodes.length
        ? childNodes[range.startOffset - 1]
        : null;
    const nextChild =
      range.startOffset >= 0 && range.startOffset < childNodes.length
        ? childNodes[range.startOffset]
        : null;
    const candidate = previousChild || nextChild || startContainer;
    if (!root || candidate === root) {
      return candidate;
    }
    return typeof root.contains === "function" && root.contains(candidate) ? candidate : startContainer;
  }

  function getComposerCollapsedRangeAdjacentNode(range) {
    if (!range?.collapsed) {
      return null;
    }
    const startContainer = range.startContainer || null;
    if (!startContainer || startContainer.nodeType !== 1) {
      return null;
    }
    const childNodes = Array.from(startContainer.childNodes || []);
    if (childNodes.length === 0) {
      return null;
    }
    if (range.startOffset > 0 && range.startOffset - 1 < childNodes.length) {
      return {
        node: childNodes[range.startOffset - 1],
        affinity: "after",
      };
    }
    if (range.startOffset >= 0 && range.startOffset < childNodes.length) {
      return {
        node: childNodes[range.startOffset],
        affinity: "before",
      };
    }
    return null;
  }

  function getComposerCollapsedListContext(range, root) {
    if (!range?.collapsed) {
      return {
        listNode: null,
        listItem: null,
      };
    }
    const startContainer = range.startContainer || null;
    const directListItem =
      (startContainer?.nodeType === 1 && String(startContainer.tagName || "").toLowerCase() === "li"
        ? startContainer
        : null) || findNearestComposerAncestor(startContainer, "li", root);
    const directListNode =
      (startContainer?.nodeType === 1 &&
      ["ul", "ol"].includes(String(startContainer.tagName || "").toLowerCase())
        ? startContainer
        : null) ||
      findNearestComposerAncestor(startContainer, "ul", root) ||
      findNearestComposerAncestor(startContainer, "ol", root);
    if (directListItem) {
      return {
        listNode:
          directListNode ||
          findNearestComposerAncestor(directListItem, "ul", root) ||
          findNearestComposerAncestor(directListItem, "ol", root),
        listItem: directListItem,
      };
    }
    const adjacent = getComposerCollapsedRangeAdjacentNode(range);
    if (!adjacent?.node) {
      return {
        listNode: directListNode || null,
        listItem: null,
      };
    }
    const adjacentNode = adjacent.node;
    const adjacentListNode =
      (adjacentNode.nodeType === 1 &&
      ["ul", "ol"].includes(String(adjacentNode.tagName || "").toLowerCase())
        ? adjacentNode
        : null) ||
      findNearestComposerAncestor(adjacentNode, "ul", root) ||
      findNearestComposerAncestor(adjacentNode, "ol", root);
    if (!adjacentListNode) {
      return {
        listNode: directListNode || null,
        listItem: null,
      };
    }
    const adjacentListItem =
      (adjacentNode.nodeType === 1 && String(adjacentNode.tagName || "").toLowerCase() === "li"
        ? adjacentNode
        : null) || findNearestComposerAncestor(adjacentNode, "li", root);
    if (adjacentListItem) {
      return {
        listNode: adjacentListNode,
        listItem: adjacentListItem,
      };
    }
    const listItems = Array.from(adjacentListNode.childNodes || []).filter(
      (child) => child?.nodeType === 1 && String(child.tagName || "").toLowerCase() === "li"
    );
    if (listItems.length === 0) {
      return {
        listNode: adjacentListNode,
        listItem: null,
      };
    }
    return {
      listNode: adjacentListNode,
      listItem: adjacent.affinity === "after" ? listItems[listItems.length - 1] : listItems[0],
    };
  }

  function getComposerRangeSelectedSingleNode(range, root) {
    if (
      !range ||
      range.collapsed ||
      range.startContainer !== range.endContainer ||
      range.startContainer?.nodeType !== 1
    ) {
      return null;
    }
    if (range.endOffset - range.startOffset !== 1) {
      return null;
    }
    const candidate = range.startContainer.childNodes?.[range.startOffset] || null;
    if (!candidate || !root) {
      return null;
    }
    if (candidate === root) {
      return candidate;
    }
    return typeof root.contains === "function" && root.contains(candidate) ? candidate : null;
  }

  function doesComposerRangeCoverNodeContents(range, node) {
    if (!range || !node || !globalThis.document?.createRange || typeof range.compareBoundaryPoints !== "function") {
      return false;
    }
    const nodeRange = globalThis.document.createRange();
    nodeRange.selectNodeContents(node);
    const startToStart = typeof Range === "function" ? Range.START_TO_START : 0;
    const endToEnd = typeof Range === "function" ? Range.END_TO_END : 2;
    return (
      range.compareBoundaryPoints(startToStart, nodeRange) === 0 &&
      range.compareBoundaryPoints(endToEnd, nodeRange) === 0
    );
  }

  function findComposerFullySelectedInlineFormatNode(range, tagName, root) {
    const normalizedTagName = String(tagName || "").trim().toLowerCase();
    const selectedNode = getComposerRangeSelectedSingleNode(range, root);
    if (
      selectedNode?.nodeType === 1 &&
      String(selectedNode.tagName || "").toLowerCase() === normalizedTagName
    ) {
      return selectedNode;
    }
    const startAncestor = findNearestComposerAncestor(range.startContainer, normalizedTagName, root);
    const endAncestor = findNearestComposerAncestor(range.endContainer, normalizedTagName, root);
    if (
      startAncestor &&
      startAncestor === endAncestor &&
      doesComposerRangeCoverNodeContents(range, startAncestor)
    ) {
      return startAncestor;
    }
    return null;
  }

  function findComposerFullySelectedCodeBlockNode(range, root) {
    const selectedNode = getComposerRangeSelectedSingleNode(range, root);
    if (selectedNode?.nodeType === 1) {
      const selectedTagName = String(selectedNode.tagName || "").toLowerCase();
      if (selectedTagName === "pre") {
        return selectedNode;
      }
      if (selectedTagName === "code") {
        return findNearestComposerAncestor(selectedNode, "pre", root);
      }
    }
    const selectedCodeNode = findComposerFullySelectedInlineFormatNode(range, "code", root);
    return selectedCodeNode ? findNearestComposerAncestor(selectedCodeNode, "pre", root) : null;
  }

  function deriveRichComposerToolbarState(context = {}) {
    return {
      bold: Boolean(context.bold),
      italic: Boolean(context.italic),
      list: Boolean(context.list),
      codeBlock: Boolean(context.codeBlock),
    };
  }

  function getRichComposerSelectionContext(element) {
    const range = getComposerSelectionRange(element);
    if (!range) {
      return buildDefaultComposerToolbarState();
    }
    const contextNode = getComposerRangeContextNode(range, element);
    const collapsedListContext = range.collapsed
      ? getComposerCollapsedListContext(range, element)
      : null;
    return deriveRichComposerToolbarState({
      bold: Boolean(findNearestComposerAncestor(contextNode, "strong", element)),
      italic: Boolean(findNearestComposerAncestor(contextNode, "em", element)),
      list: Boolean(
        collapsedListContext?.listItem || findNearestComposerAncestor(contextNode, "li", element)
      ),
      codeBlock: Boolean(findNearestComposerAncestor(contextNode, "code", element)),
    });
  }

  function applyComposerToolbarStateToButtons(root, toolbarState = buildDefaultComposerToolbarState()) {
    if (!root?.querySelectorAll) {
      return;
    }
    root.querySelectorAll("[data-composer-markdown-action]").forEach((button) => {
      const stateKey = normalizeComposerToolbarActionStateKey(
        button.getAttribute("data-composer-markdown-action")
      );
      const isActive = Boolean(toolbarState[stateKey]);
      button.classList?.toggle("is-active", isActive);
      if (typeof button.setAttribute === "function") {
        button.setAttribute("aria-pressed", isActive ? "true" : "false");
      }
    });
  }

  function isRichComposerDomNodeStructurallyEmpty(node) {
    if (!node) {
      return true;
    }
    if (node.nodeType === 3) {
      return stripComposerZeroWidthSpaces(String(node.textContent || "")).trim().length === 0;
    }
    if (node.nodeType !== 1) {
      return true;
    }
    if (String(node.tagName || "").toLowerCase() === "br") {
      return true;
    }
    return Array.from(node.childNodes || []).every((child) => isRichComposerDomNodeStructurallyEmpty(child));
  }

  function findNearestEmptyComposerBlockAncestor(node, root) {
    let current = node;
    while (current && current !== root) {
      if (
        current.nodeType === 1 &&
        ["p", "div"].includes(String(current.tagName || "").toLowerCase()) &&
        isRichComposerDomNodeStructurallyEmpty(current)
      ) {
        return current;
      }
      current = current.parentNode;
    }
    return null;
  }

  function buildComposerEmptyLineBlock() {
    if (!globalThis.document?.createElement || !globalThis.document?.createTextNode) {
      return null;
    }
    const line = globalThis.document.createElement("div");
    const marker = globalThis.document.createElement("span");
    marker.setAttribute("data-composer-empty-line", "true");
    marker.appendChild(globalThis.document.createTextNode("\u200B"));
    line.appendChild(marker);
    return line;
  }

  function isComposerEmptyLineInlineMarker(node) {
    return (
      node?.nodeType === 1 &&
      String(node.tagName || "").toLowerCase() === "span" &&
      String(node.getAttribute?.("data-composer-empty-line") || "").toLowerCase() === "true"
    );
  }

  function isComposerEmptyLineBlock(node) {
    if (
      node?.nodeType !== 1 ||
      !["p", "div"].includes(String(node.tagName || "").toLowerCase())
    ) {
      return false;
    }
    const children = Array.from(node.childNodes || []);
    return (
      children.length > 0 &&
      children.every(
        (child) => child?.nodeType === 3 || isComposerEmptyLineInlineMarker(child)
      ) &&
      stripComposerZeroWidthSpaces(String(node.textContent || "")).trim().length === 0
    );
  }

  function isComposerPlainTextCarrierNode(node) {
    if (!node) {
      return false;
    }
    if (node.nodeType === 3) {
      return stripComposerZeroWidthSpaces(String(node.textContent || "")).trim().length > 0;
    }
    if (node.nodeType !== 1) {
      return false;
    }
    if (isComposerEmptyLineBlock(node)) {
      return true;
    }
    const normalizedTag = String(node.tagName || "").toLowerCase();
    if (["pre", "ul", "ol"].includes(normalizedTag)) {
      return false;
    }
    if (["p", "div"].includes(normalizedTag)) {
      return !Array.from(node.childNodes || []).some(
        (child) => child?.nodeType === 1 && isRichComposerBlockTag(child.tagName)
      );
    }
    return stripComposerZeroWidthSpaces(String(node.textContent || "")).trim().length > 0;
  }

  function ensureComposerAdjacentTextLine(codeBlock, root, position = "after") {
    if (!codeBlock?.parentNode || !root) {
      return null;
    }
    const sibling = position === "before" ? codeBlock.previousSibling : codeBlock.nextSibling;
    if (isComposerPlainTextCarrierNode(sibling)) {
      return sibling;
    }
    const spacerLine = buildComposerEmptyLineBlock();
    if (!spacerLine) {
      return null;
    }
    codeBlock.parentNode.insertBefore(
      spacerLine,
      position === "before" ? codeBlock : codeBlock.nextSibling || null
    );
    return spacerLine;
  }

  function ensureComposerCaretInAdjacentTextLine(codeBlock, root, position = "after") {
    const line = ensureComposerAdjacentTextLine(codeBlock, root, position);
    if (!line) {
      return false;
    }
    if (position === "before") {
      return placeComposerCaretAtEnd(line);
    }
    if (isComposerEmptyLineBlock(line)) {
      const marker = line.querySelector?.('[data-composer-empty-line="true"]');
      const markerText = marker?.firstChild || null;
      if (markerText?.nodeType === 3) {
        return placeComposerCaretInsideNode(markerText, 1);
      }
    }
    return placeComposerCaretAtStart(line);
  }

  function removeComposerAdjacentCodeBlockSpacerLine(node) {
    if (isComposerEmptyLineBlock(node)) {
      node.remove();
    }
  }

  function replaceComposerNodeWithHtml(node, html) {
    if (!node?.parentNode || !globalThis.document?.createElement || !globalThis.document?.createDocumentFragment) {
      return [];
    }
    const container = globalThis.document.createElement("div");
    container.innerHTML = String(html || "");
    const insertedNodes = Array.from(container.childNodes || []);
    const fragment = globalThis.document.createDocumentFragment();
    insertedNodes.forEach((child) => fragment.appendChild(child));
    node.parentNode.insertBefore(fragment, node);
    node.parentNode.removeChild(node);
    return insertedNodes;
  }

  function replaceComposerElementContentsWithHtml(element, html) {
    if (!element || !globalThis.document?.createElement || !globalThis.document?.createDocumentFragment) {
      return [];
    }
    const container = globalThis.document.createElement("div");
    container.innerHTML = String(html || "");
    const insertedNodes = Array.from(container.childNodes || []);
    const fragment = globalThis.document.createDocumentFragment();
    insertedNodes.forEach((child) => fragment.appendChild(child));
    element.innerHTML = "";
    element.appendChild(fragment);
    return Array.from(element.childNodes || []);
  }

  function createComposerCaretMarkerElement() {
    if (!globalThis.document?.createElement) {
      return null;
    }
    const marker = globalThis.document.createElement("span");
    marker.setAttribute("data-composer-caret-marker", "true");
    return marker;
  }

  function isComposerCaretMarkerElement(node) {
    return (
      node?.nodeType === 1 &&
      String(node.tagName || "").toLowerCase() === "span" &&
      String(node.getAttribute?.("data-composer-caret-marker") || "").toLowerCase() === "true"
    );
  }

  function findComposerCaretMarkerInNode(node) {
    if (!node) {
      return null;
    }
    if (isComposerCaretMarkerElement(node)) {
      return node;
    }
    if (node.nodeType === 1 && typeof node.querySelector === "function") {
      return node.querySelector('[data-composer-caret-marker="true"]');
    }
    return null;
  }

  function findComposerCaretMarkerInNodes(nodes = []) {
    for (const node of Array.isArray(nodes) ? nodes : []) {
      const marker = findComposerCaretMarkerInNode(node);
      if (marker) {
        return marker;
      }
    }
    return null;
  }

  function restoreComposerCaretFromMarker(marker) {
    if (!marker) {
      return false;
    }
    const nextSibling = marker.nextSibling || null;
    const previousSibling = marker.previousSibling || null;
    let restored = false;
    if (nextSibling?.nodeType === 3) {
      restored = placeComposerCaretInsideNode(nextSibling, 0);
    } else if (nextSibling) {
      restored = placeComposerCaretAtStart(nextSibling);
    } else if (previousSibling?.nodeType === 3) {
      restored = placeComposerCaretInsideNode(previousSibling, String(previousSibling.textContent || "").length);
    } else if (previousSibling) {
      restored = placeComposerCaretAtEnd(previousSibling);
    } else if (marker.parentNode) {
      restored = placeComposerCaretInsideNode(marker.parentNode, 0);
    }
    marker.remove();
    return restored;
  }

  function findNearestComposerListConvertibleBlock(node, root) {
    let current = node;
    while (current && current !== root) {
      if (
        current.nodeType === 1 &&
        ["p", "div"].includes(String(current.tagName || "").toLowerCase()) &&
        !Array.from(current.childNodes || []).some(
          (child) => child?.nodeType === 1 && isRichComposerBlockTag(child.tagName)
        )
      ) {
        return current;
      }
      current = current.parentNode;
    }
    if (
      root?.nodeType === 1 &&
      !Array.from(root.childNodes || []).some(
        (child) => child?.nodeType === 1 && isRichComposerBlockTag(child.tagName)
      )
    ) {
      return root;
    }
    return null;
  }

  function runComposerSync(syncState, element, options = {}) {
    if (typeof syncState === "function") {
      return syncState(element, options);
    }
    return null;
  }

  function applyComposerInlineFormat(tagName, element, syncState) {
    const range = getComposerSelectionRange(element);
    if (!range) {
      return false;
    }
    if (range.collapsed) {
      const existing = findNearestComposerAncestor(range.startContainer, tagName, element);
      if (existing) {
        placeComposerCaretAfterNode(existing);
        runComposerSync(syncState, element);
        return true;
      }
      const wrapper = globalThis.document.createElement(tagName);
      const marker = globalThis.document.createTextNode("\u200B");
      wrapper.appendChild(marker);
      range.insertNode(wrapper);
      placeComposerCaretInsideNode(marker, 1);
      runComposerSync(syncState, element);
      return true;
    }
    const toggleTarget = findComposerFullySelectedInlineFormatNode(range, tagName, element);
    if (toggleTarget) {
      const insertedNodes = replaceComposerNodeWithHtml(
        toggleTarget,
        unwrapRichComposerInlineTagHtml(toggleTarget.outerHTML || "", tagName)
      );
      const lastInsertedNode = insertedNodes[insertedNodes.length - 1] || null;
      if (!placeComposerCaretAtEnd(lastInsertedNode) && !selectComposerNodes(insertedNodes)) {
        placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
      }
      const selectionBookmark = captureRichComposerSelectionBookmark(element);
      runComposerSync(syncState, element, { selectionBookmark });
      return true;
    }
    const wrapper = globalThis.document.createElement(tagName);
    wrapper.appendChild(range.extractContents());
    range.insertNode(wrapper);
    placeComposerCaretAtEnd(wrapper);
    const selectionBookmark = captureRichComposerSelectionBookmark(element);
    runComposerSync(syncState, element, { selectionBookmark });
    return true;
  }

  function applyComposerListFormat(element, syncState) {
    const range = getComposerSelectionRange(element);
    if (!range) {
      return false;
    }
    const contextNode = getComposerRangeContextNode(range, element);
    const collapsedListContext = range.collapsed
      ? getComposerCollapsedListContext(range, element)
      : null;
    const currentListItem = collapsedListContext?.listItem || null;
    const existingList =
      collapsedListContext?.listNode ||
      findNearestComposerAncestor(contextNode, "ul", element) ||
      findNearestComposerAncestor(contextNode, "ol", element);
    if (existingList && currentListItem) {
      const marker = createComposerCaretMarkerElement();
      if (!marker) {
        return false;
      }
      range.deleteContents();
      range.insertNode(marker);
      const exitHtml = exitRichComposerCurrentListItemHtml(existingList.outerHTML || "");
      const insertedNodes = replaceComposerNodeWithHtml(existingList, exitHtml);
      const restoredMarker =
        findComposerCaretMarkerInNodes(insertedNodes) || findComposerCaretMarkerInNode(element);
      if (!restoreComposerCaretFromMarker(restoredMarker)) {
        const exitBlock =
          insertedNodes.find(
            (node) =>
              node?.nodeType === 1 && ["p", "div"].includes(String(node.tagName || "").toLowerCase())
          ) || null;
        if (exitBlock) {
          placeComposerCaretAtEnd(exitBlock);
        }
      }
      const selectionBookmark = captureRichComposerSelectionBookmark(element);
      runComposerSync(syncState, element, { selectionBookmark });
      return true;
    }
    if (existingList) {
      const insertedNodes = replaceComposerNodeWithHtml(
        existingList,
        unwrapRichComposerListHtml(existingList.outerHTML || "")
      );
      if (!selectComposerNodes(insertedNodes)) {
        placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
      }
      runComposerSync(syncState, element);
      return true;
    }
    const list = globalThis.document.createElement("ul");
    if (range.collapsed) {
      const marker = createComposerCaretMarkerElement();
      if (!marker) {
        return false;
      }
      range.deleteContents();
      range.insertNode(marker);
      const convertibleBlock =
        findNearestComposerListConvertibleBlock(marker, element) ||
        findNearestEmptyComposerBlockAncestor(marker, element) ||
        element;
      const targetHtml =
        convertibleBlock === element
          ? element.innerHTML
          : String(convertibleBlock.outerHTML || "");
      const wrappedHtml = wrapRichComposerBlockHtmlInList(targetHtml);
      const insertedNodes =
        convertibleBlock === element
          ? replaceComposerElementContentsWithHtml(element, wrappedHtml)
          : replaceComposerNodeWithHtml(convertibleBlock, wrappedHtml);
      const restoredMarker =
        findComposerCaretMarkerInNodes(insertedNodes) || findComposerCaretMarkerInNode(element);
      if (!restoreComposerCaretFromMarker(restoredMarker)) {
        const firstItem = element.querySelector?.("li") || null;
        if (firstItem) {
          placeComposerCaretAtEnd(firstItem);
        }
      }
      runComposerSync(syncState, element);
      return true;
    }
    const selectedText = String(range.toString() || "");
    if (selectedText.includes("\n")) {
      selectedText.split("\n").forEach((line) => {
        const item = globalThis.document.createElement("li");
        item.textContent = line;
        list.appendChild(item);
      });
    } else {
      const item = globalThis.document.createElement("li");
      item.appendChild(range.extractContents());
      list.appendChild(item);
    }
    range.insertNode(list);
    const firstItem = list.querySelector?.("li") || list.firstChild;
    if (firstItem) {
      selectComposerNodeContents(firstItem);
    }
    runComposerSync(syncState, element);
    return true;
  }

  function handleRichComposerListDeletion(event, element, { syncState } = {}) {
    const key = String(event?.key || "");
    if (!["Backspace", "Delete"].includes(key)) {
      return false;
    }
    const range = getComposerSelectionRange(element);
    if (!range || !range.collapsed) {
      return false;
    }
    const listItem = findNearestComposerAncestor(range.startContainer, "li", element);
    if (!listItem || !isRichComposerDomNodeStructurallyEmpty(listItem)) {
      return false;
    }

    event.preventDefault();
    const list = listItem.parentNode;
    const previousItem = listItem.previousElementSibling;
    const nextItem = listItem.nextElementSibling;
    listItem.remove();

    if (!list || !list.querySelector?.("li")) {
      list?.remove();
      runComposerSync(syncState, element);
      placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
      return true;
    }

    runComposerSync(syncState, element);
    if (previousItem) {
      placeComposerCaretAtEnd(previousItem);
      return true;
    }
    if (nextItem) {
      selectComposerNodeContents(nextItem);
      return true;
    }
    placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
    return true;
  }

  function applyComposerCodeBlockFormat(element, syncState) {
    const range = getComposerSelectionRange(element);
    if (!range) {
      return false;
    }
    const fullySelectedCodeBlock = findComposerFullySelectedCodeBlockNode(range, element);
    const existingCodeBlock =
      findNearestComposerAncestor(range.startContainer, "pre", element) || fullySelectedCodeBlock;
    if (range.collapsed && existingCodeBlock) {
      removeComposerAdjacentCodeBlockSpacerLine(existingCodeBlock.previousSibling);
      removeComposerAdjacentCodeBlockSpacerLine(existingCodeBlock.nextSibling);
      const insertedNodes = replaceComposerNodeWithHtml(
        existingCodeBlock,
        unwrapRichComposerCodeBlockHtml(existingCodeBlock.outerHTML || "")
      );
      if (!selectComposerNodes(insertedNodes)) {
        placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
      }
      runComposerSync(syncState, element);
      return true;
    }
    if (fullySelectedCodeBlock) {
      removeComposerAdjacentCodeBlockSpacerLine(fullySelectedCodeBlock.previousSibling);
      removeComposerAdjacentCodeBlockSpacerLine(fullySelectedCodeBlock.nextSibling);
      const insertedNodes = replaceComposerNodeWithHtml(
        fullySelectedCodeBlock,
        unwrapRichComposerCodeBlockHtml(existingCodeBlock.outerHTML || "")
      );
      if (!selectComposerNodes(insertedNodes)) {
        placeComposerCaretInsideNode(element, element.childNodes?.length || 0);
      }
      runComposerSync(syncState, element);
      return true;
    }
    const pre = globalThis.document.createElement("pre");
    const code = globalThis.document.createElement("code");
    if (range.collapsed) {
      const marker = globalThis.document.createTextNode("\u200B");
      code.appendChild(marker);
      pre.appendChild(code);
      const emptyBlockAncestor = findNearestEmptyComposerBlockAncestor(range.startContainer, element);
      if (emptyBlockAncestor?.parentNode) {
        const beforeLine = buildComposerEmptyLineBlock();
        const afterLine = buildComposerEmptyLineBlock();
        const fragment = globalThis.document.createDocumentFragment();
        if (beforeLine) {
          fragment.appendChild(beforeLine);
        }
        fragment.appendChild(pre);
        if (afterLine) {
          fragment.appendChild(afterLine);
        }
        emptyBlockAncestor.parentNode.insertBefore(fragment, emptyBlockAncestor);
        emptyBlockAncestor.remove();
      } else {
        range.insertNode(pre);
        ensureComposerAdjacentTextLine(pre, element, "before");
        ensureComposerAdjacentTextLine(pre, element, "after");
      }
      placeComposerCaretInsideNode(marker, 1);
    } else {
      code.textContent = String(range.toString() || "");
      pre.appendChild(code);
      range.deleteContents();
      range.insertNode(pre);
      ensureComposerAdjacentTextLine(pre, element, "before");
      ensureComposerAdjacentTextLine(pre, element, "after");
      selectComposerNodeContents(code);
    }
    runComposerSync(syncState, element);
    return true;
  }

  function handleRichComposerToolbarAction(action, element, { onAttach, syncState } = {}) {
    const normalizedAction = String(action || "").trim();
    if (!normalizedAction) {
      return false;
    }
    if (normalizedAction === "attach") {
      if (typeof onAttach === "function") {
        onAttach();
      }
      return false;
    }
    if (!isRichTextComposerElement(element) || isComposerElementDisabled(element)) {
      return false;
    }
    switch (normalizedAction) {
      case "bold":
        return applyComposerInlineFormat("strong", element, syncState);
      case "italic":
        return applyComposerInlineFormat("em", element, syncState);
      case "list":
        return applyComposerListFormat(element, syncState);
      case "code-block":
        return applyComposerCodeBlockFormat(element, syncState);
      default:
        return false;
    }
  }

  function insertComposerLineBreak(element, syncState) {
    const range = getComposerSelectionRange(element);
    if (!range) {
      return false;
    }
    range.deleteContents();
    const lineBreak = globalThis.document.createElement("br");
    range.insertNode(lineBreak);
    placeComposerCaretAfterNode(lineBreak);
    runComposerSync(syncState, element);
    return true;
  }

  function insertComposerPlainText(element, text, { preserveNewlines = true, syncState } = {}) {
    const range = getComposerSelectionRange(element);
    if (!range || !globalThis.document?.createTextNode) {
      return false;
    }
    range.deleteContents();
    const fragment = globalThis.document.createDocumentFragment();
    const parts = String(text || "").split("\n");
    parts.forEach((part, index) => {
      fragment.appendChild(globalThis.document.createTextNode(part));
      if (preserveNewlines && index < parts.length - 1) {
        fragment.appendChild(globalThis.document.createElement("br"));
      }
    });
    const lastNode = fragment.lastChild;
    range.insertNode(fragment);
    if (lastNode) {
      placeComposerCaretAfterNode(lastNode);
    }
    runComposerSync(syncState, element);
    return true;
  }

  function handleRichComposerShiftEnter(element, { syncState } = {}) {
    const range = getComposerSelectionRange(element);
    if (!range) {
      return false;
    }
    const listItem = findNearestComposerAncestor(range.startContainer, "li", element);
    if (listItem) {
      const marker = createComposerCaretMarkerElement();
      if (!marker) {
        return false;
      }
      range.deleteContents();
      range.insertNode(marker);
      const splitHtml = splitRichComposerListItemHtmlAtCaret(listItem.outerHTML || "");
      const insertedNodes = replaceComposerNodeWithHtml(listItem, splitHtml);
      const restoredMarker =
        findComposerCaretMarkerInNodes(insertedNodes) || findComposerCaretMarkerInNode(element);
      if (!restoreComposerCaretFromMarker(restoredMarker)) {
        const nextItem = insertedNodes[1] || insertedNodes[0] || null;
        if (nextItem) {
          placeComposerCaretAtStart(nextItem);
        }
      }
      runComposerSync(syncState, element);
      return true;
    }
    if (findNearestComposerAncestor(range.startContainer, "code", element)) {
      return insertComposerPlainText(element, "\n", { preserveNewlines: true, syncState });
    }
    return insertComposerLineBreak(element, syncState);
  }

  function createRichComposerRuntime({ getToolbarRoot, onAttach, syncState } = {}) {
    const resolveToolbarRoot = () =>
      typeof getToolbarRoot === "function" ? getToolbarRoot() : getToolbarRoot || null;

    return {
      syncToolbarStateFromElement(element) {
        const toolbarState =
          !isRichTextComposerElement(element) || isComposerElementDisabled(element)
            ? buildDefaultComposerToolbarState()
            : getRichComposerSelectionContext(element);
        applyComposerToolbarStateToButtons(resolveToolbarRoot(), toolbarState);
        return toolbarState;
      },
      handleToolbarAction(action, element) {
        return handleRichComposerToolbarAction(action, element, { onAttach, syncState });
      },
      handleListDeletion(event, element) {
        return handleRichComposerListDeletion(event, element, { syncState });
      },
      handleShiftEnter(element) {
        return handleRichComposerShiftEnter(element, { syncState });
      },
      insertPlainText(element, text, options = {}) {
        return insertComposerPlainText(element, text, { ...options, syncState });
      },
    };
  }

  globalThis.SupportPortalComposer = {
    escapeHtml,
    sanitizeUrl,
    formatMultilineText,
    renderMarkdownMessage,
    buildDefaultComposerToolbarState,
    normalizeComposerToolbarActionStateKey,
    renderComposerFormattingToolbarButtons,
    normalizeRichComposerHtmlString,
    serializeRichComposerHtmlToMarkdown,
    buildRichComposerHtmlFromMarkdown,
    ensureRichComposerEditableLinesAroundCodeBlocksHtml,
    captureComposerPreservationState,
    restoreComposerPreservationState,
    captureRichComposerSelectionBookmark,
    restoreRichComposerSelectionBookmark,
    isTextComposerElement,
    isRichTextComposerElement,
    isComposerElementDisabled,
    getRichComposerSelectionContext,
    applyComposerToolbarStateToButtons,
    placeComposerCaretAtEnd,
    placeComposerCaretAtStart,
    handleRichComposerToolbarAction,
    handleRichComposerListDeletion,
    handleRichComposerShiftEnter,
    insertComposerPlainText,
    createRichComposerRuntime,
  };
})();
