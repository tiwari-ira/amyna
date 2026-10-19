import { describe, expect, it } from "vitest";

import type { FinchNodeClaimSummary, PatientNotification } from "../src/coreClient";
import {
  APPROVAL_BUTTONS,
  DATA_CONSENT_TEXT,
  QUESTION_BUTTONS,
  copy,
  listItems,
  renderNotification,
} from "../src/ux/messages";

const base: PatientNotification = {
  notification_id: "ntf_1",
  case_id: "case_1",
  kind: "confirmation_question",
  text: "Did you receive anesthesia during your visit on September 15, 2026?",
  created_at: "2026-09-16T00:00:00Z",
};

describe("renderNotification", () => {
  it("renders questions with YES / NO / NOT SURE buttons tagged with the notification id", () => {
    const message = renderNotification(base);
    expect(message.buttons?.map((b) => b.label)).toEqual(["Yes", "No", "Not sure"]);
    expect(message.buttons?.map((b) => b.id)).toEqual(["ntf:ntf_1:YES", "ntf:ntf_1:NO", "ntf:ntf_1:NOT_SURE"]);
  });

  it("renders approval requests with Review / Approve / Reject", () => {
    const message = renderNotification({ ...base, kind: "approval_request", text: "ready" });
    expect(message.buttons?.map((b) => b.label)).toEqual(APPROVAL_BUTTONS.map((b) => b.label));
  });

  it("renders handoffs with Core's buttons and plain status updates without buttons", () => {
    const handoff = renderNotification({
      ...base,
      kind: "human_handoff",
      buttons: [
        { id: "DONE", label: "Done" },
        { id: "CANCEL", label: "Cancel call" },
      ],
    });
    expect(handoff.buttons?.map((b) => b.id)).toEqual(["ntf:ntf_1:DONE", "ntf:ntf_1:CANCEL"]);
    expect(renderNotification({ ...base, kind: "status_update" }).buttons).toBeUndefined();
  });

  it("keeps consent wording long enough for Core (>= 10 chars) and question buttons stable", () => {
    expect(DATA_CONSENT_TEXT.length).toBeGreaterThan(10);
    expect(QUESTION_BUTTONS.map((b) => b.id)).toEqual(["YES", "NO", "NOT_SURE"]);
  });
});

describe("visit notes", () => {
  it("reads as a sentence, with ruled-out services as 'no ...'", () => {
    const text = copy.visitNotes([
      { description: "MRI with contrast" },
      { description: "lorazepam (dose 1 mg)" },
      { description: "NOT anesthesia", negated: true },
    ]).text;
    expect(text).toMatch(
      /^Here's what I noted from your visit: MRI with contrast, lorazepam \(dose 1 mg\) and no anesthesia\.\n/,
    );
    expect(listItems(["a"])).toBe("a");
    expect(listItems(["a", "b"])).toBe("a and b");
  });

  it("says so plainly when nothing billable was heard", () => {
    expect(copy.visitNotes([]).text).toMatch(/^I went through your visit but didn't hear/);
  });
});

describe("pickBill", () => {
  const bill = (n: number) =>
    ({
      external_claim_id: `CLM-2026-0${n}`,
      provider_name: `Provider ${n}`,
      date_of_service: "2026-09-15",
      total_billed_cents: 1000 * n,
      itemized: true,
    }) as FinchNodeClaimSummary;

  it("lists every bill when there are few, with no extra note", () => {
    const message = copy.pickBill([bill(1), bill(2)]);
    expect(message.buttons?.length).toBe(2);
    expect(message.text).not.toMatch(/more bill/);
  });

  it("keeps four buttons but tells the patient how many more there are", () => {
    // The handler slices to MAX_BILL_BUTTONS and passes the hidden count here.
    const bills = [bill(1), bill(2), bill(3), bill(4), bill(5), bill(6)];
    const shown = bills.slice(0, 4);
    const message = copy.pickBill(shown, bills.length - shown.length);
    expect(message.buttons?.length).toBe(4);
    expect(message.text).toMatch(/2 more bills -- send its claim number/);
  });

  it("uses singular wording for exactly one hidden bill", () => {
    const message = copy.pickBill([bill(1), bill(2), bill(3), bill(4), bill(5)], 1);
    expect(message.text).toMatch(/1 more bill -- send its claim number/);
  });
});
