import { describe, expect, it } from "vitest";

import {
  correctionInputType,
  correctionInputValue,
  parseFieldCorrection,
} from "./correctionInput";

describe("warranty field corrections", () => {
  it.each([
    "merchant_or_manufacturer",
    "product_name",
    "order_number",
    "desired_outcome",
    "serial_number",
    "purchase_proof",
    "defect_description",
    "defect_media",
  ])("edits %s as ordinary text without JSON quotes", (field) => {
    expect(correctionInputType(field, "旧内容")).toBe("text");
    expect(correctionInputValue(field, "旧内容")).toBe("旧内容");
    expect(parseFieldCorrection(field, "旧内容", '  本地测试 "修正"  ')).toBe(
      '本地测试 "修正"',
    );
  });

  it("uses a date input and preserves the calendar date without timezone conversion", () => {
    expect(correctionInputType("purchase_date", "2026-09-01")).toBe("date");
    expect(correctionInputValue("purchase_date", "2026-09-01")).toBe("2026-09-01");
    expect(parseFieldCorrection("purchase_date", "2026-09-01", "2024-02-29")).toBe(
      "2024-02-29",
    );
  });

  it.each(["", "  ", "2026-02-29", "2026-04-31", "2026-13-01", "2026-00-01", "2026-01-00", "0000-01-01", "2026-9-1", "2026-09-01T00:00:00Z"])(
    "rejects the invalid date %j before submission",
    (input) => {
      expect(() => parseFieldCorrection("purchase_date", "2026-09-01", input)).toThrow();
    },
  );

  it("rejects blank and overlong plain text corrections", () => {
    expect(() => parseFieldCorrection("product_name", "旧内容", " \n ")).toThrow();
    expect(() => parseFieldCorrection("product_name", "旧内容", "字".repeat(1001))).toThrow();
    expect(parseFieldCorrection("product_name", "旧内容", "字".repeat(1000))).toHaveLength(1000);
  });

  it("retains JSON editing for unrelated fields and structured values", () => {
    expect(correctionInputType("custom_number", 200)).toBe("json");
    expect(correctionInputType("product_name", { model: "旧型号" })).toBe("json");
    expect(correctionInputType("purchase_date", null)).toBe("json");
    expect(correctionInputValue("amount", 200)).toBe("200");
    expect(correctionInputValue("custom_text", "旧内容")).toBe('"旧内容"');
    expect(parseFieldCorrection("amount", 200, "300")).toBe(300);
    expect(parseFieldCorrection("product_name", { model: "旧型号" }, '{"model":"新型号"}')).toEqual({ model: "新型号" });
    expect(() => parseFieldCorrection("custom_text", "旧内容", "不带引号")).toThrow();
  });
});

describe("four-template fact corrections", () => {
  it.each([
    "merchant", "item_name", "return_reason", "currency", "expense_category",
    "business_purpose", "return_tracking_number", "approval_reference",
    "order_or_reference", "cancellation_confirmation_reference", "reference_evidence",
    "delivery_proof", "item_condition", "receipt", "payment_proof",
  ])("keeps %s editable as ordinary text after confirmation", (field) => {
    expect(correctionInputType(field, "合成旧值")).toBe("text");
    expect(parseFieldCorrection(field, "合成旧值", "  合成修正值  ")).toBe("合成修正值");
  });

  it.each(["delivery_date", "expense_date", "purchase_or_event_date"])("uses a calendar date for %s", field => {
    expect(correctionInputType(field, "2026-09-01")).toBe("date");
    expect(parseFieldCorrection(field, "2026-09-01", "2024-02-29")).toBe("2024-02-29");
    expect(() => parseFieldCorrection(field, "2026-09-01", "2026-02-29")).toThrow();
  });

  it.each(["amount", "paid_amount"])("preserves the existing numeric %s without currency conversion", field => {
    expect(correctionInputType(field, 200)).toBe("number");
    expect(correctionInputValue(field, 200)).toBe("200");
    expect(parseFieldCorrection(field, 200, "300")).toBe(300);
    expect(() => parseFieldCorrection(field, 200, "")).toThrow();
    expect(() => parseFieldCorrection(field, 200, "Infinity")).toThrow();
    expect(() => parseFieldCorrection(field, 200, '"300"')).toThrow();
    expect(() => parseFieldCorrection(field, 200, "200.5")).toThrow();
    expect(() => parseFieldCorrection(field, 200, "9007199254740993")).toThrow();
    expect(parseFieldCorrection(field, "200.00", "300.00")).toBe("300.00");
  });
});
