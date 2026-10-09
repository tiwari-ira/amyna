/** Final patient report once a case reaches a terminal state. */
import type { CaseDetail } from "../coreClient";
import type { Conversation } from "../store";
import { type HandlerContext, say } from "./context";

function money(cents: number): string {
  return `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2 })}`;
}

export function buildReport(detail: CaseDetail): string {
  const lines = [`Case summary (${detail.state})`];
  for (const finding of detail.findings ?? []) {
    lines.push(`- ${finding.title}: ${money(finding.amount_cents)} [${finding.status}]`);
  }
  const call = detail.calls?.at(-1);
  const response = call?.insurer_response;
  if (response) {
    const byId = new Map((detail.claim?.lines ?? []).map((line) => [line.line_id, line]));
    const describe = (ids: string[] | undefined) =>
      (ids ?? []).map((id) => byId.get(id)).filter(Boolean).map((l) => money(l!.billed_cents));
    const removed = describe(response.removed_line_ids);
    const itemized = describe(response.itemization_requested_line_ids);
    if (removed.length) lines.push(`Insurer will remove: ${removed.join(", ")}.`);
    if (itemized.length) lines.push(`Itemization requested for: ${itemized.join(", ")}.`);
    if (response.reference_number) lines.push(`Reference number: ${response.reference_number}`);
  }
  lines.push("Synthetic demo data. Keep this message for your records.");
  return lines.join("\n");
}

export async function sendCaseReport(
  ctx: HandlerContext,
  convo: Conversation,
  detail: CaseDetail,
): Promise<void> {
  if (convo.reportSent) return;
  convo.reportSent = true;
  await say(ctx, convo, buildReport(detail));
}
