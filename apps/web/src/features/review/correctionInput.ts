import type { JsonValue } from "./types";

const textFields = new Set([
  "merchant_or_manufacturer",
  "product_name",
  "order_number",
  "desired_outcome",
  "serial_number",
  "purchase_proof",
  "defect_description",
  "defect_media",
  "merchant", "item_name", "paid_amount", "amount", "return_reason", "currency",
  "expense_category", "business_purpose", "return_tracking_number", "approval_reference",
  "order_or_reference", "cancellation_confirmation_reference", "reference_evidence",
  "delivery_proof", "item_condition", "receipt", "payment_proof",
]);
const dateFields = new Set(["purchase_date", "delivery_date", "expense_date", "purchase_or_event_date"]);

export function correctionInputType(
  field: string,
  value: JsonValue,
): "text" | "date" | "number" | "json" {
  if (["amount", "paid_amount"].includes(field) && typeof value === "number") return "number";
  if (typeof value !== "string") return "json";
  if (dateFields.has(field)) return "date";
  return textFields.has(field) ? "text" : "json";
}

export function correctionInputValue(field: string, value: JsonValue): string {
  return correctionInputType(field, value) === "json"
    ? JSON.stringify(value, null, 2)
    : String(value);
}

export function parseFieldCorrection(
  field: string,
  currentValue: JsonValue,
  input: string,
): JsonValue {
  const type = correctionInputType(field, currentValue);
  if (type === "json") {
    try {
      return JSON.parse(input) as JsonValue;
    } catch {
      throw new Error('修正值必须是有效 JSON，例如字符串需要写成 "修正内容"。');
    }
  }
  const value = input.trim();
  if (!value) throw new Error("请填写修正值，不能只填写空白。");
  if (value.length > 1000) throw new Error("修正值不能超过 1000 个字符。");
  if (type === "number") {
    let parsed: unknown;
    try { parsed = JSON.parse(value); } catch { throw new Error("请填写有效数值，保留原值的金额单位。"); }
    if (typeof parsed !== "number" || !Number.isSafeInteger(parsed)) throw new Error("请填写安全范围内的整数，保留原值的金额单位。");
    return parsed;
  }
  if (type === "date" && !validCalendarDate(value)) {
    throw new Error("请填写有效日期，格式为 YYYY-MM-DD。");
  }
  return value;
}

function validCalendarDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  if (year < 1 || month < 1 || month > 12 || day < 1) return false;
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= days[month - 1];
}
