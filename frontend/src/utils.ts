export function formatDate(value?: string) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(value));
}

export function percent(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return Math.round(value * 100) + "%";
}

export function formatFilingDisplayName(fileName: string) {
  const withoutDocumentCount = fileName
    .replace(/^\s*\d+\.\s*/, "")
    .replace(/\s*\(\d+\s+documents?\)\s*$/i, "")
    .trim();
  const extensionMatch = withoutDocumentCount.match(/(\.[a-z0-9]+)$/i);
  const extension = extensionMatch?.[1] || "";
  const nameWithoutExtension = extension ? withoutDocumentCount.slice(0, -extension.length) : withoutDocumentCount;
  const readableName = nameWithoutExtension
    .replace(/(?:\s*\(\d+\))+$/g, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return `${readableName || nameWithoutExtension}${extension}`;
}

export function summarizeFTWilliamsMessage(message: string) {
  const normalized = message.replace(/\s+/g, " ").trim();
  const lowered = normalized.toLowerCase();

  if (lowered.includes("getaddrinfo") || lowered.includes("name resolution")) {
    return "Could not reach FT Williams. Try again shortly.";
  }
  if (lowered.includes("timeout") || lowered.includes("timed out")) {
    return "FT Williams did not respond in time.";
  }
  if (
    (lowered.includes("company id") && lowered.includes("not valid")) ||
    lowered.includes("did not find a matching plan")
  ) {
    return "No matching FT Williams plan was found.";
  }

  const firstMessage = normalized.split(/;|\n/)[0]?.trim() || normalized;
  return firstMessage.length > 110 ? `${firstMessage.slice(0, 107).trimEnd()}...` : firstMessage;
}
