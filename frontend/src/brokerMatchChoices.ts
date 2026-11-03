import type { FTWilliamsReview } from "./types";

export interface BrokerMatchChoice { index: number; label: string }

export function brokerMatchChoices(review: FTWilliamsReview | null): BrokerMatchChoice[] {
  const record = review?.schedule_a_records?.find((item) => String(item.ftw_seq_no) === String(review.ftw_seq_no));
  if (!record) return [];
  const subparts = record.query_subparts as { Broker?: Record<string, unknown>[] } | undefined;
  let rows = subparts?.Broker?.filter((row) => Object.values(row).some((value) => String(value ?? "").trim())) || [];
  const suffix = (tag: string) => tag.match(/^(?:Name|AddressLine1|AddressLine2|City|State|ZipCode|PostalCode|ProvinceOrState|ForeignAddy|Country|CommPdAmt|FeesPdAmt|Code|FeesPdText)(\d{1,2})$/)?.[1];
  if (!rows.length || (rows.length > 1 && rows.every((row) => Object.keys(row).length === 1))) {
    const entries = rows.length ? rows.flatMap(Object.entries) : Object.entries((record.query_results || {}) as Record<string, unknown>);
    const indexed = entries.map(([tag, value]) => ({ tag, value, index: Number(suffix(tag)) }));
    if ((indexed.every(({ index }) => index > 0) && new Set(indexed.map(({ index }) => index)).size > 1) || !rows.length) {
      const grouped = new Map<number, Record<string, unknown>>();
      for (const { tag, value, index } of indexed) {
        if (!(index > 0)) continue;
        grouped.set(index, { ...grouped.get(index), [tag]: value });
      }
      rows = [...grouped].sort(([first], [second]) => first - second).map(([, row]) => row);
    }
  }
  const value = (row: Record<string, unknown>, name: string) => String(Object.entries(row)
    .find(([tag]) => new RegExp(`^${name}(?:XX|\\d{1,2})$`).test(tag))?.[1] ?? "").trim();
  return rows.map((row, index) => ({ index, label: `FT Williams row ${index + 1}: ${[
    value(row, "Name"), value(row, "AddressLine1"), value(row, "AddressLine2"),
    value(row, "City"), value(row, "State") || value(row, "ProvinceOrState"),
    value(row, "ZipCode") || value(row, "PostalCode"),
  ].filter(Boolean).join(", ")}` })).filter(({ label }) => !label.endsWith(": "));
}
