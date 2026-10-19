/** [3.2] deliverPending polls with a cursor after the first pull (delta only). */
import { describe, expect, it } from "vitest";

import { deliverPending } from "../src/handlers/humanHandoff";
import { CASE, setup } from "./support";

describe("notification polling cursor", () => {
  it("the first pull is full; later pulls send since= and only deliver the delta", async () => {
    const { core, relay, ctx } = setup();
    const convo = ctx.store.get("patient");
    convo.caseId = CASE;
    core.note("status_update", "first message", { created_at: "2026-10-17T10:00:00Z" });
    await deliverPending(ctx, convo);
    expect(core.notificationCalls[0]).toBeUndefined();
    expect(relay.outbox.at(-1)!.message.text).toBe("first message");
    expect(convo.lastNotificationAt).toBe("2026-10-17T10:00:00Z");

    core.note("status_update", "second message", { created_at: "2026-10-17T10:00:01Z" });
    await deliverPending(ctx, convo);
    expect(core.notificationCalls[1]).toBe("2026-10-17T10:00:00Z");
    // Only the delta was rendered; the first message is not repeated.
    expect(relay.outbox.at(-1)!.message.text).toBe("second message");
    expect(relay.outbox.filter((m) => m.message.text === "first message")).toHaveLength(1);
    expect(convo.lastNotificationAt).toBe("2026-10-17T10:00:01Z");
  });
});
