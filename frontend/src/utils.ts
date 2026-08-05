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
