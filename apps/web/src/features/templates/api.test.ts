import { describe, expect, it } from "vitest";

import { listTemplates } from "./api";
import {
  templateCatalogView,
  templateIdentity,
  templateOptionLabel,
} from "./presenters";
import type { TemplateCatalogItem } from "./types";

const catalog: TemplateCatalogItem[] = [
  {
    case_type: "return_refund",
    template_id: "return_refund_claim",
    template_version: 1,
    title: "商品退货、退款或价差处理",
    goal_options: ["refund", "replacement"],
    required_claims: ["merchant", "order_number"],
    evidence_requirements: [{ key: "purchase_proof", description: "购买凭证" }],
    content_sha256: "a".repeat(64),
  },
];

describe("template catalog client", () => {
  it("loads reviewed selector and immutable identity metadata", async () => {
    const request = async () =>
      new Response(JSON.stringify(catalog), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });

    await expect(listTemplates(request)).resolves.toEqual(catalog);
    expect(templateIdentity(catalog[0])).toBe("return_refund_claim@1");
    expect(templateOptionLabel(catalog[0])).toBe("商品退货、退款或价差处理");
  });

  it("distinguishes offline and safe typed failures", async () => {
    const offline = async () => {
      throw new TypeError("network unavailable");
    };
    const failed = async () =>
      new Response(
        JSON.stringify({
          error: {
            code: "template_catalog_unavailable",
            message: "Template catalog could not be read",
            details: {},
            retryable: true,
            request_id: "req_synthetic",
          },
        }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      );

    await expect(listTemplates(offline)).rejects.toMatchObject({ code: "network_error" });
    await expect(listTemplates(failed)).rejects.toMatchObject({
      code: "template_catalog_unavailable",
      retryable: true,
    });
  });
});

describe("template catalog presentation", () => {
  it("covers loading, empty, offline, failure, and populated states", () => {
    expect(templateCatalogView("loading", 0)).toBe("loading");
    expect(templateCatalogView("ready", 0)).toBe("empty");
    expect(templateCatalogView("offline", 0)).toBe("offline");
    expect(templateCatalogView("error", 0)).toBe("failure");
    expect(templateCatalogView("ready", 3)).toBe("populated");
  });
});
